"""Cumulative water meters -> consumption per interval, robust to blanks, jitter and meter exchanges.

A cumulative meter only ever grows, so consumption is the difference between readings. What
can go wrong (all present in the data, all handled here):

* **blank reading**       - consumption is unknown for that step. When the next reading
  arrives, the *total* over the gap is exact; we report it on that row (q = span,
  ``span_steps`` = gap length) instead of inventing a per-step split.
* **meter exchange**      - the counter restarts: a large drop. That step is q = reset,
  delta = NaN (never a negative or a huge number); the new reading becomes the new baseline.
* **meter jitter**        - a freshly exchanged meter can wobble by a few litres, producing
  small negative steps. Those are q = jitter, delta = 0. Positive wobble cannot be told
  apart from use, so rows within ``POST_EXCHANGE_STEPS`` of a reset are flagged
  ``post_exchange`` - treat low flows there with extra suspicion (S02 "barely above normal").
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

import numpy as np
import pandas as pd

from .building import Building
from .quality import (
    Q_JITTER, Q_MISSING, Q_OK, Q_RESET, Q_SPAN, Q_SUSPECT, IssueLog, find_runs, gap_table, prepare_long,
)

STEP_H = 0.25  # 15 min in hours
JITTER_L = 10.0  # a negative step up to this size is meter noise; larger = counter reset
POST_EXCHANGE_STEPS = 7 * 96  # one week of extra suspicion after an exchange
RESET_CONFIRM_WINDOW = pd.Timedelta(hours=12)


@dataclass
class Cumulative:
    cum: pd.DataFrame
    delta_l: pd.DataFrame  # litres in the interval (total over span for q=span rows)
    span_steps: pd.DataFrame  # int: steps covered by delta_l (1 = normal)
    flow_lph: pd.DataFrame  # litres/hour averaged over the span
    quality: pd.DataFrame
    post_exchange: pd.DataFrame
    raw_missing: pd.DataFrame


@dataclass
class WaterData:
    rooms: Cumulative
    main: Cumulative
    balance: pd.DataFrame
    resets: pd.DataFrame
    gaps: pd.DataFrame = field(default_factory=pd.DataFrame)


def process_cumulative(cum_raw: pd.DataFrame) -> Cumulative:
    idx_t = cum_raw.index
    v = cum_raw.to_numpy(dtype=float).copy()
    n_t, n_c = v.shape
    raw_missing = np.isnan(v)
    neg = ~raw_missing & (v < 0)  # a cumulative meter can't be negative
    v[neg] = np.nan
    valid = ~np.isnan(v)

    rows = np.arange(n_t).reshape(-1, 1)
    last_valid = np.maximum.accumulate(np.where(valid, rows, -1), axis=0)
    prev_idx = np.full((n_t, n_c), -1)
    prev_idx[1:] = last_valid[:-1]
    has_prev = prev_idx >= 0
    prev_val = np.where(has_prev, np.take_along_axis(v, np.clip(prev_idx, 0, None), axis=0), np.nan)
    span = np.where(has_prev, rows - prev_idx, 0)
    raw_delta = np.where(valid & has_prev, v - prev_val, np.nan)

    q = np.full(v.shape, Q_OK, dtype=np.int8)
    q[raw_missing] = Q_MISSING
    q[neg] = Q_SUSPECT
    q[valid & ~has_prev] = Q_MISSING  # first reading: no baseline yet

    delta = raw_delta.copy()
    with np.errstate(invalid="ignore"):
        reset = valid & has_prev & (raw_delta < -JITTER_L)
        jitter = valid & has_prev & (raw_delta < 0) & ~reset
        multi = valid & has_prev & (span > 1) & (raw_delta >= 0)
    delta[reset] = np.nan
    delta[jitter] = 0.0
    q[reset] = Q_RESET
    q[jitter] = Q_JITTER
    q[multi] = Q_SPAN

    eff_span = np.where(valid & has_prev, span, 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        flow = np.where(eff_span > 0, delta / (np.maximum(eff_span, 1) * STEP_H), np.nan)

    last_reset = np.maximum.accumulate(np.where(reset, rows, -1), axis=0)
    post = (last_reset >= 0) & (rows - last_reset <= POST_EXCHANGE_STEPS)

    mk = lambda a: pd.DataFrame(a, index=idx_t, columns=cum_raw.columns)  # noqa: E731
    return Cumulative(cum=mk(v), delta_l=mk(delta), span_steps=mk(eff_span.astype(int)),
                      flow_lph=mk(flow), quality=mk(q).astype(np.int8), post_exchange=mk(post),
                      raw_missing=mk(raw_missing))


def _wide_cumulative(df: pd.DataFrame, grid, columns, index_col: str, value_col: str) -> pd.DataFrame:
    return df.pivot(index="timestamp_utc", columns=index_col, values=value_col).reindex(index=grid, columns=columns)


def process_water(room_water: pd.DataFrame, water_main: pd.DataFrame, work_orders: pd.DataFrame,
                  building: Building, grid: pd.DatetimeIndex, log: IssueLog,
                  gateway_down: pd.DataFrame) -> WaterData:
    rooms = list(building.rooms)

    if room_water.empty:
        rw = pd.DataFrame(np.nan, index=grid, columns=rooms)
    else:
        d = room_water.copy()
        d["meter_total_l"] = pd.to_numeric(d["meter_total_l"], errors="coerce")
        d = d[d["room_id"].isin(rooms)]
        d = prepare_long(d, ["timestamp_utc", "room_id"], "room_water", log)
        rw = _wide_cumulative(d, grid, rooms, "room_id", "meter_total_l")

    if water_main.empty:
        wm = pd.DataFrame(np.nan, index=grid, columns=["WM_MAIN"])
    else:
        d = water_main.copy()
        d["meter_total_l"] = pd.to_numeric(d["meter_total_l"], errors="coerce")
        d = prepare_long(d, ["timestamp_utc"], "water_main", log)
        wm = d.set_index("timestamp_utc")[["meter_total_l"]].rename(columns={"meter_total_l": "WM_MAIN"}) \
            .reindex(grid)

    room_c = process_cumulative(rw)
    main_c = process_cumulative(wm)

    # ---- resets, confirmed against metering work orders --------------------------------------
    metering = pd.DataFrame()
    if not work_orders.empty and "type" in work_orders.columns:
        metering = work_orders[work_orders["type"].astype(str).str.lower() == "metering"]
    reset_rows: List[dict] = []
    for label, cum in (("room_water", room_c), ("water_main", main_c)):
        m = (cum.quality == Q_RESET).to_numpy()
        for j, col in enumerate(cum.quality.columns):
            for s, e in find_runs(m[:, j]):
                ts = cum.quality.index[s]
                prev_val = cum.cum.iloc[:s, j].dropna()
                before = float(prev_val.iloc[-1]) if len(prev_val) else float("nan")
                after = float(cum.cum.iloc[s, j])
                confirmed = ""
                for wo in metering.itertuples(index=False):
                    if str(wo.asset_id) in (f"WM_{col}", col):
                        for cand in (getattr(wo, "scheduled_utc", pd.NaT), getattr(wo, "completed_utc", pd.NaT)):
                            if pd.notna(cand) and abs(cand - ts) <= RESET_CONFIRM_WINDOW:
                                confirmed = wo.work_order_id
                reset_rows.append(dict(source=label, asset_id=col, timestamp_utc=ts, before_l=before,
                                       after_l=after, confirmed_by=confirmed))
                log.add(label, "meter_reset", asset_id=col, start_utc=ts, end_utc=ts, steps=1,
                        action="new_baseline",
                        detail=("confirmed_by=" + confirmed) if confirmed else "no matching work order")
    resets = pd.DataFrame(reset_rows, columns=["source", "asset_id", "timestamp_utc", "before_l", "after_l",
                                               "confirmed_by"])

    # ---- gaps (with cause) ------------------------------------------------------------------
    gaps = gap_table(room_c.raw_missing, room_c.quality, "room_water", column="meter_total_l")
    if len(gaps):
        for i, r in gaps.iterrows():
            gw = building.gateway_of.get(r["asset_id"])
            if gw in gateway_down.columns:
                if gateway_down.reindex(grid).loc[r["start_utc"]:r["end_utc"], gw].mean() >= 0.5:
                    gaps.at[i, "cause"] = "gateway_outage"
    mg = gap_table(main_c.raw_missing, main_c.quality, "water_main", column="meter_total_l")
    gaps = pd.concat([gaps, mg], ignore_index=True) if len(mg) else gaps

    # ---- water balance: main - sum(rooms) = common areas + losses (+ unmetered leaks) ------
    good = room_c.quality.isin([Q_OK, Q_JITTER]) & (room_c.span_steps == 1)
    complete = good.all(axis=1)
    rooms_sum = room_c.delta_l.where(good).sum(axis=1, min_count=1)
    main_ok = main_c.quality["WM_MAIN"].isin([Q_OK]) & (main_c.span_steps["WM_MAIN"] == 1)
    main_delta = main_c.delta_l["WM_MAIN"].where(main_ok)
    residual = (main_delta - rooms_sum).where(complete & main_ok)
    balance = pd.DataFrame({
        "main_delta_l": main_delta,
        "rooms_sum_l": rooms_sum.where(complete),
        "residual_l": residual,
        "rooms_valid": good.sum(axis=1),
        "complete": complete,
    })
    return WaterData(rooms=room_c, main=main_c, balance=balance, resets=resets, gaps=gaps)
