"""Raw loading: read every dataset file exactly as delivered (nothing cleaned yet).

* every column ending in ``_utc`` is parsed to tz-aware UTC (unparseable -> NaT),
* blank cells become NaN (never 0),
* we count raw blanks per file/column so the report can show what was really missing,
* missing files are tolerated (empty frame + warning) so the pipeline never crashes on
  a dataset that lacks an optional file.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

import pandas as pd

CSV_FILES = [
    "bookings", "access_log", "room_telemetry", "room_water", "water_main", "energy", "weather",
    "weather_forecast", "tariff", "equipment", "logistics", "staff_roster", "work_orders",
    "guest_requests",
]


@dataclass
class RawData:
    data_dir: Path
    building_json: Path
    manifest: dict
    frames: Dict[str, pd.DataFrame]
    rows: Dict[str, int] = field(default_factory=dict)
    blanks: Dict[str, Dict[str, int]] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


def _parse_utc_columns(df: pd.DataFrame) -> pd.DataFrame:
    for col in df.columns:
        if col.endswith("_utc"):
            df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")
    return df


def _read_events(path: Path, warnings: List[str]) -> pd.DataFrame:
    records, bad = [], 0
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                bad += 1
    if bad:
        warnings.append(f"events.jsonl: {bad} unparseable line(s) skipped")
    df = pd.DataFrame.from_records(records)
    if df.empty:
        return pd.DataFrame(columns=["event_id", "timestamp_utc", "type"])
    return _parse_utc_columns(df)


def load_raw(data_dir) -> RawData:
    d = Path(data_dir)
    if not d.is_dir():
        raise FileNotFoundError(f"data directory not found: {d}")
    warnings: List[str] = []
    frames: Dict[str, pd.DataFrame] = {}

    for name in CSV_FILES:
        p = d / f"{name}.csv"
        if not p.exists():
            warnings.append(f"{name}.csv missing - treated as empty")
            frames[name] = pd.DataFrame()
            continue
        frames[name] = _parse_utc_columns(pd.read_csv(p))

    ev_path = d / "events.jsonl"
    if ev_path.exists():
        frames["events"] = _read_events(ev_path, warnings)
    else:
        warnings.append("events.jsonl missing - treated as empty")
        frames["events"] = pd.DataFrame(columns=["event_id", "timestamp_utc", "type"])

    manifest: dict = {}
    mp = d / "manifest.json"
    if mp.exists():
        manifest = json.loads(mp.read_text(encoding="utf-8"))
    else:
        warnings.append("manifest.json missing")

    bp = d / "building.json"
    if not bp.exists():
        raise FileNotFoundError(f"building.json is required but missing in {d}")

    rows = {k: int(len(v)) for k, v in frames.items()}
    blanks = {}
    for k, v in frames.items():
        na = v.isna().sum()
        na = na[na > 0]
        # events.jsonl is sparse by design (type-specific fields) - blanks there are not "missing data"
        if k != "events" and len(na):
            blanks[k] = {c: int(n) for c, n in na.items()}

    return RawData(data_dir=d, building_json=bp, manifest=manifest, frames=frames, rows=rows,
                   blanks=blanks, warnings=warnings)
