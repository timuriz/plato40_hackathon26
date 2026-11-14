"""Guest-request decisions: one per request, fixed draft, no invented delivery or consent."""
import unittest

import pandas as pd

from plato.decide import decide
from plato.decide.guard import guard
from plato.decide.playbooks import propose
from plato.llm.fallback import rule_based_incident
from plato.understand.detectors.guest import detect_guest_requests, draft_message
from plato.understand.episodes import build_episodes
from tests.fake_obs import grid


def _obs(rows, occupied):
    g = grid(40)
    rooms = list(occupied)
    occ = pd.DataFrame({r: occupied[r] for r in rooms}, index=g)
    return type("Obs", (), {
        "grid": g,
        "guest_requests": pd.DataFrame(rows),
        "rooms": type("Rooms", (), {"occupied": occ})(),
    })()


class TestGuestRequests(unittest.TestCase):
    def test_draft_uses_the_guest_language_and_falls_back_to_english(self):
        self.assertIn("Handtücher", draft_message("extra_towels", "de"))
        self.assertIn("towels", draft_message("extra_towels", "xx").lower())

    def test_skips_requests_already_handled_and_does_not_release_a_late_checkout(self):
        obs = _obs([
            {"request_id": "GR1", "timestamp_utc": "2026-01-05T01:00:00Z", "room_id": "R101",
             "language": "en", "request_type": "early_checkin", "priority": "high",
             "response_due_utc": "2026-01-05T03:00:00Z", "requires_guest_consent": "not_required"},
            {"request_id": "GR2", "timestamp_utc": "2026-01-05T01:00:00Z", "room_id": "R101",
             "language": "de", "request_type": "extra_towels", "priority": "low",
             "response_due_utc": "2026-01-05T06:00:00Z", "requires_guest_consent": "not_required"},
            {"request_id": "GR3", "timestamp_utc": "2026-01-05T02:00:00Z", "room_id": "R102",
             "language": "en", "request_type": "late_checkout", "priority": "medium",
             "response_due_utc": "2026-01-05T04:00:00Z", "requires_guest_consent": "not_required"},
        ], {"R101": [True] * 40, "R102": [False] * 40})
        cands = detect_guest_requests(obs)
        ids = {c.features["request_id"] for c in cands}
        self.assertNotIn("GR1", ids)
        towels = [c for c in cands if c.features["request_id"] == "GR2" and not c.features["overdue"]]
        self.assertEqual(len(towels), 1)
        understanding = rule_based_incident(towels[0].packet())
        decisions = decide(understanding, towels[0].packet(), guest_request_ids=["GR2"])
        self.assertEqual([d.record["action"] for d in decisions], ["notify_guest"])
        self.assertIn("GR2", decisions[0].record["guest_request_ids"])
        self.assertIn("not sent", decisions[0].record["reason"])
        self.assertIn("Handtücher", decisions[0].record["reason"])
        self.assertFalse(decisions[0].record["requires_guest_consent"])

        late = next(c for c in cands if c.features["request_id"] == "GR3" and not c.features["overdue"])
        actions = [p.action for p in propose(rule_based_incident(late.packet()), late.packet())]
        self.assertEqual(actions, ["notify_guest"])
        self.assertNotIn("release_room", actions)

    def test_occupied_fault_needs_approval_and_consent_and_a_missed_deadline_raises_priority(self):
        obs = _obs([
            {"request_id": "GR4", "timestamp_utc": "2026-01-05T01:00:00Z", "room_id": "R201",
             "language": "en", "request_type": "water_issue", "priority": "low",
             "response_due_utc": "2026-01-05T03:00:00Z", "requires_guest_consent": "required"},
        ], {"R201": [True] * 40})
        cands = detect_guest_requests(obs)
        self.assertEqual(len(cands), 2)
        opened, late = cands
        self.assertEqual(opened.severity_hint, "low")
        self.assertEqual(late.severity_hint, "high")
        self.assertTrue(late.features["overdue"])
        emissions = build_episodes(cands)
        self.assertEqual(len({e.episode_id for e in emissions}), 1)
        self.assertEqual(sum(e.is_followup for e in emissions), 1)
        understanding = rule_based_incident(opened.packet())
        proposal = propose(understanding, opened.packet())[0]
        guarded = guard(proposal, understanding, opened.packet())
        self.assertEqual(guarded.action, "request_inspection")
        self.assertTrue(guarded.requires_human_approval)
        self.assertTrue(guarded.requires_guest_consent)


if __name__ == "__main__":
    unittest.main()
