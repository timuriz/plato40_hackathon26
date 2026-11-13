"""Deterministic rule-based classifier: used when there is no API key, the LLM output stays invalid
after one retry, or the call fails. It reads the same evidence packet and returns the same shape as
the LLM (`validate_incident` passes), so the rest of the pipeline never needs to know which one ran.

Packet convention (built by the detectors, see `examples` in scripts/llm_probe.py):
    {"detector": "water_baseline" | "comfort" | "sensor_quality" | ..., "asset": "R104",
     "features": {...numbers/flags...}, "quality_flags": [...]}
Unknown detectors fall back to a low-confidence `flag_anomaly`.
"""
from __future__ import annotations

from typing import Any, Dict


def _out(issue_type, cause, action, priority, conf, reason, used) -> Dict[str, Any]:
    return {"issue_type": issue_type, "likely_cause": cause, "action": action, "priority": priority,
            "confidence": conf, "reason": reason, "evidence_used": list(used)}


def rule_based_incident(packet: Dict[str, Any]) -> Dict[str, Any]:
    det = packet.get("detector", "")
    f = packet.get("features", {}) or {}
    flags = set(packet.get("quality_flags", []) or [])
    asset = packet.get("asset", "unknown asset")

    if det == "water_baseline":
        if "reset" in flags or f.get("meter_reset_in_window"):
            return _out("data_quality", "meter_artefact", "flag_anomaly", "low", 0.7,
                        f"{asset}: the jump coincides with a meter counter reset, not a real flow.",
                        ["quality_flags", "meter_reset_in_window"])
        vacant = f.get("occupied_share", 1.0) <= 0.05
        steady = f.get("flow_cv", 9.9) <= 0.3 and f.get("duration_h", 0) >= 2
        high = f.get("flow_z", 0) >= 4
        if vacant and steady and high:
            return _out("water_leak", "equipment_fault", "request_human_approval", "high", 0.75,
                        f"{asset}: steady high flow in a vacant room, far above its own baseline "
                        f"(z={f.get('flow_z')}) for {f.get('duration_h')} h.",
                        ["occupied_share", "flow_cv", "flow_z", "duration_h"])
        if f.get("occupied_share", 0) > 0.5 and f.get("duration_h", 0) < 1:
            return _out("no_incident", "guest_usage", "defer_action", "low", 0.7,
                        f"{asset}: short burst while occupied, consistent with guest use.",
                        ["occupied_share", "duration_h"])
        return _out("water_leak", "unknown", "flag_anomaly", "medium", 0.4,
                    f"{asset}: unusual water use but the evidence is not conclusive.",
                    ["flow_z", "occupied_share"])

    if det == "comfort":
        cold = f.get("temp_c") is not None and f.get("temp_c") < 20
        if cold and f.get("guest_cold_requests", 0) > 0:
            cause = "equipment_fault" if f.get("hp_alarm") else "weather_load"
            return _out("heating_comfort", cause, "adjust_room_setpoint", "high", 0.7,
                        f"{asset}: below 20 C with a guest cold request; raise the setpoint within 20-23 C.",
                        ["temp_c", "guest_cold_requests", "hp_alarm"])
        return _out("no_incident", "unknown", "defer_action", "low", 0.5,
                    f"{asset}: comfort is inside the allowed band.", ["temp_c"])

    if det == "sensor_quality":
        return _out("data_quality", "sensor_fault", "flag_anomaly", "low", 0.7,
                    f"{asset}: sensor looks faulty ({', '.join(sorted(flags)) or 'quality flags'}).",
                    ["quality_flags"])

    if det == "room_turnaround":
        if f.get("housekeeping_state") != "clean":
            return _out("room_turnaround", "staffing", "schedule_cleaning", "medium", 0.7,
                        f"{asset}: room is not clean, so it cannot be released; schedule cleaning first.",
                        ["housekeeping_state"])
        return _out("room_turnaround", "unknown", "release_room", "low", 0.6,
                    f"{asset}: room is clean and can be released.", ["housekeeping_state"])

    return _out("data_quality", "unknown", "flag_anomaly", "low", 0.2,
                f"{asset}: no rule for detector '{det}', flagged for review.", [])
