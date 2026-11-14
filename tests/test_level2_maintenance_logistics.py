"""Level 2 tests: S04 maintenance (AHU1 filter) and the logistics detector, on hand-made fake observations.

Self-contained on purpose (own fake obs builders) so it does not depend on edits to tests/fake_obs.py.
Uses the real causal ``AsOf`` view for roster / work orders / events so as-of behaviour is the real one.
"""
import unittest
from types import SimpleNamespace

import numpy as np
import pandas as pd

from plato.decide import decide
from plato.llm.fallback import rule_based_incident
from plato.observe.asof import AsOf
from plato.understand.candidate import LOGISTICS, MAINTENANCE, MAINTENANCE_GAP
from plato.understand.detectors.logistics import detect_logistics
from plato.understand.detectors.maintenance import _roster_slot, detect_maintenance
from plato.understand.detectors import ALL_DETECTORS
from plato.understand.episodes import GAP_TOLERANCE, build_episodes

ROSTER_COLS = ["shift_id", "resource_id", "role", "start_utc", "end_utc", "status", "status_known_utc"]
WO_COLS = ["work_order_id", "created_utc", "asset_id", "type", "status", "scheduled_utc", "completed_utc",
           "assigned_to", "summary"]
EVENT_COLS = ["event_id", "timestamp_utc", "type", "asset_id", "resource_id"]
BUILDING_RAW = {
    "assets": [{"id": "AHU1", "nominal_airflow_m3h": 4200},
               {"id": "VEND1", "capacity": {"drinks": 100, "snacks": 60}}],
    "operations": {"technician_shift_local": "08:00-16:30 Mon-Fri"},
}
T0 = pd.Timestamp("2026-01-05T00:00:00Z")  # a Monday


def utc(s):
    return pd.Timestamp(s).tz_convert("UTC") if pd.Timestamp(s).tzinfo else pd.Timestamp(s, tz="UTC")


def _obs(grid, **kw):
    o = SimpleNamespace(grid=grid, building=SimpleNamespace(raw=BUILDING_RAW),
                        roster=pd.DataFrame(columns=ROSTER_COLS),
                        work_orders=pd.DataFrame(columns=WO_COLS),
                        events=pd.DataFrame(columns=EVENT_COLS), **kw)
    for c in ("roster", "work_orders", "events"):
        for col in ("start_utc", "end_utc", "status_known_utc", "created_utc", "scheduled_utc", "completed_utc",
                    "timestamp_utc"):
            if col in getattr(o, c).columns:
                getattr(o, c)[col] = pd.to_datetime(getattr(o, c)[col], utc=True)
    o.asof = lambda t: AsOf(o, utc(t))
    return o


# ------------------------------------------------------------------------------- AHU1 fake data
def roster(overrides=None, start="2026-01-05", days=21, tech="TECH_1"):
    """Weekday technician shifts 07:00-15:30 UTC (08:00-16:30 local). overrides: {'2026-01-15': (status, known)}"""
    overrides = overrides or {}
    rows = []
    for d in pd.date_range(start, periods=days, freq="D", tz="UTC"):
        if d.weekday() >= 5:
            continue
        key = d.strftime("%Y-%m-%d")
        status, known = overrides.get(key, ("scheduled", d - pd.Timedelta(days=14)))
        rows.append({"shift_id": f"SH{key}", "resource_id": tech, "role": "technician",
                     "start_utc": d + pd.Timedelta(hours=7), "end_utc": d + pd.Timedelta(hours=15, minutes=30),
                     "status": status, "status_known_utc": utc(known)})
    return pd.DataFrame(rows, columns=ROSTER_COLS)


def ahu_arrays(n, ramp_from=None, ramp_rate=0.002, ramp_cap=1.8, gap=None, flow_loss_from=None, seed=3):
    g = pd.date_range(T0, periods=n, freq="15min", tz="UTC")
    hour = g.hour
    day = (hour >= 5) & (hour < 22)
    air = np.where(day, 4200.0, 3000.0)
    rng = np.random.default_rng(seed)
    f = np.ones(n)
    if ramp_from is not None:
        f = np.where(np.arange(n) >= ramp_from, np.minimum(ramp_cap, 1 + (np.arange(n) - ramp_from) * ramp_rate), 1.0)
    dp = 107.0 * (air / 4200.0) ** 2 * f * (1 + rng.normal(0, 0.01, n))
    fan = 3.4 * (air / 4200.0) ** 3 * (1 + 0.15 * (f - 1)) * (1 + rng.normal(0, 0.01, n))
    raw_missing = np.zeros(n, dtype=bool)
    if gap is not None:
        dp[gap[0]:gap[1]] = np.nan
        raw_missing[gap[0]:gap[1]] = True
    if flow_loss_from is not None:
        air[flow_loss_from:] *= 0.88
        fan[flow_loss_from:] *= 1.5
    return g, dp, air, fan, raw_missing


def make_ahu_obs(g, dp, air, fan, raw_missing, **kw):
    vals = pd.DataFrame({"AHU1.filter_dp_pa": dp, "AHU1.airflow_m3h": air, "AHU1.fan_kw": fan}, index=g)
    miss = pd.DataFrame({"AHU1.filter_dp_pa": raw_missing, "AHU1.airflow_m3h": False, "AHU1.fan_kw": False}, index=g)
    eq = SimpleNamespace(wide=SimpleNamespace(values=vals, raw_missing=miss))
    return _obs(g, equipment=eq, **kw)


class TestMaintenanceDetector(unittest.TestCase):
    def test_clean_filter_raises_nothing(self):
        obs = make_ahu_obs(*ahu_arrays(960))
        self.assertEqual(detect_maintenance(obs), [])

    def test_loading_filter_is_detected_from_valid_data(self):
        obs = make_ahu_obs(*ahu_arrays(960, ramp_from=672))
        obs.roster = roster()
        obs.roster["status_known_utc"] = pd.to_datetime(obs.roster["status_known_utc"], utc=True)
        cands = [c for c in detect_maintenance(obs) if c.detector == MAINTENANCE]
        self.assertTrue(cands)
        first = cands[0]
        self.assertGreaterEqual(first.t, T0 + pd.Timedelta(days=7))              # not before the ramp starts
        self.assertEqual(first.features["basis"], "filter_dp_trend")
        self.assertFalse(first.features["dp_sensor_missing"])
        self.assertGreaterEqual(first.features["dp_vs_baseline_ratio"], 1.15)
        self.assertEqual(first.route, "rule")
        self.assertEqual(first.asset, "AHU1")
        # the technician slot is a real, future point inside a scheduled weekday shift
        slot = pd.Timestamp(first.features["technician_next_slot_utc"])
        self.assertGreater(slot, first.t)
        self.assertLess(slot.weekday(), 5)
        self.assertTrue(7 <= slot.hour + slot.minute / 60 < 15.5)
        self.assertEqual(first.features["technician_id"], "TECH_1")

    def test_gap_is_not_degradation(self):
        g, dp, air, fan, miss = ahu_arrays(960, gap=(400, 640))           # ~2.5 days of blank dp, clean otherwise
        cands = detect_maintenance(make_ahu_obs(g, dp, air, fan, miss))
        self.assertTrue(cands)
        self.assertTrue(all(c.detector == MAINTENANCE_GAP for c in cands),
                        "a blank filter_dp_pa run must never become degradation")
        c = cands[0]
        self.assertIn("sensor_gap", c.quality_flags)
        self.assertEqual(c.features["issue"], "filter_dp_pa_gap")
        self.assertFalse(c.features["fan_airflow_drift_corroborates"])
        self.assertFalse(c.features["pre_gap_degradation_in_valid_data"])
        self.assertGreaterEqual(c.features["gap_hours_so_far"], 2.0)
        self.assertGreaterEqual(c.t, g[400 + 7])
        self.assertLessEqual(c.t, g[639])

    def test_gap_candidates_use_only_gap_rows_and_never_fabricate_values(self):
        g, dp, air, fan, miss = ahu_arrays(960, ramp_from=300, gap=(400, 640))
        cands = [c for c in detect_maintenance(make_ahu_obs(g, dp, air, fan, miss)) if c.detector == MAINTENANCE_GAP]
        self.assertTrue(cands)
        for c in cands:
            self.assertTrue(400 <= list(g).index(c.t) < 640)
            # the pre-gap pressure is the last real reading, not an extrapolation
            self.assertAlmostEqual(c.features["last_valid_dp_pa"], float(dp[399]), places=2)

    def test_blind_sensor_with_measured_flow_loss_is_labelled_not_assumed(self):
        g, dp, air, fan, miss = ahu_arrays(960, gap=(300, 960), flow_loss_from=500)
        cands = detect_maintenance(make_ahu_obs(g, dp, air, fan, miss))
        maint = [c for c in cands if c.detector == MAINTENANCE]
        self.assertTrue(maint)
        c = maint[0]
        self.assertEqual(c.features["basis"], "airflow_fan_without_dp")
        self.assertTrue(c.features["dp_sensor_missing"])
        self.assertIn("dp_sensor_gap", c.quality_flags)
        self.assertIsNone(c.features["dp_vs_baseline_ratio"])
        self.assertGreaterEqual(c.features["airflow_shortfall_share"], 0.05)

    def test_detector_is_causal(self):
        args = ahu_arrays(960, ramp_from=672)
        full = detect_maintenance(make_ahu_obs(*args))
        self.assertTrue(full)
        for k in (700, 780, 860):
            g, dp, air, fan, miss = (a[:k] for a in args)
            part = detect_maintenance(make_ahu_obs(g, dp, air, fan, miss))
            expect = [(c.t, c.detector, c.severity_hint) for c in full if c.t <= g[-1]]
            self.assertEqual([(c.t, c.detector, c.severity_hint) for c in part], expect)

    def test_episode_builder_keeps_a_persistent_case_to_few_decisions(self):
        cands = detect_maintenance(make_ahu_obs(*ahu_arrays(960, ramp_from=672)))
        eps = build_episodes(cands)
        self.assertLessEqual(len(eps), 3)
        self.assertEqual(sum(1 for e in eps if not e.is_followup), 1)


class TestTechnicianSlot(unittest.TestCase):
    """Roster facts as known at t. Wed 2026-01-14 14:00 UTC: today's shift is nearly over."""
    T = utc("2026-01-14T14:00:00Z")

    def _slot(self, roster_df, t=None):
        obs = _obs(pd.date_range(T0, periods=4, freq="15min", tz="UTC"))
        obs.roster = roster_df
        return _roster_slot(obs, t or self.T)

    def test_next_shift_when_nobody_is_out(self):
        s = self._slot(roster())
        self.assertEqual(s["technician_next_slot_utc"], "2026-01-15T07:00:00Z")
        self.assertFalse(s["contractor_needed"])
        self.assertEqual(s["technician_shift_local"], "08:00-16:30 Mon-Fri")

    def test_sick_technician_moves_the_slot(self):
        sick_known = roster({"2026-01-15": ("sick", "2026-01-14T12:00:00Z")})
        s = self._slot(sick_known)
        self.assertEqual(s["technician_next_slot_utc"], "2026-01-16T07:00:00Z")   # Thursday is lost
        self.assertEqual(s["technician_unavailable"][0]["status"], "sick")

    def test_sick_not_yet_known_does_not_move_the_slot(self):
        late = roster({"2026-01-15": ("sick", "2026-01-14T20:00:00Z")})            # learned after t
        s = self._slot(late)
        self.assertEqual(s["technician_next_slot_utc"], "2026-01-15T07:00:00Z")
        self.assertEqual(s["technician_unavailable"], [])

    def test_vacation_week_means_contractor(self):
        days = {d: ("vacation", "2025-12-20T07:00:00Z") for d in
                ("2026-01-15", "2026-01-16", "2026-01-19", "2026-01-20", "2026-01-21", "2026-01-22", "2026-01-23")}
        s = self._slot(roster(days, days=14))
        self.assertTrue(s["contractor_needed"])
        self.assertIsNone(s["technician_next_slot_utc"])

    def test_no_roster_rows_never_invents_a_slot(self):
        s = self._slot(pd.DataFrame(columns=ROSTER_COLS))
        self.assertFalse(s["technician_roster_known"])
        self.assertIsNone(s["technician_next_slot_utc"])
        self.assertTrue(s["contractor_needed"])


# --------------------------------------------------------------------------------------- logistics
def make_log_obs(series, start="2026-01-09T00:00:00Z", events=None):
    n = len(next(iter(series.values())))
    idx = pd.date_range(utc(start), periods=n, freq="1h", tz="UTC")
    vals = pd.DataFrame({k: np.asarray(v, dtype=float) for k, v in series.items()}, index=idx)
    o = _obs(idx, logistics=SimpleNamespace(wide=SimpleNamespace(values=vals)))
    if events is not None:
        o.events = events
    return o


def sawtooth_main(n, rate, start_level=2.0):
    """WASTE_MAIN starting right after a Friday 06:00 UTC pickup (grid starts Fri 06:00)."""
    return start_level + rate * np.arange(n)


class TestLogisticsDetector(unittest.TestCase):
    START = "2026-01-09T06:00:00Z"  # Friday, the moment of a regular pickup

    def test_fast_filling_container_orders_an_extra_pickup_in_time(self):
        n = 95                                           # next regular pickup Tue 2026-01-13 06:00Z = +96 h
        obs = make_log_obs({"WASTE_MAIN.fill_pct": np.minimum(sawtooth_main(n, 1.2), 100)}, start=self.START)
        cands = [c for c in detect_logistics(obs) if c.asset == "WASTE_MAIN"]
        self.assertTrue(cands)
        c = cands[0]
        self.assertEqual(c.detector, LOGISTICS)
        self.assertEqual(c.route, "rule")
        f = c.features
        self.assertEqual(f["subject"], "waste_container")
        self.assertEqual(f["next_regular_service_utc"], "2026-01-13T06:00:00Z")
        self.assertGreater(f["projected_pct_at_next_service"], 95)
        self.assertTrue(f["extra_service_helps"])
        self.assertLessEqual(f["hours_to_full"] - 24, 18.0 + 1e-6)     # raised once the order window is open
        self.assertGreater(f["hours_to_full"], 24 - 1e-6)              # and the extra pickup can still land first
        self.assertEqual(c.severity_hint, "medium")
        # the order window is never earlier than a full day of data
        self.assertGreaterEqual(c.t, utc(self.START) + pd.Timedelta(hours=24))

    def test_slow_container_makes_no_noise(self):
        obs = make_log_obs({"WASTE_MAIN.fill_pct": sawtooth_main(95, 0.25),
                            "VEND1.stock_drinks": np.maximum(100 - 0.1 * np.arange(95), 0),
                            "WASTE_F1.fill_pct": 10 + 0.1 * np.arange(95)}, start=self.START)
        self.assertEqual(detect_logistics(obs), [])

    def test_pickup_reset_ends_the_case(self):
        n = 120                                          # fills for 60 h, the Tuesday pickup then empties it
        fill = np.concatenate([np.minimum(sawtooth_main(60, 1.7), 100), np.full(n - 60, 3.0)])
        obs = make_log_obs({"WASTE_MAIN.fill_pct": fill}, start="2026-01-10T18:00:00Z")   # Sat 18:00 -> Tue 06:00 = 60 h
        cands = detect_logistics(obs)
        self.assertTrue(cands)
        reset_t = utc("2026-01-10T18:00:00Z") + pd.Timedelta(hours=60)
        self.assertTrue(all(c.t < reset_t for c in cands))

    def test_extra_pickup_that_cannot_help_is_reported(self):
        # full in ~12 h, regular pickup Tue 06:00Z is 18 h away: the 24 h lead time is longer than the wait
        n = 80
        start = "2026-01-10T12:00:00Z"                   # Saturday noon; pickup is Tue 06:00 -> +66 h
        fill = np.minimum(35 + 1.3 * np.arange(n), 100)
        obs = make_log_obs({"WASTE_MAIN.fill_pct": fill}, start=start)
        cands = [c for c in detect_logistics(obs) if c.asset == "WASTE_MAIN"]
        self.assertTrue(cands)
        self.assertTrue(any(c.severity_hint == "high" for c in cands))
        late = [c for c in cands if c.features["hours_to_full"] < 24]
        for c in late:
            self.assertEqual(c.severity_hint, "high")

    def test_vending_burn_before_restock_orders_extra(self):
        n = 90
        drinks = np.maximum(100 - 2.0 * np.arange(n), 0)  # after Thu 10:00 local restock; next is Mon 10:00
        obs = make_log_obs({"VEND1.stock_drinks": drinks, "VEND1.stock_snacks": 60 - 0.05 * np.arange(n)},
                           start="2026-01-08T09:00:00Z")
        cands = [c for c in detect_logistics(obs) if c.asset == "VEND1"]
        self.assertTrue(cands)
        f = cands[0].features
        self.assertEqual(f["subject"], "vending")
        self.assertEqual(f["limiting_product"], "drinks")
        self.assertEqual(f["next_regular_service_utc"], "2026-01-12T09:00:00Z")
        self.assertTrue(f["extra_service_helps"])
        self.assertLess(f["projected_units_at_next_service"], 10)
        self.assertEqual(f["products_at_risk"], ["drinks"])

    def test_floor_bin_at_the_cap_is_a_floor_not_a_measurement(self):
        fill = np.concatenate([np.linspace(30, 100, 40), np.full(30, 100.0)])
        obs = make_log_obs({"WASTE_F2.fill_pct": fill, "WASTE_MAIN.fill_pct": np.full(70, 20.0)},
                           start="2026-01-13T08:00:00Z")
        cands = [c for c in detect_logistics(obs) if c.asset == "WASTE_F2"]
        self.assertTrue(cands)
        capped = [c for c in cands if c.features["capped_at_100"]]
        self.assertTrue(capped)
        self.assertEqual(capped[0].severity_hint, "high")
        self.assertIn("overflowing", capped[0].features["note"])
        self.assertTrue(capped[0].features["robot_can_carry"])
        self.assertFalse(capped[0].features["bathroom_cleaning_needed"])

    def test_floor_bin_robot_not_used_when_the_container_is_full(self):
        fill = np.full(70, 100.0)
        obs = make_log_obs({"WASTE_F3.fill_pct": fill, "WASTE_MAIN.fill_pct": np.full(70, 92.0)},
                           start="2026-01-13T08:00:00Z")
        cands = [c for c in detect_logistics(obs) if c.asset == "WASTE_F3"]
        self.assertTrue(cands)
        self.assertFalse(cands[0].features["robot_can_carry"])

    def test_missing_readings_stay_blank_and_raise_nothing(self):
        obs = make_log_obs({"WASTE_MAIN.fill_pct": np.full(95, np.nan), "VEND1.stock_drinks": np.full(95, np.nan)},
                           start=self.START)
        self.assertEqual(detect_logistics(obs), [])

    def test_detector_is_causal(self):
        n = 95
        series = {"WASTE_MAIN.fill_pct": np.minimum(sawtooth_main(n, 1.2), 100),
                  "VEND1.stock_drinks": np.maximum(100 - 2.0 * np.arange(n), 0)}
        full = detect_logistics(make_log_obs(series, start=self.START))
        self.assertTrue(full)
        for k in (45, 60, 80):
            part = detect_logistics(make_log_obs({a: v[:k] for a, v in series.items()}, start=self.START))
            cut = utc(self.START) + pd.Timedelta(hours=k - 1)
            expect = [(c.t, c.asset, c.severity_hint) for c in full if c.t <= cut]
            self.assertEqual([(c.t, c.asset, c.severity_hint) for c in part], expect)

    def test_episodes_merge_the_hourly_projection(self):
        obs = make_log_obs({"WASTE_MAIN.fill_pct": np.minimum(sawtooth_main(95, 1.2), 100)}, start=self.START)
        eps = build_episodes(detect_logistics(obs))
        self.assertEqual(sum(1 for e in eps if not e.is_followup), 1)
        self.assertLessEqual(len(eps), 3)


# --------------------------------------------------------------- wiring: fallback + playbooks + guard
class TestWiring(unittest.TestCase):
    def test_detectors_are_registered_with_gap_tolerances(self):
        names = {d.__name__ for d in ALL_DETECTORS}
        self.assertIn("detect_maintenance", names)
        self.assertIn("detect_logistics", names)
        for k in (MAINTENANCE, MAINTENANCE_GAP, LOGISTICS):
            self.assertIn(k, GAP_TOLERANCE)

    def _decisions(self, cand):
        pk = cand.packet()
        u = rule_based_incident(pk)
        return u, decide(u, pk, episode_id="EP-test")

    def test_gap_becomes_data_quality_flag_never_a_technician(self):
        g, dp, air, fan, miss = ahu_arrays(960, gap=(400, 640))
        c = next(c for c in detect_maintenance(make_ahu_obs(g, dp, air, fan, miss)) if c.detector == MAINTENANCE_GAP)
        u, ds = self._decisions(c)
        self.assertEqual(u["issue_type"], "data_quality")
        self.assertEqual(u["likely_cause"], "sensor_fault")
        self.assertEqual([d.record["action"] for d in ds], ["flag_anomaly"])
        self.assertIn("not as filter degradation", u["reason"])

    def test_degradation_dispatches_a_technician_with_approval_and_the_real_slot(self):
        obs = make_ahu_obs(*ahu_arrays(960, ramp_from=672))
        obs.roster = roster()
        c = next(c for c in detect_maintenance(obs) if c.detector == MAINTENANCE and c.severity_hint != "low")
        u, ds = self._decisions(c)
        self.assertEqual(u["issue_type"], "ventilation_maintenance")
        rec = ds[0].record
        self.assertEqual(rec["action"], "dispatch_technician")
        self.assertTrue(rec["requires_human_approval"])
        self.assertIn(c.features["technician_next_slot_utc"], rec["reason"])

    def test_technician_out_asks_for_a_contractor(self):
        obs = make_ahu_obs(*ahu_arrays(960, ramp_from=672))
        out = {d.strftime("%Y-%m-%d"): ("vacation", "2025-12-20T07:00:00Z")
               for d in pd.date_range("2026-01-12", periods=14, freq="D", tz="UTC")}
        obs.roster = roster(out, days=21)
        c = next(c for c in detect_maintenance(obs) if c.detector == MAINTENANCE)
        self.assertTrue(c.features["contractor_needed"])
        self.assertIsNone(c.features["technician_next_slot_utc"])
        u, ds = self._decisions(c)
        self.assertEqual(ds[0].record["action"], "dispatch_technician")
        self.assertIn("contractor", ds[0].record["reason"])

    def test_open_work_order_defers_instead_of_double_booking(self):
        obs = make_ahu_obs(*ahu_arrays(960, ramp_from=672))
        obs.roster = roster()
        obs.work_orders = pd.DataFrame([{
            "work_order_id": "WO900", "created_utc": utc("2026-01-12T00:00:00Z"), "asset_id": "AHU1",
            "type": "corrective", "status": "open", "scheduled_utc": utc("2026-01-14T07:00:00Z"),
            "completed_utc": pd.NaT, "assigned_to": "EXTERNAL_HVAC", "summary": "filters"}], columns=WO_COLS)
        for col in ("created_utc", "scheduled_utc", "completed_utc"):
            obs.work_orders[col] = pd.to_datetime(obs.work_orders[col], utc=True)
        c = next(c for c in detect_maintenance(obs) if c.detector == MAINTENANCE)
        self.assertEqual(c.features["open_work_order"], "WO900")
        u, ds = self._decisions(c)
        self.assertEqual(ds[0].record["action"], "defer_action")

    def test_logistics_container_orders_an_extra_pickup_autonomously(self):
        obs = make_log_obs({"WASTE_MAIN.fill_pct": np.minimum(sawtooth_main(95, 1.2), 100)},
                           start="2026-01-09T06:00:00Z")
        c = detect_logistics(obs)[0]
        u, ds = self._decisions(c)
        self.assertEqual(u["issue_type"], "logistics")
        rec = ds[0].record
        self.assertEqual(rec["action"], "order_service")
        self.assertFalse(rec["requires_human_approval"])
        self.assertIn("Simulated", rec["expected_outcome"])
        self.assertIn("WASTE_MAIN", rec["affected_assets"])

    def test_floor_bin_robot_is_not_downgraded_by_the_guard(self):
        obs = make_log_obs({"WASTE_F2.fill_pct": np.full(70, 100.0), "WASTE_MAIN.fill_pct": np.full(70, 20.0)},
                           start="2026-01-13T08:00:00Z")
        c = next(c for c in detect_logistics(obs) if c.asset == "WASTE_F2")
        u, ds = self._decisions(c)
        self.assertEqual(ds[0].record["action"], "dispatch_robot")
        self.assertEqual(ds[0].notes, [])


if __name__ == "__main__":
    unittest.main()
