#!/usr/bin/env python3
"""PLATO-40 pipeline entry point.

Runs the full pipeline (OBSERVE -> detect -> episodes -> UNDERSTAND -> DECIDE) and writes decisions.jsonl.

    python run.py --data data                                   # writes decisions.jsonl (LLM if a key is set)
    python run.py --data data --no-llm                          # deterministic, rule-based only, no network
    python run.py --data /path/to/holdout --out decisions_holdout.jsonl --cache-dir .cache/llm
    python run.py --data data --observe-only --report-json out/observe_report.json

Without ADAPTION_API_KEY (or with --no-llm) the run is fully deterministic and offline: the rule-based
classifier stands in for the LLM, so the one-command run always produces a valid decisions.jsonl.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from plato.observe import observe
from plato.observe.report import build_report, format_report, to_json
from plato.pipeline import format_summary, run_pipeline


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PLATO-40 pipeline")
    ap.add_argument("--data", required=True, help="dataset directory (quickstart, dev or holdout)")
    ap.add_argument("--out", default="decisions.jsonl", help="decision log output path")
    ap.add_argument("--incidents", help="incident/UNDERSTAND log output path for the UI "
                    "(default: incidents.jsonl next to --out)")
    ap.add_argument("--no-llm", action="store_true", help="skip the LLM; rule-based understand only")
    ap.add_argument("--model", help="Adaption model id (default: client default)")
    ap.add_argument("--cache-dir", help="cache LLM answers here (identical reruns, faster)")
    ap.add_argument("--llm-workers", type=int, default=12,
                    help="parallel LLM calls (the Adaption key throttles past ~12; higher is slower)")
    ap.add_argument("--observe-only", action="store_true", help="only run OBSERVE and print its report")
    ap.add_argument("--report-json", help="also write the data-quality report as JSON to this path")
    args = ap.parse_args(argv)

    try:
        if args.observe_only:
            obs = observe(args.data)
            rep = build_report(obs)
            print(format_report(rep))
            if args.report_json:
                _write(args.report_json, to_json(rep))
                print(f"\nreport written to {args.report_json}")
            return 0

        incidents_path = args.incidents or str(Path(args.out).with_name("incidents.jsonl"))
        result = run_pipeline(args.data, out_path=args.out, use_llm=not args.no_llm, model=args.model,
                              cache_dir=args.cache_dir, llm_workers=args.llm_workers,
                              incidents_path=incidents_path)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(format_summary(result.summary))
    print(f"\n{len(result.decisions)} decisions written to {result.out_path}")
    print(f"{len(result.incidents)} incidents written to {result.incidents_path}")
    if args.report_json:
        obs = observe(args.data)
        _write(args.report_json, to_json(build_report(obs)))
        print(f"data-quality report written to {args.report_json}")
    return 0


def _write(path: str, text: str) -> None:
    p = Path(path)
    if p.parent != Path("."):
        p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
