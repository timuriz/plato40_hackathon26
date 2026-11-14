"""Detector unit tests: causal helpers, then the water detector on hand-made series."""
import unittest

import numpy as np

from plato.understand.causal import (
    expanding_robust_baseline, find_runs, pooled_warmup, rolling_min_valid, run_length_so_far,
)
from plato.understand.detectors.water import detect_water
from tests.fake_obs import make_water_obs, truncate_water_obs


class TestCausalHelpers(unittest.TestCase):
    def test_rolling_min_ignores_nan_and_needs_min_valid(self):
        x = np.array([5.0, np.nan, 3.0, np.nan, np.nan])
        out = rolling_min_valid(x, window=3, min_valid=2)
        self.assertTrue(np.isnan(out[0]))          # only 1 valid
        self.assertEqual(out[2], 3.0)              # 5,3 -> min 3
        self.assertTrue(np.isnan(out[4]))          # window 2..4 has only 1 valid (3.0)

    def test_rolling_min_is_causal_under_truncation(self):
        rng = np.random.default_rng(0)
        x = rng.random(60)
        full = rolling_min_valid(x, 8, 4)
        for k in (20, 35, 50):
            trunc = rolling_min_valid(x[:k], 8, 4)
            np.testing.assert_allclose(trunc, full[:k], equal_nan=True)

    def test_baseline_excludes_current_row_and_is_causal(self):
        vals = np.array([0.0, 0.0, 0.0, 10.0, 10.0, 10.0])
        elig = np.ones(6, dtype=bool)
        med, _ = expanding_robust_baseline(vals, elig, min_count=2, warmup=(0.0, 1.0))
        # row 3 (first 10.0) must still see median 0 from the three earlier zeros
        self.assertEqual(med[3], 0.0)
        for k in (3, 4, 5):
            tm, _ = expanding_robust_baseline(vals[:k], elig[:k], 2, (0.0, 1.0))
            np.testing.assert_allclose(tm, med[:k], equal_nan=True)

    def test_run_length_and_find_runs(self):
        m = np.array([0, 1, 1, 0, 1, 1, 1], dtype=bool)
        np.testing.assert_array_equal(run_length_so_far(m), [0, 1, 2, 0, 1, 2, 3])
        self.assertEqual(find_runs(m), [(1, 3), (4, 7)])

    def test_pooled_warmup_handles_empty(self):
        self.assertIsNone(pooled_warmup(np.array([np.nan, np.nan]), np.ones(2, bool)))


def const(n, v):
    return np.full(n, float(v))


class TestWaterDetector(unittest.TestCase):
    def setUp(self):
        self.n = 96  # 24 h
        half = self.n // 2

        # R1: vacant, dry for 12 h then a steady 5 L/15min leak -> must flag
        leak = np.concatenate([const(half, 0.0), const(half, 5.0)])
        # R2: occupied guest, dry with four shower spikes -> rolling MIN stays 0 -> must NOT flag
        shower = const(self.n, 0.0)
        shower[[50, 51, 70, 71]] = 30.0
        # R3: vacant, always dry -> baseline room, never flags
        dry = const(self.n, 0.0)
        # R4: vacant+dry for 12 h, then OCCUPIED with a continuous 3 L/15min toilet valve (+spikes) -> flag
        valve = np.concatenate([const(half, 0.0), const(half, 3.0)])
        valve[[60, 80]] = 25.0

        self.flows = {"R1": leak, "R2": shower, "R3": dry, "R4": valve}
        occ_half = np.concatenate([np.zeros(half, bool), np.ones(half, bool)])
        self.occ = {"R1": np.zeros(self.n, bool), "R2": np.ones(self.n, bool),
                    "R3": np.zeros(self.n, bool), "R4": occ_half}

    def _flagged(self, obs):
        return {c.asset for c in detect_water(obs)}

    def test_steady_leak_in_vacant_room_is_flagged(self):
        obs = make_water_obs(self.flows, self.occ)
        self.assertIn("R1", self._flagged(obs))

    def test_guest_shower_is_not_flagged(self):
        obs = make_water_obs(self.flows, self.occ)
        self.assertNotIn("R2", self._flagged(obs))

    def test_dry_room_is_not_flagged(self):
        obs = make_water_obs(self.flows, self.occ)
        self.assertNotIn("R3", self._flagged(obs))

    def test_continuous_flow_while_occupied_is_flagged(self):
        obs = make_water_obs(self.flows, self.occ)
        self.assertIn("R4", self._flagged(obs))

    def test_meter_reset_window_is_not_flagged(self):
        # mark the leak room as within the post-exchange window -> its flow is ignored
        post = {r: np.zeros(self.n, bool) for r in self.flows}
        post["R1"] = np.ones(self.n, bool)
        obs = make_water_obs(self.flows, self.occ, post=post)
        self.assertNotIn("R1", self._flagged(obs))

    def test_detector_is_causal_under_truncation(self):
        obs = make_water_obs(self.flows, self.occ)
        full = detect_water(obs)
        for k in (60, 80, 96):
            cutoff = obs.grid[k - 1]
            trunc = {(c.asset, c.t) for c in detect_water(truncate_water_obs(obs, k))}
            early = {(c.asset, c.t) for c in full if c.t <= cutoff}
            self.assertEqual(trunc, early, f"candidates up to {cutoff} changed when truncating at k={k}")

    def test_output_is_deterministic(self):
        obs = make_water_obs(self.flows, self.occ)
        a = [(c.asset, c.t, c.features["flow_z"]) for c in detect_water(obs)]
        b = [(c.asset, c.t, c.features["flow_z"]) for c in detect_water(obs)]
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
