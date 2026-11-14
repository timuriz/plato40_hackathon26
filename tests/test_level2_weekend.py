"""S05 unexpected-weekend detector tests: hand-made bookings / roster, no dataset needed.

The fake building has 40 rooms, a quiet baseline (about 3 arrivals and 3 departures a day) and one surge day,
Monday 2026-01-12, with 16 rooms that check out and are booked again the same day (plus the baseline
departures). Capacity per cleaner (shift 07:00-15:30 local, rooms cleanable from 10:00 when there is no
checkout history, 45 min per room) is 6 rooms before the 15:00 arrivals and 7 by the end of the shift.
"""
import unittest
from types import SimpleNamespace

import pandas as pd

from plato.decide import decide
from plato.llm.fallback import rule_based_incident
from plato.observe.asof import AsOf
from plato.observe.timeutil import utc_ts
from plato.understand.candidate import OCCUPANCY_SURGE
from plato.understand.detectors import ALL_DETECTORS, detect_weekend
from plato.understand.episodes import GAP_TOLERANCE, build_episodes

ROOMS = [f"R{i:03d}" for i in range(1, 41)]
SURGE_ROOMS = ROOMS[20:36]            # 16 same-day turnovers on the surge day
SURGE_DAY = "2026-01-12"
FIRST_DAY, LAST_ROSTER_DAY = "2026-01-05", "2026-01-16"
EVENT_COLS = ["event_id", "timestamp_utc", "type", "room_id", "resource_id"]
REQ_COLS = ["request_id", "timestamp_utc", "room_id", "request_type"]


def _ts(s):
    return pd.Timestamp(s, tz="UTC") if "Z" not in s and "+" not in s else pd.Timestamp(s)


def baseline_bookings():
    """3 arrivals/day, 2-night stays, 4 room groups that rotate: no same-day turnovers, ~6 rooms occupied."""
    rows = []
    n = 0
    for d in pd.date_range("2026-01-01", "2026-01-18", freq="D"):
        for k in range(3):
            n += 1
            room = ROOMS[(d.day % 4) * 3 + k]
            rows.append(dict(booking_id=f"B{n:04d}", room_id=room, created_utc="2025-12-20T08:00:00Z",
                             arrival_utc=f"{d:%Y-%m-%d}T14:00:00Z",
                             departure_utc=f"{d + pd.Timedelta(days=2):%Y-%m-%d}T10:00:00Z", status="confirmed"))
    return rows


def surge_bookings(created="2025-12-21T09:00:00Z", status="confirmed"):
    rows = []
    for i, room in enumerate(SURGE_ROOMS):
        rows.append(dict(booking_id=f"S{i:03d}a", room_id=room, created_utc=created,
                         arrival_utc="2026-01-10T14:00:00Z", departure_utc=f"{SURGE_DAY}T10:00:00Z", status=status))
        rows.append(dict(booking_id=f"S{i:03d}b", room_id=room, created_utc=created,
                         arrival_utc=f"{SURGE_DAY}T14:00:00Z", departure_utc="2026-01-13T10:00:00Z", status=status))
    return rows


def roster(cleaners=("C1", "C2", "C3"), robots=("ROBOT_1", "ROBOT_2"), sick=None, last_day=LAST_ROSTER_DAY):
    """sick = {(resource_id, date): status_known_utc}: that shift is `sick` and only known from then on."""
    sick = sick or {}
    rows = []
    n = 0
    for d in pd.date_range(FIRST_DAY, last_day, freq="D"):
        day = f"{d:%Y-%m-%d}"
        for c in cleaners:
            n += 1
            known = sick.get((c, day))
            rows.append(dict(shift_id=f"SH{n:04d}", resource_id=c, role="cleaner",
                             start_utc=f"{day}T06:00:00Z", end_utc=f"{day}T14:30:00Z",
                             status="sick" if known else "scheduled",
                             status_known_utc=known or "2025-12-22T06:00:00Z"))
        for r in robots:
            n += 1
            rows.append(dict(shift_id=f"SH{n:04d}", resource_id=r, role="robot",
                             start_utc=f"{day}T05:00:00Z", end_utc=f"{day}T21:00:00Z",
                             status="scheduled", status_known_utc="2025-12-22T05:00:00Z"))
    return rows


def _frame(rows, tcols):
    df = pd.DataFrame(rows)
    for c in tcols:
        df[c] = pd.to_datetime(df[c], utc=True)
    return df


def make_obs(booking_rows, roster_rows, end="2026-01-14T00:00:00Z", requests=None):
    obs = SimpleNamespace(
        grid=pd.date_range(pd.Timestamp(f"{FIRST_DAY}T00:00:00Z"), pd.Timestamp(end), freq="15min"),
        building=SimpleNamespace(rooms=list(ROOMS)),
        bookings=_frame(booking_rows, ["created_utc", "arrival_utc", "departure_utc"]),
        roster=_frame(roster_rows, ["start_utc", "end_utc", "status_known_utc"]),
        events=pd.DataFrame({c: pd.Series(dtype="object") for c in EVENT_COLS}).assign(
            timestamp_utc=pd.Series(dtype="datetime64[ns, UTC]")),
        guest_requests=_frame(requests, ["timestamp_utc"]) if requests else pd.DataFrame(
            {c: pd.Series(dtype="object") for c in REQ_COLS}).assign(
                timestamp_utc=pd.Series(dtype="datetime64[ns, UTC]")),
    )
    obs.asof = lambda t: AsOf(obs, utc_ts(t))
    return obs


def on_day(cands, day):
    return [c for c in cands if c.features["target_date_local"] == day]


class TestWeekendDetector(unittest.TestCase):
    def test_registered_with_episode_tolerance(self):
        self.assertIn(detect_weekend, ALL_DETECTORS)
        self.assertIn(OCCUPANCY_SURGE, GAP_TOLERANCE)

    def test_quiet_building_raises_nothing(self):
        obs = make_obs(baseline_bookings(), roster(cleaners=("C1", "C2")))
        self.assertEqual(detect_weekend(obs), [])

    def test_surge_with_no_slack_is_a_contingent_warning_not_a_gap(self):
        obs = make_obs(baseline_bookings() + surge_bookings(), roster())
        cands = on_day(detect_weekend(obs), SURGE_DAY)   # the day after (16 departures) is warned separately
        self.assertEqual(len(cands), 1)
        c = cands[0]
        f = c.features
        self.assertEqual((c.detector, c.asset, c.route), (OCCUPANCY_SURGE, "BUILDING", "rule"))
        self.assertEqual(f["target_date_local"], SURGE_DAY)
        self.assertTrue(f["contingent"])
        self.assertEqual(f["gap_rooms"], 0)                                  # 3 cleaners cover it ...
        self.assertEqual(f["same_day_turnovers"], 16)
        self.assertEqual(f["capacity_rooms_before_arrivals"], 18)
        self.assertGreater(f["stress_gap_rooms_if_one_cleaner_out"], 0)      # ... but one absence would not
        self.assertEqual(c.severity_hint, "medium")
        self.assertEqual(c.t, pd.Timestamp("2026-01-10T06:00:00Z"))          # first 07:00 local that sees the day
        self.assertLessEqual(set(c.affected_assets), {"BUILDING", *SURGE_ROOMS})

    def test_sick_cleaner_known_only_from_status_known_utc(self):
        known = "2026-01-12T05:00:00Z"
        obs = make_obs(baseline_bookings() + surge_bookings(), roster(sick={("C1", SURGE_DAY): known}))
        cands = on_day(detect_weekend(obs), SURGE_DAY)
        self.assertEqual(len(cands), 2)                                       # early warning + the revision
        warn, gap = cands
        self.assertTrue(warn.features["contingent"])
        self.assertLess(warn.t, pd.Timestamp(known))                          # the sick call was not yet known
        self.assertEqual(warn.features["cleaners_absent_known"], [])
        self.assertEqual(gap.t, pd.Timestamp(known))
        f = gap.features
        self.assertFalse(f["contingent"])
        self.assertEqual(f["cleaners_absent_known"], ["C1"])
        self.assertEqual(f["cleaners_scheduled"], 2)

    def test_gap_is_explicit_and_never_exceeds_known_capacity(self):
        obs = make_obs(baseline_bookings() + surge_bookings(),
                       roster(sick={("C1", SURGE_DAY): "2026-01-12T05:00:00Z"}))
        f = detect_weekend(obs)[-1].features
        # demand 16 turnovers / 19 departures against 2 cleaners: 12 rooms before 15:00, 14 by shift end
        self.assertEqual(f["capacity_rooms_before_arrivals"], 12)
        self.assertEqual(f["capacity_rooms_by_shift_end"], 14)
        self.assertEqual(f["gap_rooms"], max(16 - 12, 19 - 14))
        self.assertEqual(f["gap_minutes"], round(f["gap_rooms"] * f["median_clean_minutes_used"], 1))
        self.assertEqual(f["arrivals_at_risk"], min(f["gap_rooms"], f["same_day_turnovers"]))
        # on-call: one person, 45 min to site + 45 min per room, 15:30-22:00 local = 4 rooms, only for the gap
        self.assertEqual(f["oncall_rooms_possible"], 4)
        self.assertLessEqual(f["oncall_would_cover_rooms"], min(f["gap_rooms"], f["oncall_rooms_possible"]))
        self.assertEqual(f["oncall_would_cover_rooms"] + f["residual_gap_after_oncall"], f["gap_rooms"])
        self.assertEqual(f["residual_gap_after_oncall"], 1)                   # unmet even after on-call
        self.assertEqual(f["robot_scope"], "floor_only")

    def test_candidate_per_affected_day_and_no_spam(self):
        obs = make_obs(baseline_bookings() + surge_bookings(), roster())
        cands = detect_weekend(obs)
        days = [c.features["target_date_local"] for c in cands]
        self.assertEqual(len(days), len(set(days)))                           # one raise per day
        em = build_episodes(cands)
        self.assertEqual(len(em), len(cands))
        self.assertTrue(all(not e.is_followup for e in em))

    def test_cancelled_bookings_do_not_make_a_surge(self):
        obs = make_obs(baseline_bookings() + surge_bookings(status="cancelled"), roster())
        self.assertEqual(detect_weekend(obs), [])

    def test_roster_not_published_means_no_claim(self):
        obs = make_obs(baseline_bookings() + surge_bookings(), roster(last_day="2026-01-10"))
        self.assertEqual(on_day(detect_weekend(obs), SURGE_DAY), [])

    def test_bookings_created_later_are_not_used_earlier(self):
        created = "2026-01-11T12:00:00Z"
        obs = make_obs(baseline_bookings() + surge_bookings(created=created), roster())
        cands = detect_weekend(obs)
        self.assertTrue(cands)
        self.assertTrue(all(c.t >= pd.Timestamp(created) for c in cands))

    def test_future_information_does_not_change_earlier_candidates(self):
        cut = pd.Timestamp("2026-01-11T12:00:00Z")
        full_rows = baseline_bookings() + surge_bookings() + [
            dict(booking_id="LATE1", room_id=ROOMS[0], created_utc="2026-01-11T18:00:00Z",
                 arrival_utc="2026-01-13T14:00:00Z", departure_utc="2026-01-14T10:00:00Z", status="confirmed")]
        sick = {("C1", SURGE_DAY): "2026-01-12T05:00:00Z"}
        full = detect_weekend(make_obs(full_rows, roster(sick=sick)))
        # the same world as it looked at `cut`: later bookings unseen, the sick call not yet reported
        early_rows = [r for r in full_rows if pd.Timestamp(r["created_utc"]) <= cut]
        early = detect_weekend(make_obs(early_rows, roster(), end=str(cut)))
        key = lambda cs: [(c.t, c.features["target_date_local"], c.features["gap_rooms"],  # noqa: E731
                           c.features["same_day_turnovers"], c.features["contingent"]) for c in cs if c.t <= cut]
        self.assertEqual(key(full), key(early))
        self.assertTrue(key(full))

    def test_robots_are_floor_only_and_only_when_on_the_roster(self):
        with_r = detect_weekend(make_obs(baseline_bookings() + surge_bookings(), roster()))[0].features
        no_r = detect_weekend(make_obs(baseline_bookings() + surge_bookings(), roster(robots=())))[0].features
        self.assertEqual(with_r["robots_in_service"], 2)
        self.assertGreater(with_r["robot_floor_jobs"], 0)
        self.assertEqual((no_r["robots_in_service"], no_r["robot_floor_jobs"]), (0, 0))
        self.assertEqual(with_r["capacity_rooms_before_arrivals"], no_r["capacity_rooms_before_arrivals"])  # no invented capacity

    def test_waiting_guest_requests_are_attached(self):
        req = [dict(request_id="GR1", timestamp_utc="2026-01-09T10:00:00Z", room_id=SURGE_ROOMS[0],
                    request_type="early_checkin"),
               dict(request_id="GR2", timestamp_utc="2026-01-09T10:00:00Z", room_id="R001", request_type="early_checkin")]
        c = detect_weekend(make_obs(baseline_bookings() + surge_bookings(), roster(), requests=req))[0]
        self.assertEqual(c.guest_request_ids, ["GR1"])


class TestWeekendRulesAndPlaybook(unittest.TestCase):
    def setUp(self):
        sick = {("C1", SURGE_DAY): "2026-01-12T05:00:00Z"}
        cands = detect_weekend(make_obs(baseline_bookings() + surge_bookings(), roster(sick=sick)))
        self.warn, self.gap = cands[0], cands[-1]

    def _decisions(self, cand, **override):
        pk = cand.packet()
        u = rule_based_incident(pk)
        u.update(override)
        return u, decide(u, pk)

    def test_rule_branch_reports_the_unmet_gap(self):
        u, _ = self._decisions(self.gap)
        self.assertEqual(u["issue_type"], "occupancy_surge")
        self.assertEqual(u["likely_cause"], "staffing")
        self.assertEqual(u["severity"], "critical")                            # 1 room unmet even with on-call
        self.assertIn("Unmet gap: 5 room(s)", u["reason"])
        self.assertIn("1 room(s) stay unmet", u["reason"])

    def test_warning_is_medium_and_names_the_stress_gap(self):
        u, _ = self._decisions(self.warn)
        self.assertEqual((u["issue_type"], u["severity"]), ("occupancy_surge", "medium"))
        self.assertIn("no slack", u["reason"])

    def test_gap_day_produces_the_four_actions(self):
        _, ds = self._decisions(self.gap)
        by = {d.record["action"]: d for d in ds}
        self.assertEqual(set(by), {"schedule_cleaning", "dispatch_robot", "notify_guest", "request_human_approval"})
        self.assertTrue(by["request_human_approval"].record["requires_human_approval"])
        self.assertFalse(by["schedule_cleaning"].record["requires_human_approval"])
        self.assertEqual(by["dispatch_robot"].notes, [])                       # floor-only: guard keeps it a robot job
        self.assertTrue(all(d.record["issue_type"] == "occupancy_surge" for d in ds))
        self.assertTrue(all(d.record["affected_assets"][0] == "BUILDING" for d in ds))

    def test_contingent_warning_never_calls_on_call_or_guests(self):
        _, ds = self._decisions(self.warn)
        self.assertEqual({d.record["action"] for d in ds}, {"schedule_cleaning", "dispatch_robot"})

    def test_no_robot_job_without_robots(self):
        cands = detect_weekend(make_obs(baseline_bookings() + surge_bookings(), roster(robots=())))
        _, ds = self._decisions(cands[0])
        self.assertNotIn("dispatch_robot", {d.record["action"] for d in ds})

    def test_low_confidence_only_flags(self):
        _, ds = self._decisions(self.gap, confidence=0.3)
        self.assertEqual([d.record["action"] for d in ds], ["flag_anomaly"])


if __name__ == "__main__":
    unittest.main()
