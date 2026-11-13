"""Offline tests for DECIDE & ACT: playbooks propose, the guard enforces the rules. No network."""
import sys
import unittest
from unittest import mock

from plato.decide import decide, guard, propose
from plato.decide.playbooks import Proposal

REQUIRED = {"event_id", "timestamp_utc", "affected_assets", "issue_type", "action", "priority", "reason",
            "requires_human_approval"}


def U(issue_type="water_leak", cause="equipment_fault", severity="high", confidence=0.85,
      reason="Because the evidence says so.", used=("flow_z",)):
    return {"issue_type": issue_type, "likely_cause": cause, "severity": severity, "confidence": confidence,
            "reason": reason, "evidence_used": list(used)}


def P(detector="water_baseline", asset="R104", time="2026-01-10T23:00Z", **features):
    return {"detector": detector, "asset": asset, "time": time, "features": features, "quality_flags": []}


class TestDecisionRecord(unittest.TestCase):
    def test_record_has_all_required_contract_fields(self):
        (d,) = decide(U(), P(occupied_share=0.0, flow_z=9.0))
        self.assertTrue(REQUIRED <= set(d.record), REQUIRED - set(d.record))
        self.assertEqual(d.record["timestamp_utc"], "2026-01-10T23:00:00Z")
        self.assertEqual(d.record["affected_assets"], ["R104"])

    def test_event_id_is_deterministic(self):
        a = decide(U(), P(occupied_share=0.0))[0].record["event_id"]
        b = decide(U(), P(occupied_share=0.0))[0].record["event_id"]
        self.assertEqual(a, b)

    def test_time_is_required(self):
        with self.assertRaises(ValueError):
            decide(U(), {"asset": "R1", "features": {}})

    def test_evidence_only_from_packet_fields(self):
        (d,) = decide(U(used=("flow_z", "invented_field")), P(flow_z=9.1, occupied_share=0.0))
        ev = " ".join(d.record["evidence"])
        self.assertIn("flow_z=9.1", ev)
        self.assertNotIn("invented", ev)

    def test_effects_are_labelled_not_claimed(self):
        (d,) = decide(U(issue_type="room_turnaround", cause="staffing", severity="medium"),
                      P("room_turnaround", "R302", housekeeping_state="dirty"))
        self.assertTrue(d.record["expected_outcome"].startswith(("Simulated", "Proposed")))


class TestPlaybooks(unittest.TestCase):
    def test_false_alarm_makes_no_decision(self):
        self.assertEqual(decide(U("no_incident", "guest_usage", "low", 0.7), P(occupied_share=1.0)), [])

    def test_confident_leak_vacant_room_needs_approval_not_consent(self):
        (d,) = decide(U(), P(occupied_share=0.0))
        r = d.record
        self.assertEqual((r["action"], r["autonomy"]), ("dispatch_technician", "human_approval"))
        self.assertTrue(r["requires_human_approval"])
        self.assertFalse(r["requires_guest_consent"])

    def test_leak_in_occupied_room_needs_approval_and_consent(self):
        (d,) = decide(U(), P(occupied_share=1.0))
        r = d.record
        self.assertEqual(r["autonomy"], "approval_and_consent")
        self.assertTrue(r["requires_human_approval"] and r["requires_guest_consent"])

    def test_medium_confidence_leak_requests_inspection(self):
        (d,) = decide(U(confidence=0.6), P(occupied_share=0.0))
        self.assertEqual(d.record["action"], "request_inspection")

    def test_weak_leak_evidence_only_flags(self):
        (d,) = decide(U(confidence=0.3), P(occupied_share=0.0))
        self.assertEqual((d.record["action"], d.record["autonomy"]), ("flag_anomaly", "autonomous"))

    def test_cold_room_with_plant_fault_gives_setpoint_then_technician(self):
        u = U("heating_comfort", "equipment_fault", "high", 0.8)
        ds = decide(u, P("comfort", "R210", temp_c=18.6, setpoint_c=21.0, occupied=True), episode_id="EP-1")
        self.assertEqual([d.record["action"] for d in ds], ["adjust_room_setpoint", "dispatch_technician"])
        self.assertEqual(ds[0].record["autonomy"], "autonomous")
        self.assertEqual(ds[1].record["autonomy"], "human_approval")
        self.assertEqual(ds[1].record["dependencies"], [ds[0].record["event_id"]])
        self.assertTrue(all(d.record["episode_id"] == "EP-1" for d in ds))
        self.assertIn("21.0 -> 22.0", ds[0].record["expected_outcome"])


class TestGuard(unittest.TestCase):
    def test_never_release_an_uncleaned_room(self):
        g = guard(Proposal("release_room", "low"), U("room_turnaround", "unknown", "low"),
                  P("room_turnaround", "R3", housekeeping_state="dirty"))
        self.assertEqual(g.action, "schedule_cleaning")
        self.assertTrue(g.notes)

    def test_release_allowed_when_clean(self):
        g = guard(Proposal("release_room", "low"), U("room_turnaround"), P("room_turnaround", housekeeping_state="clean"))
        self.assertEqual(g.action, "release_room")

    def test_guard_overrides_an_unsafe_playbook_in_the_record(self):
        # the record must show the guarded action, and say why
        module = sys.modules["plato.decide.decide"]  # the package re-exports the function under the same name
        with mock.patch.object(module, "propose", lambda u, p: [Proposal("release_room", "low")]):
            (d,) = decide(U("room_turnaround", "unknown", "low", 0.6),
                          P("room_turnaround", "R3", housekeeping_state="dirty"))
        self.assertEqual(d.record["action"], "schedule_cleaning")
        self.assertEqual(d.proposed_action, "release_room")
        self.assertIn("Safety guard", d.record["reason"])
        self.assertNotEqual(d.record["priority"], "low")

    def test_robot_cannot_clean_bathrooms(self):
        g = guard(Proposal("dispatch_robot", "medium"), U(), P(bathroom_cleaning_needed=True))
        self.assertEqual(g.action, "schedule_cleaning")

    def test_setpoint_clamped_in_occupied_room(self):
        g = guard(Proposal("adjust_room_setpoint", "high", params={"target_setpoint_c": 26.0}), U(),
                  P(occupied=True))
        self.assertEqual(g.params["target_setpoint_c"], 23.0)
        self.assertEqual(g.autonomy, "autonomous")

    def test_no_energy_saving_at_the_cost_of_comfort(self):
        g = guard(Proposal("optimize_energy", "low"), U("energy_optimization"), P(comfort_at_risk=True))
        self.assertEqual(g.action, "defer_action")

    def test_low_confidence_costly_action_goes_to_a_human(self):
        g = guard(Proposal("dispatch_technician", "high"), U(confidence=0.3), P(occupied_share=0.0))
        self.assertEqual(g.action, "request_human_approval")
        self.assertTrue(g.requires_human_approval)

    def test_unknown_action_becomes_human_approval(self):
        g = guard(Proposal("shut_off_building", "high"), U(), P())
        self.assertEqual(g.action, "request_human_approval")

    def test_water_shutoff_needs_approval_and_consent(self):
        g = guard(Proposal("request_human_approval", "critical"), U(), P(water_shutoff=True))
        self.assertTrue(g.requires_human_approval and g.requires_guest_consent)

    def test_consent_is_never_recorded_as_granted(self):
        (d,) = decide(U(), P(occupied_share=1.0))
        self.assertFalse(any("granted" in k for k in d.record))


class TestPlaybookOnlyProposes(unittest.TestCase):
    def test_propose_returns_no_flags(self):
        for p in propose(U(), P(occupied_share=0.0)):
            self.assertFalse(hasattr(p, "requires_human_approval"))


if __name__ == "__main__":
    unittest.main()
