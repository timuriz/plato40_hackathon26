#!/usr/bin/env python3
"""Sanity-check the pipeline against the quickstart hints (my readings of the data, NOT ground truth).

    python3 scripts/check_quickstart.py [--data data] [--llm]

It runs the pipeline offline by default (deterministic), lists the episodes found per issue type, and
checks a handful of expectations. A FAIL here means the pipeline disagrees with my reading - worth a look,
not necessarily a bug, since no ground truth is provided.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plato.pipeline import format_summary, run_pipeline  # noqa: E402


def episodes_for(rows, issue_type):
    out = {}
    for r in rows:
        if r["issue_type"] == issue_type:
            out.setdefault(r.get("episode_id", r["event_id"]), r)
    return list(out.values())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--llm", action="store_true", help="use the LLM (default: offline rule-based)")
    args = ap.parse_args()

    res = run_pipeline(args.data, out_path=None, use_llm=args.llm, cache_dir=".cache/llm")
    rows = res.decisions
    print(format_summary(res.summary))

    def assets(issue):
        return {a for r in rows if r["issue_type"] == issue for a in r["affected_assets"]}

    checks = []
    leaks = assets("water_leak")
    checks.append(("R104 flagged as a water leak (burst hose, before WO00003 Jan 13)", "R104" in leaks))
    checks.append(("R305 flagged as a water leak (running toilet, before WO00009 Jan 25)", "R305" in leaks))
    r403 = [r for r in rows if "R403" in r["affected_assets"]]
    checks.append(("R403 never a water leak (it was a meter exchange)",
                   not any(r["issue_type"] == "water_leak" for r in r403)))
    checks.append(("R403 meter exchange shows up as data_quality",
                   any(r["issue_type"] == "data_quality" for r in r403)))
    checks.append(("some heating_comfort episodes exist (cold spells)", bool(assets("heating_comfort"))))
    checks.append(("some room_turnaround decisions exist (checkouts)", bool(assets("room_turnaround"))))
    dq = assets("data_quality")
    checks.append(("R106 spike / R205 frozen did NOT create a leak or comfort alert",
                   "R106" not in leaks and "R205" not in leaks
                   and "R106" not in assets("heating_comfort") and "R205" not in assets("heating_comfort")))

    print("\nHints (readings, not ground truth):")
    ok = True
    for label, passed in checks:
        ok = ok and passed
        print(f"  [{'OK ' if passed else 'FAIL'}] {label}")

    print("\nEpisode counts by issue type:")
    for issue in ("water_leak", "heating_comfort", "room_turnaround", "data_quality"):
        eps = episodes_for(rows, issue)
        print(f"  {issue:16} episodes={len(eps):3}  "
              f"sample={[e['affected_assets'][0] + '@' + e['timestamp_utc'][5:16] for e in eps[:4]]}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
