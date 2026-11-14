"""End-to-end PLATO-40 pipeline: OBSERVE -> detect -> episodes -> UNDERSTAND -> DECIDE -> decisions.jsonl.

    from plato.pipeline import run_pipeline
    result = run_pipeline("data", out_path="decisions.jsonl", use_llm=True)

The run is deterministic given the same inputs: detectors and episodes are pure, the LLM uses
temperature 0 and an optional packet-keyed cache, and anything the LLM cannot answer falls back to the
rule-based classifier so one command always produces a valid file - with or without an API key.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from plato.decide import decide
from plato.llm.adaption_client import AdaptionClient
from plato.llm.fallback import rule_based_incident
from plato.observe import observe
from plato.understand import build_episodes, detect_all
from plato.understand.episodes import Emission


@dataclass
class PipelineResult:
    decisions: List[Dict[str, Any]]
    n_candidates: int
    n_episodes: int
    summary: Dict[str, Any] = field(default_factory=dict)
    out_path: Optional[str] = None
    incidents: List[Dict[str, Any]] = field(default_factory=list)
    incidents_path: Optional[str] = None


def _understand_one(emission: Emission, llm: Optional[AdaptionClient], use_llm: bool) -> Tuple[Dict[str, Any], str]:
    packet = emission.candidate.packet()
    if emission.candidate.route == "rule":
        return rule_based_incident(packet), "rule"
    if use_llm and llm is not None and llm.available:
        r = llm.report_incident(packet)
        return r.incident, r.source
    return rule_based_incident(packet), "fallback"


def _incident_record(emission: Emission, incident: Dict[str, Any], source: str) -> Dict[str, Any]:
    """One detection/UNDERSTAND record for the incident log the UI reads (`--incidents`). It shares
    ``episode_id`` with the decisions so the dashboard links incident -> decision causally."""
    cand = emission.candidate
    packet = cand.packet()
    ts = packet.get("time") or (packet.get("window") or "").split("..")[-1]
    features = packet.get("features", {}) or {}
    evidence = [f"{k}={features[k]}" for k in incident.get("evidence_used", []) if k in features]
    assets = [cand.asset] + [a for a in cand.affected_assets if a != cand.asset]
    return {
        "episode_id": emission.episode_id,
        "timestamp_utc": ts,
        "issue_type": incident["issue_type"],
        "likely_cause": incident.get("likely_cause"),
        "severity": incident.get("severity"),
        "confidence": round(float(incident.get("confidence", 0.0)), 2),
        "title": f"{cand.asset}: {str(incident['issue_type']).replace('_', ' ')}",
        "reason": incident.get("reason"),
        "affected_assets": assets,
        "detector": cand.detector,
        "status": "escalated" if emission.is_followup else "detected",
        "evidence": evidence,
        "features": features,
        "quality_flags": list(cand.quality_flags),
        "source": source,
    }


def run_pipeline(data_dir, out_path: Optional[str] = None, use_llm: bool = True,
                 model: Optional[str] = None, cache_dir: Optional[str] = None,
                 llm_workers: int = 12, env_path: str = ".env",
                 incidents_path: Optional[str] = None) -> PipelineResult:
    t0 = time.perf_counter()
    obs = observe(data_dir)
    candidates = detect_all(obs)
    episodes = build_episodes(candidates)

    llm: Optional[AdaptionClient] = None
    if use_llm:
        kw = {"cache_dir": cache_dir, "env_path": env_path}
        if model:
            kw["model"] = model
        llm = AdaptionClient(**kw)
        use_llm = llm.available

    # understand every emission (LLM ones threaded, rule ones are instant and thread-safe too)
    results: List[Tuple[Dict[str, Any], str]] = [None] * len(episodes)  # type: ignore
    if episodes:
        workers = max(1, llm_workers if use_llm else 1)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for i, res in zip(range(len(episodes)),
                              ex.map(lambda e: _understand_one(e, llm, use_llm), episodes)):
                results[i] = res

    decisions: List[Dict[str, Any]] = []
    incidents: List[Dict[str, Any]] = []
    episode_opener: Dict[str, str] = {}  # episode_id -> first decision event_id (for follow-up dependencies)
    source_counts: Dict[str, int] = {}
    for emission, (incident, source) in zip(episodes, results):
        source_counts[source] = source_counts.get(source, 0) + 1
        if incident.get("issue_type") != "no_incident":
            incidents.append(_incident_record(emission, incident, source))
        packet = emission.candidate.packet()
        recs = decide(incident, packet, episode_id=emission.episode_id,
                      guest_request_ids=emission.candidate.guest_request_ids or None)
        for d in recs:
            rec = d.record
            if emission.episode_id in episode_opener:
                rec.setdefault("dependencies", [episode_opener[emission.episode_id]])
            elif not emission.is_followup:
                episode_opener[emission.episode_id] = rec["event_id"]
            decisions.append(rec)

    decisions = _finalise(decisions)
    incidents.sort(key=lambda r: (r["timestamp_utc"], r["episode_id"]))
    summary = _summarise(obs, candidates, episodes, decisions, incidents, source_counts,
                         time.perf_counter() - t0, use_llm)

    if out_path:
        _write_jsonl(out_path, decisions)
    if incidents_path:
        _write_jsonl(incidents_path, incidents)

    return PipelineResult(decisions=decisions, n_candidates=len(candidates), n_episodes=len(episodes),
                          summary=summary, out_path=out_path, incidents=incidents,
                          incidents_path=incidents_path)


def _write_jsonl(path: str, rows: List[Dict[str, Any]]) -> None:
    p = Path(path)
    if p.parent != Path("."):
        p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fh:
        for rec in rows:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _finalise(decisions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sort by time and guarantee unique event_ids (append a suffix on the rare collision)."""
    decisions.sort(key=lambda r: (r["timestamp_utc"], r["event_id"]))
    seen: Dict[str, int] = {}
    for rec in decisions:
        eid = rec["event_id"]
        if eid in seen:
            seen[eid] += 1
            rec["event_id"] = f"{eid}-{seen[eid]}"
        else:
            seen[eid] = 0
    return decisions


def _summarise(obs, candidates, episodes, decisions, incidents, source_counts, elapsed, use_llm) -> Dict[str, Any]:
    def counts(key):
        d: Dict[str, int] = {}
        for rec in decisions:
            d[rec[key]] = d.get(rec[key], 0) + 1
        return dict(sorted(d.items(), key=lambda kv: -kv[1]))
    return {
        "dataset_profile": obs.manifest.get("profile"),
        "steps_15min": len(obs.grid),
        "candidates": len(candidates),
        "episodes": len(episodes),
        "incidents": len(incidents),
        "decisions": len(decisions),
        "by_issue_type": counts("issue_type"),
        "by_action": counts("action"),
        "by_priority": counts("priority"),
        "understand_source": dict(sorted(source_counts.items(), key=lambda kv: -kv[1])),
        "approvals_required": sum(1 for r in decisions if r.get("requires_human_approval")),
        "consent_required": sum(1 for r in decisions if r.get("requires_guest_consent")),
        "llm_used": bool(use_llm),
        "elapsed_s": round(elapsed, 1),
    }


def format_summary(s: Dict[str, Any]) -> str:
    lines = ["=" * 78,
             f"PIPELINE  profile={s['dataset_profile']}  steps={s['steps_15min']}  "
             f"candidates={s['candidates']}  episodes={s['episodes']}  "
             f"incidents={s['incidents']}  decisions={s['decisions']}",
             "=" * 78,
             f"LLM used: {s['llm_used']}   understand source: {s['understand_source']}",
             f"approvals required: {s['approvals_required']}   guest consent required: {s['consent_required']}",
             f"elapsed: {s['elapsed_s']} s", "",
             "by issue_type: " + ", ".join(f"{k}={v}" for k, v in s["by_issue_type"].items()),
             "by action:     " + ", ".join(f"{k}={v}" for k, v in s["by_action"].items()),
             "by priority:   " + ", ".join(f"{k}={v}" for k, v in s["by_priority"].items())]
    return "\n".join(lines)
