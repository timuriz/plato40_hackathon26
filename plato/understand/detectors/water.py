"""S02 water-leak detector.

Idea: a leak never pauses, a guest does. So we look at the **trailing rolling minimum** of per-step
water use. If the minimum over several hours stays clearly above a room's own vacant baseline, water is
flowing continuously - a leak - even while the room is occupied (a running toilet valve). A guest shower
drives the mean up but the rolling minimum drops back to baseline between uses, so it does not fire.

Causality: per step we use only cleaned flow at or before t, a baseline built from earlier vacant+ok
steps (with a building-wide warmup prior), and the as-of work-order / alarm view. Meter resets and the
week after an exchange are excluded here; resets are reported by the data-quality detector instead.
"""
from __future__ import annotations

from typing import List

import numpy as np
import pandas as pd

from plato.observe.quality import Q_JITTER, Q_OK

from ..candidate import WATER, Candidate
from ..causal import expanding_robust_baseline, find_runs, pooled_warmup, rolling_min_valid, run_length_so_far
from .common import bms_alarms_asof, is_night_local, work_orders_asof

WINDOW_STEPS = 12          # 3 h trailing window for the rolling minimum
MIN_VALID = 6              # need at least this many real readings in the window
BASELINE_MIN_COUNT = 24    # earlier vacant+ok steps before a per-room baseline replaces the warmup prior
ABS_FLOOR_L = 0.8          # ignore rolling minima at/below this (L/15 min): rounding noise. A real leak is
                           # caught by continuity (the rolling MIN stays positive for hours) + the z-score,
                           # not by magnitude, so this stays low to catch a slowly running toilet valve.
Z_THRESHOLD = 4.0          # rolling min this many robust sigmas above baseline = suspicious
MIN_RUN_STEPS = 8          # the condition must hold this long (2 h) before a candidate is raised


def _room_flow_l_per_step(obs, room: str) -> np.ndarray:
    """Per-step water use in L/15 min for one room, NaN unless it is a clean single-step reading."""
    delta = obs.water.rooms.delta_l[room].to_numpy(dtype=float)
    q = obs.water.rooms.quality[room].to_numpy()
    span1 = obs.water.rooms.span_steps[room].to_numpy() == 1
    post = obs.water.rooms.post_exchange[room].to_numpy(dtype=bool)
    ok = np.isin(q, (Q_OK, Q_JITTER)) & span1 & ~post  # jitter delta is 0, a valid "no use"
    out = np.where(ok, delta, np.nan)
    return out


def detect_water(obs) -> List[Candidate]:
    grid = obs.grid
    rooms = list(obs.building.rooms)
    occ_all = obs.rooms.occupied
    balance_resid = obs.water.balance["residual_l"].to_numpy(dtype=float) if len(obs.water.balance) else None
    out: List[Candidate] = []

    for room in rooms:
        flow = _room_flow_l_per_step(obs, room)
        if np.isnan(flow).all():
            continue
        occ = occ_all[room].to_numpy(dtype=bool)
        rmin = rolling_min_valid(flow, WINDOW_STEPS, MIN_VALID)
        eligible = (~occ) & ~np.isnan(flow)
        warmup = pooled_warmup(flow, eligible)
        base_med, base_sig = expanding_robust_baseline(flow, eligible, BASELINE_MIN_COUNT, warmup)

        with np.errstate(invalid="ignore"):
            z = (rmin - base_med) / base_sig
            hot = (~np.isnan(rmin)) & (rmin > ABS_FLOOR_L) & (z >= Z_THRESHOLD)
        runlen = run_length_so_far(hot)

        post = obs.water.rooms.post_exchange[room].to_numpy(dtype=bool)
        for s, e in find_runs(hot):
            # the candidate is raised once the run has lasted MIN_RUN_STEPS, at that step (causal)
            onset = s + MIN_RUN_STEPS - 1
            if onset >= e:
                continue
            for i in range(onset, e):
                if runlen[i] < MIN_RUN_STEPS:
                    continue
                # only keep the onset step and then every 2 h; the episode builder merges the rest
                if i != onset and (i - onset) % 8 != 0:
                    continue
                out.append(_mk_candidate(obs, room, grid, flow, rmin, z, base_med, occ, post,
                                         balance_resid, s, i))
    out += _main_residual_candidates(obs)
    out.sort(key=lambda c: (c.t, c.asset))
    return out


def _mk_candidate(obs, room, grid, flow, rmin, z, base_med, occ, post, balance_resid, run_start, i) -> Candidate:
    t = grid[i]
    seg = flow[run_start:i + 1]
    segv = seg[~np.isnan(seg)]
    occ_share = float(occ[run_start:i + 1].mean())
    cv = float(np.std(segv) / np.mean(segv)) if segv.size and np.mean(segv) > 0 else float("nan")
    night = any(is_night_local(grid[j]) for j in range(run_start, i + 1))
    resid_med = float("nan")
    if balance_resid is not None:
        rr = balance_resid[run_start:i + 1]
        rr = rr[~np.isnan(rr)]
        resid_med = float(np.median(rr)) if rr.size else float("nan")
    wos = work_orders_asof(obs, t, [room])
    flags = []
    if post[i]:
        flags.append("post_exchange")
    est_share = _label_share(obs, room, run_start, i, {4, 8})  # estimated / span
    feats = {
        "min_flow_l_per_15min": float(rmin[i]),
        "median_flow_l_per_15min": float(np.median(segv)) if segv.size else float("nan"),
        "baseline_median_l_per_15min": float(base_med[i]) if base_med[i] == base_med[i] else 0.0,
        "flow_z": float(z[i]),
        "flow_cv": cv,
        "occupied_share": occ_share,
        "duration_h": round((i - run_start + 1) * 0.25, 2),
        "night_flow_present": bool(night),
        "estimated_or_span_share": round(est_share, 3),
        "main_minus_rooms_residual_l_median": resid_med,
    }
    sev = "high" if (z[i] >= 6 and (i - run_start + 1) >= 16) else "medium"
    return Candidate(detector=WATER, asset=room, t=t, start=grid[run_start], severity_hint=sev,
                     features=feats, quality_flags=flags, affected_assets=[room, "WM_MAIN"],
                     work_orders=wos, bms_alarms=bms_alarms_asof(obs, t, [room, "WM_MAIN"]))


def _label_share(obs, room, s, i, codes) -> float:
    q = obs.water.rooms.quality[room].to_numpy()[s:i + 1]
    return float(np.isin(q, list(codes)).mean()) if len(q) else 0.0


def _main_residual_candidates(obs) -> List[Candidate]:
    """Leak visible only between the main meter and the room meters: residual high vs its hour-of-day norm."""
    bal = obs.water.balance
    if not len(bal) or "residual_l" not in bal:
        return []
    grid = obs.grid
    resid = bal["residual_l"].to_numpy(dtype=float)
    complete = bal["complete"].to_numpy(dtype=bool) if "complete" in bal else ~np.isnan(resid)
    hod = pd.Series(grid).dt.hour.to_numpy()
    # causal hour-of-day baseline: median/MAD of earlier complete residuals at the same hour
    med = np.full(len(grid), np.nan)
    sig = np.full(len(grid), np.nan)
    pools = {h: [] for h in range(24)}
    for i in range(len(grid)):
        p = pools[hod[i]]
        if len(p) >= 8:
            m = float(np.median(p)); med[i] = m
            sig[i] = max(1.4826 * float(np.median(np.abs(np.array(p) - m))), 1e-6)
        if complete[i] and not np.isnan(resid[i]):
            p.append(float(resid[i]))
    with np.errstate(invalid="ignore"):
        z = (resid - med) / sig
        hot = complete & (~np.isnan(z)) & (z >= 6.0) & (resid > 20.0)
    runlen = run_length_so_far(hot)
    out: List[Candidate] = []
    for s, e in find_runs(hot):
        onset = s + MIN_RUN_STEPS - 1
        for i in range(max(onset, s), e):
            if runlen[i] < MIN_RUN_STEPS or (i != max(onset, s) and (i - s) % 8 != 0):
                continue
            t = grid[i]
            seg = resid[s:i + 1]; seg = seg[~np.isnan(seg)]
            feats = {"residual_l_median": float(np.median(seg)) if seg.size else float("nan"),
                     "residual_z": float(z[i]), "duration_h": round((i - s + 1) * 0.25, 2),
                     "note": "excess water at the main meter not explained by room meters (common areas or hidden leak)"}
            out.append(Candidate(detector=WATER, asset="WM_MAIN", t=t, start=grid[s], severity_hint="medium",
                                 features=feats, affected_assets=["WM_MAIN"],
                                 work_orders=work_orders_asof(obs, t, ["WM_MAIN"])))
    return out
