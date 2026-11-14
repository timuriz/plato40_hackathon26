"""S04 maintenance cascade: AHU1 filter loading vs. a plain data gap, and who can fix it when.

What the data looks like: airflow follows a day/night schedule, so raw ``filter_dp_pa`` swings with it. The
filter pressure drop scales roughly with airflow squared, so we divide it by ``(airflow / nominal)^2`` and
get a flow-independent "loading" number. A clean filter sits at its own baseline; a loading filter drifts up
and (while the fan tries to hold the airflow) ``fan_kw`` per airflow^3 rises with it, and eventually the
delivered airflow falls below its usual schedule.

Two kinds of candidate come out of this file:

* ``ventilation_maintenance`` (asset AHU1): degradation **seen in valid data**: a rising normalised
  ``filter_dp_pa`` trend (or a level far above the cleanest level seen so far). If the pressure sensor is
  blind, the same candidate is only raised when *measured* airflow is below its schedule while the fan works
  harder (a restricted flow); that case is labelled ``dp_sensor_missing`` and is lower confidence.
* ``ventilation_sensor_gap`` (asset AHU1): a raw-blank run of ``filter_dp_pa``. A gap is missing data, never a
  measured pressure drop, so this is a data-quality finding. Whatever the pressure did before the gap and the
  fan/airflow drift during it is attached as corroboration only.

Both carry the technician view as known at ``t`` (roster sick/vacation honours ``status_known_utc``): the next
real slot inside a scheduled shift, or "contractor needed" if nobody in-house is available soon.

Causality: every number uses rows at or before ``t`` (trailing rolling medians, a cumulative-minimum baseline,
an expanding per-time-of-day airflow norm) plus ``obs.asof(t)`` for the roster and work orders.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from plato.observe.timeutil import LOCAL_TZ

from ..candidate import MAINTENANCE, MAINTENANCE_GAP, Candidate, _iso
from ..causal import find_runs, run_length_so_far
from .common import work_orders_asof

DP, AIR, FAN = "AHU1.filter_dp_pa", "AHU1.airflow_m3h", "AHU1.fan_kw"
NOMINAL_AIRFLOW = 4200.0
MIN_AIRFLOW = 1000.0          # below this the unit is effectively off: do not normalise
STEPS_PER_DAY = 96

W_LEVEL = 48                  # 12 h trailing median for the current level
MIN_LEVEL = 16                # real readings needed inside that window
W_NOW = 8                     # 2 h window: the level must still be there right now (ends fast after a repair)
MIN_NOW = 4
W_BASE = 96                   # 24 h median used to learn the cleanest level seen so far
MIN_BASE = 48
RATIO_RISING = 1.15           # level vs clean baseline that counts once it is also rising
SLOPE_REL_PER_DAY = 0.03      # rise of the 12 h level over 24 h, as a share of baseline
RATIO_LEVEL = 1.35            # level alone (no trend needed) at this ratio
RATIO_HIGH = 1.8
SHORTFALL_FLOW = 0.05         # airflow this far under its own schedule = flow is restricted
FAN_RATIO_FLOW = 1.3          # fan power per airflow^3 vs baseline when the dp sensor is blind
MIN_RUN_STEPS = 4             # condition must hold 1 h before a candidate is raised
REEMIT_STEPS = 24             # re-raise every 6 h while it persists (episode builder merges them)
GAP_MIN_STEPS = 8             # a blank dp run of 2 h makes a gap candidate
GAP_REEMIT_STEPS = 48         # every 12 h while the gap lasts
CONTRACTOR_AFTER_H = 72.0     # no in-house slot within this long -> ask an external contractor
RESPONSE_LEAD = pd.Timedelta("1h")
MIN_SLOT_H = 2.0              # a shift remainder shorter than this is not a usable slot
UNAVAILABLE = ("sick", "vacation", "out_of_service")


def _series(obs, name: str) -> Optional[np.ndarray]:
    v = obs.equipment.wide.values
    if name not in v.columns:
        return None
    return v[name].to_numpy(dtype=float)


def _nominal_airflow(obs) -> float:
    try:
        for a in obs.building.raw.get("assets", []):
            if a.get("id") == "AHU1" and a.get("nominal_airflow_m3h"):
                return float(a["nominal_airflow_m3h"])
    except Exception:  # fake obs / odd building file
        pass
    return NOMINAL_AIRFLOW


def _roll_median(x: np.ndarray, window: int, min_periods: int) -> np.ndarray:
    return pd.Series(x).rolling(window, min_periods=min_periods).median().to_numpy()


def _cummin(x: np.ndarray) -> np.ndarray:
    """Running minimum that ignores NaN and stays NaN only until the first real value (causal)."""
    out = np.full(len(x), np.nan)
    cur = np.nan
    for i, v in enumerate(x):
        if not np.isnan(v) and (np.isnan(cur) or v < cur):
            cur = v
        out[i] = cur
    return out


def _expected_airflow(obs, air: np.ndarray) -> np.ndarray:
    """Airflow the unit usually delivers at this local time of day: median of the last 14 earlier values in the
    same quarter-hour slot (needs 3). NaN until learned. Row i never uses row i itself or later rows."""
    loc = obs.grid.tz_convert(LOCAL_TZ)
    slot = (loc.hour * 4 + loc.minute // 15).to_numpy()
    hist: Dict[int, List[float]] = {}
    out = np.full(len(air), np.nan)
    for i in range(len(air)):
        h = hist.setdefault(int(slot[i]), [])
        if len(h) >= 3:
            out[i] = float(np.median(h))
        if not np.isnan(air[i]) and air[i] >= MIN_AIRFLOW:
            h.append(float(air[i]))
            if len(h) > 14:
                h.pop(0)
    return out


def _roster_slot(obs, t: pd.Timestamp) -> Dict[str, Any]:
    """Technician availability as known at t. Never invents a slot: with no roster rows we say so."""
    ops = {}
    try:
        ops = obs.building.raw.get("operations", {}) or {}
    except Exception:
        pass
    shift_local = ops.get("technician_shift_local", "08:00-16:30 Mon-Fri")
    r = obs.asof(t).roster()
    out: Dict[str, Any] = {"technician_shift_local": shift_local, "technician_roster_known": False,
                           "technician_next_slot_utc": None, "technician_slot_end_utc": None,
                           "technician_id": None, "technician_unavailable": [], "contractor_needed": True,
                           "technician_wait_h": None}
    if r is None or r.empty or "role" not in r.columns:
        return out
    tech = r[r["role"] == "technician"]
    if tech.empty:
        return out
    out["technician_roster_known"] = True
    tech = tech[tech["end_utc"] > t].sort_values("start_utc")
    ok = tech[tech["status"] == "scheduled"]
    for row in ok.itertuples(index=False):
        start = max(pd.Timestamp(row.start_utc), t + RESPONSE_LEAD)
        end = pd.Timestamp(row.end_utc)
        if (end - start) >= pd.Timedelta(hours=MIN_SLOT_H):
            start = start.ceil("15min")
            out.update(technician_next_slot_utc=_iso(start), technician_slot_end_utc=_iso(end),
                       technician_id=str(row.resource_id),
                       technician_wait_h=round((start - t).total_seconds() / 3600.0, 1))
            break
    horizon = t + pd.Timedelta(days=7)
    bad = tech[tech["status"].isin(UNAVAILABLE) & (tech["start_utc"] <= horizon)]
    out["technician_unavailable"] = [
        {"resource": str(b.resource_id), "status": str(b.status), "shift_start_utc": _iso(b.start_utc)}
        for b in bad.itertuples(index=False)][:8]
    wait = out["technician_wait_h"]
    out["contractor_needed"] = wait is None or wait > CONTRACTOR_AFTER_H
    return out


def _open_work_order(obs, t: pd.Timestamp) -> Dict[str, Any]:
    w = obs.asof(t).work_orders()
    if w.empty or "asset_id" not in w.columns:
        return {}
    w = w[w["asset_id"].astype(str) == "AHU1"]
    if "status" in w.columns:
        w = w[~w["status"].astype(str).isin(["completed", "cancelled"])]
    if w.empty:
        return {}
    r = w.iloc[-1]
    sched = r.get("scheduled_utc") if hasattr(r, "get") else None
    return {"open_work_order": str(r.get("work_order_id", "")),
            "open_work_order_scheduled_utc": _iso(sched) if sched is not None and not pd.isna(sched) else None}


def _num(x) -> Optional[float]:
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else float(x)


def detect_maintenance(obs) -> List[Candidate]:
    dp, air, fan = _series(obs, DP), _series(obs, AIR), _series(obs, FAN)
    if dp is None or air is None:
        return []
    n = len(dp)
    grid = obs.grid
    nominal = _nominal_airflow(obs)
    raw_missing = (obs.equipment.wide.raw_missing[DP].to_numpy(dtype=bool)
                   if DP in obs.equipment.wide.raw_missing.columns else np.isnan(dp))
    fan = fan if fan is not None else np.full(n, np.nan)

    flow_ok = ~np.isnan(air) & (air >= MIN_AIRFLOW)
    scale = np.where(flow_ok, air / nominal, np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        dpn = dp / scale ** 2           # flow-independent filter loading (Pa at nominal airflow)
        fann = fan / scale ** 3         # fan power at nominal airflow
    expected = _expected_airflow(obs, air)
    with np.errstate(invalid="ignore", divide="ignore"):
        shortfall = np.clip(1.0 - air / expected, 0.0, None)

    lvl = _roll_median(dpn, W_LEVEL, MIN_LEVEL)
    lvl_prev = pd.Series(lvl).shift(STEPS_PER_DAY).to_numpy()   # the same measure one day earlier
    now = _roll_median(dpn, W_NOW, MIN_NOW)
    base = _cummin(_roll_median(dpn, W_BASE, MIN_BASE))         # cleanest 24 h level seen up to t
    fan_lvl = _roll_median(fann, W_LEVEL, MIN_LEVEL)
    fan_base = _cummin(_roll_median(fann, W_BASE, MIN_BASE))
    short_now = _roll_median(shortfall, W_NOW, MIN_NOW)
    dp_valid_recent = pd.Series((~np.isnan(dp)).astype(float)).rolling(W_NOW, min_periods=1).sum().to_numpy()

    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = lvl / base
        ratio_now = now / base
        slope_rel = (lvl - lvl_prev) / base
        fan_ratio = fan_lvl / fan_base
    dp_valid = ~np.isnan(dp)
    rising = (ratio >= RATIO_RISING) & (slope_rel >= SLOPE_REL_PER_DAY)
    hot_dp = dp_valid & (ratio_now >= RATIO_RISING) & (rising | (ratio >= RATIO_LEVEL))
    blind = dp_valid_recent < MIN_NOW
    hot_flow = blind & ~hot_dp & (short_now >= SHORTFALL_FLOW) & (fan_ratio >= FAN_RATIO_FLOW)
    hot = hot_dp | hot_flow
    hot_run = run_length_so_far(hot)

    out: List[Candidate] = []
    for s, e in find_runs(hot):
        onset = s + MIN_RUN_STEPS - 1
        for i in range(onset, e):
            if hot_run[i] < MIN_RUN_STEPS or (i - onset) % REEMIT_STEPS != 0:
                continue
            out.append(_maintenance_candidate(obs, grid, i, s, dp, dpn, air, fan, base, ratio, slope_rel,
                                              fan_ratio, short_now, hot_dp[i]))
    out += _gap_candidates(obs, grid, dp, dpn, air, fan, raw_missing, base, ratio, slope_rel, fan_ratio,
                           short_now, hot_dp)
    out.sort(key=lambda c: (c.t, c.detector, c.asset))
    return out


def _last_valid(arr: np.ndarray, i: int) -> Optional[int]:
    idx = np.where(~np.isnan(arr[:i + 1]))[0]
    return int(idx[-1]) if idx.size else None


def _maintenance_candidate(obs, grid, i, run_start, dp, dpn, air, fan, base, ratio, slope_rel, fan_ratio,
                           short_now, via_dp) -> Candidate:
    t = grid[i]
    sf = _num(short_now[i]) or 0.0
    r = _num(ratio[i])
    fr = _num(fan_ratio[i])
    if via_dp:
        high = (r is not None and r >= RATIO_HIGH) or sf >= SHORTFALL_FLOW or (fr is not None and fr >= 1.5)
        sev = "high" if high else ("medium" if (r or 0) >= RATIO_LEVEL else "low")
    else:  # flow loss without a readable pressure sensor: measured, but the cause is not confirmed
        sev = "high" if sf >= 0.10 else "medium"
    feats: Dict[str, Any] = {
        "basis": "filter_dp_trend" if via_dp else "airflow_fan_without_dp",
        "dp_sensor_missing": not bool(via_dp),
        "filter_dp_pa": _num(dp[i]),
        "dp_at_nominal_airflow_pa": _num(dpn[i]),
        "dp_baseline_pa": _num(base[i]),
        "dp_vs_baseline_ratio": r,
        "dp_rise_share_per_day": _num(slope_rel[i]),
        "fan_kw": _num(fan[i]),
        "fan_power_vs_baseline_ratio": fr,
        "airflow_m3h": _num(air[i]),
        "airflow_shortfall_share": round(sf, 3),
        "duration_h": round((i - run_start + 1) * 0.25, 2),
    }
    feats.update(_roster_slot(obs, t))
    feats.update(_open_work_order(obs, t))
    flags = [] if via_dp else ["dp_sensor_gap"]
    return Candidate(detector=MAINTENANCE, asset="AHU1", t=t, start=grid[run_start], severity_hint=sev,
                     features=feats, quality_flags=flags, route="rule", affected_assets=["AHU1"],
                     work_orders=work_orders_asof(obs, t, ["AHU1"]))


def _gap_candidates(obs, grid, dp, dpn, air, fan, raw_missing, base, ratio, slope_rel, fan_ratio, short_now,
                    hot_dp) -> List[Candidate]:
    gap = raw_missing & np.isnan(dp)
    run = run_length_so_far(gap)
    out: List[Candidate] = []
    for s, e in find_runs(gap):
        onset = s + GAP_MIN_STEPS - 1
        for i in range(onset, e):
            if run[i] < GAP_MIN_STEPS or (i - onset) % GAP_REEMIT_STEPS != 0:
                continue
            t = grid[i]
            lv = _last_valid(dp, s - 1) if s > 0 else None
            hours = round((i - s + 1) * 0.25, 2)
            fr = _num(fan_ratio[i])
            sf = _num(short_now[i]) or 0.0
            feats: Dict[str, Any] = {
                "issue": "filter_dp_pa_gap", "metric": "AHU1.filter_dp_pa",
                "gap_start_utc": _iso(grid[s]), "gap_hours_so_far": hours, "duration_h": hours,
                "last_valid_utc": _iso(grid[lv]) if lv is not None else None,
                "last_valid_dp_pa": _num(dp[lv]) if lv is not None else None,
                "pre_gap_dp_vs_baseline_ratio": _num(ratio[lv]) if lv is not None else None,
                "pre_gap_dp_rise_share_per_day": _num(slope_rel[lv]) if lv is not None else None,
                "pre_gap_degradation_in_valid_data": bool(lv is not None and hot_dp[lv]),
                "fan_kw": _num(fan[i]), "fan_power_vs_baseline_ratio": fr,
                "airflow_m3h": _num(air[i]), "airflow_shortfall_share": round(sf, 3),
                "fan_airflow_drift_corroborates": bool((fr or 0) >= FAN_RATIO_FLOW or sf >= SHORTFALL_FLOW),
                "note": "blank readings are missing data, not a measured pressure drop; the gap is not degradation",
            }
            sev = "medium" if hours >= 24 else "low"
            out.append(Candidate(detector=MAINTENANCE_GAP, asset="AHU1", t=t, start=grid[s], severity_hint=sev,
                                 features=feats, quality_flags=["sensor_gap"], route="rule",
                                 affected_assets=["AHU1"]))
    return out
