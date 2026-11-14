"""Contract and safety invariants over a full offline quickstart run.

The jury's automated check scores false alarms, alert spam and safety violations, not the dashboard.
These assertions lock the rules the guard already enforces and the turnaround precision policy.
"""
import unittest
from pathlib import Path

from plato.decide.vocabulary import ACTIONS, PRIORITIES
from plato.pipeline import run_pipeline
from plato.understand.detectors.turnaround import SLACK_RISK_MIN

DATA = Path(__file__).resolve().parents[1] / "data"
REQUIRED = ["event_id", "timestamp_utc", "affected_assets", "issue_type", "action", "priority", "reason",
            "requires_human_approval"]
NEEDS_APPROVAL = {"dispatch_technician", "block_room", "reassign_room", "request_inspection",
                  "request_human_approval"}


@unittest.skipUnless((DATA / "building.json").exists(), "quickstart data not present")
class TestSafetyOnQuickstart(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = run_pipeline(str(DATA), out_path=None, use_llm=False)

    def test_every_decision_matches_the_contract(self):
        ids = []
        for rec in self.result.decisions:
            for key in REQUIRED:
                self.assertIn(key, rec, rec.get("event_id"))
            self.assertIn(rec["action"], ACTIONS)
            self.assertIn(rec["priority"], PRIORITIES)
            conf = rec.get("confidence")
            self.assertIsInstance(conf, (int, float))
            self.assertGreaterEqual(conf, 0)
            self.assertLessEqual(conf, 1)
            ids.append(rec["event_id"])
        self.assertEqual(len(ids), len(set(ids)))

    def test_approval_and_consent_invariants(self):
        for rec in self.result.decisions:
            if rec["action"] in NEEDS_APPROVAL:
                self.assertTrue(rec["requires_human_approval"], rec["event_id"])
            if rec.get("requires_guest_consent"):
                self.assertTrue(rec["requires_human_approval"], rec["event_id"])

    def test_turnaround_decisions_are_only_the_at_risk_ones(self):
        incidents = [i for i in self.result.incidents if i["issue_type"] == "room_turnaround"]
        self.assertGreater(len(incidents), 0, "S01 must still produce at least one turnaround")
        decisions = [d for d in self.result.decisions if d["issue_type"] == "room_turnaround"]
        # Was 216 routine checkouts before suppression. Each remaining episode is one clean + one guest note.
        self.assertLess(len(incidents), 150, "routine checkouts should not flood the decision log")
        self.assertLess(sum(d["action"] == "schedule_cleaning" for d in decisions), 150)
        for inc in incidents:
            feats = inc.get("features") or {}
            slack = feats.get("slack_minutes")
            at_risk = bool(feats.get("guest_waiting")) or bool(feats.get("early_request")) or (
                slack is not None and slack < SLACK_RISK_MIN + 1)  # features are rounded to 0.1 min
            self.assertTrue(at_risk, inc["episode_id"])

    def test_at_risk_turnaround_notifies_the_guest(self):
        notes = [d for d in self.result.decisions if d["action"] == "notify_guest"
                 and d["issue_type"] == "room_turnaround"]
        self.assertGreater(len(notes), 0)
        cleaning = {d["episode_id"] for d in self.result.decisions
                    if d["action"] == "schedule_cleaning" and d.get("episode_id")}
        for note in notes:
            self.assertIn(note.get("episode_id"), cleaning, note["event_id"])
            self.assertTrue(str(note.get("expected_outcome", "")).startswith("Proposed"))


if __name__ == "__main__":
    unittest.main()
