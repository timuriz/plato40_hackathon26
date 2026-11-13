"""Human-readable + JSON data-quality report for an Observation."""
from __future__ import annotations

import json
from typing import Any, Dict

import numpy as np
import pandas as pd

from .quality import Q_NAMES
from .telemetry import NUMERIC_VARS

LIMITATIONS = [
    "Blank = missing, never 0. Short gaps are carried forward and labelled 'estimated'; nothing is interpolated.",
    "All cleaning rules are causal (no look-ahead). Frozen sensors are flagged only after the run reached the threshold.",
    "bookings.csv has no cancellation timestamp: a cancelled booking cannot be placed in time (flagged cancel_time_unknown).",
    "Occupancy comes from stay events; presence (motion) is kept separately because sleeping guests do not trigger it.",
    "Physical bounds / jitter / frozen thresholds are generic constants, not fitted to one dataset.",
]


def _jsonable(o: Any):
    if isinstance(o, (pd.Timestamp,)):
        return o.isoformat()
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if np.isnan(o) else float(o)
    if isinstance(o, float) and np.isnan(o):
        return None
    raise TypeError(type(o))


def _q_counts(q: pd.DataFrame) -> Dict[str, int]:
    arr = q.to_numpy()
    return {Q_NAMES[c]: int((arr == c).sum()) for c in sorted(Q_NAMES) if (arr == c).any()}


def build_report(obs) -> Dict[str, Any]:
    m = obs.meta
    rep: Dict[str, Any] = {
        "dataset": {
            "profile": obs.manifest.get("profile"),
            "start_utc": obs.grid[0] if len(obs.grid) else None,
            "end_utc": obs.grid[-1] if len(obs.grid) else None,
            "steps_15min": int(len(obs.grid)),
            "rooms": len(obs.building.rooms),
        },
        "rows_per_file": m["raw_rows"],
        "raw_blanks": m["raw_blanks"],
        "warnings": m["warnings"],
        "quality_codes": {
            "room_telemetry": {v: _q_counts(obs.rooms.quality[v]) for v in NUMERIC_VARS + ["housekeeping_state"]},
            "room_water": _q_counts(obs.water.rooms.quality),
            "water_main": _q_counts(obs.water.main.quality),
            "equipment": {c: _q_counts(obs.equipment.wide.quality[[c]]) for c in obs.equipment.wide.quality.columns},
            "energy": _q_counts(obs.energy.wide.quality),
        },
        "issue_counts": (obs.issues.groupby(["source", "issue"]).agg(episodes=("steps", "size"), steps=("steps", "sum"))
                         .reset_index().to_dict("records") if len(obs.issues) else []),
        "gateway_outages": obs.issues[obs.issues["issue"] == "gateway_outage"][["asset_id", "start_utc", "end_utc", "steps"]]
        .to_dict("records") if len(obs.issues) else [],
        "meter_resets": obs.water.resets.to_dict("records"),
        "longest_gaps": obs.gaps.sort_values("steps", ascending=False).head(12).to_dict("records") if len(obs.gaps) else [],
        "implausible_or_frozen": obs.issues[obs.issues["issue"].isin(["implausible_value", "frozen_sensor"])]
        .to_dict("records") if len(obs.issues) else [],
        "cross_checks": m["room_checks"],
        "event_types": m["event_types"],
        "event_types_not_used_for_state": m["event_types_not_used_for_state"],
        "energy_balance": {
            "abs_residual_gt_1kw_share": float((obs.energy.balance_residual_kw.abs() > 1.0).mean())
            if obs.energy.balance_residual_kw.notna().any() else None,
            "max_abs_residual_kw": float(obs.energy.balance_residual_kw.abs().max())
            if obs.energy.balance_residual_kw.notna().any() else None,
        },
        "water_balance": {
            "complete_steps": int(obs.water.balance["complete"].sum()),
            "residual_l_median": float(obs.water.balance["residual_l"].median()),
        },
        "limitations": LIMITATIONS,
    }
    return rep


def _fmt_ts(x) -> str:
    return pd.Timestamp(x).strftime("%m-%d %H:%M") if pd.notna(x) else "-"


def format_report(rep: Dict[str, Any]) -> str:
    out = []
    ds = rep["dataset"]
    out.append("=" * 78)
    out.append(f"OBSERVE  |  profile={ds['profile']}  {_fmt_ts(ds['start_utc'])} .. {_fmt_ts(ds['end_utc'])} UTC"
               f"  steps={ds['steps_15min']}  rooms={ds['rooms']}")
    out.append("=" * 78)
    for w in rep["warnings"]:
        out.append(f"WARNING: {w}")

    out.append("\nRows per file / raw blanks (blank = missing, kept as unknown)")
    for f, n in rep["rows_per_file"].items():
        bl = rep["raw_blanks"].get(f)
        extra = ("  blanks: " + ", ".join(f"{c}={k}" for c, k in bl.items())) if bl else ""
        out.append(f"  {f:<18}{n:>8}{extra}")

    out.append("\nQuality labels after cleaning (cells)")
    for v, c in rep["quality_codes"]["room_telemetry"].items():
        out.append(f"  room.{v:<20} " + "  ".join(f"{k}={n}" for k, n in c.items()))
    out.append(f"  water.rooms            " + "  ".join(f"{k}={n}" for k, n in rep["quality_codes"]["room_water"].items()))
    out.append(f"  water.main             " + "  ".join(f"{k}={n}" for k, n in rep["quality_codes"]["water_main"].items()))
    for col, c in rep["quality_codes"]["equipment"].items():
        out.append(f"  equip.{col:<19}" + "  ".join(f"{k}={n}" for k, n in c.items()))

    out.append("\nData-quality findings (episodes)")
    for r in rep["issue_counts"]:
        out.append(f"  {r['source']:<16}{r['issue']:<26}episodes={r['episodes']:<5} steps={r['steps']}")

    if rep["implausible_or_frozen"]:
        out.append("\nImplausible / frozen values")
        for r in rep["implausible_or_frozen"][:15]:
            out.append(f"  {r['source']}.{r['column']}  {r['asset_id']:<10}{_fmt_ts(r['start_utc'])} .. "
                       f"{_fmt_ts(r['end_utc'])}  steps={r['steps']}  {r['issue']}  {r['detail']}")

    if rep["gateway_outages"]:
        out.append("\nGateway outages (whole floor goes dark -> sensor data unknown, not zero)")
        for r in rep["gateway_outages"]:
            out.append(f"  {r['asset_id']}  {_fmt_ts(r['start_utc'])} .. {_fmt_ts(r['end_utc'])}  steps={r['steps']}")

    if rep["meter_resets"]:
        out.append("\nWater meter resets (exchange)")
        for r in rep["meter_resets"]:
            out.append(f"  {r['source']}.{r['asset_id']}  {_fmt_ts(r['timestamp_utc'])}  "
                       f"{r['before_l']:.1f} -> {r['after_l']:.1f} L  work order: {r['confirmed_by'] or 'NONE'}")

    out.append("\nLongest gaps (not filled beyond the carry-forward limit)")
    for r in rep["longest_gaps"]:
        out.append(f"  {r['source']}.{r['column']:<18}{str(r['asset_id']):<8}{_fmt_ts(r['start_utc'])} .. "
                   f"{_fmt_ts(r['end_utc'])}  steps={r['steps']:<4} filled={r['filled_steps']:<3} cause={r['cause']}")

    cc = rep["cross_checks"]
    out.append("\nCross-source checks")
    out.append(f"  housekeeping_state: telemetry vs cleaning events agree on {cc['housekeeping_event_agreement']:.1%} "
               f"of {cc['housekeeping_event_overlap_cells']} comparable cells")
    out.append(f"  presence rate: {cc['presence_rate_when_occupied']:.1%} when a stay is active, "
               f"{cc['presence_rate_when_vacant']:.1%} when vacant")
    eb, wb = rep["energy_balance"], rep["water_balance"]
    if eb["max_abs_residual_kw"] is not None:
        out.append(f"  energy balance: |residual|>1 kW in {eb['abs_residual_gt_1kw_share']:.1%} of steps "
                   f"(max {eb['max_abs_residual_kw']:.2f} kW)")
    out.append(f"  water balance: {wb['complete_steps']} complete steps, median common-area+loss "
               f"residual {wb['residual_l_median']:.1f} L / 15 min")
    if rep["event_types_not_used_for_state"]:
        out.append(f"  event types not used for room state: {', '.join(rep['event_types_not_used_for_state'])}")

    out.append("\nLimitations")
    for line in rep["limitations"]:
        out.append(f"  - {line}")
    return "\n".join(out)


def to_json(rep: Dict[str, Any]) -> str:
    return json.dumps(rep, indent=2, default=_jsonable)
