"""Causal array helpers shared by the detectors.

Everything here depends only on data at or before each row, so truncating the input after time ``t``
never changes the output at or before ``t``. This is the property the detector tests check.
"""
from __future__ import annotations

from typing import List, Tuple

import numpy as np
import pandas as pd


def rolling_min_valid(x: np.ndarray, window: int, min_valid: int) -> np.ndarray:
    """Trailing rolling minimum over ``window`` steps, ignoring NaN, requiring ``min_valid`` real values.

    Result at row i uses rows (i-window+1 .. i) only (causal). Rows with too few valid values are NaN.
    """
    n = len(x)
    out = np.full(n, np.nan)
    for i in range(n):
        lo = max(0, i - window + 1)
        seg = x[lo:i + 1]
        good = seg[~np.isnan(seg)]
        if good.size >= min_valid:
            out[i] = good.min()
    return out


def expanding_robust_baseline(values: np.ndarray, eligible: np.ndarray, min_count: int,
                              warmup) -> Tuple[np.ndarray, np.ndarray]:
    """Causal per-row robust baseline (median and MAD-based sigma) over all *earlier* eligible values.

    ``values``   : 1-D float series.
    ``eligible`` : bool mask, which earlier rows may feed the baseline (e.g. vacant & ok).
    ``warmup``   : a (median, sigma) fallback used until ``min_count`` eligible values exist (the pooled
                   building baseline). Pass None to get NaN during warmup.
    Returns (median, sigma) arrays aligned to ``values``; row i excludes row i itself.
    """
    n = len(values)
    med = np.full(n, np.nan)
    sig = np.full(n, np.nan)
    pool: List[float] = []
    wu_med, wu_sig = (warmup if warmup is not None else (np.nan, np.nan))
    for i in range(n):
        if len(pool) >= min_count:
            m = float(np.median(pool))
            s = 1.4826 * float(np.median(np.abs(np.array(pool) - m)))
            med[i], sig[i] = m, max(s, 1e-9)
        else:
            med[i], sig[i] = wu_med, (max(wu_sig, 1e-9) if wu_sig == wu_sig else np.nan)
        if eligible[i] and not np.isnan(values[i]):
            pool.append(float(values[i]))
    return med, sig


def find_runs(mask: np.ndarray) -> List[Tuple[int, int]]:
    """(start, stop) index pairs (stop exclusive) of consecutive True values in a 1-D bool array."""
    m = np.concatenate(([0], mask.astype(np.int8), [0]))
    d = np.diff(m)
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0]))


def run_length_so_far(mask: np.ndarray) -> np.ndarray:
    """For each True cell, how many consecutive True cells end here (1-based); 0 where False. Causal."""
    out = np.zeros(len(mask), dtype=int)
    c = 0
    for i, v in enumerate(mask):
        c = c + 1 if v else 0
        out[i] = c
    return out


def pooled_warmup(values: np.ndarray, eligible: np.ndarray):
    """A single (median, sigma) over *all* eligible values, used only as the warmup pool for early rows.

    This is computed once from the whole series. It is a prior, not a per-row look-ahead: a detector still
    only fires using its causal per-row baseline, and early rows merely fall back to this building-wide
    number instead of having no baseline at all.
    """
    v = values[eligible & ~np.isnan(values)]
    if v.size == 0:
        return None
    m = float(np.median(v))
    s = 1.4826 * float(np.median(np.abs(v - m)))
    return m, max(s, 1e-9)
