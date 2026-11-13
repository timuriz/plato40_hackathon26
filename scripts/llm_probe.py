#!/usr/bin/env python3
"""Probe the Adaption models: list hosted models, then test forced `report_incident` function calling.

    python3 scripts/llm_probe.py                                  # default 3 models
    python3 scripts/llm_probe.py --models qwen3.5-397b glm-5.2 --repeat 2

Test packets are SYNTHETIC fixtures (shaped like detector output, numbers invented for the test);
they are not observations from any dataset. Each has an expected `issue_type` so we also see whether
the answer is sensible, not just well-formed.

Reported per model:
  first-try valid  = share of calls whose FIRST answer passed strict validation
  valid after retry= share valid after the single allowed retry (what the pipeline relies on)
  fallback         = share that needed the rule-based fallback
  latency          = per-call wall time (first attempt), seconds
"""
from __future__ import annotations

import argparse
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plato.llm.adaption_client import AdaptionClient  # noqa: E402

DEFAULT_MODELS = ["qwen3.5-397b", "glm-5.2", "deepseek-v4-flash"]

# (name, expected issue_type, packet)
PACKETS = [
    ("leak_vacant_steady", "water_leak", {
        "detector": "water_baseline", "asset": "R104", "window": "2026-01-10T22:00Z..2026-01-11T06:00Z",
        "features": {"occupied_share": 0.0, "flow_l_per_15min_median": 26.4, "baseline_median": 0.0,
                     "flow_z": 9.1, "flow_cv": 0.08, "duration_h": 8, "meter_reset_in_window": False,
                     "main_minus_rooms_residual_l": 210},
        "quality_flags": [], "bms_alarms": []}),
    ("guest_shower", "no_incident", {
        "detector": "water_baseline", "asset": "R212", "window": "2026-01-12T07:00Z..2026-01-12T07:30Z",
        "features": {"occupied_share": 1.0, "flow_l_per_15min_median": 30.0, "baseline_median": 2.0,
                     "flow_z": 5.2, "flow_cv": 0.6, "duration_h": 0.5, "meter_reset_in_window": False},
        "quality_flags": [], "bms_alarms": []}),
    ("meter_exchange", "data_quality", {
        "detector": "water_baseline", "asset": "R403", "window": "2026-01-18T10:00Z..2026-01-18T10:15Z",
        "features": {"occupied_share": 1.0, "cumulative_before_l": 27020.8, "cumulative_after_l": 0.4,
                     "delta_l": -27020.4, "meter_reset_in_window": True},
        "quality_flags": ["reset"], "work_orders": [{"id": "WO00004", "type": "meter_exchange", "asset": "R403"}]}),
    ("toilet_valve_running", "water_leak", {
        "detector": "water_baseline", "asset": "R305", "window": "2026-01-24T00:00Z..2026-01-25T00:00Z",
        "features": {"occupied_share": 0.3, "flow_l_per_15min_median": 4.5, "baseline_median": 0.4,
                     "flow_z": 6.0, "flow_cv": 0.12, "duration_h": 24, "night_flow_present": True,
                     "meter_reset_in_window": False},
        "quality_flags": [], "bms_alarms": []}),
    ("cold_room_hp_lockout", "heating_comfort", {
        "detector": "comfort", "asset": "R210", "time": "2026-01-17T06:00Z",
        "features": {"temp_c": 18.6, "setpoint_c": 21.0, "occupied": True, "guest_cold_requests": 2,
                     "hp_alarm": "lockout_high_pressure", "outdoor_c": -6.0, "tariff_ct_kwh": 31},
        "quality_flags": []}),
    ("comfort_ok", "no_incident", {
        "detector": "comfort", "asset": "R118", "time": "2026-01-15T14:00Z",
        "features": {"temp_c": 21.4, "setpoint_c": 21.0, "occupied": True, "guest_cold_requests": 0,
                     "hp_alarm": None, "outdoor_c": 2.0},
        "quality_flags": []}),
    ("frozen_sensor", "data_quality", {
        "detector": "sensor_quality", "asset": "R205", "window": "2026-01-12T14:15Z..2026-01-13T11:30Z",
        "features": {"identical_repeats": 85, "value_c": 17.95, "other_rooms_same_floor_changing": True},
        "quality_flags": ["frozen"]}),
    ("spike_85c", "data_quality", {
        "detector": "sensor_quality", "asset": "R106", "window": "2026-01-24T15:45Z",
        "features": {"raw_value_c": 85.0, "neighbour_values_c": [22.8, 22.7], "setpoint_c": 21.0},
        "quality_flags": ["suspect"]}),
    ("turnaround_dirty", "room_turnaround", {
        "detector": "room_turnaround", "asset": "R302", "time": "2026-01-24T09:00Z",
        "features": {"housekeeping_state": "dirty", "guest_departed_utc": "2026-01-24T08:40Z",
                     "next_checkin_request_utc": "2026-01-24T11:00Z", "cleaners_available": 1,
                     "cleaners_sick": ["CLEANER_1"], "queue_length": 5},
        "quality_flags": []}),
    ("unclear_water", "water_leak", {
        "detector": "water_baseline", "asset": "R309", "window": "2026-01-14T01:00Z..2026-01-14T02:00Z",
        "features": {"occupied_share": 0.0, "flow_l_per_15min_median": 3.0, "baseline_median": 0.0,
                     "flow_z": 4.2, "flow_cv": 0.5, "duration_h": 1, "meter_reset_in_window": False},
        "quality_flags": ["estimated"]}),
]


def pct(n, d):
    return f"{100 * n / d:5.1f}%" if d else "  n/a"


def run_model(client: AdaptionClient, model: str, repeat: int, workers: int):
    jobs = [(name, exp, pkt) for _ in range(repeat) for (name, exp, pkt) in PACKETS]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(lambda j: (j, client.report_incident(j[2], model=model)), jobs))
    n = len(results)
    first_ok = sum(1 for _, r in results if r.attempts and r.attempts[0].ok)
    after_retry = sum(1 for _, r in results if r.source in ("llm", "llm_retry"))
    fb = sum(1 for _, r in results if r.source == "fallback")
    sensible = sum(1 for (j, r) in results if r.incident["issue_type"] == j[1])
    methods = {}
    for _, r in results:
        for a in r.attempts[:1]:
            methods[a.method or "none"] = methods.get(a.method or "none", 0) + 1
    lat = [r.attempts[0].latency_s for _, r in results if r.attempts]
    toks = [(r.attempts[0].usage.get("input_tokens") or 0, r.attempts[0].usage.get("output_tokens") or 0)
            for _, r in results if r.attempts]
    errs = sorted({e for _, r in results for a in r.attempts for e in a.errors})
    return dict(model=model, n=n, first_ok=first_ok, after_retry=after_retry, fallback=fb, sensible=sensible,
                methods=methods, lat=lat, toks=toks, errs=errs, results=results)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    ap.add_argument("--repeat", type=int, default=1, help="times each packet is sent per model")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--list-only", action="store_true")
    args = ap.parse_args()

    client = AdaptionClient()
    if not client.available:
        print("Adaption client unavailable: set ADAPTION_API_KEY in .env and `pip install adaption`.")
        return 2
    hosted = client.list_models()
    print("Hosted models for this key:")
    for m in hosted:
        print(f"  - {m}")
    if args.list_only:
        return 0

    stats = []
    for model in args.models:
        if model not in hosted:
            print(f"\n[skip] {model} is not in the hosted list for this key")
            continue
        print(f"\nRunning {model} on {len(PACKETS) * args.repeat} packets ...", flush=True)
        stats.append(run_model(client, model, args.repeat, args.workers))

    print("\n=== Forced function calling `report_incident` ===")
    hdr = f"{'model':20s} {'n':>3s} {'1st-try valid':>14s} {'valid+retry':>12s} {'fallback':>9s} {'issue_type ok':>14s}  latency s (p50 / mean / max)"
    print(hdr)
    for s in stats:
        lat = s["lat"]
        print(f"{s['model']:20s} {s['n']:3d} {pct(s['first_ok'], s['n']):>14s} {pct(s['after_retry'], s['n']):>12s} "
              f"{pct(s['fallback'], s['n']):>9s} {pct(s['sensible'], s['n']):>14s}  "
              f"{statistics.median(lat):.1f} / {statistics.mean(lat):.1f} / {max(lat):.1f}")
    for s in stats:
        ti, to = (sum(x[0] for x in s["toks"]), sum(x[1] for x in s["toks"]))
        print(f"\n{s['model']}: first-attempt answer came via {s['methods']}; tokens in/out total {ti}/{to}")
        for e in s["errs"][:5]:
            print(f"    error seen: {e[:160]}")
        miss = [(j[0], j[1], r.incident["issue_type"]) for (j, r) in s["results"] if r.incident["issue_type"] != j[1]]
        for name, exp, got in miss:
            print(f"    differs: {name}: expected {exp}, got {got}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
