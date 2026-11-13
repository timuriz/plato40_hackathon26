"""Tests for the observe stage.  Run:  python3 -m unittest discover -s tests -v"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"  # quickstart dataset
sys.path.insert(0, str(ROOT))

from plato.observe import observe  # noqa: E402
from plato.observe.quality import (  # noqa: E402
    Q_ESTIMATED, Q_FROZEN, Q_JITTER, Q_MISSING, Q_OK, Q_RESET, Q_SPAN, Q_SUSPECT, Rule, clean_matrix,
    run_position,
)
from plato.observe.timeutil import at_local  # noqa: E402
from plato.observe.water import process_cumulative  # noqa: E402

N = np.nan


def eq_nan(a, b):
    return np.array_equal(np.asarray(a, dtype=float), np.asarray(b, dtype=float), equal_nan=True)


class TestRunPosition(unittest.TestCase):
    def test_positions(self):
        m = np.array([[0], [1], [1], [0], [1], [1], [1]], dtype=bool)
        self.assertEqual(run_position(m)[:, 0].tolist(), [0, 1, 2, 0, 1, 2, 3])


class TestCleanMatrix(unittest.TestCase):
    def test_missing_is_never_zero(self):
        v, q, _ = clean_matrix(np.full((6, 1), N), Rule(lo=0, hi=10))
        self.assertTrue(np.isnan(v).all())
        self.assertTrue((q == Q_MISSING).all())

    def test_implausible_is_set_to_unknown(self):
        v, q, _ = clean_matrix(np.array([[20.0], [85.0], [21.0]]), Rule(lo=5, hi=40))
        self.assertTrue(np.isnan(v[1, 0]))
        self.assertEqual(q[:, 0].tolist(), [Q_OK, Q_SUSPECT, Q_OK])

    def test_locf_has_limit_and_labels_estimated(self):
        raw = np.array([[20.0], [N], [N], [N], [N], [N], [21.0]])
        v, q, _ = clean_matrix(raw, Rule(locf_limit=3))
        self.assertEqual(q[:, 0].tolist(), [Q_OK, Q_ESTIMATED, Q_ESTIMATED, Q_ESTIMATED, Q_MISSING, Q_MISSING, Q_OK])
        self.assertTrue(np.isnan(v[4, 0]))
        self.assertEqual(v[1, 0], 20.0)

    def test_no_fill_after_a_bad_reading(self):
        raw = np.array([[20.0], [85.0], [N], [N], [21.0]])
        v, q, _ = clean_matrix(raw, Rule(lo=5, hi=40, locf_limit=4))
        self.assertTrue((q[2:4, 0] == Q_MISSING).all())  # last reading before gap was suspect

    def test_frozen_flags_only_after_threshold(self):
        raw = np.array([[20.0]] + [[21.0]] * 6)  # 21.0 repeated 6x -> 5 repeats
        _, q, _ = clean_matrix(raw, Rule(frozen_repeats=3))
        self.assertEqual(q[:, 0].tolist(), [Q_OK, Q_OK, Q_OK, Q_OK, Q_FROZEN, Q_FROZEN, Q_FROZEN])

    def test_jump_flags_single_spike_only(self):
        raw = np.array([[20.0], [32.0], [21.0], [21.5]])
        _, q, _ = clean_matrix(raw, Rule(max_jump=8.0))
        self.assertEqual(q[:, 0].tolist(), [Q_OK, Q_SUSPECT, Q_OK, Q_OK])

    def test_cleaning_is_causal(self):
        """Cleaning the first k rows must equal the first k rows of cleaning everything."""
        rng = np.random.default_rng(7)
        raw = 20 + rng.normal(0, 0.3, size=(300, 5)).round(1)
        raw[rng.random(raw.shape) < 0.08] = N
        raw[40:60, 1] = 21.0  # frozen
        raw[100:112, 2] = N  # long gap
        raw[150, 3] = 85.0  # implausible
        raw[200, 4] = 33.0  # jump
        rule = Rule(lo=5, hi=40, frozen_repeats=8, max_jump=8.0, locf_limit=4)
        v_full, q_full, _ = clean_matrix(raw, rule)
        for k in (30, 55, 101, 150, 151, 201, 299):
            v_k, q_k, _ = clean_matrix(raw[:k], rule)
            self.assertTrue(eq_nan(v_full[:k], v_k), f"values differ at k={k}")
            self.assertTrue(np.array_equal(q_full[:k], q_k), f"quality differs at k={k}")


class TestCumulativeMeter(unittest.TestCase):
    def test_blank_jitter_reset_span(self):
        idx = pd.date_range("2026-01-01", periods=9, freq="15min", tz="UTC")
        #            0      1     2    3     4     5     6    7     8
        cum = [100.0, 110.0, N, N, 130.0, 128.0, 3.0, 5.0, 5.0]
        r = process_cumulative(pd.DataFrame({"R1": cum}, index=idx))
        q = r.quality["R1"].tolist()
        d = r.delta_l["R1"].tolist()
        self.assertEqual(q[0], Q_MISSING)  # first reading, no baseline
        self.assertEqual(q[1], Q_OK)
        self.assertEqual(d[1], 10.0)
        self.assertEqual(q[2:4], [Q_MISSING, Q_MISSING])
        self.assertTrue(np.isnan(d[2]) and np.isnan(d[3]))  # blank != 0
        self.assertEqual(q[4], Q_SPAN)  # total over the gap, 3 steps
        self.assertEqual(d[4], 20.0)
        self.assertEqual(r.span_steps["R1"].iloc[4], 3)
        self.assertAlmostEqual(r.flow_lph["R1"].iloc[4], 20.0 / (3 * 0.25))
        self.assertEqual(q[5], Q_JITTER)  # -2 L: meter noise
        self.assertEqual(d[5], 0.0)
        self.assertEqual(q[6], Q_RESET)  # 128 -> 3: counter restarted
        self.assertTrue(np.isnan(d[6]))
        self.assertEqual(d[7], 2.0)  # new baseline is the post-reset reading
        self.assertTrue(bool(r.post_exchange["R1"].iloc[7]))


class TestTime(unittest.TestCase):
    def test_dst(self):
        self.assertEqual(at_local("2026-03-28", "15:00"), pd.Timestamp("2026-03-28T14:00:00Z"))  # CET
        self.assertEqual(at_local("2026-03-29", "15:00"), pd.Timestamp("2026-03-29T13:00:00Z"))  # CEST
        self.assertEqual(at_local("2026-01-10", "07:00"), pd.Timestamp("2026-01-10T06:00:00Z"))


@unittest.skipUnless((DATA / "building.json").exists() and (DATA / "room_telemetry.csv").exists(),
                     "quickstart dataset not present")
class TestQuickstart(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.obs = observe(DATA)

    def test_no_value_is_fabricated(self):
        """Wherever the quality says unknown/bad, the value must be NaN, never a number."""
        bad = (Q_MISSING, Q_SUSPECT, Q_FROZEN, Q_RESET)
        for var in ("temperature_c", "humidity_pct", "co2_ppm", "setpoint_c", "presence", "window_open"):
            v = self.obs.rooms.values[var].to_numpy()
            q = self.obs.rooms.quality[var].to_numpy()
            self.assertTrue(np.isnan(v[np.isin(q, bad)]).all(), var)
        eq = self.obs.equipment.wide
        self.assertTrue(np.isnan(eq.values.to_numpy()[np.isin(eq.quality.to_numpy(), bad)]).all())

    def test_known_findings(self):
        o = self.obs
        issues = o.issues
        self.assertEqual(int(issues[issues.issue == "duplicate_rows"].steps.sum()), 2)
        imp = issues[issues.issue == "implausible_value"]
        self.assertTrue(((imp.asset_id == "R106") & (imp.detail.str.contains("85.0"))).any())
        self.assertTrue((issues[issues.issue == "frozen_sensor"].asset_id == "R205").any())
        self.assertEqual(set(issues[issues.issue == "gateway_outage"].asset_id), {"GW_F3"})
        resets = o.water.resets
        self.assertEqual(resets.asset_id.tolist(), ["R403"])
        self.assertEqual(resets.confirmed_by.tolist(), ["WO00004"])

    def test_filter_dp_gap_is_not_filled(self):
        g = self.obs.gaps
        row = g[(g.asset_id == "AHU1") & (g.column == "filter_dp_pa")].iloc[0]
        self.assertEqual(row.steps, 404)
        self.assertEqual(row.filled_steps, 0)

    def test_housekeeping_blanks_are_derived_not_invented(self):
        q = self.obs.rooms.quality["housekeeping_state"]
        self.assertEqual(int((q == 5).to_numpy().sum()), 210)  # Q_DERIVED, one per blank
        self.assertGreater(self.obs.meta["room_checks"]["housekeeping_event_agreement"], 0.99)

    def test_asof_roster_respects_status_known_time(self):
        o = self.obs
        sick = pd.Timestamp("2026-01-08T05:15:00Z")  # CLEANER_1 sick, known 05:15
        def status(t):
            r = o.asof(t).roster()
            return r[r.shift_id == "SH000019"].status.tolist()
        self.assertEqual(status(sick - pd.Timedelta(minutes=15)), ["scheduled"])
        self.assertEqual(status(sick), ["sick"])

    def test_asof_forecast_tariff_and_series(self):
        o = self.obs
        t = pd.Timestamp("2026-01-08T12:00:00Z")
        fc = o.asof(t).forecast()
        self.assertTrue((fc.issued_utc <= t).all() and (fc.target_utc >= t).all())
        self.assertEqual(fc.issued_utc.nunique(), 1)
        tar = o.asof(t).tariff()
        self.assertTrue((tar.published_utc <= t).all())
        self.assertLessEqual(o.asof(t).series("room.temperature_c").index.max(), t)
        self.assertLessEqual(o.asof(t).events().timestamp_utc.max(), t)
        self.assertTrue((o.asof(t).work_orders().created_utc <= t).all())


if __name__ == "__main__":
    unittest.main()
