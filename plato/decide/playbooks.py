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
        if conf < CONF_PROPOSE:
            return [Proposal("flag_anomaly", "medium", "evidence is weak; record and keep watching")]
        if f.get("open_work_order"):
            return [Proposal("defer_action", sev, f"work order {f['open_work_order']} already covers AHU1; "
                                                  "keep monitoring the filter signal")]
        if f.get("contractor_needed"):
            why = "no in-house technician slot soon; request an external HVAC contractor (needs approval)"
        elif f.get("technician_next_slot_utc"):
            why = f"service AHU1 filters in the next real technician slot ({f['technician_next_slot_utc']})"
        else:
            why = "ventilation maintenance needed"
        return [Proposal("dispatch_technician", sev, why, params={
            "slot_utc": f.get("technician_next_slot_utc"), "contractor": bool(f.get("contractor_needed"))})]

    if it == "logistics":
        if conf < CONF_PROPOSE:
            return [Proposal("flag_anomaly", "low", "evidence is weak; record and keep watching")]
        if f.get("subject") == "floor_bin":
            if f.get("robot_can_carry"):
                return [Proposal("dispatch_robot", sev, "robot carries the full floor bin to the central container")]
            return [Proposal("schedule_cleaning", sev, "housekeeping empties the floor bin early")]
        if f.get("extra_service_helps") is False:
            return [Proposal("request_human_approval", sev, "an extra service cannot arrive before the regular "
                                                            "one; a person must arrange relief")]
        return [Proposal("order_service", sev, "order an extra service now (about 24 h lead time)")]

    if it == "energy_optimization":
        # autonomous, low priority; the guard converts it to defer_action when features.comfort_at_risk
        shift = [k for k in ("battery_plan", "preheat_plan") if f.get(k)]
        return [Proposal("optimize_energy", "low",
                         "shift load to cheap hours (" + (", ".join(shift) or "tariff spread") + ") without touching comfort",
                         params={"shift": shift})]

    if it == "occupancy_surge":
        if conf < CONF_PROPOSE:
            return [Proposal("flag_anomaly", "medium", "evidence is weak; record and keep watching")]
        gap = int(f.get("gap_rooms") or 0)
        out = [Proposal("schedule_cleaning", sev,
                        "surge day: order the turnovers (same-day arrivals first) onto the cleaners known to be on shift",
                        params={"scope": "same_day_turnovers_first", "gap_rooms": gap})]
        if int(f.get("robots_in_service") or 0) > 0 and int(f.get("robot_floor_jobs") or 0) > 0:
            out.append(Proposal("dispatch_robot", "medium",
                                "robots on shift take the floor passes only; bathrooms stay with staff",
                                params={"scope": "floor_only", "jobs": int(f["robot_floor_jobs"])}))
        if gap > 0 and not f.get("contingent"):
            if int(f.get("arrivals_at_risk") or 0) > 0:
                out.append(Proposal("notify_guest", sev,
                                    "tell the arrivals at risk a realistic ready time instead of a promised 15:00",
                                    params={"arrivals_at_risk": int(f["arrivals_at_risk"]),
                                            "ready_window_local": f.get("oncall_ready_window_local")}))
            if f.get("oncall_needed"):
                out.append(Proposal("request_human_approval", sev,
                                    "the staffed capacity is short by "
                                    f"{gap} room(s); the costly on-call housekeeper (waiting guests only, one room at a "
                                    "time) needs a human decision",
                                    params={"resource": "ONCALL_1", "rooms": int(f.get("oncall_would_cover_rooms") or 0),
                                            "residual_gap_rooms": int(f.get("residual_gap_after_oncall") or 0)}))
        return out

    if it == "data_quality" and packet.get("detector") == "ventilation_sensor_gap":
        long_gap = float(f.get("gap_hours_so_far") or 0) >= 24
        return [Proposal("flag_anomaly", "medium" if long_gap else "low",
                         "AHU1 filter_dp_pa is blank: record the gap and restore the sensor feed; "
                         "a gap is not evidence of filter degradation")]

    # data_quality and anything else: record it, do not act physically
    return [Proposal("flag_anomaly", sev if it != "data_quality" else "low", "record for review")]
