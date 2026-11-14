#!/usr/bin/env python3
"""Measure the LLM step's real cost: retry rate, truncation rate, latency, wall time.

Runs a deterministic sample of the ACTUAL packets the pipeline produces (not synthetic ones) through a
fresh, uncached client so we can compare models / token budgets / worker counts fairly.

    python3 scripts/llm_speed.py --data data --n 50 --model deepseek-v4-flash --max-tokens 4000 --workers 12
    python3 scripts/llm_speed.py --data data --n 50 --model deepseek-v4-flash --max-tokens 8000
    python3 scripts/llm_speed.py --data data --n 50 --model gemma-4-31b-it --max-tokens 2000
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plato.llm.adaption_client import AdaptionClient  # noqa: E402
from plato.observe import observe  # noqa: E402
from plato.understand import build_episodes, detect_all  # noqa: E402


def llm_packets(data_dir):
    obs = observe(data_dir)
    episodes = build_episodes(detect_all(obs))
    return [e.candidate.packet() for e in episodes if e.candidate.route == "llm"]


def sample(packets, n):
    if n >= len(packets):
        return packets
    step = len(packets) / n  # even spread across the run, deterministic
    return [packets[int(i * step)] for i in range(n)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--model", default="deepseek-v4-flash")
    ap.add_argument("--max-tokens", type=int, default=4000)
    ap.add_argument("--workers", type=int, default=12)
    args = ap.parse_args()

    pkts = sample(llm_packets(args.data), args.n)
    client = AdaptionClient(model=args.model, max_output_tokens=args.max_tokens, cache_dir=None)
    if not client.available:
        print("no ADAPTION_API_KEY / SDK: cannot measure the LLM path")
        return 2

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        results = list(ex.map(client.report_incident, pkts))
    wall = time.perf_counter() - t0

    n = len(results)
    retried = sum(1 for r in results if len(r.attempts) > 1)
    truncated = sum(1 for r in results if any(a.truncated for a in r.attempts))
    transport = sum(1 for r in results if any(a.transport_error for a in r.attempts))
    fell_back = sum(1 for r in results if r.source == "fallback")
    first_lat = [r.attempts[0].latency_s for r in results if r.attempts]
    out_tok = [a.usage.get("output_tokens") or 0 for r in results for a in r.attempts]

    print(f"model={args.model}  max_tokens={args.max_tokens}  workers={args.workers}  n={n}")
    print(f"  wall time:        {wall:6.1f} s   ({wall / n:.1f} s/packet amortized)")
    print(f"  retried:          {retried:3} ({100*retried/n:.0f}%)   truncated: {truncated:3} ({100*truncated/n:.0f}%)"
          f"   transport_err: {transport}   fell_back: {fell_back}")
    print(f"  first-call latency s: p50={statistics.median(first_lat):.1f}  "
          f"mean={statistics.mean(first_lat):.1f}  max={max(first_lat):.1f}")
    if out_tok:
        print(f"  output tokens/call: p50={int(statistics.median(out_tok))}  max={max(out_tok)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
