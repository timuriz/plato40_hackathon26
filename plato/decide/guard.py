"""Deterministic safety guard: the last step before a decision is written.

It enforces the hard "never" rules and sets autonomy / approval / consent flags in plain code, so neither
the LLM nor a playbook can weaken them. Every change it makes is returned as a note (shown in the demo).

Rules enforced (CHALLENGE section 7):
  * only vocabulary actions; unknown -> request_human_approval
  * never release a room that is not clean -> schedule_cleaning instead
  * robots cannot clean bathrooms (dispatch_robot -> schedule_cleaning when a bathroom is involved)
  * occupied-room setpoints stay inside 20-23 C
  * never trade comfort for energy: optimize_energy is deferred if comfort is at risk
  * approval table (BASE_AUTONOMY); entering an occupied room or a water shutoff also needs guest consent
  * low confidence + a costly action -> ask a human instead of acting
Consent is only ever marked as *needed*; this code never records it as granted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from .playbooks import CONF_PROPOSE, Proposal, room_occupied
from .vocabulary import (
    ACTIONS, APPROVAL_AND_CONSENT, BASE_AUTONOMY, COMFORT_MAX_C, COMFORT_MIN_C, HUMAN_APPROVAL, PRIORITIES,
)

COSTLY = {"dispatch_technician", "block_room", "reassign_room"}


@dataclass
class Guarded:
    action: str
    priority: str
    autonomy: str
    requires_human_approval: bool
    requires_guest_consent: bool
    params: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)


def guard(p: Proposal, u: Dict[str, Any], packet: Dict[str, Any]) -> Guarded:
    f = packet.get("features", {}) or {}
    notes: List[str] = []
    action, priority, params = p.action, p.priority, dict(p.params)
    occ = room_occupied(packet)

    if action not in ACTIONS:
        notes.append(f"action '{action}' is not in the vocabulary -> request_human_approval")
        action = "request_human_approval"
    if priority not in PRIORITIES:
        notes.append(f"priority '{priority}' invalid -> medium")
        priority = "medium"

    if action == "release_room" and f.get("housekeeping_state") != "clean":
        notes.append("never release an uncleaned room -> schedule_cleaning")
        action = "schedule_cleaning"

    if action == "dispatch_robot" and f.get("bathroom_cleaning_needed", True):
        notes.append("robots cannot clean bathrooms -> schedule_cleaning (staff)")
        action = "schedule_cleaning"

    if action == "adjust_room_setpoint":
        tgt = params.get("target_setpoint_c")
        if tgt is None:
            notes.append("no target setpoint given -> flag_anomaly")
            action = "flag_anomaly"
        elif occ and not COMFORT_MIN_C <= tgt <= COMFORT_MAX_C:
            clamped = min(COMFORT_MAX_C, max(COMFORT_MIN_C, tgt))
            notes.append(f"occupied room: setpoint {tgt} C outside {COMFORT_MIN_C}-{COMFORT_MAX_C} C -> {clamped} C")
            params["target_setpoint_c"] = clamped

    if action == "optimize_energy" and f.get("comfort_at_risk"):
        notes.append("never trade comfort for energy: comfort is at risk -> defer_action")
        action = "defer_action"

    if action in COSTLY and u["confidence"] < CONF_PROPOSE:
        notes.append(f"confidence {u['confidence']:.2f} too low for a costly action -> request_human_approval")
        action = "request_human_approval"

    autonomy = BASE_AUTONOMY[action]
    consent = False
    if p.enters_room and occ and action in ("request_inspection", "dispatch_technician"):
        autonomy, consent = APPROVAL_AND_CONSENT, True
        notes.append("a person would enter an occupied room: approval and guest consent are needed (not granted)")
    if f.get("water_shutoff"):
        autonomy, consent = APPROVAL_AND_CONSENT, True
        notes.append("water shutoff: approval and guest consent are needed (not granted)")

    return Guarded(action=action, priority=priority, autonomy=autonomy,
                   requires_human_approval=autonomy in (HUMAN_APPROVAL, APPROVAL_AND_CONSENT),
                   requires_guest_consent=consent, params=params, notes=notes)
