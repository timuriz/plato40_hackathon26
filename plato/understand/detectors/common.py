"""Small shared helpers for detectors: as-of lookups that stay causal."""
from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

from ..candidate import _iso
from plato.observe.timeutil import local_hour


def work_orders_asof(obs, t: pd.Timestamp, assets) -> List[Dict[str, Any]]:
    """Open/known work orders at time t touching any of ``assets`` (room id or meter id)."""
    w = obs.asof(t).work_orders()
    if w.empty:
        return []
    assets = set(assets) | {f"WM_{a}" for a in assets}
    hit = w[w["asset_id"].astype(str).isin(assets)]
    out = []
    for r in hit.itertuples(index=False):
        out.append({"id": getattr(r, "work_order_id", ""), "type": getattr(r, "type", ""),
                    "status": getattr(r, "status", ""), "asset": getattr(r, "asset_id", ""),
                    "summary": str(getattr(r, "summary", ""))[:120]})
    return out


def bms_alarms_asof(obs, t: pd.Timestamp, assets, window=pd.Timedelta("24h")) -> List[str]:
    """Raised BMS alarms in the last ``window`` touching ``assets`` (clue only, not ground truth)."""
    ev = obs.asof(t).events()
    if ev.empty or "type" not in ev.columns or "alarm_code" not in ev.columns:
        return []
    assets = set(assets)
    a = ev[(ev["type"] == "bms_alarm") & (ev["timestamp_utc"] >= t - window)]
    if "asset_id" in a.columns:
        a = a[a["asset_id"].astype(str).isin(assets)]
    out = []
    for r in a.itertuples(index=False):
        out.append(f"{getattr(r, 'alarm_code', '?')}:{getattr(r, 'state', '?')}@{_iso(r.timestamp_utc)}")
    return out[-6:]


def is_night_local(t) -> bool:
    """True if the local (Europe/Vienna) hour is in the quiet night window 00:00-05:00."""
    return local_hour(t) < 5.0
