"""DECIDE & ACT: UNDERSTAND result + evidence packet -> contract-shaped decision records.

    understanding = llm.report_incident(packet).incident        # stage 2: UNDERSTAND
    decisions = decide(understanding, packet)                    # stage 3: DECIDE & ACT
    for d in decisions: d.record  # one decisions.jsonl line;  d.notes  # what the safety guard changed

Nothing is executed here: `record` is the decision log entry, effects are labelled simulated/proposed, and
consent is only marked as needed. The mock executor (not written yet) would consume these records.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .guard import guard
from .playbooks import propose
from .vocabulary import OUTCOME, VERIFY


@dataclass
class Decision:
    record: Dict[str, Any]                       # DECISION_CONTRACT.md object
    notes: List[str] = field(default_factory=list)  # safety-guard interventions (empty = untouched)
    proposed_action: str = ""                    # what the playbook proposed before the guard


def packet_time(packet: Dict[str, Any]) -> str:
    t = packet.get("time") or (packet.get("window") or "").split("..")[-1]
    if not t:
        raise ValueError("packet needs 'time' or 'window' so the decision time is known (as-of rule)")
    t = str(t)
    return t if t.count(":") >= 2 else t.replace("Z", ":00Z")


def _evidence(u: Dict[str, Any], packet: Dict[str, Any]) -> List[str]:
    """Evidence references built from the packet itself. Field names the LLM cites that are not in the packet
    are dropped (it cannot invent evidence)."""
    f = packet.get("features", {}) or {}
    where = f"{packet.get('detector', 'packet')}:{packet.get('asset', '?')}:{packet.get('window') or packet.get('time')}"
    out = [f"{where}:{k}={f[k]}" for k in u.get("evidence_used", []) if k in f]
    for k in ("quality_flags", "work_orders", "bms_alarms"):
        if k in u.get("evidence_used", []) and packet.get(k):
            out.append(f"{where}:{k}={packet[k]}")
    return out


def decide(u: Dict[str, Any], packet: Dict[str, Any], episode_id: Optional[str] = None,
           guest_request_ids: Optional[List[str]] = None) -> List[Decision]:
    ts = packet_time(packet)
    asset = packet.get("asset", "BUILDING")
    decisions: List[Decision] = []
    first_id: Optional[str] = None
    for p in propose(u, packet):
        g = guard(p, u, packet)
        if g.action != p.action and g.action == "schedule_cleaning" and g.priority == "low":
            g.priority = "medium"  # a guarded fix still has to happen
        reason = u["reason"].strip()
        if g.notes:
            reason += " Safety guard: " + "; ".join(g.notes) + "."
        eid = "EVT-" + hashlib.sha1(f"{asset}|{ts}|{u['issue_type']}|{g.action}".encode()).hexdigest()[:10]
        outcome = OUTCOME.get(g.action, "")
        if g.action == "adjust_room_setpoint" and "target_setpoint_c" in g.params:
            outcome = (f"Simulated: setpoint {g.params.get('from_setpoint_c')} -> {g.params['target_setpoint_c']} C; "
                       "temperature response not yet measured.")
        rec: Dict[str, Any] = {
            "event_id": eid, "timestamp_utc": ts,
            "affected_assets": [asset] + [a for a in packet.get("affected_assets", []) if a != asset],
            "issue_type": u["issue_type"], "action": g.action, "priority": g.priority, "reason": reason,
            "requires_human_approval": g.requires_human_approval, "requires_guest_consent": g.requires_guest_consent,
            "autonomy": g.autonomy, "evidence": _evidence(u, packet), "expected_outcome": outcome,
            "verification_plan": VERIFY.get(g.action, ""), "confidence": round(float(u["confidence"]), 2),
        }
        if episode_id:
            rec["episode_id"] = episode_id
        if guest_request_ids:
            rec["guest_request_ids"] = list(guest_request_ids)
        if first_id:
            rec["dependencies"] = [first_id]
        first_id = first_id or eid
        decisions.append(Decision(rec, g.notes, p.action))
    return decisions
