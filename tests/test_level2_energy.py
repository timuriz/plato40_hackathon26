"""Level 2 energy-optimisation detector: hand-made day-ahead tariff / forecast / battery / comfort data.

All fake data lives in this file (the real OBSERVE stage is not run). The real ``AsOf`` view is reused on a
tiny stand-in observation so the published/issued availability rules are the real ones.
"""
import unittest
from types import SimpleNamespace

import numpy as np
import pandas as pd

from plato.decide.decide import decide
from plato.llm.fallback import rule_based_incident
from plato.observe.asof import AsOf
from plato.observe.quality import Q_OK
from plato.understand.candidate import ENERGY
from plato.understand.detectors import ALL_DETECTORS
from plato.understand.detectors.energy import SAVINGS_LABEL, detect_energy
from plato.understand.episodes import GAP_TOLERANCE, build_episodes

START = "2026-01-05T00:00:00Z"
DAYS = 4
ROOMS = ["R101", "R102"]


def _grid(days=DAYS):
    return pd.date_range(pd.Timestamp(START), periods=96 * days, freq="15min", tz="UTC")


def _price(hour_utc: int, amp: float = 0.06) -> float:
    """Smooth day shape: cheapest around 16:00 UTC, dearest around 04:00 UTC (so from the 12:00 UTC decision
    the cheap hours come first and the expensive ones later: charge, then discharge)."""
    return round(0.26 + amp * np.cos((hour_utc - 4) * 2 * np.pi / 24), 4)


def _tariff(days=DAYS, amp=0.06, flat=None):
    rows = []
    for d in range(-1, days + 1):
        day = pd.Timestamp(START) + pd.Timedelta(days=d)
        pub = day - pd.Timedelta(hours=12)           # 12:00Z on the previous day (13:00 local)
        for h in range(24):
            p = flat if flat is not None else _price(h, amp)
            rows.append({"timestamp_utc": day + pd.Timedelta(hours=h), "price_eur_kwh": p, "published_utc": pub})
    return pd.DataFrame(rows)


def _forecast(days=DAYS, cold_from_hour=None, cold_c=-12.0, base_c=-2.0):
    """One forecast per day issued 05:00Z for the next 48 h. Optionally turns cold from a UTC hour of day+1."""
    rows = []
    for d in range(-1, days):
        issued = pd.Timestamp(START) + pd.Timedelta(days=d, hours=5)
        for k in range(1, 49):
            target = issued + pd.Timedelta(hours=k)
            v = base_c
            if cold_from_hour is not None and target.hour >= cold_from_hour and target.day != issued.day:
                v = cold_c
            rows.append({"issued_utc": issued, "target_utc": target, "outdoor_c_forecast": v})
    return pd.DataFrame(rows)


def make_obs(days=DAYS, soc=30.0, amp=0.06, flat=None, cold_from_hour=None, cold_rooms=(), grid=None):
    g = _grid(days) if grid is None else grid
    n = len(g)
    cols = ["building_load_kw", "hvac_electric_kw", "pv_kw", "battery_discharge_kw_signed", "battery_soc_kwh",
            "grid_import_kw", "grid_export_kw"]
    en = pd.DataFrame({"building_load_kw": 40.0, "hvac_electric_kw": 25.0, "pv_kw": 0.0,
                       "battery_discharge_kw_signed": 0.0, "battery_soc_kwh": float(soc),
                       "grid_import_kw": 40.0, "grid_export_kw": 0.0}, index=g)[cols]
    temp = pd.DataFrame({r: np.full(n, 22.0) for r in ROOMS}, index=g)
    sp = pd.DataFrame({r: np.full(n, 22.0) for r in ROOMS}, index=g)
    for r in cold_rooms:
        temp[r] = 17.0                                # occupied, far below 20 C and its 22 C setpoint
    qual = pd.DataFrame(Q_OK, index=g, columns=ROOMS)
    occ = pd.DataFrame(True, index=g, columns=ROOMS)
    weather = pd.DataFrame({"outdoor_c": np.full(n, -2.0)}, index=g)
    obs = SimpleNamespace(
        grid=g,
        building=SimpleNamespace(rooms=ROOMS, comfort_c=(20.0, 23.0), soc_bounds_kwh=(10.0, 95.0),
                                 battery_usable_kwh=100.0, battery_max_kw=40.0, raw={}),
        rooms=SimpleNamespace(values={"temperature_c": temp, "setpoint_c": sp},
                              quality={"temperature_c": qual}, occupied=occ),
        energy=SimpleNamespace(wide=SimpleNamespace(values=en)),
        weather=SimpleNamespace(values=weather),
        tariff=_tariff(days, amp, flat),
        forecast=_forecast(days, cold_from_hour),
    )
    obs.asof = lambda t: AsOf(obs, pd.Timestamp(t))
    return obs


def truncate(obs, k):
    g = obs.grid[:k]
    sl = lambda df: df.iloc[:k]  # noqa: E731
    out = SimpleNamespace(
        grid=g, building=obs.building,
        rooms=SimpleNamespace(values={v: sl(df) for v, df in obs.rooms.values.items()},
                              quality={v: sl(df) for v, df in obs.rooms.quality.items()},
                              occupied=sl(obs.rooms.occupied)),
        energy=SimpleNamespace(wide=SimpleNamespace(values=sl(obs.energy.wide.values))),
        weather=SimpleNamespace(values=sl(obs.weather.values)),
        tariff=obs.tariff, forecast=obs.forecast)          # tables keep their future rows on purpose
    out.asof = lambda t: AsOf(out, pd.Timestamp(t))
    return out


def _key(c):
    return (c.t, c.asset, c.features["price_spread_eur_kwh"], c.features.get("simulated_saving_eur"),
            c.features["comfort_at_risk"])


class TestEnergyDetector(unittest.TestCase):
    def test_registered_and_wired(self):
        self.assertIn(detect_energy, ALL_DETECTORS)
        self.assertEqual(ENERGY, "energy_optimization")
        self.assertIn(ENERGY, GAP_TOLERANCE)

    def test_one_rule_routed_proposal_per_day_after_publication(self):
        obs = make_obs()
        cs = detect_energy(obs)
        self.assertGreaterEqual(len(cs), 2)
        days = [c.t.tz_convert("Europe/Vienna").date() for c in cs]
        self.assertEqual(len(days), len(set(days)))                       # at most one per local day
        for c in cs:
            self.assertEqual(c.detector, ENERGY)
            self.assertEqual(c.route, "rule")
            self.assertTrue(c.features["tariff_published_before_decision"])
            # decided at/after the 12:00Z publication (or the first grid step for day-one data)
            self.assertTrue(c.t == obs.grid[0] or c.t.hour == 12)
        # the later episodes are separate, one per day
        self.assertEqual(len(build_episodes(cs)), len(cs))

    def test_cheap_expensive_hours_and_battery_plan(self):
        obs = make_obs(soc=30.0)
        c = [x for x in detect_energy(obs) if x.t == pd.Timestamp("2026-01-06T12:00:00Z")][0]
        f = c.features
        self.assertGreater(f["price_expensive_mean_eur_kwh"], f["price_cheap_mean_eur_kwh"])
        cheap_prices = [_price(pd.Timestamp(h).hour) for h in f["cheap_hours_utc"]]
        exp_prices = [_price(pd.Timestamp(h).hour) for h in f["expensive_hours_utc"]]
        self.assertLess(max(cheap_prices), min(exp_prices))
        bp = f["battery_plan"]
        self.assertIsNotNone(bp)
        # battery physics: within 95 kWh bound and 40 kW per hour, and a round trip loses energy
        self.assertLessEqual(bp["charge_kwh"], 40.0 * len(bp["charge_hours_utc"]) + 1e-6)
        self.assertLessEqual(30.0 + bp["charge_kwh"] * np.sqrt(0.9), 95.0 + 0.05)   # values are rounded
        self.assertLess(bp["discharge_kwh"], bp["charge_kwh"])
        self.assertAlmostEqual(bp["discharge_kwh"] / bp["charge_kwh"], 0.9, delta=0.02)
        # charge first (cheap), discharge later (expensive)
        self.assertLess(min(bp["charge_hours_utc"]), max(bp["discharge_hours_utc"]))
        self.assertGreater(f["simulated_saving_eur"], 0.0)

    def test_savings_are_labelled_simulated_never_measured(self):
        obs = make_obs()
        for c in detect_energy(obs):
            f = c.features
            if "simulated_saving_eur" in f:
                self.assertIn("SIMULATED", f["simulated_saving_label"])
                self.assertEqual(f["simulated_saving_label"], SAVINGS_LABEL)
                self.assertIn("SIMULATED", f["expected_outcome"])
                self.assertIn("not a measurement", f["expected_outcome"])
            self.assertNotIn("measured_saving_eur", f)
            self.assertNotIn("saving_eur", f)

    def test_flat_or_small_spread_gives_no_proposal_and_no_savings(self):
        self.assertEqual(detect_energy(make_obs(flat=0.25)), [])
        self.assertEqual(detect_energy(make_obs(amp=0.01)), [])       # spread far below the threshold

    def test_unknown_or_partial_tariff_gives_nothing(self):
        obs = make_obs()
        obs.tariff = obs.tariff.iloc[0:0]
        self.assertEqual(detect_energy(obs), [])
        obs = make_obs()
        obs.tariff["price_eur_kwh"] = np.nan                          # every price unknown
        self.assertEqual(detect_energy(obs), [])

    def test_full_battery_has_no_battery_plan(self):
        obs = make_obs(soc=95.0)
        for c in detect_energy(obs):
            self.assertIsNone(c.features["battery_plan"])
            self.assertNotIn("simulated_saving_eur", c.features)

    def test_unknown_soc_has_no_battery_plan(self):
        obs = make_obs()
        obs.energy.wide.values["battery_soc_kwh"] = np.nan
        for c in detect_energy(obs):
            self.assertIsNone(c.features["battery_plan"])

    def test_preheat_before_forecast_cold_uses_cheap_hours_before_it(self):
        obs = make_obs(cold_from_hour=2)
        c = [x for x in detect_energy(obs) if x.t == pd.Timestamp("2026-01-06T12:00:00Z")][0]
        pre = c.features["preheat_plan"]
        self.assertIsNotNone(pre)
        self.assertIn("HP1", c.affected_assets)
        first_cold = pd.Timestamp(pre["first_cold_hour_utc"])
        self.assertGreater(first_cold, c.t)
        for h in pre["preheat_hours_utc"]:
            self.assertLess(pd.Timestamp(h) + pd.Timedelta(hours=1), first_cold + pd.Timedelta(seconds=1))
            self.assertIn(h, c.features["cheap_hours_utc"])
        self.assertLessEqual(pre["preheat_max_delta_c"], 3.0)         # stays inside the 20-23 C band

    def test_no_preheat_without_a_cold_forecast(self):
        for c in detect_energy(make_obs()):
            self.assertIsNone(c.features["preheat_plan"])

    def test_cold_room_sets_comfort_at_risk_and_guard_defers(self):
        obs = make_obs(cold_rooms=["R101"])
        cs = detect_energy(obs)
        late = [c for c in cs if c.t >= pd.Timestamp("2026-01-06T12:00:00Z")]
        self.assertTrue(late)
        for c in late:
            self.assertTrue(c.features["comfort_at_risk"])
            self.assertEqual(c.features["cold_rooms"], ["R101"])
            u = rule_based_incident(c.packet())
            self.assertEqual(u["issue_type"], "energy_optimization")
            ds = decide(u, c.packet())
            self.assertEqual([d.proposed_action for d in ds], ["optimize_energy"])
            self.assertEqual(ds[0].record["action"], "defer_action")
            self.assertTrue(any("comfort" in n for n in ds[0].notes))

    def test_comfortable_building_proposes_optimize_energy_autonomously(self):
        obs = make_obs()
        c = [x for x in detect_energy(obs) if x.t == pd.Timestamp("2026-01-06T12:00:00Z")][0]
        self.assertFalse(c.features["comfort_at_risk"])
        u = rule_based_incident(c.packet())
        self.assertEqual(u["issue_type"], "energy_optimization")
        self.assertEqual(u["severity"], "low")
        self.assertIn("simulated", u["reason"].lower())
        ds = decide(u, c.packet())
        self.assertEqual(ds[0].record["action"], "optimize_energy")
        self.assertFalse(ds[0].record["requires_human_approval"])

    def test_causal_truncation_gives_same_candidates(self):
        obs = make_obs(cold_from_hour=2, cold_rooms=["R102"])
        full = detect_energy(obs)
        for k in (96 * 2 + 60, 96 * 3 + 52, 96 * 3 + 90):
            tr = detect_energy(truncate(obs, k))
            expect = [c for c in full if c.t <= obs.grid[k - 1]]
            self.assertEqual([_key(c) for c in tr], [_key(c) for c in expect], f"k={k}")

    def test_prices_published_after_t_are_invisible(self):
        base = make_obs()
        changed = make_obs()
        late = changed.tariff["published_utc"] >= pd.Timestamp("2026-01-07T12:00:00Z")
        changed.tariff.loc[late, "price_eur_kwh"] = 0.9               # absurd prices known only later
        a = [c for c in detect_energy(base) if c.t < pd.Timestamp("2026-01-07T12:00:00Z")]
        b = [c for c in detect_energy(changed) if c.t < pd.Timestamp("2026-01-07T12:00:00Z")]
        self.assertEqual([_key(c) for c in a], [_key(c) for c in b])

    def test_duplicate_publications_same_local_day_do_not_spam(self):
        obs = make_obs()
        extra = obs.tariff.copy()
        extra["published_utc"] = extra["published_utc"] + pd.Timedelta(hours=3)   # a re-publication
        obs.tariff = pd.concat([obs.tariff, extra], ignore_index=True)
        cs = detect_energy(obs)
        days = [c.t.tz_convert("Europe/Vienna").date() for c in cs]
        self.assertEqual(len(days), len(set(days)))

    def test_forecast_issued_after_t_is_ignored(self):
        obs = make_obs()
        # a deep-cold forecast issued 1 h AFTER the 2026-01-06 decision must not create a pre-heat plan then
        extra = [{"issued_utc": pd.Timestamp("2026-01-06T13:00:00Z"),
                  "target_utc": pd.Timestamp("2026-01-06T13:00:00Z") + pd.Timedelta(hours=k),
                  "outdoor_c_forecast": -25.0} for k in range(1, 49)]
        obs.forecast = pd.concat([obs.forecast, pd.DataFrame(extra)], ignore_index=True)
        c = [x for x in detect_energy(obs) if x.t == pd.Timestamp("2026-01-06T12:00:00Z")][0]
        self.assertIsNone(c.features["preheat_plan"])


if __name__ == "__main__":
    unittest.main()
