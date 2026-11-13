"""Playbooks: map an UNDERSTAND result (+ the packet facts) to proposed actions.

Deterministic and readable on purpose. These only PROPOSE; the safety guard (guard.py) has the last word
and sets the approval / consent flags. A false alarm (`no_incident`) produces no action at all, because
alert spam counts against us.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from .vocabulary import COMFORT_MAX_C

CONF_ACT = 0.8      # at or above: take the stronger action
CONF_PROPOSE = 0.5  # below this: only flag, never act


@dataclass
class Proposal:
    action: str
    priority: str
    why: str = ""                      # one line: why this action for this understanding
    enters_room: bool = False          # would a person enter the guest room?
    params: Dict[str, Any] = field(default_factory=dict)  # e.g. target_setpoint_c


def room_occupied(packet: Dict[str, Any]) -> bool:
    f = packet.get("features", {}) or {}
    return bool(f.get("occupied")) or float(f.get("occupied_share", 0) or 0) > 0.5


def propose(u: Dict[str, Any], packet: Dict[str, Any]) -> List[Proposal]:
    it, cause, sev, conf = u["issue_type"], u["likely_cause"], u["severity"], u["confidence"]
    f = packet.get("features", {}) or {}
    occ = room_occupied(packet)

    if it == "no_incident":
        return []

    if it == "water_leak":
        if conf >= CONF_ACT and cause == "equipment_fault":
            return [Proposal("dispatch_technician", sev, "leak is likely; a technician must confirm and repair",
                             enters_room=True)]
        if conf >= CONF_PROPOSE:
            return [Proposal("request_inspection", sev, "leak is plausible; confirm before spending a technician",
                             enters_room=True)]
        return [Proposal("flag_anomaly", "medium", "evidence is weak; record and keep watching")]

    if it == "heating_comfort":
        out: List[Proposal] = []
        sp = f.get("setpoint_c")
        if sp is not None and sp < COMFORT_MAX_C:
            out.append(Proposal("adjust_room_setpoint", sev, "raise the setpoint within the comfort band",
                                params={"target_setpoint_c": min(COMFORT_MAX_C, float(sp) + 1.0),
                                        "from_setpoint_c": float(sp)}))
        if cause == "equipment_fault" and conf >= CONF_PROPOSE:
            out.append(Proposal("dispatch_technician", sev, "the plant looks faulty; setpoint alone will not fix it"))
        return out or [Proposal("flag_anomaly", sev, "comfort problem without a safe automatic remedy")]

    if it == "room_turnaround":
        if f.get("housekeeping_state") == "clean":
            return [Proposal("release_room", "low", "room is clean")]
        pri = "high" if f.get("queue_length", 0) >= 3 or sev in ("high", "critical") else sev
        return [Proposal("schedule_cleaning", pri, "room is not clean and cannot be released")]

    if it == "ventilation_maintenance":
        return [Proposal("dispatch_technician", sev, "ventilation maintenance needed")] if conf >= CONF_PROPOSE \
            else [Proposal("flag_anomaly", "medium", "evidence is weak; record and keep watching")]

    if it == "logistics":
        return [Proposal("order_service", sev, "logistics limit is at risk")] if conf >= CONF_PROPOSE \
            else [Proposal("flag_anomaly", "low", "evidence is weak; record and keep watching")]

    if it == "energy_optimization":
        return [Proposal("optimize_energy", "low", "shift load without touching comfort")]

    # data_quality, occupancy_surge and anything else: record it, do not act physically
    return [Proposal("flag_anomaly", sev if it != "data_quality" else "low", "record for review")]
