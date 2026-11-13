"""Equipment (HP1/AHU1), energy, weather, logistics, tariff and forecast cleaning."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict

import numpy as np
import pandas as pd

from .building import Building
from .quality import (
    GAP_COLS, Q_FROZEN, Q_MISSING, Q_SUSPECT, IssueLog, Rule, clean_frame, gap_table, mask_runs,
    prepare_long,
)

HP_STATUS_KNOWN = {"running", "standby", "defrost"}  # anything else (e.g. lockout_*) is kept and reported


@dataclass
class Wide:
    """A cleaned wide signal table with parallel quality codes."""

    values: pd.DataFrame
    quality: pd.DataFrame
    raw_missing: pd.DataFrame


def _log_quality(wide: Wide, source: str, log: IssueLog) -> pd.DataFrame:
    for code, issue, action in ((Q_SUSPECT, "implausible_value", "set_to_unknown"),
                                (Q_FROZEN, "frozen_sensor", "set_to_unknown_after_threshold")):
        runs = mask_runs(wide.quality == code)
        for r in runs.itertuples(index=False):
            a, _, c = str(r.asset_id).partition(".")
            log.add(source, issue, asset_id=a if c else "", column=c or a, start_utc=r.start_utc,
                    end_utc=r.end_utc, steps=r.steps, action=action)
    g = gap_table(wide.raw_missing, wide.quality, source)
    if len(g):
        parts = g["asset_id"].str.partition(".")
        has_dot = parts[1] == "."
        g["column"] = np.where(has_dot, parts[2], g["asset_id"])
        g["asset_id"] = np.where(has_dot, parts[0], "")
    return g if len(g) else pd.DataFrame(columns=GAP_COLS)


def _wide_from_long(df: pd.DataFrame, series_col: str, grid) -> pd.DataFrame:
    return df.pivot(index="timestamp_utc", columns=series_col, values="value").reindex(index=grid)


# ---------------------------------------------------------------- equipment
EQUIPMENT_RULES: Dict[str, Rule] = {
    "HP1.heat_output_kw": Rule(lo=-5, hi=200),
    "HP1.electric_kw": Rule(lo=-1, hi=100),
    "HP1.supply_temp_c": Rule(lo=10, hi=80, locf_limit=2),
    "AHU1.airflow_m3h": Rule(lo=0, hi=8000, frozen_repeats=8),
    # filter differential pressure is never filled: a gap here is exactly the "missing
    # sensor data vs real degradation" question of S04 and must stay visible.
    "AHU1.filter_dp_pa": Rule(lo=0, hi=1000, frozen_repeats=8),
    "AHU1.fan_kw": Rule(lo=0, hi=20),
    "AHU1.supply_air_c": Rule(lo=-20, hi=50, frozen_repeats=8),
}


@dataclass
class EquipmentData:
    wide: Wide
    status: pd.DataFrame  # asset.status -> string labels (NaN = missing)
    gaps: pd.DataFrame


def process_equipment(df: pd.DataFrame, grid, log: IssueLog) -> EquipmentData:
    if df.empty:
        e = pd.DataFrame(index=grid)
        return EquipmentData(Wide(e, e.astype(np.int8), e.astype(bool)), e, pd.DataFrame(columns=GAP_COLS))
    d = df.copy()
    d["series"] = d["asset_id"].astype(str) + "." + d["metric"].astype(str)
    d = prepare_long(d[["timestamp_utc", "series", "value"]], ["timestamp_utc", "series"], "equipment", log)
    is_status = d["series"].str.endswith(".status")
    num = d[~is_status].copy()
    num["value"] = pd.to_numeric(num["value"], errors="coerce")
    wide_num = _wide_from_long(num, "series", grid)
    values, quality, raw_missing = clean_frame(wide_num, lambda c: EQUIPMENT_RULES.get(c, Rule()))
    wide = Wide(values, quality, raw_missing)
    gaps = _log_quality(wide, "equipment", log)

    st = d[is_status]
    status = _wide_from_long(st, "series", grid) if len(st) else pd.DataFrame(index=grid)
    for col in status.columns:
        unusual = status[col].dropna()
        unusual = unusual[~unusual.isin(HP_STATUS_KNOWN)]
        if len(unusual):
            for label, n in unusual.value_counts().items():
                log.add("equipment", "unusual_status_code", asset_id=col.split(".")[0], column="status",
                        steps=int(n), action="kept_flag_for_decision", detail=str(label))
    return EquipmentData(wide, status, gaps)


# ------------------------------------------------------------------- energy
@dataclass
class EnergyData:
    wide: Wide
    balance_residual_kw: pd.Series
    gaps: pd.DataFrame


def process_energy(df: pd.DataFrame, building: Building, grid, log: IssueLog) -> EnergyData:
    cols = ["building_load_kw", "hvac_electric_kw", "pv_kw", "battery_discharge_kw_signed",
            "battery_soc_kwh", "grid_import_kw", "grid_export_kw"]
    if df.empty:
        e = pd.DataFrame(np.nan, index=grid, columns=cols)
        return EnergyData(Wide(e, pd.DataFrame(Q_MISSING, index=grid, columns=cols).astype(np.int8),
                               e.isna()), pd.Series(np.nan, index=grid), pd.DataFrame(columns=GAP_COLS))
    d = df.copy()
    for c in cols:
        d[c] = pd.to_numeric(d[c], errors="coerce") if c in d.columns else np.nan
    d = prepare_long(d, ["timestamp_utc"], "energy", log)
    wide_raw = d.set_index("timestamp_utc")[cols].reindex(grid)
    bm = building.battery_max_kw * 1.2
    rules = {
        "building_load_kw": Rule(lo=0, hi=1000),
        "hvac_electric_kw": Rule(lo=0, hi=500),
        "pv_kw": Rule(lo=0, hi=building.pv_kwp * 1.5),
        "battery_discharge_kw_signed": Rule(lo=-bm, hi=bm),
        "battery_soc_kwh": Rule(lo=0, hi=building.battery_usable_kwh * 1.1),
        "grid_import_kw": Rule(lo=0, hi=1000),
        "grid_export_kw": Rule(lo=0, hi=1000),
    }
    # constant values (idle battery, night PV) are legitimate -> no frozen rule for energy
    values, quality, raw_missing = clean_frame(wide_raw, lambda c: rules[c])
    wide = Wide(values, quality, raw_missing)
    gaps = _log_quality(wide, "energy", log)
    # grid_import + pv + battery_discharge(signed) = load + grid_export
    resid = (values["grid_import_kw"] + values["pv_kw"] + values["battery_discharge_kw_signed"]
             - values["building_load_kw"] - values["grid_export_kw"])
    return EnergyData(wide, resid, gaps)


# ------------------------------------------------------------------ weather
def process_weather(df: pd.DataFrame, grid, log: IssueLog) -> Wide:
    cols = ["outdoor_c", "cloud_cover_pct"]
    if df.empty:
        e = pd.DataFrame(np.nan, index=grid, columns=cols)
        return Wide(e, pd.DataFrame(Q_MISSING, index=grid, columns=cols).astype(np.int8), e.isna())
    d = df.copy()
    for c in cols:
        d[c] = pd.to_numeric(d[c], errors="coerce") if c in d.columns else np.nan
    d = prepare_long(d, ["timestamp_utc"], "weather", log)
    wide_raw = d.set_index("timestamp_utc")[cols].reindex(grid)
    rules = {"outdoor_c": Rule(lo=-45, hi=50, locf_limit=2), "cloud_cover_pct": Rule(lo=0, hi=100, locf_limit=2)}
    values, quality, raw_missing = clean_frame(wide_raw, lambda c: rules[c])
    wide = Wide(values, quality, raw_missing)
    _log_quality(wide, "weather", log)
    return wide


# ---------------------------------------------------------------- logistics
@dataclass
class LogisticsData:
    wide: Wide
    full_or_overflowing: pd.DataFrame  # fill_pct at the 100 % cap: a full bin may be overflowing
    gaps: pd.DataFrame


def process_logistics(df: pd.DataFrame, log: IssueLog) -> LogisticsData:
    if df.empty:
        e = pd.DataFrame()
        return LogisticsData(Wide(e, e.astype(np.int8), e.astype(bool)), e.astype(bool), pd.DataFrame(columns=GAP_COLS))
    d = df.copy()
    d["series"] = d["asset_id"].astype(str) + "." + d["metric"].astype(str)
    d["value"] = pd.to_numeric(d["value"], errors="coerce")
    d = prepare_long(d[["timestamp_utc", "series", "value"]], ["timestamp_utc", "series"], "logistics", log,
                     step="1h")
    grid = pd.date_range(d["timestamp_utc"].min(), d["timestamp_utc"].max(), freq="1h", tz="UTC")
    wide_raw = _wide_from_long(d, "series", grid)

    def rule_for(c: str) -> Rule:
        if c.endswith(".fill_pct"):
            return Rule(lo=0, hi=100, locf_limit=2)
        if ".stock_" in c:
            return Rule(lo=0, hi=1000, locf_limit=2)
        return Rule()

    values, quality, raw_missing = clean_frame(wide_raw, rule_for)
    wide = Wide(values, quality, raw_missing)
    gaps = _log_quality(wide, "logistics", log)
    fill_cols = [c for c in values.columns if c.endswith(".fill_pct")]
    capped = values[fill_cols] >= 99.5  # values are capped at 100 -> true fill may be higher
    return LogisticsData(wide, capped, gaps)


# ------------------------------------------------ tariff / forecast / pass-through tables
def process_tariff(df: pd.DataFrame, log: IssueLog) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["timestamp_utc", "price_eur_kwh", "published_utc"])
    d = df.copy()
    d["price_eur_kwh"] = pd.to_numeric(d["price_eur_kwh"], errors="coerce")
    bad = d["price_eur_kwh"].isna() | (d["price_eur_kwh"] < -1) | (d["price_eur_kwh"] > 5)
    if bad.any():
        log.add("tariff", "missing_or_implausible_price", steps=int(bad.sum()), action="set_to_unknown")
        d.loc[bad, "price_eur_kwh"] = np.nan
    no_pub = d["published_utc"].isna()
    if no_pub.any():
        log.add("tariff", "missing_publication_time", steps=int(no_pub.sum()),
                action="rows_never_visible_in_as_of_view")
    d = d.drop_duplicates(["timestamp_utc", "published_utc"], keep="last")
    return d.sort_values(["published_utc", "timestamp_utc"]).reset_index(drop=True)


def process_forecast(df: pd.DataFrame, log: IssueLog) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["issued_utc", "target_utc", "outdoor_c_forecast"])
    d = df.copy()
    d["outdoor_c_forecast"] = pd.to_numeric(d["outdoor_c_forecast"], errors="coerce")
    bad = d["outdoor_c_forecast"].isna() | (d["outdoor_c_forecast"] < -50) | (d["outdoor_c_forecast"] > 60)
    if bad.any():
        log.add("weather_forecast", "missing_or_implausible_value", steps=int(bad.sum()), action="set_to_unknown")
        d.loc[bad, "outdoor_c_forecast"] = np.nan
    d = d.drop_duplicates(["issued_utc", "target_utc"], keep="last")
    return d.sort_values(["issued_utc", "target_utc"]).reset_index(drop=True)
