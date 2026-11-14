"""End-to-end pipeline test on the bundled quickstart dataset, offline (rule-based understand, no network).

Skips automatically if the dataset folder is absent, so the suite still runs elsewhere.
"""
import json
import unittest
from pathlib import Path

from plato.decide.vocabulary import ACTIONS, PRIORITIES
from plato.pipeline import run_pipeline

DATA = Path(__file__).resolve().parents[1] / "data"
REQUIRED = ["event_id", "timestamp_utc", "affected_assets", "issue_type", "action", "priority", "reason",
            "requires_human_approval"]


@unittest.skipUnless((DATA / "building.json").exists(), "quickstart data not present")
class TestPipelineOnQuickstart(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = run_pipeline(str(DATA), out_path=None, use_llm=False)
        cls.rows = cls.result.decisions

    def test_produces_decisions(self):
        self.assertGreater(len(self.rows), 0)

    def test_every_record_matches_the_contract(self):
        for r in self.rows:
            for k in REQUIRED:
                self.assertIn(k, r)
            self.assertIsInstance(r["affected_assets"], list)
            self.assertTrue(r["affected_assets"])
            self.assertIn(r["action"], ACTIONS)
            self.assertIn(r["priority"], PRIORITIES)
            self.assertIsInstance(r["requires_human_approval"], bool)
            # the decision timestamp must be a proper ISO-UTC instant
            self.assertRegex(r["timestamp_utc"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_event_ids_are_unique(self):
        ids = [r["event_id"] for r in self.rows]
        self.assertEqual(len(ids), len(set(ids)))

    def test_records_are_time_sorted(self):
        ts = [r["timestamp_utc"] for r in self.rows]
        self.assertEqual(ts, sorted(ts))

    def test_run_is_reproducible(self):
        again = run_pipeline(str(DATA), out_path=None, use_llm=False).decisions
        self.assertEqual(json.dumps(self.rows, sort_keys=True), json.dumps(again, sort_keys=True))

    def test_known_leaks_are_detected(self):
        leaks = {a for r in self.rows if r["issue_type"] == "water_leak" for a in r["affected_assets"]}
        self.assertIn("R104", leaks)  # burst hose before WO00003
        self.assertIn("R305", leaks)  # running toilet valve before WO00009

    def test_meter_exchange_is_data_quality_not_a_leak(self):
        r403 = [r for r in self.rows if "R403" in r["affected_assets"]]
        self.assertTrue(any(r["issue_type"] == "data_quality" for r in r403))
        self.assertFalse(any(r["issue_type"] == "water_leak" for r in r403))

    def test_no_decision_releases_an_uncleaned_room(self):
        # the guard guarantees this; assert it holds across the whole real run
        for r in self.rows:
            if r["action"] == "release_room":
                self.assertEqual(r["issue_type"], "room_turnaround")

    def test_approvals_and_consent_are_flagged_for_costly_actions(self):
        for r in self.rows:
            if r["action"] in ("dispatch_technician", "block_room"):
                self.assertTrue(r["requires_human_approval"], r["event_id"])


if __name__ == "__main__":
    unittest.main()
