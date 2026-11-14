"""Level 2 energy-optimisation detector (day-ahead battery shifting and pre-heating).

Once a day, when the next day's tariff becomes known (published 13:00 local on the previous day), look at
the next 24 h and ask: are there cheap and expensive hours far enough apart to make load shifting worth
proposing? If so, raise ONE low-priority proposal for that day:
  * charge the battery in the cheap hours and discharge it in the expensive hours,
  * pre-heat in a cheap hour before the forecast says it will turn cold.

Causal on purpose: tariff rows with ``published_utc <= t`` (``view.tariff()``), the forecast issued at or
before ``t`` (``view.forecast()``), and PV / load / battery SOC / measured weather read only at indices
``<= t``. The PV and load "tomorrow" proxy is simply the same hour yesterday (persistence), never data
from after ``t``.

Comfort comes first: ``comfort_at_risk`` is set from the comfort detector's own cold-episode mask
(``_cold_on_matrix``), so the safety guard turns ``optimize_energy`` into ``defer_action`` while any
occupied room is cold. Savings are only ever a clearly labelled SIMULATED estimate inside ``features``
(never a measurement), and only when the cheap/expensive spread is meaningful. No more than one proposal
is emitted per local day, so the detector cannot produce alert spam.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from plato.observe.timeutil import to_local

from ..candidate import ENERGY, Candidate
from .comfort import _cold_on_matrix

HORIZON_H = 24
MIN_PRICED_HOURS = 12        # fewer known hourly prices than this -> no plan (bad / partial tariff)
CHEAP_Q = 0.25               # cheapest / most expensive quarter of the known hours
MIN_SPREAD_EUR_KWH = 0.05    # mean(expensive hours) - mean(cheap hours) must be at least this ...
MIN_SPREAD_RATIO = 1.20      # ... and at least this ratio (must beat the 0.9 round-trip loss with margin)
MIN_SAVING_EUR = 0.5         # smaller simulated savings are not worth a proposal
COLD_DROP_C = 3.0            # forecast this much colder than the last 24 h measured mean = cold spell
PREHEAT_MAX_DELTA_C = 1.0    # pre-heat by at most this much, always inside the comfort band
DEFAULT_RTE = 0.9
SAVINGS_LABEL = ("SIMULATED estimate from an hourly day-ahead dispatch model (published tariff, 0.9 round-trip "
                 "efficiency, SOC bounds, persistence load/PV). Not a measurement and not guaranteed.")


def _value_at(df: pd.DataFrame, col: str, t) -> float:
    if col not in df.columns:
        return float("nan")
    s = df[col]
    pos = s.index.searchsorted(t, side="right") - 1
    if pos < 0:
        return float("nan")
    v = s.iloc[pos]
    return float(v) if not pd.isna(v) else float("nan")


def _hour_mean(df: pd.DataFrame, col: str, start, end) -> float:
    """Mean of cleaned values with start <= index < end (callers keep end <= t). NaN if nothing is known."""
    if col not in df.columns:
        return float("nan")
    s = df[col]
    lo = s.index.searchsorted(start, side="left")
    hi = s.index.searchsorted(end, side="left")
    v = s.iloc[lo:hi].dropna()
    return float(v.mean()) if len(v) else float("nan")


def _horizon_prices(view, t) -> pd.DataFrame:
    """Known hourly prices for the next 24 h (hour containing t onwards), unknown prices dropped."""
    tr = view.tariff()
    if tr.empty or "price_eur_kwh" not in tr.columns:
        return pd.DataFrame(columns=["timestamp_utc", "price_eur_kwh"])
    h0 = t.floor("h")
    m = (tr["timestamp_utc"] >= h0) & (tr["timestamp_utc"] < h0 + pd.Timedelta(hours=HORIZON_H))
    out = tr.loc[m, ["timestamp_utc", "price_eur_kwh"]].dropna(subset=["price_eur_kwh"])
    return out.sort_values("timestamp_utc").reset_index(drop=True)


def _battery_params(obs):
    b = obs.building
    lo, hi = getattr(b, "soc_bounds_kwh", (10.0, 95.0))
    max_kw = float(getattr(b, "battery_max_kw", 40.0))
    rte = DEFAULT_RTE
    for a in (getattr(b, "raw", {}) or {}).get("building", {}).get("assets", []):
        if a.get("id") == "BAT1" and a.get("round_trip_efficiency"):
            rte = float(a["round_trip_efficiency"])
    return float(lo), float(hi), max_kw, rte


def _battery_plan(obs, t, prices: pd.DataFrame, cheap: np.ndarray, expensive: np.ndarray, soc0: float):
    """Chronological hourly dispatch: charge from the grid in cheap hours, discharge ONLY energy this plan
    stored into expensive hours (limited by a persistence net-load proxy, so we never export). The energy
    already in the battery stays untouched as a reserve. Returns None when it is not possible."""
    if np.isnan(soc0):
        return None
    lo, hi, max_kw, rte = _battery_params(obs)
    eta = float(np.sqrt(rte))
    vals = obs.energy.wide.values
    soc = float(min(max(soc0, lo), hi))
    stored = 0.0                      # kWh in the battery that came from this plan
    charge_kwh = discharge_kwh = cost = revenue = 0.0
    h0 = t.floor("h")
    hours_c: List[str] = []
    hours_d: List[str] = []
    for k, row in enumerate(prices.itertuples(index=False)):
        p = float(row.price_eur_kwh)
        ts = pd.Timestamp(row.timestamp_utc)
        if cheap[k]:
            grid_kwh = min(max_kw, max(0.0, (hi - soc) / eta))
            if grid_kwh > 1e-9:
                soc += grid_kwh * eta
                stored += grid_kwh * eta
                charge_kwh += grid_kwh
                cost += grid_kwh * p
                hours_c.append(ts.strftime("%Y-%m-%dT%H:%MZ"))
        elif expensive[k] and stored > 1e-9:
            # same hour yesterday (<= t because the horizon is < 24 h ahead): causal net-load proxy
            ago = ts - pd.Timedelta(hours=24)
            end = min(ago + pd.Timedelta(hours=1), t + pd.Timedelta(minutes=15))
            load = _hour_mean(vals, "building_load_kw", ago, end)
            pv = _hour_mean(vals, "pv_kw", ago, end)
            net = load - (0.0 if np.isnan(pv) else pv) if not np.isnan(load) else float("nan")
            cap = 0.0 if np.isnan(net) else min(max_kw, max(0.0, net))  # unknown load -> credit nothing
            out_kwh = min(cap, stored * eta)
            if out_kwh > 1e-9:
                soc -= out_kwh / eta
                stored -= out_kwh / eta
                discharge_kwh += out_kwh
                revenue += out_kwh * p
                hours_d.append(ts.strftime("%Y-%m-%dT%H:%MZ"))
    if charge_kwh <= 0 or discharge_kwh <= 0:
        return None
    return {"charge_kwh": charge_kwh, "discharge_kwh": discharge_kwh, "saving_eur": revenue - cost,
            "charge_hours_utc": hours_c, "discharge_hours_utc": hours_d, "soc_start_kwh": soc0,
            "rte": rte}


def _preheat_plan(obs, view, t, prices: pd.DataFrame, cheap: np.ndarray) -> Optional[Dict[str, Any]]:
    """Cheap hours that lie BEFORE the first forecast-cold hour: pre-heat there instead of later."""
    f = view.forecast()
    if f.empty or "outdoor_c_forecast" not in f.columns:
        return None
    w = obs.weather.values
    if "outdoor_c" not in w.columns:
        return None
    meas = w["outdoor_c"]
    recent = meas[(meas.index > t - pd.Timedelta("24h")) & (meas.index <= t)].dropna()
    if len(recent) < 16:        # need ~4 h of measured weather to know what "colder than now" means
        return None
    ref = float(recent.mean())
    nxt = f[(f["target_utc"] >= t) & (f["target_utc"] <= t + pd.Timedelta(hours=HORIZON_H))].dropna(
        subset=["outdoor_c_forecast"])
    cold = nxt[nxt["outdoor_c_forecast"] <= ref - COLD_DROP_C]
    if cold.empty:
        return None
    first_cold = pd.Timestamp(cold["target_utc"].min())
    pre = [pd.Timestamp(ts) for ts, c in zip(prices["timestamp_utc"], cheap)
           if c and pd.Timestamp(ts) + pd.Timedelta(hours=1) <= first_cold]
    if not pre:
        return None
    return {"preheat_hours_utc": [x.strftime("%Y-%m-%dT%H:%MZ") for x in pre],
            "first_cold_hour_utc": first_cold.strftime("%Y-%m-%dT%H:%MZ"),
            "forecast_min_c": float(nxt["outdoor_c_forecast"].min()),
            "measured_mean_24h_c": ref,
            "preheat_max_delta_c": PREHEAT_MAX_DELTA_C}


def _decision_times(obs):
    """First grid step at/after each tariff publication time, at most one per local day."""
    tr = obs.tariff
    if tr.empty or "published_utc" not in tr.columns:
        return []
    grid = obs.grid
    pubs = sorted(pd.Timestamp(p) for p in tr["published_utc"].dropna().unique())
    seen = set()
    out = []
    for p in pubs:
        pos = grid.searchsorted(p, side="left")
        if pos >= len(grid):
            continue
        t = grid[pos]
        day = to_local(t).date()
        if day in seen:
            continue
        seen.add(day)
        out.append(t)
    return out


def detect_energy(obs) -> List[Candidate]:
    times = _decision_times(obs)
    if not times:
        return []
    lo_c, _ = obs.building.comfort_c
    cold_on = _cold_on_matrix(obs, lo_c)
    cold_idx = cold_on.index
    cold_arr = cold_on.to_numpy()
    cold_cols = list(cold_on.columns)
    ev = obs.energy.wide.values
    out: List[Candidate] = []

    for t in times:
        view = obs.asof(t)
        prices = _horizon_prices(view, t)
        if len(prices) < MIN_PRICED_HOURS:
            continue
        p = prices["price_eur_kwh"].to_numpy(dtype=float)
        # rank based (stable): the cheapest and the most expensive quarter of the known hours never overlap
        n_q = max(1, int(len(p) * CHEAP_Q))
        order = np.argsort(p, kind="stable")
        cheap = np.zeros(len(p), dtype=bool)
        expensive = np.zeros(len(p), dtype=bool)
        cheap[order[:n_q]] = True
        expensive[order[::-1][:n_q]] = True
        cheap_mean, exp_mean = float(p[cheap].mean()), float(p[expensive].mean())
        spread = exp_mean - cheap_mean
        if spread < MIN_SPREAD_EUR_KWH or exp_mean < MIN_SPREAD_RATIO * cheap_mean:
            continue                                  # spread not meaningful: nothing worth proposing

        soc0 = _value_at(ev, "battery_soc_kwh", t)
        plan = _battery_plan(obs, t, prices, cheap, expensive, soc0)
        if plan is not None and plan["saving_eur"] < MIN_SAVING_EUR:
            plan = None
        pre = _preheat_plan(obs, view, t, prices, cheap)
        if plan is None and pre is None:
            continue

        pos = cold_idx.searchsorted(t, side="right") - 1
        cold_rooms = [cold_cols[j] for j in range(len(cold_cols)) if pos >= 0 and cold_arr[pos, j]]
        feats: Dict[str, Any] = {
            "comfort_at_risk": bool(cold_rooms),
            "rooms_cold_now": len(cold_rooms),
            "cold_rooms": cold_rooms[:12],
            "horizon_h": HORIZON_H,
            "priced_hours_known": int(len(prices)),
            "cheap_hours_utc": [pd.Timestamp(x).strftime("%Y-%m-%dT%H:%MZ") for x in prices["timestamp_utc"][cheap]],
            "expensive_hours_utc": [pd.Timestamp(x).strftime("%Y-%m-%dT%H:%MZ")
                                    for x in prices["timestamp_utc"][expensive]],
            "price_cheap_mean_eur_kwh": cheap_mean,
            "price_expensive_mean_eur_kwh": exp_mean,
            "price_spread_eur_kwh": spread,
            "tariff_published_before_decision": True,
            "battery_soc_kwh_now": soc0,
            "battery_plan": None,
            "preheat_plan": pre,
            "route_note": "rule-based day-ahead proposal; at most one per local day",
        }
        if plan is not None:
            feats["battery_plan"] = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in plan.items()
                                     if k != "saving_eur"}
            feats["simulated_saving_eur"] = round(plan["saving_eur"], 2)
            feats["simulated_saving_label"] = SAVINGS_LABEL
            feats["expected_outcome"] = (
                f"SIMULATED estimate: shifting about {plan['discharge_kwh']:.0f} kWh from cheap to expensive "
                f"hours could save roughly {plan['saving_eur']:.2f} EUR over the next 24 h (not a measurement).")
        else:
            feats["expected_outcome"] = ("No savings estimate: only a pre-heating shift is proposed "
                                         "and no thermal model is available to price it.")
        assets = ["BAT1", "PV1"] + (["HP1"] if pre is not None else [])
        out.append(Candidate(detector=ENERGY, asset="BAT1", t=t, start=t, severity_hint="low",
                             features=feats, affected_assets=assets, route="rule"))
    out.sort(key=lambda c: (c.t, c.asset))
    return out
