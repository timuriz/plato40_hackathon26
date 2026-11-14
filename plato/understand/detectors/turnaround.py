"""S01 perfect-checkout / room-turnaround detector.

A turnaround is at risk when a room is free but not yet clean and a guest is arriving (or already
waiting). The detector raises a candidate at the moment the risk becomes visible - a checkout with a
booked or requested arrival, or a guest waiting in the lobby - and attaches the evidence DECIDE needs to
schedule cleaning and give the guest a realistic ready time.

Everything is as-of the trigger time: housekeeping state, roster (a sick shift counts only once
status_known_utc has passed), the dirty-room queue, and the cleaning speed learnt from past events.
The detector never releases a room; it only surfaces the risk.
"""
from __future__ import annotations

from typing import List, Optional

import numpy as np
import pandas as pd

from plato.observe.timeutil import to_local

from ..candidate import TURNAROUND, Candidate

CHECKOUT_TYPES = ("checkout", "checkout_early")
HK_READY = "clean"
DEFAULT_CLEAN_MIN = 45     # fallback cleaning minutes before any history exists
HOUSEKEEPING_END_LOCAL = "15:30"


def _state_at(obs, room: str, t: pd.Timestamp) -> Optional[str]:
    col = obs.rooms.values["housekeeping_state"][room]
    pos = col.index.searchsorted(t, side="right") - 1
    if pos < 0:
        return None
    v = col.iloc[pos]
    return None if (isinstance(v, float) and np.isnan(v)) else str(v)


def _median_clean_minutes(events: pd.DataFrame) -> float:
    if events.empty or "type" not in events.columns:
        return DEFAULT_CLEAN_MIN
    st = events[events["type"] == "cleaning_started"][["room_id", "timestamp_utc"]]
    co = events[events["type"] == "cleaning_completed"][["room_id", "timestamp_utc"]]
    if st.empty or co.empty:
        return DEFAULT_CLEAN_MIN
    durs = []
    for room, g in st.groupby("room_id"):
        comp = co[co["room_id"] == room]["timestamp_utc"].sort_values().reset_index(drop=True)
        for s in g["timestamp_utc"].sort_values():
            after = comp[comp > s]
            if len(after):
                d = (after.iloc[0] - s).total_seconds() / 60.0
                if 5 <= d <= 180:
                    durs.append(d)
    return float(np.median(durs)) if durs else DEFAULT_CLEAN_MIN


def _cleaners_on_shift(roster: pd.DataFrame, t: pd.Timestamp) -> int:
    if roster.empty:
        return 0
    r = roster[(roster["role"] == "cleaner") & (roster["status"] == "scheduled")
               & (roster["start_utc"] <= t) & (roster["end_utc"] >= t)]
    return int(r["resource_id"].nunique())


def _next_cleaner_shift_start(roster: pd.DataFrame, t: pd.Timestamp) -> Optional[pd.Timestamp]:
    if roster.empty:
        return None
    r = roster[(roster["role"] == "cleaner") & (roster["status"] == "scheduled") & (roster["end_utc"] >= t)]
    if r.empty:
        return None
    return pd.Timestamp(r["start_utc"].min())


def _dirty_queue(obs, t: pd.Timestamp) -> int:
    n = 0
    for room in obs.building.rooms:
        if _state_at(obs, room, t) in ("dirty", "in_progress"):
            n += 1
    return n


def _next_arrival(bookings: pd.DataFrame, room: str, t: pd.Timestamp) -> Optional[pd.Timestamp]:
    if bookings.empty:
        return None
    b = bookings[(bookings["room_id"] == room) & (bookings.get("status", "confirmed") != "cancelled")
                 & (bookings["arrival_utc"] >= t - pd.Timedelta("18h"))]
    return pd.Timestamp(b["arrival_utc"].min()) if len(b) else None


def _projected_ready(t, queue_pos: int, clean_min: float, cleaners: int,
                     next_shift: Optional[pd.Timestamp]) -> pd.Timestamp:
    """Deterministic estimate of when THIS room is clean: queue position / parallel cleaners, from the
    moment cleaning can start (now if staff are on shift, else the next shift)."""
    start = t if cleaners > 0 else (next_shift or t)
    lanes = max(cleaners, 1)
    waves = (queue_pos + lanes) // lanes  # this room is in wave ceil((pos+1)/lanes)
    return pd.Timestamp(start) + pd.Timedelta(minutes=clean_min * waves)


def detect_turnaround(obs) -> List[Candidate]:
    ev = obs.events
    if ev.empty or "type" not in ev.columns:
        return []
    out: List[Candidate] = []
    triggers = ev[ev["type"].isin(CHECKOUT_TYPES + ("arrival_waiting_room",))]
    for r in triggers.itertuples(index=False):
        t = pd.Timestamp(r.timestamp_utc)
        room = getattr(r, "room_id", None)
        if room is None or (isinstance(room, float) and np.isnan(room)) or room not in obs.building.rooms:
            continue
        view = obs.asof(t)
        roster, bookings, requests = view.roster(), view.bookings(), view.guest_requests()
        waiting = r.type == "arrival_waiting_room"
        state = _state_at(obs, room, t)
        if not waiting and state == HK_READY:
            continue  # clean at checkout and nobody waiting: nothing to do

        gr = requests[requests["room_id"] == room] if len(requests) else requests
        early = gr[gr["request_type"].isin(["early_checkin", "waiting_for_room"])] if len(gr) else gr
        arrival = _next_arrival(bookings, room, t)
        if not waiting and arrival is None and early.empty:
            continue  # free and dirty but nobody is coming soon

        clean_min = _median_clean_minutes(view.events())
        cleaners = _cleaners_on_shift(roster, t)
        next_shift = _next_cleaner_shift_start(roster, t)
        queue = _dirty_queue(obs, t)
        projected = _projected_ready(t, queue, clean_min, cleaners, next_shift)

        target = t if waiting else (arrival if arrival is not None else t)
        slack_min = (target - projected).total_seconds() / 60.0 if target is not None else None
        sick = roster[(roster["role"] == "cleaner") & (roster["status"] == "sick")]["resource_id"].unique().tolist() \
            if len(roster) else []
        feats = {
            "trigger": r.type,
            "housekeeping_state": state,
            "guest_waiting": bool(waiting),
            "arrival_target_utc": _to_iso(target),
            "projected_ready_utc": _to_iso(projected),
            "slack_minutes": round(slack_min, 1) if slack_min is not None else None,
            "cleaners_on_shift": cleaners,
            "cleaners_sick_known": sick,
            "dirty_queue_length": queue,
            "median_clean_minutes": round(clean_min, 1),
            "housekeeping_shift_end_local": HOUSEKEEPING_END_LOCAL,
            "local_time": to_local(t).strftime("%Y-%m-%d %H:%M %Z"),
        }
        # severity: a guest already waiting, or the room cannot be ready in time, is worse
        if waiting or (slack_min is not None and slack_min < 0):
            sev = "high"
        elif slack_min is not None and slack_min < 60:
            sev = "medium"
        else:
            sev = "low"
        gids = early["request_id"].tolist() if len(early) else []
        # Turnaround is deterministic logistics: the detector already computed the queue, the projected
        # ready time and the slack. The rule classifier reproduces the same judgement at zero latency, so
        # these skip the LLM (which is reserved for the genuinely ambiguous water / comfort cases).
        out.append(Candidate(detector=TURNAROUND, asset=room, t=t, severity_hint=sev, features=feats,
                             affected_assets=[room], guest_request_ids=gids, route="rule"))
    out.sort(key=lambda c: (c.t, c.asset))
    return out


def _to_iso(t) -> Optional[str]:
    if t is None or (isinstance(t, float) and np.isnan(t)):
        return None
    return pd.Timestamp(t).strftime("%Y-%m-%dT%H:%M:%SZ")
