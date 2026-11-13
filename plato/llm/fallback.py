"""Deterministic rule-based UNDERSTAND fallback: used when there is no API key, the LLM output stays invalid
after one retry, or the call fails. It reads the same evidence packet and returns the same shape as the LLM
(`validate_incident` passes). Like the LLM it only classifies; it never picks an action (that is DECIDE).

Packet convention (built by the detectors, see PACKETS in scripts/llm_probe.py):
    {"detector": "water_baseline" | "comfort" | "sensor_quality" | "room_turnaround" | ..., "asset": "R104",
     "features": {...numbers/flags...}, "quality_flags": [...]}
Unknown detectors fall back to a low-confidence `data_quality` finding.
"""
from __future__ import annotations

from typing import Any, Dict


def _out(issue_type, cause, severity, conf, reason, used) -> Dict[str, Any]:
    return {"issue_type": issue_type, "likely_cause": cause, "severity": severity,
            "confidence": conf, "reason": reason, "evidence_used": list(used)}


def rule_based_incident(packet: Dict[str, Any]) -> Dict[str, Any]:
    det = packet.get("detector", "")
    f = packet.get("features", {}) or {}
    flags = set(packet.get("quality_flags", []) or [])
    asset = packet.get("asset", "unknown asset")

    if det == "water_baseline":
        if "reset" in flags or f.get("meter_reset_in_window"):
            return _out("data_quality", "meter_artefact", "low", 0.7,
                        f"{asset}: the jump coincides with a meter counter reset, not a real flow.",
                        ["quality_flags", "meter_reset_in_window"])
        vacant = f.get("occupied_share", 1.0) <= 0.05
        steady = f.get("flow_cv", 9.9) <= 0.3 and f.get("duration_h", 0) >= 2
        high = f.get("flow_z", 0) >= 4
        if vacant and steady and high:
            return _out("water_leak", "equipment_fault", "high", 0.75,
                        f"{asset}: steady high flow in a vacant room, far above its own baseline "
                        f"(z={f.get('flow_z')}) for {f.get('duration_h')} h.",
                        ["occupied_share", "flow_cv", "flow_z", "duration_h"])
        if f.get("occupied_share", 0) > 0.5 and f.get("duration_h", 0) < 1:
            return _out("no_incident", "guest_usage", "low", 0.7,
                        f"{asset}: short burst while occupied, consistent with guest use.",
                        ["occupied_share", "duration_h"])
        return _out("water_leak", "unknown", "medium", 0.4,
                    f"{asset}: unusual water use but the evidence is not conclusive.",
                    ["flow_z", "occupied_share"])

    if det == "comfort":
        cold = f.get("temp_c") is not None and f.get("temp_c") < 20
        if cold and f.get("guest_cold_requests", 0) > 0:
            cause = "equipment_fault" if f.get("hp_alarm") else "weather_load"
            return _out("heating_comfort", cause, "high", 0.7,
                        f"{asset}: below 20 C with a guest cold request.",
                        ["temp_c", "guest_cold_requests", "hp_alarm"])
        return _out("no_incident", "unknown", "low", 0.5,
                    f"{asset}: comfort is inside the allowed band.", ["temp_c"])

    if det == "sensor_quality":
        return _out("data_quality", "sensor_fault", "low", 0.7,
                    f"{asset}: sensor looks faulty ({', '.join(sorted(flags)) or 'quality flags'}).",
                    ["quality_flags"])

    if det == "room_turnaround":
        if f.get("housekeeping_state") != "clean":
            return _out("room_turnaround", "staffing", "medium", 0.7,
                        f"{asset}: room is not clean, so it is not ready for the next guest.",
                        ["housekeeping_state"])
        return _out("room_turnaround", "unknown", "low", 0.6,
                    f"{asset}: room is clean and ready for the next step.", ["housekeeping_state"])

    return _out("data_quality", "unknown", "low", 0.2,
                f"{asset}: no rule for detector '{det}', flagged for review.", [])
