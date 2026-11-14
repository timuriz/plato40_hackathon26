"""A Candidate is one thing a detector noticed at one moment. It carries the evidence packet that the
UNDERSTAND step (LLM or rule-based fallback) and then DECIDE consume.

The packet shape is exactly what `plato.llm` and `plato.decide` already expect:
    {"detector": str, "asset": str, "time"|"window": str, "features": {...}, "quality_flags": [...],
     "affected_assets": [...], "work_orders": [...], "bms_alarms": [...]}
Detectors only ever look at data up to ``t`` (causal); the Observation's ``asof(t)`` view enforces that
for the event/roster/forecast tables, and the detectors here only read cleaned cells at indices <= t.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

# detector kinds (also the first part of an episode key)
WATER = "water_baseline"
TURNAROUND = "room_turnaround"
COMFORT = "comfort"
QUALITY = "sensor_quality"
MAINTENANCE = "ventilation_maintenance"   # S04: AHU1 filter loading seen in valid data
MAINTENANCE_GAP = "ventilation_sensor_gap"  # S04: AHU1 filter_dp_pa blank run (data gap, not degradation)
LOGISTICS = "logistics"                   # waste bins / WASTE_MAIN / VEND1 versus the regular service
ENERGY = "energy_optimization"
GUEST = "guest_request"
OCCUPANCY_SURGE = "occupancy_surge"      # S05: forecast cleaning demand versus known staffed capacity

# severities a detector may assign from the signal alone (an a-priori hint; the LLM may revise)
SEV_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _iso(t) -> str:
    return pd.Timestamp(t).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Candidate:
    detector: str
    asset: str
    t: pd.Timestamp                       # the moment the candidate is raised (decision time, UTC)
    severity_hint: str = "medium"         # from the signal; UNDERSTAND may change it
    start: Optional[pd.Timestamp] = None  # when the underlying condition began (for a window packet)
    features: Dict[str, Any] = field(default_factory=dict)
    quality_flags: List[str] = field(default_factory=list)
    affected_assets: List[str] = field(default_factory=list)
    work_orders: List[Dict[str, Any]] = field(default_factory=list)
    bms_alarms: List[str] = field(default_factory=list)
    guest_request_ids: List[str] = field(default_factory=list)
    requires_guest_consent: bool = False  # from guest_requests, if the detector knows it
    route: str = "llm"                    # "llm" or "rule": quality candidates skip the LLM

    def packet(self) -> Dict[str, Any]:
        """The JSON-able evidence packet handed to UNDERSTAND and DECIDE."""
        p: Dict[str, Any] = {"detector": self.detector, "asset": self.asset,
                             "features": _jsonable(self.features), "quality_flags": list(self.quality_flags)}
        if self.start is not None and pd.Timestamp(self.start) != pd.Timestamp(self.t):
            p["window"] = f"{_iso(self.start)}..{_iso(self.t)}"
        else:
            p["time"] = _iso(self.t)
        extra = [a for a in self.affected_assets if a != self.asset]
        if extra:
            p["affected_assets"] = extra
        if self.work_orders:
            p["work_orders"] = _jsonable(self.work_orders)
        if self.bms_alarms:
            p["bms_alarms"] = list(self.bms_alarms)
        return p


def _jsonable(o):
    """Turn numpy / pandas scalars into plain Python so json.dumps (and the cache hash) are stable."""
    import numpy as np
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if (f != f) else round(f, 4)  # NaN -> None; round so the hash is stable
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (pd.Timestamp,)):
        return _iso(o)
    return o
