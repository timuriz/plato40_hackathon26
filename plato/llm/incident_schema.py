"""The `report_incident` contract between the evidence packets and the LLM.

The LLM never executes anything: `report_incident` is a function tool that exists only to force
a JSON shape. Safety flags (`requires_human_approval`, `requires_guest_consent`, autonomy) are NOT
part of it; plain code sets them afterwards so the LLM cannot downgrade them.
"""
from __future__ import annotations

from typing import Any, Dict, List

ISSUE_TYPES = [
    "room_turnaround", "water_leak", "heating_comfort", "ventilation_maintenance", "occupancy_surge",
    "energy_optimization", "logistics", "data_quality",
    "no_incident",  # the candidate was a false alarm / normal operation
]
LIKELY_CAUSES = [
    "equipment_fault", "guest_usage", "cleaning_activity", "meter_artefact", "sensor_fault",
    "weather_load", "staffing", "unknown",
]
ACTIONS = [
    "schedule_cleaning", "dispatch_robot", "adjust_room_setpoint", "optimize_energy", "block_room",
    "release_room", "reassign_room", "request_inspection", "dispatch_technician", "order_service",
    "notify_guest", "flag_anomaly", "defer_action", "request_human_approval",
]
PRIORITIES = ["critical", "high", "medium", "low"]

TOOL_NAME = "report_incident"

INCIDENT_TOOL: Dict[str, Any] = {
    "type": "function",
    "name": TOOL_NAME,
    "description": (
        "Report your assessment of ONE candidate incident from a hotel building-operations evidence packet. "
        "Use only facts that are in the packet. Never invent measurements."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "issue_type": {"type": "string", "enum": ISSUE_TYPES},
            "likely_cause": {"type": "string", "enum": LIKELY_CAUSES},
            "action": {"type": "string", "enum": ACTIONS},
            "priority": {"type": "string", "enum": PRIORITIES},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reason": {"type": "string", "description": "One or two sentences a hotel operator can read."},
            "evidence_used": {
                "type": "array", "items": {"type": "string"},
                "description": "Names of the packet fields your decision relied on.",
            },
        },
        "required": ["issue_type", "likely_cause", "action", "priority", "confidence", "reason",
                     "evidence_used"],
        "additionalProperties": False,
    },
}

SYSTEM_INSTRUCTIONS = (
    "You are the analysis step of an autonomous operations system for a fictional 40-room hotel (PLATO-40). "
    "You receive one evidence packet produced by deterministic detectors. Decide what the candidate incident "
    f"really is and call the function `{TOOL_NAME}` exactly once. Rules: use only information in the packet; "
    "treat BMS alarms as clues, not truth; blank means unknown, never zero; values labelled estimated or "
    "suspect are not measurements; if the evidence is weak say so with a low confidence; a false alarm is "
    "`no_incident`. You only propose; you never claim an action was executed."
)

_REQUIRED = ["issue_type", "likely_cause", "action", "priority", "confidence", "reason", "evidence_used"]
_ENUMS = {"issue_type": ISSUE_TYPES, "likely_cause": LIKELY_CAUSES, "action": ACTIONS, "priority": PRIORITIES}


def validate_incident(obj: Any) -> List[str]:
    """Return a list of problems (empty list = valid). Deliberately strict: no silent coercion."""
    if not isinstance(obj, dict):
        return [f"top level must be a JSON object, got {type(obj).__name__}"]
    errs: List[str] = []
    for k in _REQUIRED:
        if k not in obj:
            errs.append(f"missing field '{k}'")
    for k in obj:
        if k not in _REQUIRED:
            errs.append(f"unexpected field '{k}'")
    for k, allowed in _ENUMS.items():
        if k in obj and obj[k] not in allowed:
            errs.append(f"'{k}' must be one of {allowed}, got {obj[k]!r}")
    c = obj.get("confidence")
    if "confidence" in obj and (isinstance(c, bool) or not isinstance(c, (int, float)) or not 0 <= c <= 1):
        errs.append(f"'confidence' must be a number in [0, 1], got {c!r}")
    r = obj.get("reason")
    if "reason" in obj and (not isinstance(r, str) or not r.strip()):
        errs.append("'reason' must be a non-empty string")
    e = obj.get("evidence_used")
    if "evidence_used" in obj and (not isinstance(e, list) or not all(isinstance(x, str) for x in e)):
        errs.append("'evidence_used' must be an array of strings")
    return errs
