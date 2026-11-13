#!/usr/bin/env python3
"""Show the two stages separately on synthetic evidence packets.

    python3 examples/understand_decide_demo.py                 # live LLM (needs ADAPTION_API_KEY in .env)
    python3 examples/understand_decide_demo.py --offline       # rule-based UNDERSTAND, no network
    python3 examples/understand_decide_demo.py --only R104 R403 --model deepseek-v4-flash

Stage 2 UNDERSTAND: what is it and why?  -> issue_type, likely_cause, severity, confidence, reason
Stage 3 DECIDE & ACT: what do we do?      -> action, priority, autonomy, approval/consent (playbook + guard)
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plato.decide import decide  # noqa: E402
from plato.llm.adaption_client import AdaptionClient  # noqa: E402
from plato.llm.fallback import rule_based_incident  # noqa: E402
from scripts.llm_probe import PACKETS  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--model", default="deepseek-v4-flash")
    ap.add_argument("--only", nargs="*", help="asset ids to show, e.g. R104 R403")
    args = ap.parse_args()

    packets = [(n, p) for n, _, p in PACKETS if not args.only or p["asset"] in args.only]
    client = AdaptionClient(model=args.model)
    live = client.available and not args.offline

    def understand(pkt):
        if live:
            r = client.report_incident(pkt)
            return r.incident, f"LLM {args.model} ({r.source}, {r.latency_s:.1f}s)"
        return rule_based_incident(pkt), "rule-based fallback (offline)"

    with ThreadPoolExecutor(5) as ex:
        understood = list(ex.map(lambda np_: understand(np_[1]), packets))

    for (name, pkt), (u, how) in zip(packets, understood):
        print("=" * 80)
        print(f"{name}   asset={pkt['asset']}   detector={pkt['detector']}")
        print(f"\n[2 UNDERSTAND] {how}")
        print(f"  issue_type={u['issue_type']}  cause={u['likely_cause']}  severity={u['severity']}  "
              f"confidence={u['confidence']}")
        print(f"  reason: {u['reason']}")
        ds = decide(u, pkt)
        print("\n[3 DECIDE & ACT]")
        if not ds:
            print("  no decision: not an incident (no alert is raised for a false alarm)")
        for d in ds:
            r = d.record
            print(f"  action={r['action']}  priority={r['priority']}  autonomy={r['autonomy']}  "
                  f"approval={r['requires_human_approval']}  consent_needed={r['requires_guest_consent']}")
            if d.notes:
                print(f"  guard: playbook proposed '{d.proposed_action}'; " + " | ".join(d.notes))
            print(f"  evidence: {r['evidence']}")
            print(f"  expected: {r['expected_outcome']}")
            print(f"  verify:   {r['verification_plan']}")
    print("\nOne full decisions.jsonl line:")
    for ds in (decide(u, p) for (_, p), (u, _) in zip(packets, understood)):
        if ds:
            print(json.dumps(ds[0].record, indent=2))
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
