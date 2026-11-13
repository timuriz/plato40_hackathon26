"""Deterministic data-quality toolkit (no LLM, no randomness).

Design rules
------------
* Blank means *missing*, never zero.
* Every cell carries a quality code (``Q_*``) next to its value.
* All rules are **causal**: the value/quality at time t depends only on data at or
  before t. That keeps the replay honest (no look-ahead through the cleaning step).
  - range checks are point-wise,
  - frozen-sensor detection flags a value only once the run has *already* lasted
    ``frozen_repeats`` steps,
  - gap filling is last-observation-carried-forward with a hard limit, applied only
    when the last real reading was itself good. Nothing is interpolated.
* A filled value is always labelled ``estimated`` (or ``derived`` when it comes from
  another data source); it is never presented as a measurement.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

# ----------------------------------------------------------------------------
# quality codes
# ----------------------------------------------------------------------------
Q_OK = 0  # measured, passed all checks
Q_MISSING = 1  # blank / absent row; value is NaN (unknown)
Q_SUSPECT = 2  # implausible (out of range / impossible jump); value set to NaN
Q_FROZEN = 3  # sensor stuck on one value for too long; value set to NaN
Q_ESTIMATED = 4  # short gap carried forward from last good reading (not a measurement)
Q_DERIVED = 5  # taken from another source (e.g. cleaning events), not this sensor
Q_RESET = 6  # cumulative meter counter restarted (meter exchange)
Q_JITTER = 7  # tiny negative step on a cumulative meter = meter noise
Q_SPAN = 8  # value covers several steps (reading arrived after a gap)

Q_NAMES = {
    Q_OK: "ok",
    Q_MISSING: "missing",
    Q_SUSPECT: "suspect",
    Q_FROZEN: "frozen",
    Q_ESTIMATED: "estimated",
    Q_DERIVED: "derived",
    Q_RESET: "reset",
    Q_JITTER: "jitter",
    Q_SPAN: "span",
}


JUMP_MEMORY_STEPS = 4  # how long the last good reading stays a valid reference for the jump check


@dataclass(frozen=True)
class Rule:
    """Cleaning rule for one numeric signal."""

    lo: Optional[float] = None  # hard physical lower bound
    hi: Optional[float] = None  # hard physical upper bound
    binary: bool = False  # only 0/1 allowed
    frozen_repeats: Optional[int] = None  # flag after this many identical repeats
    max_jump: Optional[float] = None  # max plausible change between consecutive steps
    locf_limit: int = 0  # max steps a missing run may be carried forward (0 = never fill)


# ----------------------------------------------------------------------------
# array helpers
# ----------------------------------------------------------------------------
def run_position(mask: np.ndarray) -> np.ndarray:
    """For each True cell, its 1-based position inside the current run of True along axis 0."""
    t = mask.shape[0]
    idx = np.arange(t).reshape(-1, 1)
    last_false = np.maximum.accumulate(np.where(mask, -1, idx), axis=0)
    return np.where(mask, idx - last_false, 0)


def find_runs(mask_1d: np.ndarray) -> List[Tuple[int, int]]:
    """(start, stop) index pairs (stop exclusive) of consecutive True values."""
    m = np.concatenate(([0], mask_1d.astype(np.int8), [0]))
    d = np.diff(m)
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0]))


def clean_matrix(raw: np.ndarray, rule: Rule) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Clean a (time x series) float matrix.

    Returns ``(values, quality, raw_missing)`` where ``raw_missing`` marks cells that were
    blank in the source (before any rule or fill).
    """
    v = np.array(raw, dtype=float, copy=True)
    n_t, n_c = v.shape
    raw_missing = np.isnan(v)
    q = np.full(v.shape, Q_OK, dtype=np.int8)
    q[raw_missing] = Q_MISSING

    with np.errstate(invalid="ignore"):
        bad = np.zeros(v.shape, dtype=bool)
        if rule.binary:
            bad |= ~raw_missing & ~np.isin(v, (0.0, 1.0))
        if rule.lo is not None:
            bad |= v < rule.lo
        if rule.hi is not None:
            bad |= v > rule.hi

        # causal jump check against the last *good* reading (a spike never becomes the
        # reference, so the value right after it is not flagged too). The reference is only
        # trusted for JUMP_MEMORY_STEPS steps so a long gap cannot cause false flags.
        if rule.max_jump is not None and n_t > 1:
            last_good = np.full(n_c, np.nan)
            age = np.full(n_c, 10**9)
            for i in range(n_t):
                cand = ~raw_missing[i] & ~bad[i]
                jump = cand & (age <= JUMP_MEMORY_STEPS) & (np.abs(v[i] - last_good) > rule.max_jump)
                bad[i] |= jump
                accept = cand & ~jump
                last_good = np.where(accept, v[i], last_good)
                age = np.where(accept, 0, age + 1)

    q[bad & ~raw_missing] = Q_SUSPECT
    v[q == Q_SUSPECT] = np.nan

    # frozen sensor: flag only once the identical run has reached the threshold (causal)
    if rule.frozen_repeats and n_t > 1:
        valid = q == Q_OK
        eq = np.zeros(v.shape, dtype=bool)
        with np.errstate(invalid="ignore"):
            eq[1:] = valid[1:] & valid[:-1] & (v[1:] == v[:-1])
        frozen = run_position(eq) >= rule.frozen_repeats
        q[frozen] = Q_FROZEN
        v[frozen] = np.nan

    # short-gap fill: carry the last *good* reading forward, labelled estimated
    if rule.locf_limit > 0:
        miss = q == Q_MISSING
        pos = run_position(miss)
        rows = np.arange(n_t).reshape(-1, 1)
        src = np.clip(rows - pos, 0, None)
        src_q = np.take_along_axis(q, src, axis=0)
        src_ok = (rows - pos >= 0) & (src_q == Q_OK)
        fill = miss & (pos <= rule.locf_limit) & src_ok
        v = np.where(fill, np.take_along_axis(v, src, axis=0), v)
        q = np.where(fill, Q_ESTIMATED, q).astype(np.int8)

    return v, q, raw_missing


def clean_frame(df: pd.DataFrame, rule_for) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Apply ``rule_for(column) -> Rule`` column by column to a wide DataFrame."""
    vals, quals, miss = {}, {}, {}
    for col in df.columns:
        v, q, m = clean_matrix(df[[col]].to_numpy(dtype=float), rule_for(col))
        vals[col], quals[col], miss[col] = v[:, 0], q[:, 0], m[:, 0]
    mk = lambda d, dt=None: pd.DataFrame(d, index=df.index, columns=df.columns)  # noqa: E731
    return mk(vals), mk(quals).astype(np.int8), mk(miss).astype(bool)


# ----------------------------------------------------------------------------
# issue / gap tables
# ----------------------------------------------------------------------------
ISSUE_COLS = ["source", "issue", "asset_id", "column", "start_utc", "end_utc", "steps", "action", "detail"]


class IssueLog:
    """Collects data-quality findings as a flat table."""

    def __init__(self) -> None:
        self.rows: List[dict] = []

    def add(self, source: str, issue: str, asset_id: str = "", column: str = "", start_utc=None,
            end_utc=None, steps: int = 0, action: str = "", detail: str = "") -> None:
        self.rows.append(dict(source=source, issue=issue, asset_id=asset_id, column=column,
                              start_utc=start_utc, end_utc=end_utc, steps=int(steps),
                              action=action, detail=detail))

    def add_runs(self, runs: pd.DataFrame, source: str, issue: str, action: str, column: str = "",
                 detail: str = "") -> None:
        for r in runs.itertuples(index=False):
            d = detail
            if getattr(r, "first_value", None) is not None and not pd.isna(r.first_value):
                d = (detail + f" first_raw={r.first_value}").strip()
            self.add(source, issue, asset_id=r.asset_id, column=column, start_utc=r.start_utc,
                     end_utc=r.end_utc, steps=r.steps, action=action, detail=d)

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.rows, columns=ISSUE_COLS)


def mask_runs(mask: pd.DataFrame, values: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Runs of True per column of a boolean frame -> table (asset_id, start, end, steps, first_value)."""
    rows = []
    arr = mask.to_numpy(dtype=bool)
    idx = mask.index
    for j, col in enumerate(mask.columns):
        for s, e in find_runs(arr[:, j]):
            fv = values.iloc[s, j] if values is not None else None
            rows.append(dict(asset_id=col, start_utc=idx[s], end_utc=idx[e - 1], steps=int(e - s),
                             first_value=fv))
    return pd.DataFrame(rows, columns=["asset_id", "start_utc", "end_utc", "steps", "first_value"])


GAP_COLS = ["source", "asset_id", "column", "start_utc", "end_utc", "steps", "filled_steps", "cause"]


def gap_table(raw_missing: pd.DataFrame, q_after: pd.DataFrame, source: str,
              column: str = "") -> pd.DataFrame:
    """One row per run of blank cells, with how many steps were carried forward (estimated)."""
    rows = []
    rm = raw_missing.to_numpy(dtype=bool)
    qa = q_after.to_numpy()
    idx = raw_missing.index
    for j, col in enumerate(raw_missing.columns):
        for s, e in find_runs(rm[:, j]):
            rows.append(dict(source=source, asset_id=col, column=column, start_utc=idx[s],
                             end_utc=idx[e - 1], steps=int(e - s),
                             filled_steps=int((qa[s:e, j] == Q_ESTIMATED).sum()), cause="sensor_gap"))
    return pd.DataFrame(rows, columns=GAP_COLS)


# ----------------------------------------------------------------------------
# long-format preparation shared by all loaders
# ----------------------------------------------------------------------------
def prepare_long(df: pd.DataFrame, keys: Sequence[str], source: str, log: IssueLog,
                 step: str = "15min", time_col: str = "timestamp_utc") -> pd.DataFrame:
    """Drop rows with unparseable / off-grid timestamps and duplicate keys (logged)."""
    out = df.copy()
    bad_ts = out[time_col].isna()
    if bad_ts.any():
        log.add(source, "bad_timestamp", steps=int(bad_ts.sum()), action="dropped_rows")
        out = out[~bad_ts]
    off = (out[time_col] - out[time_col].dt.floor(step)) != pd.Timedelta(0)
    if off.any():
        log.add(source, "off_grid_timestamp", steps=int(off.sum()), action="dropped_rows",
                detail=f"not on {step} grid")
        out = out[~off]
    dup_all = out.duplicated(list(keys), keep=False)
    if dup_all.any():
        value_cols = [c for c in out.columns if c not in keys]
        grp = out[dup_all].groupby(list(keys))[value_cols]
        conflicting = int((grp.nunique(dropna=False) > 1).any(axis=1).sum())
        n_rows = int(out.duplicated(list(keys), keep="last").sum())
        log.add(source, "duplicate_rows", steps=n_rows, action="kept_last",
                detail=f"conflicting_groups={conflicting}")
        out = out.drop_duplicates(list(keys), keep="last")
    return out
