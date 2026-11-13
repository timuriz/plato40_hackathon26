"""Adaption LLM client (Responses API) with forced function calling, validation, one retry and a
rule-based fallback.

    from plato.llm.adaption_client import AdaptionClient
    llm = AdaptionClient(model="qwen3.5-397b")
    res = llm.report_incident(packet)       # always returns a valid incident dict
    res.incident, res.source                # source: "llm" | "llm_retry" | "fallback" | "cache"

Design rules
* The LLM only classifies and explains. `report_incident` is a function tool used purely to force a
  JSON shape; nothing is executed. Safety flags are set later by plain code.
* Output is validated strictly (`validate_incident`). Invalid -> ONE retry that quotes the errors ->
  deterministic fallback (`rule_based_incident`). A missing key or a network failure goes straight to
  the fallback, so `run.py` still works offline.
* Optional on-disk cache keyed by hash(model, packet) so reruns are identical.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .fallback import rule_based_incident
from .incident_schema import INCIDENT_TOOL, SYSTEM_INSTRUCTIONS, TOOL_NAME, validate_incident

DEFAULT_MODEL = "qwen3.5-397b"


def load_env(path: str = ".env") -> None:
    """Minimal .env loader (tolerates `KEY = value` with spaces and quotes). Never overrides real env vars."""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


@dataclass
class Attempt:
    """One call to the model."""
    ok: bool  # valid incident obtained
    latency_s: float
    method: str = ""  # "function_call" | "text_json" | ""
    errors: List[str] = field(default_factory=list)
    usage: Dict[str, Any] = field(default_factory=dict)
    transport_error: bool = False  # the call itself failed (timeout, 5xx), as opposed to invalid content
    truncated: bool = False  # hit max_output_tokens (reasoning models spend the budget on thinking)


@dataclass
class IncidentResult:
    incident: Dict[str, Any]
    source: str  # llm | llm_retry | fallback | cache
    model: str
    attempts: List[Attempt] = field(default_factory=list)

    @property
    def latency_s(self) -> float:
        return sum(a.latency_s for a in self.attempts)


def _extract(response: Any) -> "tuple[Optional[Any], str, List[str]]":
    """Pull the JSON object out of a response: prefer the forced function call, else JSON in the text."""
    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", "") == "function_call" and getattr(item, "name", "") == TOOL_NAME:
            try:
                return json.loads(item.arguments or "{}"), "function_call", []
            except json.JSONDecodeError as exc:
                return None, "function_call", [f"function_call arguments are not valid JSON: {exc}"]
    text = (getattr(response, "output_text", "") or "").strip()
    if text:
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S).strip()
        try:
            return json.loads(text), "text_json", []
        except json.JSONDecodeError:
            return None, "text_json", ["no function call and the text is not valid JSON"]
    return None, "", ["empty response: no function call and no text"]


class AdaptionClient:
    def __init__(self, model: str = DEFAULT_MODEL, api_key: Optional[str] = None, timeout: float = 60.0,
                 max_output_tokens: int = 4000, temperature: float = 0.0,
                 cache_dir: Optional[str] = None, env_path: str = ".env") -> None:
        load_env(env_path)
        self.model = model
        self.timeout = timeout
        self.max_output_tokens = max_output_tokens
        self.temperature = temperature
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self._key = api_key or os.environ.get("ADAPTION_API_KEY")
        self._client = None
        if self._key:
            try:
                from adaption import Adaption  # imported lazily: the pipeline must run without the SDK too
                self._client = Adaption(api_key=self._key, timeout=timeout)
            except Exception:  # noqa: BLE001  SDK missing or misconfigured -> fallback only
                self._client = None

    @property
    def available(self) -> bool:
        return self._client is not None

    # ---- discovery ---------------------------------------------------------------------
    def list_models(self) -> List[str]:
        if not self.available:
            raise RuntimeError("Adaption client unavailable (no ADAPTION_API_KEY or SDK not installed)")
        return [m.id for m in self._client.models.list(type="hosted").data]

    # ---- one model call ----------------------------------------------------------------
    def _call(self, model: str, packet: Dict[str, Any], previous_errors: Optional[List[str]] = None,
              max_output_tokens: Optional[int] = None) -> "tuple[Attempt, Optional[Dict[str, Any]]]":
        user = "Evidence packet (JSON):\n" + json.dumps(packet, indent=2, sort_keys=True, default=str)
        messages: List[Dict[str, str]] = [{"role": "user", "content": user}]
        if previous_errors:
            messages.append({"role": "user", "content": (
                "Your previous answer was rejected: " + "; ".join(previous_errors) +
                f". Call `{TOOL_NAME}` again with every field valid and nothing else.")})
        t0 = time.perf_counter()
        try:
            resp = self._client.responses.create(
                model=model, instructions=SYSTEM_INSTRUCTIONS, input=messages,
                tools=[INCIDENT_TOOL], tool_choice={"type": "function", "name": TOOL_NAME},
                max_output_tokens=max_output_tokens or self.max_output_tokens, temperature=self.temperature,
            )
        except Exception as exc:  # noqa: BLE001  network / API errors are an attempt that failed
            return Attempt(False, time.perf_counter() - t0, errors=[f"{type(exc).__name__}: {str(exc)[:200]}"],
                           transport_error=True), None
        lat = time.perf_counter() - t0
        usage = {}
        u = getattr(resp, "usage", None)
        if u is not None:
            usage = {"input_tokens": getattr(u, "input_tokens", None), "output_tokens": getattr(u, "output_tokens", None)}
        obj, method, errs = _extract(resp)
        if not errs:
            errs = validate_incident(obj)
        truncated = getattr(resp, "status", "") == "incomplete" and \
            getattr(getattr(resp, "incomplete_details", None), "reason", "") == "max_output_tokens"
        if errs and truncated:
            errs = ["response was cut off at the output token limit"] + errs
        return (Attempt(not errs, lat, method=method, errors=errs, usage=usage, truncated=truncated and bool(errs)),
                (obj if not errs else None))

    # ---- public API --------------------------------------------------------------------
    def _cache_path(self, model: str, packet: Dict[str, Any]) -> Optional[Path]:
        if not self.cache_dir:
            return None
        h = hashlib.sha256(json.dumps([model, packet], sort_keys=True, default=str).encode()).hexdigest()[:24]
        return self.cache_dir / f"{h}.json"

    def report_incident(self, packet: Dict[str, Any], model: Optional[str] = None, retry: bool = True,
                        fallback: bool = True) -> IncidentResult:
        """Valid incident for one evidence packet: LLM -> (one retry) -> rule-based fallback."""
        model = model or self.model
        cp = self._cache_path(model, packet)
        if cp is not None and cp.is_file():
            return IncidentResult(json.loads(cp.read_text(encoding="utf-8")), "cache", model)

        attempts: List[Attempt] = []
        if self.available:
            a, obj = self._call(model, packet)
            attempts.append(a)
            source = "llm"
            if obj is None and retry:
                # a transport error (timeout, 5xx) is retried without quoting it back to the model
                prev = None if a.transport_error else a.errors
                # a cut-off answer is a budget problem: retry with twice the budget
                budget = self.max_output_tokens * 2 if a.truncated else None
                a, obj = self._call(model, packet, previous_errors=prev, max_output_tokens=budget)
                attempts.append(a)
                source = "llm_retry"
            if obj is not None:
                if cp is not None:
                    cp.parent.mkdir(parents=True, exist_ok=True)
                    cp.write_text(json.dumps(obj, sort_keys=True), encoding="utf-8")
                return IncidentResult(obj, source, model, attempts)
        if not fallback:
            raise RuntimeError("no valid LLM output and fallback disabled: " +
                               "; ".join(e for a in attempts for e in a.errors))
        return IncidentResult(rule_based_incident(packet), "fallback", model, attempts)
