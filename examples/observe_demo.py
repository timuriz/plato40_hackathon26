#!/usr/bin/env python3
"""Visual demo of the OBSERVE stage: raw sensor data vs. cleaned + quality-labelled data.

    python3 examples/observe_demo.py --data data            # writes out/observe_demo.png

Four panels, each one a real problem found in the dataset:
  1. implausible spike  -> set to unknown
  2. frozen sensor      -> flagged only after 8 repeats (causal), then unknown
  3. short blank gap    -> carried forward, labelled "estimated" (never interpolated)
  4. as-of view         -> what the pipeline may know at one moment in time
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from plato.observe import observe
from plato.observe.quality import Q_ESTIMATED, Q_FROZEN, Q_MISSING, Q_OK, Q_SUSPECT

COLORS = {Q_OK: "#2a9d8f", Q_SUSPECT: "#e63946", Q_FROZEN: "#6a4c93", Q_ESTIMATED: "#f4a261", Q_MISSING: "#999999"}
LABELS = {Q_OK: "ok", Q_SUSPECT: "suspect (removed)", Q_FROZEN: "frozen (removed)",
          Q_ESTIMATED: "estimated (carried forward)", Q_MISSING: "missing (unknown)"}


def first_run(q: pd.Series, code: int, min_len: int = 1, max_len: int = 10**9):
    """Index range (start, end) of the first run of `code` in a quality series."""
    m = (q == code).to_numpy()
    s = None
    for i, v in enumerate(list(m) + [False]):
        if v and s is None:
            s = i
        elif not v and s is not None:
            if min_len <= i - s <= max_len:
                return s, i - 1
            s = None
    return None


def panel(ax, title, raw, vals, qual, window):
    lo, hi = window
    t = raw.index[lo:hi]
    ax.plot(t, raw.iloc[lo:hi], color="#bbbbbb", lw=1, ls="--", label="raw file value", zorder=1)
    v, q = vals.iloc[lo:hi], qual.iloc[lo:hi]
    for code, color in COLORS.items():
        sel = q == code
        if sel.any() and code in (Q_OK, Q_ESTIMATED):
            ax.scatter(t[sel.to_numpy()], v[sel], s=14, color=color, label=LABELS[code], zorder=3)
    r = raw.iloc[lo:hi]
    for code in (Q_SUSPECT, Q_FROZEN):
        sel = (q == code).to_numpy() & r.notna().to_numpy()
        if sel.any():
            ax.scatter(t[sel], r[sel], s=22, marker="x", color=COLORS[code], label=LABELS[code], zorder=4)
    miss = (q == Q_MISSING).to_numpy()
    for x in t[miss]:
        ax.axvspan(x - pd.Timedelta("7min"), x + pd.Timedelta("7min"), color="#dddddd", alpha=0.8, zorder=0)
    ax.set_title(title, fontsize=10, loc="left")
    ax.set_ylabel("temperature °C")
    ax.legend(fontsize=7, loc="best")
    ax.tick_params(labelsize=7)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--out", default="out/observe_demo.png")
    args = ap.parse_args()

    obs = observe(args.data)
    raw = pd.read_csv(Path(args.data) / "room_telemetry.csv", parse_dates=["timestamp_utc"])
    raw_t = raw.pivot_table(index="timestamp_utc", columns="room_id", values="temperature_c", aggfunc="last")
    raw_t = raw_t.reindex(obs.grid)
    vals, qual = obs.rooms.values["temperature_c"], obs.rooms.quality["temperature_c"]

    fig, axes = plt.subplots(2, 2, figsize=(14, 8))
    fig.suptitle("PLATO-40 OBSERVE stage: raw data (dashed) vs. cleaned + quality-labelled data (dots, x = removed)", fontsize=12)

    cases = [
        ("1. Implausible spike: removed", Q_SUSPECT, 1, 3),
        ("2. Frozen sensor: caught after 8 repeats (causal)", Q_FROZEN, 8, 10**9),
        ("3. Short gap: carried forward, labelled estimated", Q_ESTIMATED, 1, 10**9),
    ]
    for ax, (title, code, mn, mx) in zip(axes.flat, cases):
        found = None
        for room in qual.columns:
            r = first_run(qual[room], code, mn, mx)
            if r:
                found = (room, r)
                break
        if not found:
            ax.set_title(f"{title}: none in this dataset", fontsize=10, loc="left")
            continue
        room, (s, e) = found
        pad = 24 if code != Q_FROZEN else 16
        lo, hi = max(0, s - pad), min(len(qual), e + pad)
        panel(ax, f"{title}  [{room}]", raw_t[room], vals[room], qual[room], (lo, hi))

    # 4. as-of view: the Jan 8 sick cleaner is invisible until status_known_utc
    ax = axes.flat[3]
    ax.axis("off")
    ros = obs.roster
    sick = ros[(ros["resource_id"] == "CLEANER_1") & (ros["status"] == "sick")].iloc[0]
    known = sick["status_known_utc"]
    lines = [f"4. As-of view: shift {sick['shift_id']} of CLEANER_1 ({sick['start_utc']:%d %b %H:%M} UTC)", ""]
    for label, t in (("1 h before it is known", known - pd.Timedelta("1h")), ("at the moment it is known", known)):
        row = obs.asof(t).roster().query("shift_id == @sick.shift_id").iloc[0]
        lines.append(f"{t:%d %b %H:%M} UTC ({label}):")
        lines.append(f"    CLEANER_1 status seen by the pipeline = {row['status']!r}")
    lines += ["", "Same idea for the other sources, e.g. at " + f"{known:%d %b %H:%M} UTC:"]
    view = obs.asof(known)
    lines.append(f"    room temperature rows visible: {len(view.series('room.temperature_c'))} of {len(obs.grid)}")
    lines.append(f"    forecast rows (latest issued <= t): {len(view.forecast())}")
    lines.append(f"    tariff rows (published <= t):       {len(view.tariff())}")
    lines += ["", "No look-ahead: forecast by issued_utc, tariff by published_utc,",
              "roster by status_known_utc, work orders by completion time."]
    ax.text(0.0, 1.0, "\n".join(lines), va="top", family="monospace", fontsize=9)

    fig.tight_layout(rect=(0, 0, 1, 0.96))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=130)
    print(f"written {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
