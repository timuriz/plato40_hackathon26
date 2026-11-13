"""Action vocabulary and autonomy table (DECISION_CONTRACT.md, CHALLENGE section 7)."""
from __future__ import annotations

ACTIONS = [
    "schedule_cleaning", "dispatch_robot", "adjust_room_setpoint", "optimize_energy", "block_room",
    "release_room", "reassign_room", "request_inspection", "dispatch_technician", "order_service",
    "notify_guest", "flag_anomaly", "defer_action", "request_human_approval",
]
PRIORITIES = ["critical", "high", "medium", "low"]

AUTONOMOUS = "autonomous"
HUMAN_APPROVAL = "human_approval"
APPROVAL_AND_CONSENT = "approval_and_consent"

# Base autonomy per action. The guard may raise it (never lower it), e.g. entering an occupied room.
BASE_AUTONOMY = {
    "schedule_cleaning": AUTONOMOUS,
    "dispatch_robot": AUTONOMOUS,
    "adjust_room_setpoint": AUTONOMOUS,  # only inside the 20-23 C band for occupied rooms (guard clamps)
    "optimize_energy": AUTONOMOUS,       # never at the expense of comfort (guard checks)
    "release_room": AUTONOMOUS,          # only when the room is clean (guard checks)
    "order_service": AUTONOMOUS,
    "notify_guest": AUTONOMOUS,
    "flag_anomaly": AUTONOMOUS,
    "defer_action": AUTONOMOUS,
    "request_inspection": HUMAN_APPROVAL,
    "dispatch_technician": HUMAN_APPROVAL,
    "block_room": HUMAN_APPROVAL,
    "reassign_room": HUMAN_APPROVAL,
    "request_human_approval": HUMAN_APPROVAL,
}

COMFORT_MIN_C, COMFORT_MAX_C = 20.0, 23.0

# Simulated outcome and verification text per action (always labelled as simulated / proposed).
OUTCOME = {
    "schedule_cleaning": "Simulated: cleaning is scheduled; the room becomes clean only when housekeeping_state says so.",
    "adjust_room_setpoint": "Simulated: setpoint change sent to the room controller; the effect on temperature is not yet measured.",
    "request_inspection": "Proposed: a person inspects and confirms or rules out the suspected issue.",
    "dispatch_technician": "Proposed: a technician is sent; nothing is repaired until a work order is completed.",
    "release_room": "Simulated: room released for the next guest because it is clean.",
    "flag_anomaly": "Anomaly recorded for review; no physical action taken.",
    "request_human_approval": "A human decides; no action is taken before approval.",
    "defer_action": "No action now; keep monitoring.",
    "order_service": "Simulated: service order created; not confirmed by the provider.",
    "optimize_energy": "Simulated: schedule shift proposed; savings are an estimate, not a measurement.",
}
VERIFY = {
    "schedule_cleaning": "Release only after housekeeping_state=clean; escalate if not clean before the deadline.",
    "adjust_room_setpoint": "Check room temperature trend over the next hours; escalate if still below 20 C.",
    "request_inspection": "Compare consumption or readings against baseline after the inspection; escalate if worse.",
    "dispatch_technician": "Confirm work-order completion, then check the signal returns to baseline.",
    "release_room": "Confirm housekeeping_state stays clean until check-in.",
    "flag_anomaly": "Re-evaluate on the next data; escalate if the anomaly persists or grows.",
    "request_human_approval": "Wait for the approver; re-escalate if no answer before the deadline.",
    "defer_action": "Re-check at the next evaluation step.",
    "order_service": "Check the logistics signal (e.g. fill level) stays below its limit until the service happens.",
    "optimize_energy": "Check comfort stays in band while the schedule is shifted.",
}
