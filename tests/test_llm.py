"""Offline tests for the LLM client: validation, one retry, fallback, cache. No network, no API key."""
import json
import tempfile
import unittest
from types import SimpleNamespace as NS

from plato.llm.adaption_client import AdaptionClient
from plato.llm.fallback import rule_based_incident
from plato.llm.incident_schema import TOOL_NAME, validate_incident

GOOD = {"issue_type": "water_leak", "likely_cause": "equipment_fault", "action": "request_human_approval",
        "priority": "high", "confidence": 0.8, "reason": "Steady flow in a vacant room.",
        "evidence_used": ["flow_z"]}
PACKET = {"detector": "water_baseline", "asset": "R104",
          "features": {"occupied_share": 0.0, "flow_z": 9.0, "flow_cv": 0.1, "duration_h": 8}, "quality_flags": []}


def fc(args):
    return NS(type="function_call", name=TOOL_NAME, arguments=json.dumps(args) if not isinstance(args, str) else args)


def resp(*items, status="completed", reason=None):
    return NS(output=list(items), output_text="", status=status,
              incomplete_details=NS(reason=reason) if reason else None, usage=NS(input_tokens=1, output_tokens=1))


class FakeResponses:
    def __init__(self, scripted):
        self.scripted, self.calls = list(scripted), []

    def create(self, **kw):
        self.calls.append(kw)
        r = self.scripted.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def make_client(scripted, **kw):
    c = AdaptionClient(api_key=None, env_path="/nonexistent", **kw)
    fake = FakeResponses(scripted)
    c._client = NS(responses=fake)
    return c, fake


class TestValidation(unittest.TestCase):
    def test_good(self):
        self.assertEqual(validate_incident(GOOD), [])

    def test_bad_enum_missing_and_extra(self):
        bad = dict(GOOD, priority="urgent", extra=1)
        del bad["reason"]
        errs = " ".join(validate_incident(bad))
        self.assertIn("priority", errs)
        self.assertIn("missing field 'reason'", errs)
        self.assertIn("unexpected field 'extra'", errs)

    def test_confidence_must_be_number_in_range(self):
        for c in ("0.8", 1.5, True, None):
            self.assertTrue(validate_incident(dict(GOOD, confidence=c)), c)

    def test_not_an_object(self):
        self.assertTrue(validate_incident([GOOD]))


class TestClientFlow(unittest.TestCase):
    def test_valid_first_try(self):
        c, fake = make_client([resp(fc(GOOD))])
        r = c.report_incident(PACKET)
        self.assertEqual((r.source, len(r.attempts), r.incident), ("llm", 1, GOOD))
        self.assertEqual(fake.calls[0]["tool_choice"], {"type": "function", "name": TOOL_NAME})

    def test_invalid_then_valid_retry_quotes_errors(self):
        c, fake = make_client([resp(fc(dict(GOOD, priority="urgent"))), resp(fc(GOOD))])
        r = c.report_incident(PACKET)
        self.assertEqual((r.source, len(r.attempts)), ("llm_retry", 2))
        self.assertFalse(r.attempts[0].ok)
        self.assertIn("priority", fake.calls[1]["input"][-1]["content"])

    def test_invalid_twice_falls_back_only_one_retry(self):
        c, fake = make_client([resp(fc("{not json")), resp(fc({}))])
        r = c.report_incident(PACKET)
        self.assertEqual((r.source, len(fake.calls)), ("fallback", 2))
        self.assertEqual(validate_incident(r.incident), [])

    def test_truncation_retries_with_double_budget(self):
        c, fake = make_client([resp(status="incomplete", reason="max_output_tokens"), resp(fc(GOOD))],
                              max_output_tokens=100)
        r = c.report_incident(PACKET)
        self.assertEqual(r.source, "llm_retry")
        self.assertTrue(r.attempts[0].truncated)
        self.assertEqual((fake.calls[0]["max_output_tokens"], fake.calls[1]["max_output_tokens"]), (100, 200))

    def test_transport_error_retried_without_quoting_error(self):
        c, fake = make_client([TimeoutError("boom"), resp(fc(GOOD))])
        r = c.report_incident(PACKET)
        self.assertEqual(r.source, "llm_retry")
        self.assertEqual(len(fake.calls[1]["input"]), 1)  # no "rejected" message

    def test_text_json_accepted_when_no_function_call(self):
        c, _ = make_client([NS(output=[], output_text="```json\n" + json.dumps(GOOD) + "\n```", status="completed",
                               incomplete_details=None, usage=None)])
        r = c.report_incident(PACKET)
        self.assertEqual((r.source, r.attempts[0].method), ("llm", "text_json"))

    def test_no_key_uses_fallback_without_calls(self):
        c = AdaptionClient(api_key=None, env_path="/nonexistent")
        c._client = None
        r = c.report_incident(PACKET)
        self.assertEqual((r.source, r.attempts), ("fallback", []))
        self.assertEqual(r.incident["issue_type"], "water_leak")

    def test_fallback_disabled_raises(self):
        c, _ = make_client([resp(fc({})), resp(fc({}))])
        with self.assertRaises(RuntimeError):
            c.report_incident(PACKET, fallback=False)

    def test_cache_hit_makes_no_call(self):
        with tempfile.TemporaryDirectory() as d:
            c, fake = make_client([resp(fc(GOOD))], cache_dir=d)
            c.report_incident(PACKET)
            r = c.report_incident(PACKET)
            self.assertEqual((r.source, len(fake.calls)), ("cache", 1))
            self.assertEqual(r.incident, GOOD)


class TestFallbackRules(unittest.TestCase):
    def test_always_valid_shape(self):
        for pkt in (PACKET, {"detector": "comfort", "asset": "R1", "features": {"temp_c": 18, "guest_cold_requests": 1}},
                    {"detector": "sensor_quality", "asset": "R2", "quality_flags": ["frozen"]},
                    {"detector": "room_turnaround", "asset": "R3", "features": {"housekeeping_state": "dirty"}},
                    {"detector": "unknown_thing"}, {}):
            self.assertEqual(validate_incident(rule_based_incident(pkt)), [], pkt)

    def test_meter_reset_is_not_a_leak(self):
        out = rule_based_incident({"detector": "water_baseline", "asset": "R403", "quality_flags": ["reset"],
                                   "features": {"flow_z": 99, "occupied_share": 0.0}})
        self.assertEqual(out["issue_type"], "data_quality")

    def test_dirty_room_is_never_released(self):
        out = rule_based_incident({"detector": "room_turnaround", "asset": "R3",
                                   "features": {"housekeeping_state": "dirty"}})
        self.assertNotEqual(out["action"], "release_room")


if __name__ == "__main__":
    unittest.main()
