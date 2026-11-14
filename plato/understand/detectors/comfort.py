"""S03 energy-vs-comfort detector.

Comfort comes first: an occupied room that stays below the 20 C comfort floor is a problem whatever the
energy price. The detector tracks a cold episode per room with hysteresis (it opens after the room has
been cold for a while and closes only once the room has recovered for a while, so a brief wobble does not
spam alerts), and it records how many rooms are cold at the same time so UNDERSTAND can tell a single-room
fault from a plant-wide one. When several rooms are cold together it raises one building-level candidate
against HP1 instead of many room candidates.

Energy evidence (outdoor temperature, the next-24 h forecast, the day-ahead tariff, the heat-pump status)
is attached so DECIDE can weigh comfort against cost - but the comfort floor itself is never traded away.
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

from plato.observe.quality import Q_OK

from ..candidate import COMFORT, Candidate
from ..causal import find_runs, run_length_so_far
from .common import bms_alarms_asof, work_orders_asof

COLD_ONSET_STEPS = 4       # 1 h below the floor before an episode opens
RECOVER_STEPS = 4          # 1 h back inside the band before it closes (hysteresis)
PLANT_MIN_ROOMS = 3        # this many rooms cold at once -> one building/HP1 candidate
PERIODIC_STEPS = 8         # re-emit every 2 h while the episode persists (episode builder merges)


SETPOINT_MARGIN = 0.5  # the room must be this far below its own setpoint too (a real heating shortfall)


def _cold_on_matrix(obs, lo: float) -> pd.DataFrame:
    """Stateful, causal per-room 'in a cold episode' mask. Opens after COLD_ONSET_STEPS below the comfort
    floor AND below the room's own setpoint while occupied (a genuine shortfall, not a guest who turned the
    setpoint down); closes after RECOVER_STEPS back inside the band."""
    temp = obs.rooms.values["temperature_c"]
    q = obs.rooms.quality["temperature_c"]
    sp = obs.rooms.values["setpoint_c"]
    occ = obs.rooms.occupied
    rooms = list(temp.columns)
    on = np.zeros((len(temp), len(rooms)), dtype=bool)
    for j, room in enumerate(rooms):
        tv = temp[room].to_numpy(dtype=float)
        spv = sp[room].to_numpy(dtype=float)
        ok = (q[room].to_numpy() == Q_OK) & occ[room].to_numpy(dtype=bool)
        # below the comfort floor and failing to reach the wanted setpoint (setpoint unknown -> use the floor)
        want = np.where(np.isnan(spv), lo, np.maximum(spv, lo))
        below = ok & (tv < lo) & (tv < want - SETPOINT_MARGIN)
        recovered = (~np.isnan(tv)) & (tv >= lo)
        state = False
        cold_run = rec_run = 0
        for i in range(len(tv)):
            cold_run = cold_run + 1 if below[i] else 0
            rec_run = rec_run + 1 if recovered[i] else 0
            if not state and cold_run >= COLD_ONSET_STEPS:
                state = True
            elif state and rec_run >= RECOVER_STEPS:
                state = False
            on[i, j] = state
    return pd.DataFrame(on, index=temp.index, columns=rooms)


def _hp_status_counts(obs, t, window=pd.Timedelta("24h")) -> Dict[str, int]:
    st = obs.equipment.status
    col = "HP1.status"
    if col not in st.columns:
        return {}
    s = st[col]
    s = s[(s.index <= t) & (s.index >= t - window)].dropna()
    return {str(k): int(v) for k, v in s.value_counts().items()}


def _forecast_min_24h(view, t) -> float:
    f = view.forecast()
    if f.empty or "outdoor_c_forecast" not in f.columns:
        return float("nan")
    nxt = f[(f["target_utc"] >= t) & (f["target_utc"] <= t + pd.Timedelta("24h"))]
    return float(nxt["outdoor_c_forecast"].min()) if len(nxt) else float("nan")


def _tariff_now_and_next(view, t):
    tr = view.tariff()
    if tr.empty or "price_eur_kwh" not in tr.columns:
        return float("nan"), float("nan")
    now = tr[tr["timestamp_utc"] <= t]
    nxt = tr[(tr["timestamp_utc"] >= t) & (tr["timestamp_utc"] <= t + pd.Timedelta("24h"))]
    pn = float(now["price_eur_kwh"].iloc[-1]) if len(now) else float("nan")
    return pn, (float(nxt["price_eur_kwh"].max()) if len(nxt) else float("nan"))


def _series_at(obs, var, room, t):
    col = obs.rooms.values[var][room]
    pos = col.index.searchsorted(t, side="right") - 1
    if pos < 0:
        return float("nan")
    v = col.iloc[pos]
    return float(v) if isinstance(v, (int, float, np.floating)) and not pd.isna(v) else float("nan")


def _cold_requests(view, room: str) -> List[str]:
    gr = view.guest_requests()
    if gr.empty:
        return []
    m = gr[(gr["request_type"] == "temperature_cold") & (gr["room_id"].isin([room, "BUILDING"]))]
    return m["request_id"].tolist()


def detect_comfort(obs) -> List[Candidate]:
    lo, hi = obs.building.comfort_c
    temp = obs.rooms.values["temperature_c"]
    grid = temp.index
    rooms = list(temp.columns)
    on = _cold_on_matrix(obs, lo)
    on_arr = on.to_numpy()
    cold_now = on_arr.sum(axis=1)
    plant = cold_now >= PLANT_MIN_ROOMS
    out: List[Candidate] = []

    # ---- building / plant-wide cold ----------------------------------------------------------
    plant_runlen = run_length_so_far(plant)
    for s, e in find_runs(plant):
        for i in range(s, e):
            if i != s and (i - s) % PERIODIC_STEPS != 0:
                continue
            t = grid[i]
            view = obs.asof(t)
            cold_rooms = [rooms[j] for j in range(len(rooms)) if on_arr[i, j]]
            pn, pmax = _tariff_now_and_next(view, t)
            feats = {
                "rooms_cold_now": int(cold_now[i]),
                "cold_rooms": cold_rooms[:12],
                "outdoor_c": _weather_at(obs, t),
                "forecast_min_24h_c": _forecast_min_24h(view, t),
                "hp_status_counts_24h": _hp_status_counts(obs, t),
                "hp_heat_output_kw": _equip_at(obs, "HP1.heat_output_kw", t),
                "tariff_now_eur_kwh": pn,
                "tariff_max_next_24h_eur_kwh": pmax,
                "duration_h": round((i - s + 1) * 0.25, 2),
                "note": "several occupied rooms below the comfort floor at once: likely a heating-plant problem",
            }
            out.append(Candidate(detector=COMFORT, asset="HP1", t=t, start=grid[s], severity_hint="high",
                                 features=feats, affected_assets=["HP1"] + cold_rooms,
                                 work_orders=work_orders_asof(obs, t, ["HP1", "AHU1"]),
                                 bms_alarms=bms_alarms_asof(obs, t, ["HP1"] + cold_rooms)))

    # ---- single-room cold (only while it is NOT plant-wide, to avoid double counting) --------
    for j, room in enumerate(rooms):
        room_on = on_arr[:, j] & ~plant
        for s, e in find_runs(room_on):
            for i in range(s, e):
                if i != s and (i - s) % PERIODIC_STEPS != 0:
                    continue
                t = grid[i]
                view = obs.asof(t)
                gids = _cold_requests(view, room)
                pn, pmax = _tariff_now_and_next(view, t)
                tmin = float(np.nanmin(temp[room].to_numpy()[s:i + 1])) if i >= s else float("nan")
                feats = {
                    "temp_c": _series_at(obs, "temperature_c", room, t),
                    "min_temp_c": tmin,
                    "setpoint_c": _series_at(obs, "setpoint_c", room, t),
                    "window_open": bool(_series_at(obs, "window_open", room, t) == 1.0),
                    "occupied": True,
                    "guest_cold_requests": len(gids),
                    "rooms_cold_now": int(cold_now[i]),
                    "outdoor_c": _weather_at(obs, t),
                    "forecast_min_24h_c": _forecast_min_24h(view, t),
                    "hp_status_counts_24h": _hp_status_counts(obs, t),
                    "tariff_now_eur_kwh": pn,
                    "tariff_max_next_24h_eur_kwh": pmax,
                    "comfort_band_c": [lo, hi],
                    "duration_h": round((i - s + 1) * 0.25, 2),
                }
                consent = bool(len(_cold_requests_consent(view, room)))
                sev = "high" if len(gids) else "medium"
                out.append(Candidate(detector=COMFORT, asset=room, t=t, start=grid[s], severity_hint=sev,
                                     features=feats, affected_assets=[room], guest_request_ids=gids,
                                     requires_guest_consent=consent,
                                     bms_alarms=bms_alarms_asof(obs, t, [room])))
    out.sort(key=lambda c: (c.t, c.asset))
    return out


def _cold_requests_consent(view, room: str) -> List[str]:
    gr = view.guest_requests()
    if gr.empty or "requires_guest_consent" not in gr.columns:
        return []
    m = gr[(gr["request_type"] == "temperature_cold") & (gr["room_id"].isin([room, "BUILDING"]))
           & (gr["requires_guest_consent"].astype(str) == "required")]
    return m["request_id"].tolist()


def _weather_at(obs, t) -> float:
    w = obs.weather.values
    if "outdoor_c" not in w.columns:
        return float("nan")
    s = w["outdoor_c"]
    pos = s.index.searchsorted(t, side="right") - 1
    return float(s.iloc[pos]) if pos >= 0 and not pd.isna(s.iloc[pos]) else float("nan")


def _equip_at(obs, series, t) -> float:
    w = obs.equipment.wide.values
    if series not in w.columns:
        return float("nan")
    s = w[series]
    pos = s.index.searchsorted(t, side="right") - 1
    return float(s.iloc[pos]) if pos >= 0 and not pd.isna(s.iloc[pos]) else float("nan")
