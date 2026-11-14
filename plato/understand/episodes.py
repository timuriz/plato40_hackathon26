"""Episode builder: turn a stream of per-step Candidates into a few decisions per incident.

Repeated alerts for one incident must count as one detection, and alert spam is penalised. So candidates
with the same (detector, asset) that are close in time belong to one **episode** with a stable
``episode_id``. Per episode we emit:
  * the opening candidate (one decision), and
  * at most ``MAX_FOLLOWUPS`` follow-ups, only when the situation escalates (severity rises or it has
    persisted past a limit). Follow-ups depend on the opener.
An episode ends when candidates stop (the signal returned to baseline for longer than the gap tolerance).
Everything is deterministic: the same candidates always produce the same episode_ids and emissions.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Dict, List, Tuple

import pandas as pd

from .candidate import SEV_ORDER, Candidate

GAP_TOLERANCE = {
    # must exceed each detector's periodic re-emission interval so one persistent incident stays one episode
    "water_baseline": pd.Timedelta("3h"),
    "comfort": pd.Timedelta("3h"),
    "room_turnaround": pd.Timedelta("6h"),
    "sensor_quality": pd.Timedelta("15min"),
    "ventilation_maintenance": pd.Timedelta("12h"),  # re-emitted every 6 h while the filter stays loaded
    "ventilation_sensor_gap": pd.Timedelta("14h"),   # re-emitted every 12 h while the dp sensor is blank
    "logistics": pd.Timedelta("14h"),                # hourly projection re-emitted every 12 h
    "energy_optimization": pd.Timedelta("12h"),  # at most one proposal per local day -> one episode per day
    "occupancy_surge": pd.Timedelta("12h"),      # raised once per affected day (<=1 revision), days are >12 h apart
    "guest_request": pd.Timedelta("48h"),        # one request, plus a higher-priority nudge if the reply deadline passes
}
DEFAULT_GAP = pd.Timedelta("1h")
MAX_FOLLOWUPS = 2
PERSIST_ESCALATION_H = {"water_baseline": 12.0, "comfort": 6.0, "ventilation_maintenance": 24.0}  # emit a follow-up when duration crosses this


@dataclass
class Emission:
    candidate: Candidate
    episode_id: str
    is_followup: bool


def _episode_id(detector: str, asset: str, start: pd.Timestamp) -> str:
    key = f"{detector}|{asset}|{pd.Timestamp(start).strftime('%Y-%m-%dT%H:%M:%SZ')}"
    return "EP-" + hashlib.sha1(key.encode()).hexdigest()[:10]


def build_episodes(candidates: List[Candidate]) -> List[Emission]:
    by_key: Dict[Tuple[str, str], List[Candidate]] = {}
    for c in candidates:
        # A guest request keeps the room as its asset, but each request_id is its own episode.
        group = str((c.features or {}).get("episode_asset") or c.asset)
        by_key.setdefault((c.detector, group), []).append(c)

    emissions: List[Emission] = []
    for (detector, asset), cs in by_key.items():
        cs = sorted(cs, key=lambda c: c.t)
        gap = GAP_TOLERANCE.get(detector, DEFAULT_GAP)
        ep_start = None
        ep_id = None
        prev_t = None
        max_sev = -1
        followups = 0
        crossed_persist = False
        for c in cs:
            new_episode = prev_t is None or (c.t - prev_t) > gap
            if new_episode:
                ep_start = c.start or c.t
                ep_id = _episode_id(detector, asset, ep_start)
                max_sev = SEV_ORDER.get(c.severity_hint, 1)
                followups = 0
                crossed_persist = False
                emissions.append(Emission(c, ep_id, is_followup=False))
            else:
                sev = SEV_ORDER.get(c.severity_hint, 1)
                escalate = sev > max_sev
                dur = float(c.features.get("duration_h", 0) or 0)
                persist_limit = PERSIST_ESCALATION_H.get(detector)
                persist = (persist_limit is not None and not crossed_persist and dur >= persist_limit)
                if (escalate or persist) and followups < MAX_FOLLOWUPS:
                    followups += 1
                    max_sev = max(max_sev, sev)
                    crossed_persist = crossed_persist or persist
                    emissions.append(Emission(c, ep_id, is_followup=True))
            prev_t = c.t
    emissions.sort(key=lambda e: (e.candidate.t, e.candidate.asset, e.is_followup))
    return emissions
