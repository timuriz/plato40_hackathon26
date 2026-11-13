"""Causal "as of t" view: only information that was actually known at time t.

Availability rules (CHALLENGE section 5):
* telemetry / events / requests / access log : timestamp <= t
* weather forecast : latest forecast *issued* at or before t (target times >= t)
* tariff           : rows whose ``published_utc`` <= t (day-ahead prices for future hours)
* roster           : a sick/vacation status counts only once ``status_known_utc`` <= t;
                     before that the shift still looks ``scheduled``
* bookings         : ``created_utc`` <= t. NOTE: the file gives no cancellation time, so a
                     cancelled booking is flagged ``cancel_time_unknown`` (we cannot tell
                     when the cancellation became known)
* work orders      : created <= t; ``completed_utc`` is hidden until it has happened
"""
from __future__ import annotations

from typing import Optional

import pandas as pd


class AsOf:
    def __init__(self, obs, t: pd.Timestamp) -> None:
        self.obs = obs
        self.t = t

    # -- time series (wide, time-indexed) ---------------------------------------------------
    def series(self, name: str, lookback: Optional[pd.Timedelta] = None) -> pd.DataFrame:
        """Cleaned time-indexed table up to and including t (see Observation.frames for names)."""
        try:
            df = self.obs.frames[name]
        except KeyError:
            raise KeyError(f"unknown series '{name}'. Available: {sorted(self.obs.frames)}") from None
        start = self.t - lookback if lookback is not None else None
        return df.loc[start:self.t]

    def latest(self, name: str) -> pd.Series:
        df = self.series(name)
        return df.iloc[-1] if len(df) else pd.Series(dtype=object)

    # -- tables -----------------------------------------------------------------------------
    @staticmethod
    def _upto(df: pd.DataFrame, col: str, t: pd.Timestamp) -> pd.DataFrame:
        if df.empty or col not in df.columns:
            return df.iloc[0:0]
        return df[df[col] <= t]

    def events(self) -> pd.DataFrame:
        return self._upto(self.obs.events, "timestamp_utc", self.t)

    def guest_requests(self) -> pd.DataFrame:
        return self._upto(self.obs.guest_requests, "timestamp_utc", self.t)

    def access_log(self) -> pd.DataFrame:
        return self._upto(self.obs.access_log, "timestamp_utc", self.t)

    def bookings(self) -> pd.DataFrame:
        b = self._upto(self.obs.bookings, "created_utc", self.t).copy()
        if "status" in b.columns:
            b["cancel_time_unknown"] = b["status"] == "cancelled"
        return b

    def forecast(self) -> pd.DataFrame:
        f = self.obs.forecast
        known = self._upto(f, "issued_utc", self.t)
        if known.empty:
            return known
        latest = known["issued_utc"].max()
        out = known[(known["issued_utc"] == latest) & (known["target_utc"] >= self.t)]
        return out.reset_index(drop=True)

    def tariff(self) -> pd.DataFrame:
        known = self._upto(self.obs.tariff, "published_utc", self.t)
        if known.empty:
            return known
        latest = known.sort_values("published_utc").drop_duplicates("timestamp_utc", keep="last")
        return latest.sort_values("timestamp_utc").reset_index(drop=True)

    def roster(self) -> pd.DataFrame:
        r = self.obs.roster
        if r.empty:
            return r
        known = r["status_known_utc"] <= self.t
        scheduled = r["status"] == "scheduled"
        out = r[~(scheduled & ~known)].copy()  # a plan that isn't published yet is invisible
        hidden_change = (out["status"] != "scheduled") & (out["status_known_utc"] > self.t)
        out.loc[hidden_change, "status"] = "scheduled"  # absence not known yet: still looks planned
        return out.reset_index(drop=True)

    def work_orders(self) -> pd.DataFrame:
        w = self._upto(self.obs.work_orders, "created_utc", self.t).copy()
        if w.empty:
            return w
        pending = w["completed_utc"].notna() & (w["completed_utc"] > self.t)
        w.loc[pending, "completed_utc"] = pd.NaT
        w.loc[pending, "status"] = "open"
        return w
