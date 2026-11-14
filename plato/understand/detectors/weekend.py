"""S05 unexpected-weekend / occupancy-surge detector.

There is no "event weekend" flag in the data, so the surge is inferred from what is *known at t*:
bookings with ``created_utc <= t`` give, per local day, the occupancy, arrivals, departures and the
same-day turnovers (a room that checks out and checks in again the same day). The surge is judged against
a baseline learnt from the same known bookings (median + scaled MAD over the days around t), not against
constants tuned to one dataset.

Demand is then forecast against the cleaning capacity that is actually known at t:
  * cleaners on ``scheduled`` shifts that are not sick / on vacation (``view.roster()`` honours
    ``status_known_utc``, so a sick call is invisible until it was reported),
  * minutes per room learnt from past cleaning events (reuses the turnaround detector's
    ``_median_clean_minutes`` / ``_cleaners_on_shift``),
  * rooms can only be cleaned once vacated, and same-day arrivals need a clean room by 15:00 local,
  * robots (roster-known) do floor passes only and never bathrooms; they are credited with time savings
    only if the history shows one, never as extra "room capacity",
  * the on-call housekeeper (ONCALL_1, 15:30-22:00 local, ~45 min to site, one room at a time, costly,
    waiting guests only) is reported as an *optional* mitigation that needs human approval; it is not
    counted in the staffed capacity and never invented beyond what one person can do.

A candidate is raised once per affected day, at the first evaluation that sees the problem (a second one
only if it gets materially worse, e.g. a cleaner calls in sick). Room-level conflicts are the turnaround
detector's job; this one reports the building-level unmet capacity gap (in rooms and minutes) and lists the
turnover rooms it concerns in ``affected_assets``. Everything is deterministic, rule-routed and causal.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from plato.observe.timeutil import at_local, to_local

from ..candidate import OCCUPANCY_SURGE, Candidate
from .turnaround import _cleaners_on_shift, _median_clean_minutes

BUILDING_ASSET = "BUILDING"
EVAL_LOCAL = "07:00"            # daily evaluation (start of the housekeeping shift)
HK_END_LOCAL = "15:30"
ARRIVAL_CUTOFF_LOCAL = "15:00"  # check-in from 15:00 local: same-day arrivals need a clean room by then
ONCALL_END_LOCAL = "22:00"
ONCALL_START_LOCAL = "15:30"
ONCALL_TRAVEL_MIN = 45
ROBOT_FLOOR_MIN = 30
LOOKAHEAD_DAYS = 2              # evaluate the current/next working day and the two after it
BASELINE_BACK_DAYS = 28
BASELINE_FWD_DAYS = 3
MIN_BASELINE_DAYS = 5
TIGHT_UTILISATION = 0.85        # contingent warning only when staffed capacity is at least this used
SURGE_K = 2.0                   # surge = median + max(K * scaled MAD, margin)
SURGE_MIN_MARGIN = {"occupancy": 4, "arrivals": 5, "departures": 5}
DEFAULT_READY_HOUR = 10.0       # local hour rooms become cleanable when no checkout history exists
MAX_ROOMS_LISTED = 20
RANK = {"medium": 1, "high": 2, "critical": 3}
EPOCH = pd.Timestamp("1970-01-01")


def _day_int(ts) -> int:
    return int((pd.Timestamp(ts).tz_localize(None).normalize() - EPOCH).days)


def _day_str(d: int) -> str:
    return (EPOCH + pd.Timedelta(days=int(d))).strftime("%Y-%m-%d")


def _local_day_array(s: pd.Series) -> np.ndarray:
    if s.empty:
        return np.zeros(0, dtype=int)
    loc = to_local(pd.to_datetime(s, utc=True)).dt.tz_localize(None).dt.normalize()
    return ((loc - EPOCH).dt.days).to_numpy(dtype=int)


def _hhmm(t) -> str:
    return to_local(t).strftime("%H:%M")


def _profile(b: pd.DataFrame, days: List[int]) -> Dict[int, Dict[str, Any]]:
    """Per local day: occupancy (night), arrivals, departures, same-day turnovers (+ their rooms)."""
    out: Dict[int, Dict[str, Any]] = {}
    if b.empty:
        return {d: {"occupancy": 0, "arrivals": 0, "departures": 0, "turnover_rooms": []} for d in days}
    ad, dd = _local_day_array(b["arrival_utc"]), _local_day_array(b["departure_utc"])
    room = b["room_id"].astype(str).to_numpy()
    for d in days:
        a, p = ad == d, dd == d
        out[d] = {"occupancy": int(((ad <= d) & (dd > d)).sum()), "arrivals": int(a.sum()),
                  "departures": int(p.sum()), "turnover_rooms": sorted(set(room[a]) & set(room[p]))}
    return out


def _baseline(prof: Dict[int, Dict[str, Any]], d0: int, data_start: int) -> Optional[Dict[str, Tuple[float, float]]]:
    """Median and scaled MAD of the known days around t. Days before the data window are skipped: the
    bookings file only holds stays that reach into the window, so those days look artificially empty."""
    days = [d for d in prof if max(d0 - BASELINE_BACK_DAYS, data_start) <= d <= d0 + BASELINE_FWD_DAYS]
    if len(days) < MIN_BASELINE_DAYS:
        return None
    res = {}
    for k in ("occupancy", "arrivals", "departures"):
        v = np.array([prof[d][k] for d in days], dtype=float)
        med = float(np.median(v))
        res[k] = (med, 1.4826 * float(np.median(np.abs(v - med))))
    return res


def _learn_ops(events: pd.DataFrame, t: pd.Timestamp) -> Dict[str, Any]:
    """Cleaning minutes (overall / robot-assisted / unassisted) and the typical checkout hour, from the
    last four weeks of events known at t."""
    ev = events
    if ev.empty or "type" not in ev.columns:
        overall = _median_clean_minutes(ev)
        return {"overall": overall, "with_robot": None, "without_robot": None, "ready_hour": DEFAULT_READY_HOUR}
    ev = ev[ev["timestamp_utc"] >= t - pd.Timedelta(days=28)]
    overall = _median_clean_minutes(ev)
    co = ev[ev["type"] == "checkout"]
    ready = DEFAULT_READY_HOUR
    if len(co) >= 8:
        loc = to_local(pd.to_datetime(co["timestamp_utc"], utc=True))
        ready = float(np.median(loc.dt.hour + loc.dt.minute / 60.0))
    ready = min(11.0, max(7.0, ready))
    with_r = without_r = None
    st = ev[ev["type"] == "cleaning_started"][["room_id", "timestamp_utc"]]
    rs = ev[ev["type"] == "robot_floor_cleaning_started"][["room_id", "timestamp_utc"]]
    if len(st) >= 10:
        m = st.merge(rs.rename(columns={"timestamp_utc": "ts_r"}), on="room_id", how="left")
        m["near"] = (m["ts_r"] - m["timestamp_utc"]).abs() < pd.Timedelta("3h")
        assisted = m[m["near"]][["room_id", "timestamp_utc"]].drop_duplicates()
        key = m.groupby(["room_id", "timestamp_utc"])["near"].any().reset_index()
        plain = key[~key["near"]][["room_id", "timestamp_utc"]]
        done = ev[ev["type"] == "cleaning_completed"][["room_id", "timestamp_utc"]]
        done = done.assign(type="cleaning_completed")

        def _med(starts: pd.DataFrame) -> Optional[float]:
            if len(starts) < 5:
                return None
            sub = pd.concat([starts.assign(type="cleaning_started"), done], ignore_index=True)
            return _median_clean_minutes(sub)

        with_r, without_r = _med(assisted), _med(plain)
        if with_r is not None and without_r is not None and with_r > without_r:
            with_r = without_r  # a robot never makes it slower; do not credit anything odd
    return {"overall": overall, "with_robot": with_r, "without_robot": without_r, "ready_hour": ready}


def _capacity(shifts: List[Tuple[pd.Timestamp, pd.Timestamp]], ready_from: pd.Timestamp,
              cutoff: pd.Timestamp, minutes: float) -> Tuple[int, int]:
    """Rooms the given cleaner shifts can finish: (by the arrival cutoff, by the end of the shift)."""
    crit = tot = 0
    for s, e in shifts:
        start = max(s, ready_from)
        crit += int(max(0.0, (min(e, cutoff) - start).total_seconds() / 60.0) // minutes)
        tot += int(max(0.0, (e - start).total_seconds() / 60.0) // minutes)
    return crit, tot


def _day_rows(roster: pd.DataFrame, role: str, day: int) -> pd.DataFrame:
    if roster.empty:
        return roster
    r = roster[roster["role"] == role]
    if r.empty:
        return r
    return r[_local_day_array(r["start_utc"]) == day]


def _assess(obs, view, t: pd.Timestamp, day: int, prof: Dict[int, Dict[str, Any]],
            base: Optional[Dict[str, Tuple[float, float]]], ops: Dict[str, Any],
            roster: pd.DataFrame, requests: pd.DataFrame) -> Optional[Dict[str, Any]]:
    """Forecast one target day as of t. Returns a feature dict with a ``level`` or None if no problem."""
    cleaner_rows = _day_rows(roster, "cleaner", day)
    if cleaner_rows.empty:
        return None  # roster not published for that day: capacity unknown, so say nothing (never invent)
    p = prof[day]
    ds = _day_str(day)
    sched = cleaner_rows[cleaner_rows["status"] == "scheduled"].sort_values("start_utc")
    sched = sched.drop_duplicates("resource_id", keep="first")
    absent = cleaner_rows[cleaner_rows["status"].isin(["sick", "vacation", "out_of_service"])]
    sick_known = sorted(absent["resource_id"].astype(str).unique().tolist())
    shifts = [(pd.Timestamp(r.start_utc), pd.Timestamp(r.end_utc)) for r in sched.itertuples(index=False)]
    robot_rows = _day_rows(roster, "robot", day)
    robots = int(robot_rows[robot_rows["status"] == "scheduled"]["resource_id"].nunique()) if len(robot_rows) else 0

    # --- surge versus the baseline learnt from the same known bookings
    reasons: List[str] = []
    base_feat: Dict[str, Any] = {}
    if base is not None:
        for k in ("occupancy", "arrivals", "departures"):
            med, smad = base[k]
            thr = med + max(SURGE_K * smad, SURGE_MIN_MARGIN[k])
            base_feat[f"baseline_{k}"] = round(med, 1)
            base_feat[f"surge_threshold_{k}"] = round(thr, 1)
            if p[k] >= thr:
                reasons.append(k)
    surge = bool(reasons)
    baseline_known = base is not None

    # --- demand versus the staffed capacity known at t
    minutes = ops["with_robot"] if (robots and ops["with_robot"]) else (ops["without_robot"] or ops["overall"])
    if not robots:
        minutes = ops["without_robot"] or ops["overall"]
    minutes = float(minutes)
    ready_from = at_local(ds, f"{int(ops['ready_hour']):02d}:{int(round((ops['ready_hour'] % 1) * 60)) % 60:02d}")
    cutoff = at_local(ds, ARRIVAL_CUTOFF_LOCAL)
    cap_crit, cap_tot = _capacity(shifts, ready_from, cutoff, minutes)
    turnovers, departures = len(p["turnover_rooms"]), p["departures"]
    gap_crit, gap_tot = max(0, turnovers - cap_crit), max(0, departures - cap_tot)
    gap = max(gap_crit, gap_tot)
    utilisation = max(turnovers / cap_crit if cap_crit else (1.0 if turnovers else 0.0),
                      departures / cap_tot if cap_tot else (1.0 if departures else 0.0))

    # worst plausible single loss (one more cleaner out) as a resilience check; not an invented gap
    stress_gap = 0
    if shifts:
        best = max(range(len(shifts)), key=lambda i: _capacity([shifts[i]], ready_from, cutoff, minutes)[1])
        rest = shifts[:best] + shifts[best + 1:]
        c2, t2 = _capacity(rest, ready_from, cutoff, minutes)
        stress_gap = max(0, turnovers - c2, departures - t2)

    if gap > 0 and (surge or not baseline_known):
        contingent = False
    elif surge and stress_gap > 0 and utilisation >= TIGHT_UTILISATION:
        contingent = True
    else:
        return None

    # --- optional mitigations, each limited to what is really known
    oncall_rooms = int(((pd.Timestamp(at_local(ds, ONCALL_END_LOCAL)) - pd.Timestamp(at_local(ds, ONCALL_START_LOCAL)))
                        .total_seconds() / 60.0) // (ONCALL_TRAVEL_MIN + minutes))
    covered = min(gap, oncall_rooms)
    residual = max(0, gap - oncall_rooms)
    floor_jobs = 0
    if robots:
        floor_jobs = int(min(max(departures, 1), robots * (480 // ROBOT_FLOOR_MIN)))
    saving = None
    if ops["with_robot"] is not None and ops["without_robot"] is not None:
        saving = round(max(0.0, ops["without_robot"] - ops["with_robot"]), 1)
    oncall_first = oncall_last = None
    if covered:
        base_t = pd.Timestamp(at_local(ds, ONCALL_START_LOCAL))
        step = pd.Timedelta(minutes=ONCALL_TRAVEL_MIN + minutes)
        oncall_first = _hhmm(base_t + step)
        oncall_last = _hhmm(base_t + step * covered)

    level = "medium" if contingent else ("critical" if residual > 0 else "high")
    feats: Dict[str, Any] = {
        "target_date_local": ds,
        "weekday": pd.Timestamp(ds).day_name(),
        "lead_hours": round((at_local(ds, "07:00") - t).total_seconds() / 3600.0, 1),
        "level": level,
        "contingent": contingent,
        "surge_basis": reasons if baseline_known else ["no_baseline"],
        "occupancy_rooms": p["occupancy"],
        "occupancy_pct": round(100.0 * p["occupancy"] / max(1, len(obs.building.rooms)), 1),
        "arrivals": p["arrivals"],
        "departures": departures,
        "same_day_turnovers": turnovers,
        "cleaners_scheduled": len(shifts),
        "cleaners_scheduled_on_shift_check": _cleaners_on_shift(roster, at_local(ds, "12:00")),
        "cleaners_absent_known": sick_known,
        "robots_in_service": robots,
        "robot_scope": "floor_only",
        "bathroom_cleaning_needed": False,  # the robot task carries no bathroom work (bathrooms stay with staff)
        "robot_floor_jobs": floor_jobs,
        "robot_saving_min_per_room": saving,
        "median_clean_minutes_used": round(minutes, 1),
        "rooms_cleanable_from_local": f"{int(ops['ready_hour']):02d}:{int(round((ops['ready_hour'] % 1) * 60)) % 60:02d}",
        "capacity_rooms_before_arrivals": cap_crit,
        "capacity_rooms_by_shift_end": cap_tot,
        "capacity_utilisation": round(utilisation, 2),
        "gap_rooms": gap,
        "gap_minutes": round(gap * minutes, 1),
        "arrivals_at_risk": min(gap, turnovers),
        "stress_gap_rooms_if_one_cleaner_out": stress_gap,
        "oncall_rooms_possible": oncall_rooms,
        "oncall_would_cover_rooms": covered,
        "oncall_ready_window_local": f"{oncall_first}-{oncall_last}" if oncall_first else None,
        "residual_gap_after_oncall": residual,
        "oncall_needed": bool(covered),
        "oncall_window_local": f"{ONCALL_START_LOCAL}-{ONCALL_END_LOCAL}",
        "oncall_scope": "waiting_guests_only_one_room_at_a_time",
        "local_time": to_local(t).strftime("%Y-%m-%d %H:%M %Z"),
    }
    feats.update(base_feat)
    rooms = list(p["turnover_rooms"])
    gids: List[str] = []
    if len(requests) and rooms and "room_id" in requests.columns:
        hit = requests[requests["room_id"].isin(rooms) & requests["request_type"].isin(
            ["early_checkin", "waiting_for_room"])]
        gids = hit["request_id"].astype(str).tolist()[:10]
    feats["_rooms"] = rooms
    feats["_guest_request_ids"] = gids
    return feats


def _eval_times(obs) -> List[pd.Timestamp]:
    """Deterministic evaluation moments: daily 07:00 local, plus whenever a cleaner/robot absence
    becomes known (status_known_utc). Only moments inside the data span are used."""
    grid = obs.grid
    if len(grid) == 0:
        return []
    t0, t1 = pd.Timestamp(grid[0]), pd.Timestamp(grid[-1])
    times = set()
    d = _day_int(to_local(t0))
    d_end = _day_int(to_local(t1))
    for day in range(d, d_end + 1):
        t = at_local(_day_str(day), EVAL_LOCAL)
        if t0 <= t <= t1:
            times.add(pd.Timestamp(t))
    r = obs.roster
    if len(r) and "status_known_utc" in r.columns:
        a = r[(r["status"] != "scheduled") & r["role"].isin(["cleaner", "robot"])]
        for ts in a["status_known_utc"].dropna():
            ts = pd.Timestamp(ts)
            if t0 <= ts <= t1:
                times.add(ts)
    return sorted(times)


def detect_weekend(obs) -> List[Candidate]:
    out: List[Candidate] = []
    bookings_all = obs.bookings
    if bookings_all is None or len(bookings_all) == 0 or "arrival_utc" not in bookings_all.columns:
        return out
    data_start = _day_int(to_local(pd.Timestamp(obs.grid[0]))) if len(obs.grid) else 0
    emitted: Dict[int, int] = {}  # target day -> highest rank already raised
    ops_cache: Dict[int, Dict[str, Any]] = {}
    for t in _eval_times(obs):
        view = obs.asof(t)
        b = view.bookings()
        if "status" in b.columns:  # same treatment as turnaround; the file has no cancellation time (see AsOf)
            b = b[b["status"] != "cancelled"]
        if b.empty:
            continue
        roster = view.roster()
        if roster.empty:
            continue
        d_now = _day_int(to_local(t))
        first = d_now if (to_local(t).hour + to_local(t).minute / 60.0) < 15.0 else d_now + 1
        targets = list(range(first, first + LOOKAHEAD_DAYS + 1))
        dates = list(range(d_now - BASELINE_BACK_DAYS, d_now + BASELINE_FWD_DAYS + 1)) + targets
        prof = _profile(b, sorted(set(dates)))
        base = _baseline(prof, d_now, data_start)
        day_key = _day_int(t)
        if day_key not in ops_cache:  # learnt once per UTC day, from events known at the first eval of that day
            ops_cache[day_key] = _learn_ops(view.events(), t)
        ops = ops_cache[day_key]
        requests = view.guest_requests()
        new: List[Tuple[int, Dict[str, Any]]] = []
        for day in targets:
            f = _assess(obs, view, t, day, prof, base, ops, roster, requests)
            if f is None:
                continue
            if RANK[f["level"]] > emitted.get(day, 0):
                new.append((day, f))
        if not new:
            continue
        for day, f in new:
            emitted[day] = RANK[f["level"]]
        day, f = new[0]
        rooms = f.pop("_rooms")
        gids = f.pop("_guest_request_ids")
        if len(new) > 1:
            f["also_affected_days"] = [
                {"date": g["target_date_local"], "gap_rooms": g["gap_rooms"], "contingent": g["contingent"],
                 "stress_gap_rooms": g["stress_gap_rooms_if_one_cleaner_out"]} for _, g in new[1:]]
        for _, g in new[1:]:
            g.pop("_rooms", None)
            g.pop("_guest_request_ids", None)
        affected = [BUILDING_ASSET] + rooms[:MAX_ROOMS_LISTED]
        out.append(Candidate(detector=OCCUPANCY_SURGE, asset=BUILDING_ASSET, t=t,
                             severity_hint=f["level"], features=f, affected_assets=affected,
                             guest_request_ids=gids, route="rule"))
    out.sort(key=lambda c: (c.t, c.asset))
    return out
