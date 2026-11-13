"""State reconstructed from operational events (a second, independent data source).

Events are timestamped facts ("checkout", "cleaning_completed"...). Replaying them gives a
causal per-room state timeline: at time t only events with timestamp <= t are used.
"""
from __future__ import annotations

from typing import Dict, Sequence

import numpy as np
import pandas as pd

HK_FROM_EVENT = {
    "checkout": "dirty",
    "checkout_early": "dirty",
    "cleaning_started": "in_progress",
    "cleaning_completed": "clean",
}
OCC_START = ("checkin", "checkin_early", "checkin_after_wait", "stay_in_progress")
OCC_END = ("checkout", "checkout_early", "no_show")
HANDLED_EVENT_TYPES = set(HK_FROM_EVENT) | set(OCC_START) | set(OCC_END)


def event_state_matrix(events: pd.DataFrame, mapping: Dict[str, object], grid: pd.DatetimeIndex,
                       rooms: Sequence[str]) -> pd.DataFrame:
    """(grid x rooms) frame holding, per room, the value of the latest mapped event <= t (NaN before any)."""
    if events.empty or "room_id" not in events.columns:
        return pd.DataFrame(np.nan, index=grid, columns=list(rooms), dtype=object)
    ev = events[events["type"].isin(mapping) & events["room_id"].isin(rooms)
                & events["timestamp_utc"].notna()].copy()
    if ev.empty:
        return pd.DataFrame(np.nan, index=grid, columns=list(rooms), dtype=object)
    ev["val"] = ev["type"].map(mapping)
    ev = ev.sort_values(["timestamp_utc", "event_id"]).drop_duplicates(["timestamp_utc", "room_id"], keep="last")
    wide = ev.pivot(index="timestamp_utc", columns="room_id", values="val").reindex(columns=list(rooms))
    full = wide.reindex(wide.index.union(grid)).ffill()
    return full.reindex(grid)


def occupancy_from_events(events: pd.DataFrame, grid: pd.DatetimeIndex, rooms: Sequence[str]) -> pd.DataFrame:
    """Boolean (grid x rooms): is the room inside a stay (check-in .. check-out)?

    Before the first event of a room it is assumed vacant; the dataset opens with
    ``stay_in_progress`` events for rooms that are already occupied.
    """
    mapping = {t: 1.0 for t in OCC_START}
    mapping.update({t: 0.0 for t in OCC_END})
    m = event_state_matrix(events, mapping, grid, rooms).astype(float)
    return m.fillna(0.0).astype(bool)
