"""Room telemetry: clean, label quality, handle gaps, detect gateway outages."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

import numpy as np
import pandas as pd

from .building import Building
from .derive import HK_FROM_EVENT, event_state_matrix, occupancy_from_events
from .quality import (
    GAP_COLS, Q_DERIVED, Q_FROZEN, Q_MISSING, Q_OK, Q_SUSPECT, IssueLog, Rule, clean_matrix,
    find_runs, gap_table, mask_runs, prepare_long,
)

SOURCE = "room_telemetry"

# Physical plausibility bounds (indoor hotel room), not tuned on any dataset.
ROOM_RULES: Dict[str, Rule] = {
    "temperature_c": Rule(lo=5, hi=40, frozen_repeats=8, max_jump=8.0, locf_limit=4),
    "humidity_pct": Rule(lo=0, hi=100, frozen_repeats=8, locf_limit=4),
    "co2_ppm": Rule(lo=250, hi=5000, frozen_repeats=8, locf_limit=2),
    "setpoint_c": Rule(lo=10, hi=30, locf_limit=8),
    # motion is an event, so a missing reading is never carried forward
    "presence": Rule(binary=True, locf_limit=0),
    "window_open": Rule(binary=True, locf_limit=2),
}
NUMERIC_VARS = list(ROOM_RULES)
HK_ALLOWED = {"clean", "dirty", "in_progress", "inspection_required"}
OUTAGE_VARS = ("temperature_c", "humidity_pct", "co2_ppm")
OUTAGE_MIN_FRACTION = 0.6  # share of a floor's rooms that must go dark together
OUTAGE_MIN_STEPS = 2


@dataclass
class RoomTelemetry:
    values: Dict[str, pd.DataFrame]  # variable -> (time x room); housekeeping_state is strings
    quality: Dict[str, pd.DataFrame]  # variable -> int8 quality codes
    occupied: pd.DataFrame  # bool (time x room) from stay events
    gateway_down: pd.DataFrame  # bool (time x gateway)
    gaps: pd.DataFrame
    checks: dict = field(default_factory=dict)


def _pivot(df: pd.DataFrame, col: str, grid, rooms) -> pd.DataFrame:
    return df.pivot(index="timestamp_utc", columns="room_id", values=col).reindex(index=grid, columns=rooms)


def detect_gateway_outages(raw_missing: Dict[str, pd.DataFrame], building: Building, grid,
                           log: IssueLog) -> pd.DataFrame:
    """A floor gateway is 'down' when most of its rooms lose all key sensors at the same time."""
    rooms = list(building.rooms)
    dark = np.ones((len(grid), len(rooms)), dtype=bool)
    for var in OUTAGE_VARS:
        dark &= raw_missing[var].reindex(columns=rooms).to_numpy(dtype=bool)
    down = pd.DataFrame(False, index=grid, columns=building.gateways)
    for gw in building.gateways:
        cols = [i for i, r in enumerate(rooms) if building.gateway_of[r] == gw]
        if not cols:
            continue
        frac = dark[:, cols].mean(axis=1)
        flag = frac >= OUTAGE_MIN_FRACTION
        arr = np.zeros(len(grid), dtype=bool)
        for s, e in find_runs(flag):
            if e - s >= OUTAGE_MIN_STEPS:
                arr[s:e] = True
                log.add(SOURCE, "gateway_outage", asset_id=gw, start_utc=grid[s], end_utc=grid[e - 1],
                        steps=e - s, action="treat_all_floor_sensors_as_unknown",
                        detail=f"max_dark_share={frac[s:e].max():.2f}")
        down[gw] = arr
    return down


def process_room_telemetry(raw: pd.DataFrame, building: Building, events: pd.DataFrame,
                           grid_hint, log: IssueLog) -> RoomTelemetry:
    rooms = list(building.rooms)
    df = raw.copy()
    if df.empty:
        grid = grid_hint
        empty = pd.DataFrame(np.nan, index=grid, columns=rooms)
        qz = pd.DataFrame(Q_MISSING, index=grid, columns=rooms).astype(np.int8)
        return RoomTelemetry({v: empty.copy() for v in NUMERIC_VARS + ["housekeeping_state"]},
                             {v: qz.copy() for v in NUMERIC_VARS + ["housekeeping_state"]},
                             pd.DataFrame(False, index=grid, columns=rooms),
                             pd.DataFrame(False, index=grid, columns=building.gateways),
                             pd.DataFrame(columns=GAP_COLS))

    # unknown room ids can't be mapped to the building
    unknown = ~df["room_id"].isin(rooms)
    if unknown.any():
        log.add(SOURCE, "unknown_asset", asset_id=",".join(sorted(map(str, df.loc[unknown, "room_id"].unique()))),
                steps=int(unknown.sum()), action="dropped_rows")
        df = df[~unknown]

    # numeric coercion (non-numeric text -> NaN, counted)
    for col in NUMERIC_VARS:
        if col not in df.columns:
            df[col] = np.nan
        coerced = pd.to_numeric(df[col], errors="coerce")
        bad = coerced.isna() & df[col].notna()
        if bad.any():
            log.add(SOURCE, "unparseable_value", column=col, steps=int(bad.sum()), action="set_to_missing")
        df[col] = coerced
    if "housekeeping_state" not in df.columns:
        df["housekeeping_state"] = np.nan

    df = prepare_long(df, ["timestamp_utc", "room_id"], SOURCE, log)
    grid = pd.date_range(df["timestamp_utc"].min(), df["timestamp_utc"].max(), freq="15min", tz="UTC")
    present = df.assign(_p=1).pivot(index="timestamp_utc", columns="room_id", values="_p") \
        .reindex(index=grid, columns=rooms).notna()
    absent = int((~present).to_numpy().sum())
    if absent:
        log.add(SOURCE, "absent_rows", steps=absent, action="treated_as_missing",
                detail="expected (timestamp, room) rows that are not in the file at all")

    values, quality, raw_missing = {}, {}, {}
    for var in NUMERIC_VARS:
        mat = _pivot(df, var, grid, rooms).to_numpy(dtype=float)
        v, q, m = clean_matrix(mat, ROOM_RULES[var])
        values[var] = pd.DataFrame(v, index=grid, columns=rooms)
        quality[var] = pd.DataFrame(q, index=grid, columns=rooms)
        raw_missing[var] = pd.DataFrame(m, index=grid, columns=rooms)
        raw_vals = pd.DataFrame(mat, index=grid, columns=rooms)
        for code, issue, action in ((Q_SUSPECT, "implausible_value", "set_to_unknown"),
                                    (Q_FROZEN, "frozen_sensor", "set_to_unknown_after_threshold")):
            runs = mask_runs(quality[var] == code, raw_vals)
            log.add_runs(runs, SOURCE, issue, action, column=var)

    # ---- housekeeping state: telemetry first, events fill what telemetry lacks -------------
    hk_raw = _pivot(df, "housekeeping_state", grid, rooms)
    hk = hk_raw.where(hk_raw.isin(HK_ALLOWED))
    hk_q = np.where(hk_raw.isna(), Q_MISSING, np.where(hk.isna(), Q_SUSPECT, Q_OK)).astype(np.int8)
    hk_raw_missing = pd.DataFrame(hk_q == Q_MISSING, index=grid, columns=rooms)
    derived = event_state_matrix(events, HK_FROM_EVENT, grid, rooms)
    both = hk.notna() & derived.notna()
    agree_n = int(both.to_numpy().sum())
    agree = float((hk[both].to_numpy() == derived[both].to_numpy()).sum() / agree_n) if agree_n else float("nan")
    fill = (hk_q != Q_OK) & derived.notna().to_numpy()
    hk_vals = hk.where(~pd.DataFrame(fill, index=grid, columns=rooms), derived)
    hk_q = np.where(fill, Q_DERIVED, hk_q).astype(np.int8)
    values["housekeeping_state"] = hk_vals
    quality["housekeeping_state"] = pd.DataFrame(hk_q, index=grid, columns=rooms)
    raw_missing["housekeeping_state"] = hk_raw_missing
    bad_hk = mask_runs(quality["housekeeping_state"] == Q_SUSPECT)
    log.add_runs(bad_hk, SOURCE, "invalid_state_label", "set_to_unknown", column="housekeeping_state")

    # ---- occupancy from events, cross-checked against the motion sensor --------------------
    occupied = occupancy_from_events(events, grid, rooms)
    pres = values["presence"]
    occ_arr, pres_arr = occupied.to_numpy(), pres.to_numpy()
    ok = ~np.isnan(pres_arr)
    def _rate(mask: np.ndarray) -> float:
        sel = pres_arr[mask & ok]
        return float(sel.mean()) if sel.size else float("nan")

    checks = {
        "housekeeping_event_agreement": agree,
        "housekeeping_event_overlap_cells": agree_n,
        "presence_rate_when_occupied": _rate(occ_arr),
        "presence_rate_when_vacant": _rate(~occ_arr),
    }

    gateway_down = detect_gateway_outages(raw_missing, building, grid, log)

    # ---- gap table, with the likely cause of every gap ------------------------------------
    gap_frames: List[pd.DataFrame] = []
    for var in NUMERIC_VARS + ["housekeeping_state"]:
        g = gap_table(raw_missing[var], quality[var], SOURCE, column=var)
        if len(g):
            gap_frames.append(g)
    gaps = pd.concat(gap_frames, ignore_index=True) if gap_frames else pd.DataFrame(columns=GAP_COLS)
    if len(gaps):
        for i, r in gaps.iterrows():
            gw = building.gateway_of.get(r["asset_id"])
            if gw in gateway_down.columns:
                share = gateway_down.loc[r["start_utc"]:r["end_utc"], gw].mean()
                if share >= 0.5:
                    gaps.at[i, "cause"] = "gateway_outage"

    return RoomTelemetry(values, quality, occupied, gateway_down, gaps, checks)
