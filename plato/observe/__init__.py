"""Observe stage: load -> clean -> label quality -> expose a causal "as of t" view.

    obs = observe("path/to/dataset")
    view = obs.asof("2026-01-08T06:00:00Z")      # only what was known at that moment
    view.series("room.temperature_c").iloc[-1]   # latest cleaned room temperatures
    view.forecast(), view.tariff(), view.roster() ...
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict

import pandas as pd

from .asof import AsOf
from .building import Building
from .derive import HANDLED_EVENT_TYPES
from .loader import RawData, load_raw
from .plant import (
    EnergyData, EquipmentData, LogisticsData, Wide, process_energy, process_equipment, process_forecast,
    process_logistics, process_tariff, process_weather,
)
from .quality import GAP_COLS, IssueLog
from .telemetry import NUMERIC_VARS, RoomTelemetry, process_room_telemetry
from .timeutil import utc_ts
from .water import WaterData, process_water

__all__ = ["observe", "Observation", "AsOf"]


@dataclass
class Observation:
    data_dir: Path
    building: Building
    manifest: dict
    grid: pd.DatetimeIndex
    rooms: RoomTelemetry
    water: WaterData
    equipment: EquipmentData
    energy: EnergyData
    weather: Wide
    logistics: LogisticsData
    tariff: pd.DataFrame
    forecast: pd.DataFrame
    bookings: pd.DataFrame
    access_log: pd.DataFrame
    roster: pd.DataFrame
    work_orders: pd.DataFrame
    guest_requests: pd.DataFrame
    events: pd.DataFrame
    issues: pd.DataFrame
    gaps: pd.DataFrame
    frames: Dict[str, pd.DataFrame] = field(default_factory=dict)  # name -> time-indexed table
    meta: dict = field(default_factory=dict)

    def asof(self, t) -> AsOf:
        return AsOf(self, utc_ts(t))


def _sorted(df: pd.DataFrame, cols) -> pd.DataFrame:
    cols = [c for c in cols if c in df.columns]
    return df.sort_values(cols, kind="stable").reset_index(drop=True) if len(df) and cols else df


def _build_frames(rooms: RoomTelemetry, water: WaterData, equipment: EquipmentData, energy: EnergyData,
                  weather: Wide, logistics: LogisticsData) -> Dict[str, pd.DataFrame]:
    f: Dict[str, pd.DataFrame] = {}
    for var, df in rooms.values.items():
        f[f"room.{var}"] = df
        f[f"room_q.{var}"] = rooms.quality[var]
    f["room.occupied"] = rooms.occupied
    f["gateway_down"] = rooms.gateway_down
    for label, cum in (("water.room", water.rooms), ("water.main", water.main)):
        f[f"{label}.cum"] = cum.cum
        f[f"{label}.delta_l"] = cum.delta_l
        f[f"{label}.flow_lph"] = cum.flow_lph
        f[f"{label}.span_steps"] = cum.span_steps
        f[f"{label}.q"] = cum.quality
        f[f"{label}.post_exchange"] = cum.post_exchange
    f["water.balance"] = water.balance
    f["equipment"] = equipment.wide.values
    f["equipment_q"] = equipment.wide.quality
    f["equipment_status"] = equipment.status
    f["energy"] = energy.wide.values
    f["energy_q"] = energy.wide.quality
    f["energy.balance_residual_kw"] = energy.balance_residual_kw.to_frame("residual_kw")
    f["weather"] = weather.values
    f["weather_q"] = weather.quality
    f["logistics"] = logistics.wide.values
    f["logistics_q"] = logistics.wide.quality
    f["logistics.full_or_overflowing"] = logistics.full_or_overflowing
    return f


def observe(data_dir) -> Observation:
    """Run the whole observe stage on a dataset directory (quickstart, dev or holdout)."""
    raw: RawData = load_raw(data_dir)
    fr = raw.frames
    log = IssueLog()
    building = Building.from_json(raw.building_json)

    # events: unique ids, valid timestamps, time ordered
    events = fr["events"].copy()
    if len(events):
        n_bad = int(events["timestamp_utc"].isna().sum())
        if n_bad:
            log.add("events", "bad_timestamp", steps=n_bad, action="dropped_rows")
            events = events[events["timestamp_utc"].notna()]
        if "event_id" in events.columns:
            n_dup = int(events.duplicated("event_id").sum())
            if n_dup:
                log.add("events", "duplicate_event_id", steps=n_dup, action="kept_first")
                events = events.drop_duplicates("event_id", keep="first")
    events = _sorted(events, ["timestamp_utc", "event_id"])

    # common 15-min grid from telemetry (fallback: manifest)
    tel = fr["room_telemetry"]
    if len(tel) and tel["timestamp_utc"].notna().any():
        start, end = tel["timestamp_utc"].min(), tel["timestamp_utc"].max()
    else:
        m = raw.manifest
        start, end = utc_ts(m.get("start_utc", "1970-01-01T00:00:00Z")), utc_ts(m.get("end_utc", "1970-01-01T00:00:00Z"))
    grid = pd.date_range(start, end, freq="15min", tz="UTC")

    rooms = process_room_telemetry(tel, building, events, grid, log)
    grid = rooms.occupied.index  # authoritative grid (may differ if telemetry was empty)
    water = process_water(fr["room_water"], fr["water_main"], fr["work_orders"], building, grid, log,
                          rooms.gateway_down)
    equipment = process_equipment(fr["equipment"], grid, log)
    energy = process_energy(fr["energy"], building, grid, log)
    weather = process_weather(fr["weather"], grid, log)
    logistics = process_logistics(fr["logistics"], log)

    tariff = process_tariff(fr["tariff"], log)
    forecast = process_forecast(fr["weather_forecast"], log)

    gap_parts = [g for g in (rooms.gaps, water.gaps, equipment.gaps, energy.gaps, logistics.gaps) if len(g)]
    gaps = pd.concat(gap_parts, ignore_index=True) if gap_parts else pd.DataFrame(columns=GAP_COLS)

    event_types = events["type"].value_counts().to_dict() if len(events) else {}
    meta = {
        "raw_rows": raw.rows,
        "raw_blanks": raw.blanks,
        "warnings": raw.warnings,
        "event_types": event_types,
        "event_types_not_used_for_state": sorted(set(event_types) - HANDLED_EVENT_TYPES),
        "room_checks": rooms.checks,
    }

    return Observation(
        data_dir=raw.data_dir, building=building, manifest=raw.manifest, grid=grid, rooms=rooms,
        water=water, equipment=equipment, energy=energy, weather=weather, logistics=logistics,
        tariff=tariff, forecast=forecast,
        bookings=_sorted(fr["bookings"], ["created_utc"]),
        access_log=_sorted(fr["access_log"], ["timestamp_utc"]),
        roster=_sorted(fr["staff_roster"], ["start_utc", "resource_id"]),
        work_orders=_sorted(fr["work_orders"], ["created_utc"]),
        guest_requests=_sorted(fr["guest_requests"], ["timestamp_utc"]),
        events=events, issues=log.frame(), gaps=gaps,
        frames=_build_frames(rooms, water, equipment, energy, weather, logistics), meta=meta,
    )
