"""Build a truthful, timestamped UI payload from the existing observation pipeline."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .observe.quality import Q_NAMES
from .ui_intelligence import causal_log, snapshot, decision_lifecycle, operational_context, learning_insights

OCCUPANCY_EVENTS = {"checkin", "checkin_early", "checkin_after_wait", "stay_in_progress", "checkout", "checkout_early", "no_show"}


def _scalar(value: Any) -> Any:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value.item() if isinstance(value, np.generic) else value


def _jsonl(path: str | Path | None, through: pd.Timestamp) -> list[dict[str, Any]]:
    return causal_log(path, through)


def _last_value(frame: pd.DataFrame, timestamp: pd.Timestamp, column: str | None = None):
    if frame.empty:
        return None
    rows = frame.loc[:timestamp]
    if rows.empty:
        return None
    value = rows.iloc[-1] if column is None else rows.iloc[-1].get(column)
    return _scalar(value)


def _series(frame: pd.DataFrame, timestamp: pd.Timestamp, lookback: str = "24h", interval: str = "1h"):
    if frame.empty:
        return []
    start = timestamp - pd.Timedelta(lookback)
    sliced = frame.loc[start:timestamp]
    if sliced.empty:
        return []
    sampled = sliced.resample(interval).mean(numeric_only=True)
    return [
        {"timestamp_utc": _scalar(ts), **{str(k): _scalar(v) for k, v in row.items()}}
        for ts, row in sampled.iterrows()
    ]


def build_room_history(obs, timestamp: str | pd.Timestamp, room_id: str):
    """Return the selected room's real hourly telemetry for the preceding day."""
    if room_id not in obs.building.rooms:
        raise ValueError(f"unknown room_id: {room_id}")
    t = pd.Timestamp(timestamp)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    if len(obs.grid) == 0 or t < obs.grid[0] or t > obs.grid[-1]:
        raise ValueError("timestamp outside dataset range")
    t = obs.grid[max(obs.grid.searchsorted(t, side="right") - 1, 0)]
    history_frames = {}
    for metric in ("temperature_c", "setpoint_c", "humidity_pct", "co2_ppm"):
        frame = obs.frames.get(f"room.{metric}")
        if frame is not None and room_id in frame.columns:
            history_frames[metric] = frame[room_id]
    series = _series(pd.concat(history_frames, axis=1), t) if history_frames else []
    return {"room_id": room_id, "timestamp_utc": _scalar(t), "series": series}


def build_resource_tracking(obs, view, timestamp: pd.Timestamp):
    """Report observations, never infer live coordinates from a roster or dispatch."""
    catalogue = dict(obs.building.resources)
    roster = view.roster()
    if len(roster):
        for row in roster.to_dict("records"):
            catalogue.setdefault(row["resource_id"], row.get("role", "unknown"))
    observations = {resource_id: [] for resource_id in catalogue}
    tasks = {}
    task_types = {
        "cleaning_started": "started", "cleaning_completed": "completed",
        "robot_floor_cleaning_started": "started", "robot_floor_cleaning_completed": "completed",
    }
    for row in view.events().sort_values("timestamp_utc").to_dict("records"):
        resource_id, event_type = row.get("resource_id"), row.get("type")
        if resource_id not in catalogue or event_type not in task_types:
            continue  # A dispatch/call is a destination, not proof of presence.
        room_id = row.get("room_id")
        floor = obs.building.floor_of.get(room_id)
        if room_id in obs.building.rooms:
            location = {"kind": "room", "room_id": room_id, "floor": floor, "label": room_id}
        elif pd.notna(row.get("floor")) and row.get("floor") is not None:
            location = {"kind": "floor", "room_id": None, "floor": int(row["floor"]), "label": f"Floor {int(row['floor'])}"}
        else:
            continue
        observation = {**location, "timestamp_utc": row["timestamp_utc"], "source": "events",
                       "event_type": event_type}
        observations[resource_id].append(observation)
        tasks[resource_id] = {"state": task_types[event_type], "type": event_type,
                              "room_id": location["room_id"], "floor": location["floor"],
                              "timestamp_utc": _scalar(row["timestamp_utc"])}

    for row in view.access_log().to_dict("records"):
        resource_id, door_id = row.get("credential"), row.get("door_id")
        if resource_id not in catalogue or row.get("result") != "granted":
            continue
        if door_id in obs.building.rooms:
            location = {"kind": "room", "room_id": door_id, "floor": obs.building.floor_of[door_id], "label": door_id}
        elif door_id == "MAIN_ENTRANCE":
            location = {"kind": "entrance", "room_id": None, "floor": None, "label": "Main entrance"}
        else:
            continue
        observations[resource_id].append({**location, "timestamp_utc": row["timestamp_utc"],
                                         "source": "access_log", "event_type": "access_granted"})

    resources = []
    for resource_id, role in catalogue.items():
        shifts = roster[(roster["resource_id"] == resource_id) & (roster["start_utc"] <= timestamp)
                        & (roster["end_utc"] > timestamp)] if len(roster) else roster
        shift = shifts.sort_values("start_utc").iloc[-1] if len(shifts) else None
        last = max(observations[resource_id], key=lambda row: row["timestamp_utc"], default=None)
        location = None
        if last:
            location = {**last, "timestamp_utc": _scalar(last["timestamp_utc"]),
                        "age_minutes": round((timestamp - last["timestamp_utc"]).total_seconds() / 60, 1),
                        "precision": "last_reported", "coordinates": None}
        resources.append({"resource_id": resource_id, "role": role,
                          "shift_status": str(shift["status"]) if shift is not None else "off_shift",
                          "last_known_location": location, "last_task_report": tasks.get(resource_id)})
    return {"mode": "last_reported", "resources": resources,
            "note": "Locations come from completed/started cleaning events and granted access logs, not live tracking. Dispatches and shifts do not establish presence."}


def build_dashboard_payload(obs, timestamp: str | pd.Timestamp, decisions_path=None, incidents_path=None,
                            selected_room_id: str | None = None, actions_path=None, insights_path=None):
    """Return a single-source-of-truth view using the pipeline's causal AsOf view."""
    t = pd.Timestamp(timestamp)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    if len(obs.grid) == 0:
        raise ValueError("dataset has no replay timestamps")
    if t < obs.grid[0] or t > obs.grid[-1]:
        raise ValueError(f"timestamp outside dataset range ({obs.grid[0].isoformat()} .. {obs.grid[-1].isoformat()})")
    # Snap to the last actual replay step at or before the requested instant.
    step_index = obs.grid.searchsorted(t, side="right") - 1
    t = obs.grid[max(step_index, 0)]
    view = obs.asof(t)
    rooms = []
    occupied_matrix = obs.rooms.occupied
    known_events = view.events()
    known_room_occupancy = set(known_events.loc[known_events["type"].isin(OCCUPANCY_EVENTS), "room_id"].dropna().astype(str)) if len(known_events) and "room_id" in known_events else set()

    q_counts: dict[str, int] = {}
    q_total = 0
    room_catalog = {item["id"]: item for item in obs.building.raw.get("building", {}).get("rooms", [])}
    for room_id in obs.building.rooms:
        occupied = bool(occupied_matrix.loc[t, room_id]) if t in occupied_matrix.index else False
        occupancy_known = room_id in known_room_occupancy
        hk = _last_value(obs.rooms.values["housekeeping_state"], t, room_id)
        hk_q = _last_value(obs.rooms.quality["housekeeping_state"], t, room_id)
        temp = _last_value(obs.rooms.values["temperature_c"], t, room_id)
        temp_q = _last_value(obs.rooms.quality["temperature_c"], t, room_id)
        humidity = _last_value(obs.rooms.values["humidity_pct"], t, room_id)
        humidity_q = _last_value(obs.rooms.quality["humidity_pct"], t, room_id)
        co2 = _last_value(obs.rooms.values["co2_ppm"], t, room_id)
        co2_q = _last_value(obs.rooms.quality["co2_ppm"], t, room_id)
        setpoint = _last_value(obs.rooms.values["setpoint_c"], t, room_id)
        presence = _last_value(obs.rooms.values["presence"], t, room_id)
        water_flow = _last_value(obs.water.rooms.flow_lph, t, room_id)
        water_q = _last_value(obs.water.rooms.quality, t, room_id)

        if occupied:
            state = "occupied"
        elif hk == "in_progress":
            state = "cleaning"
        elif hk == "dirty":
            state = "dirty"
        elif hk == "inspection_required":
            state = "attention"
        elif hk == "clean" and occupancy_known:
            state = "vacant_clean"
        else:
            state = "unknown"

        signals = {
            "temperature_c": {"value": temp, "quality": Q_NAMES.get(temp_q, "missing")},
            "humidity_pct": {"value": humidity, "quality": Q_NAMES.get(humidity_q, "missing")},
            "co2_ppm": {"value": co2, "quality": Q_NAMES.get(co2_q, "missing")},
            "setpoint_c": {"value": setpoint, "quality": Q_NAMES.get(_last_value(obs.rooms.quality["setpoint_c"], t, room_id), "missing")},
            "presence": {"value": presence, "quality": Q_NAMES.get(_last_value(obs.rooms.quality["presence"], t, room_id), "missing")},
            "water_flow_lph": {"value": water_flow, "quality": Q_NAMES.get(water_q, "missing")},
            "housekeeping_state": {"value": hk, "quality": Q_NAMES.get(hk_q, "missing")},
        }
        for signal in signals.values():
            q = signal["quality"]
            q_counts[q] = q_counts.get(q, 0) + 1
            q_total += 1

        temp_valid = temp is not None and temp_q in (0, 4, 5)
        comfort = None if not occupied or not temp_valid else obs.building.comfort_c[0] <= temp <= obs.building.comfort_c[1]
        rooms.append({
            "id": room_id,
            "floor": obs.building.floor_of.get(room_id),
            "orientation": room_catalog.get(room_id, {}).get("orientation"),
            "area_m2": room_catalog.get(room_id, {}).get("area_m2"),
            "state": state,
            "occupancy": {"occupied": occupied if occupancy_known else None, "known": occupancy_known},
            "readiness": {"state": hk, "known": hk is not None, "quality": Q_NAMES.get(hk_q, "missing")},
            "comfort": {"within_band": comfort, "band_c": list(obs.building.comfort_c)},
            "signals": signals,
        })

    room_frame = obs.rooms.values["temperature_c"].join(obs.rooms.values["humidity_pct"].add_suffix("_humidity"))
    energy_now = obs.asof(t).latest("energy")
    weather_now = obs.asof(t).latest("weather")
    water_main_flow = _last_value(obs.water.main.flow_lph, t, "WM_MAIN")
    water_main_quality = _last_value(obs.water.main.quality, t, "WM_MAIN")
    energy_history = _series(obs.frames["energy"], t)
    temperature_history = _series(obs.frames["weather"], t)
    if not obs.frames["room.temperature_c"].empty:
        occupied_rooms_now = [r["id"] for r in rooms if r["occupancy"]["occupied"] is True]
        if occupied_rooms_now:
            temp_frame = obs.frames["room.temperature_c"][occupied_rooms_now]
            avg_temp = temp_frame.mean(axis=1).to_frame("occupied_avg_c")
            temperature_history = _series(avg_temp, t)

    decisions = snapshot(_jsonl(decisions_path, t), ('event_id',))
    incidents = snapshot([r for r in _jsonl(incidents_path, t) if r.get('issue_type') != 'no_incident'], ('episode_id', 'incident_id', 'event_id'))
    actions = _jsonl(actions_path, t)
    for decision in decisions:
        decision['lifecycle'] = decision_lifecycle(decision, actions)
    insights = learning_insights(obs, t) + snapshot(_jsonl(insights_path, t), ('insight_id',))
    metric_counts = {
        "occupancy": sum(room["occupancy"]["occupied"] is True for room in rooms),
        "occupancy_known_rooms": sum(room["occupancy"]["known"] for room in rooms),
        "rooms_attention": sum(room["state"] in {"dirty", "cleaning", "attention"} or room["comfort"]["within_band"] is False for room in rooms),
        "active_incidents": sum(str(item.get("status", "")).lower() in {"open", "active", "acknowledged", "in_progress", "investigating", "new"} for item in incidents),
        "decisions_generated": len(decisions),
        "pending_approvals": sum(item["lifecycle"]["approval"] in {"pending", "requested"} for item in decisions),
        "staff_available": 0,
        "robots_available": 0,
    }
    roster = view.roster()
    active_resources = []
    if len(roster):
        current = roster[(roster["start_utc"] <= t) & (roster["end_utc"] > t)]
        for row in current.to_dict("records"):
            status = str(row.get("status") or "unknown")
            resource_id = str(row.get("resource_id") or "unknown")
            role = str(row.get("role") or obs.building.resources.get(resource_id, "unknown"))
            available = status == "scheduled"
            if available and role == "robot":
                metric_counts["robots_available"] += 1
            elif available:
                metric_counts["staff_available"] += 1
            active_resources.append({"resource_id": resource_id, "role": role, "status": status, "available": available,
                                     "start_utc": _scalar(row.get("start_utc")), "end_utc": _scalar(row.get("end_utc"))})

    current_events = known_events.sort_values("timestamp_utc").tail(24) if len(known_events) else known_events
    event_records = []
    for row in current_events.to_dict("records"):
        event_records.append({key: _scalar(value) for key, value in row.items() if not (isinstance(value, float) and np.isnan(value))})

    quality_summary = {
        "counts": q_counts,
        "signals_total": q_total,
        "valid_share_pct": round(100 * q_counts.get("ok", 0) / q_total, 1) if q_total else None,
        "latest_missing": sum(1 for room in rooms for signal in room["signals"].values() if signal["value"] is None),
        "pipeline_findings": [],
    }
    if len(obs.issues):
        visible_issues = obs.issues[obs.issues["start_utc"].notna() & (obs.issues["start_utc"] <= t)]
        grouped = visible_issues.groupby(["source", "issue", "asset_id"], dropna=False).size().reset_index(name="episodes")
        quality_summary["pipeline_findings"] = [{key: _scalar(value) for key, value in row.items()} for row in grouped.to_dict("records")]

    time_local = t.tz_convert("Europe/Vienna")
    room_history = build_room_history(obs, t, selected_room_id)["series"] if selected_room_id in obs.building.rooms else []
    return {
        "schema_version": "plato-ui/1",
        "generated_at_utc": pd.Timestamp.now(tz="UTC").isoformat().replace("+00:00", "Z"),
        "building": {"id": obs.manifest.get("model_id", "BUILDING"), "name": obs.manifest.get("model_id", "Building"),
                     "room_count": len(obs.building.rooms), "floor_count": len(set(obs.building.floor_of.values())),
                     "assets": [{"id": item.get("id"), "type": item.get("type"), "name": item.get("name")} for item in obs.building.raw.get("building", {}).get("assets", [])],
                     "comfort_band_c": list(obs.building.comfort_c)},
        "dataset": {"profile": obs.manifest.get("profile"), "version": obs.manifest.get("dataset_version"),
                    "synthetic": obs.manifest.get("synthetic"), "start_utc": _scalar(obs.grid[0]), "end_utc": _scalar(obs.grid[-1]),
                    "step_minutes": 15},
        "replay": {"timestamp_utc": _scalar(t), "timestamp_local": time_local.isoformat(), "timezone": "Europe/Vienna",
                   "step_index": int(step_index), "step_count": int(len(obs.grid)), "playing": False},
        "metrics": metric_counts | {
            "energy_demand_kw": _scalar(energy_now.get("building_load_kw")),
            "hvac_kw": _scalar(energy_now.get("hvac_electric_kw")),
            "grid_import_kw": _scalar(energy_now.get("grid_import_kw")),
            "pv_kw": _scalar(energy_now.get("pv_kw")),
            "battery_soc_kwh": _scalar(energy_now.get("battery_soc_kwh")),
            "outdoor_c": _scalar(weather_now.get("outdoor_c")),
            "water_main_flow_lph": water_main_flow,
            "water_quality": Q_NAMES.get(water_main_quality, "missing"),
            "data_quality_valid_share_pct": quality_summary["valid_share_pct"],
        },
        "rooms": rooms,
        "room_history": room_history,
        "incidents": incidents,
        "decisions": decisions,
        "actions": actions,
        "insights": insights,
        "operational_context": operational_context(obs, view, t),
        "capabilities": {"approval_write": False, "execution_write": False,
                         "note": "Decision playbooks and a guard exist, but there is no connected detector runner, approval store or mock executor. Imported logs are read-only."},
        "resources": active_resources,
        "resource_tracking": build_resource_tracking(obs, view, t),
        "events": event_records,
        "quality": quality_summary,
        "series": {"energy": energy_history, "temperature": temperature_history,
                   "water": _series(obs.frames["water.main.flow_lph"].rename(columns={"WM_MAIN": "flow_lph"}), t)},
        "availability": {"incidents": bool(incidents_path and Path(incidents_path).is_file()),
                         "decisions": bool(decisions_path and Path(decisions_path).is_file()),
                         "insights": bool(insights), "execution": bool(actions_path and Path(actions_path).is_file()),
                         "note": "Replay uses causal observe.asof data. Incidents and decisions are shown only when JSONL outputs are supplied."},
        "limitations": list(obs.manifest.get("limitations", [])),
    }
