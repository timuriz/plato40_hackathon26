"""Data-quality candidates.

These come straight from what OBSERVE already found (``obs.issues``, ``obs.water.resets``): gateway
outages, frozen sensors, implausible readings and meter exchanges. They are reproducible facts, so they
are routed to the deterministic classifier (``route='rule'``), never the LLM - cheaper and identical on
every run. Each becomes a single ``flag_anomaly`` decision; repeats are merged by the episode builder.
"""
from __future__ import annotations

from typing import List

import numpy as np
import pandas as pd

from ..candidate import QUALITY, Candidate

# issues worth a decision-log entry (others stay in the quality report only)
REPORTABLE = {
    "gateway_outage": "a floor gateway went dark; those rooms' sensors are unknown, not zero",
    "frozen_sensor": "a sensor is stuck on one value; readings dropped",
    "implausible_value": "an out-of-range / impossible reading was removed",
}


def _t(row) -> pd.Timestamp:
    for k in ("end_utc", "start_utc"):
        v = row.get(k)
        if v is not None and not (isinstance(v, float) and np.isnan(v)) and not pd.isna(v):
            return pd.Timestamp(v)
    return pd.Timestamp(row.get("start_utc"))


def detect_quality(obs) -> List[Candidate]:
    out: List[Candidate] = []
    issues = obs.issues
    if len(issues):
        for row in issues.to_dict("records"):
            issue = row.get("issue")
            if issue not in REPORTABLE:
                continue
            t = _t(row)
            asset = str(row.get("asset_id") or row.get("column") or "BUILDING")
            feats = {
                "issue": issue,
                "column": row.get("column") or "",
                "steps": int(row.get("steps") or 0),
                "start_utc": _iso(row.get("start_utc")),
                "end_utc": _iso(row.get("end_utc")),
                "detail": str(row.get("detail") or ""),
                "note": REPORTABLE[issue],
            }
            sev = "medium" if issue == "gateway_outage" else "low"
            out.append(Candidate(detector=QUALITY, asset=asset or "BUILDING", t=t, start=_safe(row.get("start_utc")),
                                 severity_hint=sev, features=feats, quality_flags=[issue], route="rule",
                                 affected_assets=[asset] if asset else ["BUILDING"]))

    resets = obs.water.resets
    if len(resets):
        for row in resets.to_dict("records"):
            t = pd.Timestamp(row["timestamp_utc"])
            asset = str(row["asset_id"])
            feats = {
                "issue": "meter_exchange",
                "cumulative_before_l": _f(row.get("before_l")),
                "cumulative_after_l": _f(row.get("after_l")),
                "meter_reset_in_window": True,
                "confirmed_by_work_order": row.get("confirmed_by") or "",
                "note": "cumulative water meter was exchanged (counter restart); not a real consumption event",
            }
            wos = [{"id": row["confirmed_by"], "type": "metering"}] if row.get("confirmed_by") else []
            out.append(Candidate(detector=QUALITY, asset=asset, t=t, severity_hint="low", features=feats,
                                 quality_flags=["reset"], route="rule", work_orders=wos,
                                 affected_assets=[asset]))
    out.sort(key=lambda c: (c.t, c.asset))
    return out


def _iso(v):
    if v is None or (isinstance(v, float) and np.isnan(v)) or pd.isna(v):
        return None
    return pd.Timestamp(v).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe(v):
    if v is None or (isinstance(v, float) and np.isnan(v)) or pd.isna(v):
        return None
    return pd.Timestamp(v)


def _f(v):
    try:
        f = float(v)
        return None if f != f else round(f, 2)
    except (TypeError, ValueError):
        return None
