#!/usr/bin/env python3
"""PLATO-40 pipeline entry point.

Currently implemented stage: OBSERVE (load, clean, label quality, causal as-of view).
Understand / Decide & act will plug in after it and write decisions.jsonl.

    python run.py --data data                    # quickstart dataset (folder ./data)
    python run.py --data /path/to/dev --report-json out/observe_report.json
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from plato.observe import observe
from plato.observe.report import build_report, format_report, to_json


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PLATO-40 pipeline (observe stage)")
    ap.add_argument("--data", required=True, help="dataset directory (quickstart, dev or holdout)")
    ap.add_argument("--report-json", help="also write the data-quality report as JSON to this path")
    args = ap.parse_args(argv)

    try:
        obs = observe(args.data)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    rep = build_report(obs)
    print(format_report(rep))
    if args.report_json:
        p = Path(args.report_json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(to_json(rep), encoding="utf-8")
        print(f"\nreport written to {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
