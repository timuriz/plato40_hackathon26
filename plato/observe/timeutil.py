"""Time helpers: data is UTC, operations run on Europe/Vienna local time (DST aware)."""
from __future__ import annotations

from typing import Union

import pandas as pd

LOCAL_TZ = "Europe/Vienna"

TimeLike = Union[str, pd.Timestamp]


def utc_ts(x: TimeLike) -> pd.Timestamp:
    """Normalise anything timestamp-like to a tz-aware UTC Timestamp."""
    ts = pd.Timestamp(x)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def to_local(x):
    """Convert a UTC Timestamp / DatetimeIndex / datetime Series to Europe/Vienna."""
    if isinstance(x, pd.Series):
        return x.dt.tz_convert(LOCAL_TZ)
    if isinstance(x, pd.Timestamp):
        return utc_ts(x).tz_convert(LOCAL_TZ)
    return x.tz_convert(LOCAL_TZ)


def local_hour(x):
    """Fractional local hour of day (e.g. 15.5 = 15:30 local)."""
    loc = to_local(x)
    if isinstance(loc, pd.Series):
        return loc.dt.hour + loc.dt.minute / 60.0
    return loc.hour + loc.minute / 60.0


def at_local(date: str, hhmm: str) -> pd.Timestamp:
    """UTC timestamp of a local wall-clock time, e.g. at_local('2026-03-29', '15:00')."""
    naive = pd.Timestamp(f"{date} {hhmm}")
    return naive.tz_localize(LOCAL_TZ, ambiguous=True, nonexistent="shift_forward").tz_convert("UTC")
