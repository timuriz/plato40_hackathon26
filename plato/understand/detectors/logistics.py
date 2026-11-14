"""Logistics detector: will a bin, the waste container or the vending machine hit its limit before the next
regular service, and is there still time to order an extra one?

Facts (building.json / README): ``WASTE_MAIN`` is emptied by the municipal service Tue + Fri 07:00 local; the
vending machine ``VEND1`` is restocked Mon + Thu 10:00 local; an extra pickup / restock needs ~24 h lead time.
Floor bins ``WASTE_F1..F4`` are emptied by housekeeping in the afternoon and a robot *could* carry one to the
container (~20 min). Fill values are capped at 100 %, so a bin at the cap "may be overflowing": it is a floor,
not a measurement, and we say so.

Method (hourly, causal): the fill (or stock burn) rate is the sum of the *rising* (or falling) hourly steps in
a trailing window divided by the hours seen, so an emptying or restock jump does not corrupt it. Rate x hours
until the next regular service gives the projected level at that service. If it crosses the limit first, the
timing matters: we raise the candidate once the order window opens (limit time minus the 24 h lead time, plus
a margin), because an earlier alert is only a guess and a later one cannot be served in time. Blank values
stay blank: a missing latest reading produces no candidate.

Candidates are ``route="rule"``; the episode builder merges the repeated hourly projections of one incident.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from plato.observe.timeutil import at_local, to_local

from ..candidate import LOGISTICS, Candidate, _iso
from ..causal import find_runs
from .common import work_orders_asof

LEAD_H = 24.0                    # extra pickup / restock lead time
ACT_MARGIN_H = 18.0              # raise this long before the latest useful order time
REEMIT_STEPS = 12                # hourly grid: re-raise every 12 h while it persists

WASTE_FULL_PCT = 95.0            # projected level at which the container counts as overflowing
WASTE_MIN_NOW_PCT = 40.0
WASTE_WINDOW_H = 72
FLOOR_FULL_PCT = 100.0
FLOOR_MIN_NOW_PCT = 60.0
FLOOR_WINDOW_H = 48
FLOOR_DEADLINE_LOCAL = "14:30"   # housekeeping empties floor bins in the afternoon (events ~14:30 local)
CAPPED_PCT = 99.5
MIN_RATE_SAMPLES = 24            # hourly steps with a real delta before a rate is trusted

VEND_WINDOW_H = 72
VEND_LOW_SHARE = 0.10            # reserve: restock is due when stock falls to this share of capacity
VEND_DEFAULT_CAP = {"drinks": 100.0, "snacks": 60.0}

WASTE_PICKUP_DAYS = (1, 4)       # Tue, Fri (Monday = 0)
WASTE_PICKUP_LOCAL = "07:00"
VEND_RESTOCK_DAYS = (0, 3)       # Mon, Thu
VEND_RESTOCK_LOCAL = "10:00"
CONTAINER_L, BIN_L = 1100.0, 240.0


def _next_weekly(t: pd.Timestamp, days, hhmm: str) -> pd.Timestamp:
    """First UTC instant strictly after t that is ``hhmm`` local on one of the weekdays (DST aware)."""
    d0 = to_local(t).normalize()
    for k in range(0, 9):
        day = d0 + pd.Timedelta(days=k)
        if day.weekday() in days:
            cand = at_local(day.strftime("%Y-%m-%d"), hhmm)
            if cand > t:
                return cand
    return t + pd.Timedelta(days=7)


def _next_daily(t: pd.Timestamp, hhmm: str) -> pd.Timestamp:
    d0 = to_local(t).normalize()
    for k in range(0, 3):
        cand = at_local((d0 + pd.Timedelta(days=k)).strftime("%Y-%m-%d"), hhmm)
        if cand > t:
            return cand
    return t + pd.Timedelta(days=1)


def _rate_per_h(x: np.ndarray, window: int, sign: int) -> np.ndarray:
    """Trailing mean change per hour counting only steps of the wanted direction (+1 rise, -1 fall).
    NaN when fewer than ``MIN_RATE_SAMPLES`` hourly steps with two real readings sit in the window."""
    s = pd.Series(x)
    d = s.diff()
    wanted = (d * sign).clip(lower=0.0).where(d.notna())
    total = wanted.rolling(window, min_periods=MIN_RATE_SAMPLES).sum()
    count = d.notna().astype(float).rolling(window, min_periods=1).sum()
    with np.errstate(invalid="ignore", divide="ignore"):
        rate = total / count
    rate[count < MIN_RATE_SAMPLES] = np.nan
    return rate.to_numpy()


def _count_events(obs, t: pd.Timestamp, etype: str, asset: str, hours: int = 24) -> int:
    ev = obs.asof(t).events()
    if ev.empty or "type" not in ev.columns:
        return 0
    m = (ev["type"] == etype) & (ev["timestamp_utc"] >= t - pd.Timedelta(hours=hours))
    if "asset_id" in ev.columns:
        m &= ev["asset_id"].astype(str) == asset
    return int(m.sum())


def _vend_capacity(obs) -> Dict[str, float]:
    cap = dict(VEND_DEFAULT_CAP)
    try:
        for a in obs.building.raw.get("assets", []):
            if a.get("id") == "VEND1":
                for k, v in (a.get("capacity") or {}).items():
                    cap[k] = float(v)
    except Exception:
        pass
    return cap


def _sev(hours_to_limit: Optional[float], capped: bool, helps: bool) -> str:
    if capped or not helps or (hours_to_limit is not None and hours_to_limit < LEAD_H):
        return "high"
    return "medium"


def detect_logistics(obs) -> List[Candidate]:
    wide = obs.logistics.wide.values
    if wide is None or wide.empty:
        return []
    out: List[Candidate] = []
    out += _waste_main(obs, wide)
    for col in [c for c in wide.columns if c.startswith("WASTE_F") and c.endswith(".fill_pct")]:
        out += _floor_bin(obs, wide, col)
    out += _vending(obs, wide)
    out.sort(key=lambda c: (c.t, c.asset))
    return out


def _emit_indices(hot: np.ndarray) -> List[int]:
    idx: List[int] = []
    for s, e in find_runs(hot):
        idx += list(range(s, e, REEMIT_STEPS))
    return idx


# ------------------------------------------------------------------ central container
def _waste_main(obs, wide: pd.DataFrame) -> List[Candidate]:
    col = "WASTE_MAIN.fill_pct"
    if col not in wide.columns:
        return []
    x = wide[col].to_numpy(dtype=float)
    idx = wide.index
    rate = _rate_per_h(x, WASTE_WINDOW_H, +1)
    info: Dict[int, Dict[str, Any]] = {}
    hot = np.zeros(len(x), dtype=bool)
    for i in range(len(x)):
        f, r = x[i], rate[i]
        if np.isnan(f) or np.isnan(r) or f < WASTE_MIN_NOW_PCT:
            continue
        t = idx[i]
        tp = _next_weekly(t, WASTE_PICKUP_DAYS, WASTE_PICKUP_LOCAL)
        h_pick = (tp - t).total_seconds() / 3600.0
        proj = f + r * h_pick
        if r <= 0 or proj < WASTE_FULL_PCT:
            continue
        h_full = max(0.0, (100.0 - f) / r)
        if h_full - LEAD_H > ACT_MARGIN_H:     # too early: the order window has not opened yet
            continue
        hot[i] = True
        info[i] = {"next_pickup": tp, "h_pick": h_pick, "proj": proj, "h_full": h_full, "rate": r}
    out = []
    for i in _emit_indices(hot):
        out.append(_waste_candidate(obs, idx, x, i, info[i]))
    return out


def _waste_candidate(obs, idx, x, i, d) -> Candidate:
    t = idx[i]
    h_full, h_pick = d["h_full"], d["h_pick"]
    helps = LEAD_H < h_pick                    # an extra pickup (24 h) lands before the regular one
    capped = bool(x[i] >= CAPPED_PCT)
    feats = {
        "subject": "waste_container", "fill_pct": round(float(x[i]), 1), "capped_at_100": capped,
        "fill_rate_pct_per_h": round(float(d["rate"]), 3),
        "next_regular_service_utc": _iso(d["next_pickup"]), "hours_to_next_service": round(h_pick, 1),
        "projected_pct_at_next_service": round(float(d["proj"]), 1),
        "hours_to_full": round(h_full, 1), "lead_time_h": LEAD_H,
        "order_by_utc": _iso(t + pd.Timedelta(hours=max(0.0, h_full - LEAD_H))),
        "earliest_extra_service_utc": _iso(t + pd.Timedelta(hours=LEAD_H)),
        "extra_service_helps": bool(helps),
        "waste_bin_full_events_24h": _count_events(obs, t, "waste_bin_full", "WASTE_MAIN"),
        "duration_h": 0.0,
        "note": "values are capped at 100 %; a full container may already be overflowing" if capped else
                "projection uses the recent rising rate only; a pickup drops the level and ends the case",
    }
    return Candidate(detector=LOGISTICS, asset="WASTE_MAIN", t=t, start=t, severity_hint=_sev(h_full, capped, helps),
                     features=feats, route="rule", affected_assets=["WASTE_MAIN"],
                     work_orders=work_orders_asof(obs, t, ["WASTE_MAIN"]))


# ------------------------------------------------------------------------- floor bins
def _floor_bin(obs, wide: pd.DataFrame, col: str) -> List[Candidate]:
    asset = col.split(".")[0]
    x = wide[col].to_numpy(dtype=float)
    idx = wide.index
    rate = _rate_per_h(x, FLOOR_WINDOW_H, +1)
    main = wide["WASTE_MAIN.fill_pct"].to_numpy(dtype=float) if "WASTE_MAIN.fill_pct" in wide.columns else None
    info: Dict[int, Dict[str, Any]] = {}
    hot = np.zeros(len(x), dtype=bool)
    for i in range(len(x)):
        f = x[i]
        if np.isnan(f):
            continue
        capped = f >= CAPPED_PCT
        r = rate[i]
        t = idx[i]
        deadline = _next_daily(t, FLOOR_DEADLINE_LOCAL)
        h_dead = (deadline - t).total_seconds() / 3600.0
        proj = f + (0.0 if np.isnan(r) else r * h_dead)
        if not capped and not (f >= FLOOR_MIN_NOW_PCT and proj >= FLOOR_FULL_PCT):
            continue
        hot[i] = True
        info[i] = {"deadline": deadline, "h_dead": h_dead, "proj": proj, "rate": r, "capped": capped}
    out = []
    for i in _emit_indices(hot):
        d = info[i]
        t = idx[i]
        mnow = float(main[i]) if main is not None and not np.isnan(main[i]) else None
        carry_pct = float(x[i]) * BIN_L / CONTAINER_L
        can_carry = mnow is not None and (mnow + carry_pct) < WASTE_FULL_PCT
        feats = {
            "subject": "floor_bin", "fill_pct": round(float(x[i]), 1), "capped_at_100": bool(d["capped"]),
            "fill_rate_pct_per_h": None if np.isnan(d["rate"]) else round(float(d["rate"]), 3),
            "next_housekeeping_emptying_utc": _iso(d["deadline"]), "hours_to_next_service": round(d["h_dead"], 1),
            "projected_pct_at_next_service": round(float(d["proj"]), 1),
            "main_container_fill_pct": None if mnow is None else round(mnow, 1),
            "robot_can_carry": bool(can_carry), "bathroom_cleaning_needed": False,
            "waste_bin_full_events_24h": _count_events(obs, t, "waste_bin_full", asset),
            "duration_h": 0.0,
            "note": "values are capped at 100 %; a bin at the cap may already be overflowing" if d["capped"]
                    else "projected to fill before housekeeping empties it",
        }
        sev = "high" if d["capped"] else "medium"
        out.append(Candidate(detector=LOGISTICS, asset=asset, t=t, start=t, severity_hint=sev, features=feats,
                             route="rule", affected_assets=[asset],
                             work_orders=work_orders_asof(obs, t, [asset])))
    return out


# ---------------------------------------------------------------------------- vending
def _vending(obs, wide: pd.DataFrame) -> List[Candidate]:
    cap = _vend_capacity(obs)
    prods = [c for c in wide.columns if c.startswith("VEND1.stock_")]
    if not prods:
        return []
    idx = wide.index
    arrays = {c.split("stock_")[1]: wide[c].to_numpy(dtype=float) for c in prods}
    rates = {p: _rate_per_h(a, VEND_WINDOW_H, -1) for p, a in arrays.items()}
    info: Dict[int, Dict[str, Any]] = {}
    hot = np.zeros(len(idx), dtype=bool)
    for i in range(len(idx)):
        t = idx[i]
        tr = _next_weekly(t, VEND_RESTOCK_DAYS, VEND_RESTOCK_LOCAL)
        h_restock = (tr - t).total_seconds() / 3600.0
        rows = []
        for p, a in arrays.items():
            s, r = a[i], rates[p][i]
            if np.isnan(s):
                continue
            low = VEND_LOW_SHARE * cap.get(p, 100.0)
            if s <= 0:
                h_low = 0.0
            elif np.isnan(r) or r <= 0:
                continue
            else:
                h_low = max(0.0, (s - low) / r)
            proj = s - (0.0 if np.isnan(r) else r * h_restock)
            if s > 0 and proj > low:
                continue
            if s > 0 and h_low - LEAD_H > ACT_MARGIN_H:
                continue
            rows.append({"product": p, "stock": float(s), "rate": None if np.isnan(r) else float(r),
                         "proj": float(proj), "h_low": float(h_low), "low": low,
                         "h_empty": 0.0 if s <= 0 else (float(s / r) if (not np.isnan(r) and r > 0) else None)})
        if rows:
            hot[i] = True
            info[i] = {"restock": tr, "h_restock": h_restock, "rows": rows}
    out = []
    for i in _emit_indices(hot):
        d = info[i]
        t = idx[i]
        worst = min(d["rows"], key=lambda r: r["h_low"])
        helps = LEAD_H < d["h_restock"]
        out_now = any(r["stock"] <= 0 for r in d["rows"])
        feats = {
            "subject": "vending", "limiting_product": worst["product"], "stock_units": worst["stock"],
            "capacity_units": cap.get(worst["product"]),
            "burn_units_per_h": None if worst["rate"] is None else round(worst["rate"], 3),
            "reserve_units": round(worst["low"], 1),
            "next_regular_service_utc": _iso(d["restock"]), "hours_to_next_service": round(d["h_restock"], 1),
            "projected_units_at_next_service": round(worst["proj"], 1),
            "hours_to_reserve": round(worst["h_low"], 1),
            "hours_to_empty": None if worst["h_empty"] is None else round(worst["h_empty"], 1),
            "lead_time_h": LEAD_H,
            "order_by_utc": _iso(t + pd.Timedelta(hours=max(0.0, worst["h_low"] - LEAD_H))),
            "earliest_extra_service_utc": _iso(t + pd.Timedelta(hours=LEAD_H)),
            "extra_service_helps": bool(helps),
            "products_at_risk": [r["product"] for r in d["rows"]],
            "vending_out_of_stock_events_24h": _count_events(obs, t, "vending_out_of_stock", "VEND1"),
            "duration_h": 0.0,
        }
        sev = _sev(worst["h_low"], out_now, helps)
        out.append(Candidate(detector=LOGISTICS, asset="VEND1", t=t, start=t, severity_hint=sev, features=feats,
                             route="rule", affected_assets=["VEND1"], work_orders=work_orders_asof(obs, t, ["VEND1"])))
    return out
