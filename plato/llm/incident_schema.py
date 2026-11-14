"""The `report_incident` contract for the UNDERSTAND stage (LLM side).

UNDERSTAND only answers "what is going on, and why?": issue_type, likely_cause, severity, confidence,
reason, evidence_used. It deliberately does NOT choose an action, priority, approval or consent; those
belong to the DECIDE & ACT stage (`plato/decide`). `report_incident` is a function tool used purely to
force a JSON shape; nothing is executed.
"""
from __future__ import annotations

from typing import Any, Dict, List

ISSUE_TYPES = [
    "room_turnaround", "water_leak", "heating_comfort", "ventilation_maintenance", "occupancy_surge",
    "energy_optimization", "logistics", "guest_request", "data_quality",
    "no_incident",  # the candidate was a false alarm / normal operation
]
LIKELY_CAUSES = [
    "equipment_fault", "guest_usage", "cleaning_activity", "meter_artefact", "sensor_fault",
    "weather_load", "staffing", "unknown",
]
SEVERITIES = ["critical", "high", "medium", "low"]  # how bad the situation is if left alone

TOOL_NAME = "report_incident"

INCIDENT_TOOL: Dict[str, Any] = {
    "type": "function",
    "name": TOOL_NAME,
    "description": (
        "Report your assessment of ONE candidate incident from a hotel building-operations evidence packet: "
        "what it is, the most likely cause, how severe it is and how sure you are. Do NOT propose an action. "
        "Use only facts that are in the packet. Never invent measurements."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "issue_type": {"type": "string", "enum": ISSUE_TYPES},
            "likely_cause": {"type": "string", "enum": LIKELY_CAUSES},
            "severity": {"type": "string", "enum": SEVERITIES,
                         "description": "Impact if nothing is done (guest comfort, damage, deadlines)."},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reason": {"type": "string", "description": "One or two sentences a hotel operator can read."},
            "evidence_used": {
                "type": "array", "items": {"type": "string"},
                "description": "Names of the packet fields your assessment relied on.",
            },
        },
        "required": ["issue_type", "likely_cause", "severity", "confidence", "reason", "evidence_used"],
        "additionalProperties": False,
    },
}

SYSTEM_INSTRUCTIONS = (
    "You are the UNDERSTAND step of an autonomous operations system for a fictional 40-room hotel (PLATO-40). "
    "You receive one evidence packet produced by deterministic detectors. Work out what the candidate incident "
    f"really is and why, then call the function `{TOOL_NAME}` exactly once. You do not decide what to do: "
    "another component chooses actions and checks safety rules. Rules: use only information in the packet; "
    "treat BMS alarms as clues, not truth; blank means unknown, never zero; values labelled estimated or "
    "suspect are not measurements; if the evidence is weak say so with a low confidence; a false alarm is "
    "`no_incident`."
)

_REQUIRED = ["issue_type", "likely_cause", "severity", "confidence", "reason", "evidence_used"]
_ENUMS = {"issue_type": ISSUE_TYPES, "likely_cause": LIKELY_CAUSES, "severity": SEVERITIES}


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
