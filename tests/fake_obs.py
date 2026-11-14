"""A tiny fake Observation for detector unit tests: just the attributes the detectors read.

It lets us feed hand-made water/temperature series without running the whole OBSERVE stage, so a test
can say exactly "leak here, guest shower there" and assert what the detector does.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

STEP = pd.Timedelta("15min")


def grid(n: int, start: str = "2026-01-05T00:00:00Z") -> pd.DatetimeIndex:
    return pd.date_range(pd.Timestamp(start), periods=n, freq="15min", tz="UTC")


def _df(data: Dict[str, np.ndarray], idx) -> pd.DataFrame:
    return pd.DataFrame(data, index=idx)


class _AsOf:
    """Minimal as-of view: empty tables (detectors tolerate empties)."""
    def __init__(self, t):
        self.t = t

    def work_orders(self):
        return pd.DataFrame(columns=["work_order_id", "asset_id", "type", "status", "summary",
                                     "created_utc", "completed_utc"])

    def events(self):
        return pd.DataFrame(columns=["event_id", "timestamp_utc", "type", "alarm_code", "state", "asset_id"])

    def guest_requests(self):
        return pd.DataFrame(columns=["request_id", "room_id", "request_type", "requires_guest_consent"])

    def roster(self):
        return pd.DataFrame(columns=["resource_id", "role", "status", "start_utc", "end_utc"])

    def bookings(self):
        return pd.DataFrame(columns=["room_id", "arrival_utc", "status"])

    def forecast(self):
        return pd.DataFrame(columns=["issued_utc", "target_utc", "outdoor_c_forecast"])

    def tariff(self):
        return pd.DataFrame(columns=["timestamp_utc", "price_eur_kwh", "published_utc"])


def make_water_obs(flows: Dict[str, np.ndarray], occupied: Dict[str, np.ndarray],
                   quality: Optional[Dict[str, np.ndarray]] = None,
                   post: Optional[Dict[str, np.ndarray]] = None, start: str = "2026-01-05T00:00:00Z"):
    """Build a fake obs for the water detector. flows are L/15min per room; occupied is a bool mask."""
    rooms = list(flows)
    n = len(next(iter(flows.values())))
    g = grid(n, start)
    quality = quality or {r: np.zeros(n, dtype=np.int8) for r in rooms}  # all Q_OK
    post = post or {r: np.zeros(n, dtype=bool) for r in rooms}
    water = SimpleNamespace(
        rooms=SimpleNamespace(
            delta_l=_df({r: flows[r].astype(float) for r in rooms}, g),
            quality=_df({r: quality[r].astype(np.int8) for r in rooms}, g),
            span_steps=_df({r: np.ones(n, dtype=int) for r in rooms}, g),
            post_exchange=_df({r: post[r].astype(bool) for r in rooms}, g),
        ),
        balance=pd.DataFrame(),  # len 0 -> no main-residual candidates
        resets=pd.DataFrame(columns=["source", "asset_id", "timestamp_utc", "before_l", "after_l", "confirmed_by"]),
    )
    obs = SimpleNamespace(
        grid=g,
        building=SimpleNamespace(rooms=rooms, comfort_c=(20.0, 23.0)),
        rooms=SimpleNamespace(occupied=_df({r: occupied[r].astype(bool) for r in rooms}, g)),
        water=water,
    )
    obs.asof = lambda t: _AsOf(t)
    return obs


def truncate_water_obs(obs, k: int):
    """A copy of a water fake obs with only the first k rows (to test causality)."""
    g = obs.grid[:k]
    rooms = obs.building.rooms
    sl = lambda df: df.iloc[:k]  # noqa: E731
    water = SimpleNamespace(
        rooms=SimpleNamespace(delta_l=sl(obs.water.rooms.delta_l), quality=sl(obs.water.rooms.quality),
                              span_steps=sl(obs.water.rooms.span_steps),
                              post_exchange=sl(obs.water.rooms.post_exchange)),
        balance=pd.DataFrame(), resets=obs.water.resets)
    out = SimpleNamespace(grid=g, building=obs.building,
                          rooms=SimpleNamespace(occupied=sl(obs.rooms.occupied)), water=water)
    out.asof = lambda t: _AsOf(t)
    return out
