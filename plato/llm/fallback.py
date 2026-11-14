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
        # defrost is a normal heat-pump state; only lockouts / unknown codes count as a fault
        hp_bad = bool(f.get("hp_alarm")) or any(
            k not in ("running", "standby", "defrost") for k in (f.get("hp_status_counts_24h") or {}))
        if f.get("rooms_cold_now", 0) and f["rooms_cold_now"] >= 3:
            return _out("heating_comfort", "equipment_fault", "high", 0.7,
                        f"{asset}: {f['rooms_cold_now']} occupied rooms are below the comfort floor at once, "
                        "which points to a heating-plant problem.",
                        ["rooms_cold_now", "hp_status_counts_24h", "outdoor_c"])
        cold = f.get("temp_c") is not None and f.get("temp_c") < 20
        if cold:
            cause = "equipment_fault" if hp_bad else "weather_load"
            sev = "high" if f.get("guest_cold_requests", 0) > 0 else "medium"
            return _out("heating_comfort", cause, sev,
                        0.7 if f.get("guest_cold_requests", 0) > 0 else 0.55,
                        f"{asset}: occupied room below the comfort floor"
                        + (" with a guest cold request" if f.get("guest_cold_requests", 0) > 0 else "") + ".",
                        ["temp_c", "guest_cold_requests", "hp_status_counts_24h"])
        return _out("no_incident", "unknown", "low", 0.5,
                    f"{asset}: comfort is inside the allowed band.", ["temp_c"])

    if det == "sensor_quality":
        if "reset" in flags or f.get("meter_reset_in_window"):
            wo = f.get("confirmed_by_work_order")
            return _out("data_quality", "meter_artefact", "low", 0.8,
                        f"{asset}: cumulative meter was exchanged (counter restart)"
                        + (f", confirmed by {wo}" if wo else "") + "; not a real water event.",
                        ["quality_flags", "meter_reset_in_window", "confirmed_by_work_order"])
        return _out("data_quality", "sensor_fault", "low", 0.7,
                    f"{asset}: sensor looks faulty ({', '.join(sorted(flags)) or 'quality flags'}).",
                    ["quality_flags"])

    if det == "room_turnaround":
        state = f.get("housekeeping_state")
        waiting = bool(f.get("guest_waiting"))
        slack = f.get("slack_minutes")
        sick = f.get("cleaners_sick_known") or []
        ready = f.get("projected_ready_utc")
        if state == "clean" and not waiting:
            return _out("room_turnaround", "unknown", "low", 0.6,
                        f"{asset}: room is clean and ready for the next guest.", ["housekeeping_state"])
        cause = "staffing"
        late = slack is not None and slack < 0
        if waiting or late:
            sev, conf = "high", 0.8
        elif slack is not None and slack < 60:
            sev, conf = "medium", 0.75
        else:
            sev, conf = "low", 0.7
        reason = (f"{asset}: room is {state or 'not clean'} and the next guest "
                  + ("is already waiting" if waiting else "arrives soon")
                  + (f"; projected ready {ready}" if ready else "")
                  + (f", {abs(slack):.0f} min late" if late else "")
                  + (f"; {len(sick)} cleaner(s) called in sick" if sick else "") + ".")
        return _out("room_turnaround", cause, sev, conf, reason,
                    ["housekeeping_state", "guest_waiting", "slack_minutes", "projected_ready_utc",
                     "cleaners_sick_known"])

    return _out("data_quality", "unknown", "low", 0.2,
                f"{asset}: no rule for detector '{det}', flagged for review.", [])
