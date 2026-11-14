"""Episode builder tests: merging, gap tolerance, deterministic ids, escalation cap."""
import unittest

import pandas as pd

from plato.understand.candidate import WATER, Candidate
from plato.understand.episodes import MAX_FOLLOWUPS, build_episodes


def cand(minutes, sev="medium", dur=1.0, asset="R1", detector=WATER, start_min=None):
    t = pd.Timestamp("2026-01-05T00:00:00Z") + pd.Timedelta(minutes=minutes)
    start = t if start_min is None else pd.Timestamp("2026-01-05T00:00:00Z") + pd.Timedelta(minutes=start_min)
    return Candidate(detector=detector, asset=asset, t=t, severity_hint=sev, start=start,
                     features={"duration_h": dur})


class TestEpisodes(unittest.TestCase):
    def test_close_candidates_merge_into_one_episode(self):
        # three candidates 30 min apart (< 3 h water gap) -> one opener, no escalation (same severity)
        cs = [cand(0, start_min=0), cand(30, start_min=0), cand(60, start_min=0)]
        em = build_episodes(cs)
        self.assertEqual(len(em), 1)
        self.assertFalse(em[0].is_followup)
        self.assertEqual(len({e.episode_id for e in em}), 1)

    def test_a_long_gap_starts_a_new_episode(self):
        cs = [cand(0, start_min=0), cand(60 * 5, start_min=60 * 5)]  # 5 h apart > 3 h
        em = build_episodes(cs)
        self.assertEqual(len(em), 2)
        self.assertNotEqual(em[0].episode_id, em[1].episode_id)
        self.assertFalse(em[1].is_followup)

    def test_episode_id_is_deterministic(self):
        cs = [cand(0, start_min=0)]
        self.assertEqual(build_episodes(cs)[0].episode_id, build_episodes(cs)[0].episode_id)

    def test_severity_rise_emits_one_followup(self):
        cs = [cand(0, "medium", start_min=0), cand(30, "high", start_min=0)]
        em = build_episodes(cs)
        self.assertEqual([e.is_followup for e in em], [False, True])

    def test_followups_are_capped(self):
        cs = [cand(0, "low", start_min=0)] + [cand(30 * i, "high", start_min=0) for i in range(1, 6)]
        em = build_episodes(cs)
        followups = [e for e in em if e.is_followup]
        self.assertLessEqual(len(followups), MAX_FOLLOWUPS)

    def test_persistence_escalation_emits_followup_without_severity_change(self):
        # same severity throughout but duration crosses the water persistence limit (12 h)
        cs = [cand(0, "medium", dur=1.0, start_min=0), cand(60 * 13, "medium", dur=13.0, start_min=0)]
        # 13 h gap would normally split; keep them within gap by adding a bridge candidate
        cs = [cand(0, "medium", dur=1.0, start_min=0),
              cand(120, "medium", dur=2.0, start_min=0),
              cand(150, "medium", dur=13.0, start_min=0)]
        em = build_episodes(cs)
        self.assertTrue(any(e.is_followup for e in em))

    def test_different_assets_are_different_episodes(self):
        cs = [cand(0, asset="R1", start_min=0), cand(0, asset="R2", start_min=0)]
        em = build_episodes(cs)
        self.assertEqual(len({e.episode_id for e in em}), 2)


if __name__ == "__main__":
    unittest.main()
