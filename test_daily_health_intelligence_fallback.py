import importlib.util
import io
import json
import os
import sys
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch


class _OpenAIPlaceholder:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


openai_stub = types.ModuleType("openai")
openai_stub.OpenAI = _OpenAIPlaceholder
todays_plan_stub = types.ModuleType("todays_plan")
todays_plan_stub.build_todays_plan = Mock()

original_openai = sys.modules.get("openai")
original_todays_plan = sys.modules.get("todays_plan")
original_db = sys.modules.get("db")
original_daily_health_intelligence = sys.modules.get(
    "daily_health_intelligence"
)
sys.modules["openai"] = openai_stub
sys.modules["todays_plan"] = todays_plan_stub

spec = importlib.util.spec_from_file_location(
    "daily_health_intelligence_fallback_under_test",
    Path(__file__).with_name("daily_health_intelligence.py"),
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

db_stub = types.ModuleType("db")
db_stub.get_conn = Mock()
sys.modules["db"] = db_stub
sys.modules["daily_health_intelligence"] = module
store_spec = importlib.util.spec_from_file_location(
    "daily_health_intelligence_store_fallback_under_test",
    Path(__file__).with_name("daily_health_intelligence_store.py"),
)
store_module = importlib.util.module_from_spec(store_spec)
store_spec.loader.exec_module(store_module)
if original_db is None:
    sys.modules.pop("db", None)
else:
    sys.modules["db"] = original_db
if original_daily_health_intelligence is None:
    sys.modules.pop("daily_health_intelligence", None)
else:
    sys.modules["daily_health_intelligence"] = original_daily_health_intelligence

if original_openai is None:
    sys.modules.pop("openai", None)
else:
    sys.modules["openai"] = original_openai
if original_todays_plan is None:
    sys.modules.pop("todays_plan", None)
else:
    sys.modules["todays_plan"] = original_todays_plan


def _payload():
    return {
        "plan_date": "2026-09-01",
        "available_sections": ["training", "nutrition", "hydration", "sleep"],
        "training": {
            "category": "Active Recovery",
            "session_type": "Recovery",
        },
        "nutrition": {
            "calories": 2200,
            "protein_g": 180,
            "carbs_g": 210,
            "fat_g": 70,
            "priority": "Follow configured targets.",
        },
        "hydration": {
            "daily_target_display": "100 fl oz",
            "priority": "Spread hydration across the day.",
        },
        "sleep": {
            "sleep_target_display": "9.5 h",
            "time_in_bed_target_display": "10 h",
            "trend_summary": "Sleep remains below target.",
        },
        "daily_coaching_summary": {
            "headline": "PRIVATE_RECOVERY_PAYLOAD",
            "summary": "Recovery and sleep matter most today.",
            "top_priorities": [
                {"area": "sleep", "priority": "Prioritize sleep."},
            ],
            "top_actions": [
                "Keep today's session at active-recovery intensity.",
                "Create enough sleep opportunity tonight.",
            ],
            "warnings": ["Goal progress is baseline-building."],
            "confidence": "high",
        },
    }


def _success_brief():
    return {
        "date": "2026-09-01",
        "headline": "Ready",
        "today_summary": "Follow the plan.",
        "training": {"category": "wrong", "session": "wrong", "instruction": "Recover."},
        "nutrition": {"calories": 1, "protein_g": 1, "carbs_g": 1, "fat_g": 1, "instruction": "Eat."},
        "hydration": {"target": "wrong", "instruction": "Drink."},
        "sleep": {"sleep_target": "wrong", "time_in_bed_target": "wrong", "instruction": "Sleep."},
        "why_it_matters": ["Recovery."],
        "highest_impact_action": "Recover.",
        "trend_to_watch": "Sleep.",
        "confidence": "high",
        "uncertainty_note": "None.",
        "medical_safety_note": "Informational.",
    }


class _Response:
    output_text = json.dumps(_success_brief())


class _Responses:
    def __init__(self, side_effect=None):
        self.side_effect = side_effect
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if self.side_effect:
            raise self.side_effect
        return _Response()


class _Client:
    def __init__(self, side_effect=None):
        self.responses = _Responses(side_effect)


class RateLimitError(Exception):
    status_code = 429
    body = {"error": {"code": "insufficient_quota"}}


class APITimeoutError(Exception):
    pass


class APIConnectionError(Exception):
    pass


class InternalServerError(Exception):
    status_code = 503
    body = {"error": {"code": "server_error"}}


class DailyHealthIntelligenceFallbackTests(unittest.TestCase):
    def test_success_uses_ai_and_preserves_deterministic_values(self):
        client = _Client()
        with patch.object(module, "_client", return_value=client):
            result = module.generate_daily_health_intelligence(_payload())

        self.assertEqual(result["ai_synthesis_status"], "success")
        self.assertEqual(result["brief"]["training"]["category"], "Active Recovery")
        self.assertEqual(result["brief"]["nutrition"]["protein_g"], 180)
        self.assertEqual(client.responses.calls, 1)

    def test_insufficient_quota_returns_sanitized_fallback(self):
        client = _Client(RateLimitError("PRIVATE_EXCEPTION_TEXT"))
        output = io.StringIO()
        with patch.object(module, "_client", return_value=client), redirect_stdout(output):
            result = module.generate_daily_health_intelligence(_payload())

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["ai_synthesis_status"], "degraded")
        self.assertEqual(result["ai_synthesis_reason"], "insufficient_quota")
        self.assertEqual(result["brief"]["training"]["category"], "Active Recovery")
        self.assertNotIn("PRIVATE_RECOVERY_PAYLOAD", output.getvalue())
        self.assertNotIn("PRIVATE_EXCEPTION_TEXT", output.getvalue())
        self.assertEqual(output.getvalue().strip(), "AI_SYNTHESIS status=degraded reason=insufficient_quota")

    def test_timeout_connection_and_server_failure_return_fallback(self):
        for exc, reason in (
            (APITimeoutError(), "timeout"),
            (APIConnectionError(), "connection_error"),
            (InternalServerError(), "api_error"),
        ):
            with self.subTest(reason=reason), patch.object(module, "_client", return_value=_Client(exc)), redirect_stdout(io.StringIO()):
                result = module.generate_daily_health_intelligence(_payload())
            self.assertEqual(result["ai_synthesis_status"], "degraded")
            self.assertEqual(result["ai_synthesis_reason"], reason)

    def test_unexpected_programming_error_propagates(self):
        with patch.object(module, "_client", return_value=_Client(ValueError("bug"))):
            with self.assertRaisesRegex(ValueError, "bug"):
                module.generate_daily_health_intelligence(_payload())

    def test_supplied_payload_avoids_duplicate_deterministic_build(self):
        with patch.object(module, "build_daily_health_ai_payload") as build, patch.object(module, "_client", return_value=_Client()):
            module.generate_daily_health_intelligence(_payload())
        build.assert_not_called()

    def test_store_builds_once_and_persists_degraded_result(self):
        payload = _payload()
        generated = module._deterministic_fallback(
            payload,
            "insufficient_quota",
        )
        generator = Mock(return_value=generated)
        saved = {"id": 41}
        with (
            patch.object(
                store_module,
                "build_daily_health_ai_payload",
                return_value=payload,
            ) as build,
            patch.object(
                store_module,
                "save_intelligence",
                return_value=saved,
            ) as save,
        ):
            result = store_module.get_or_create_intelligence(
                generator=generator,
                force_refresh=True,
            )

        build.assert_called_once_with()
        generator.assert_called_once_with(payload)
        save.assert_called_once_with(payload, generated)
        self.assertEqual(result["ai_synthesis_status"], "degraded")
        self.assertFalse(result["cache"]["llm_called"])

    def test_openai_client_disables_sdk_retries(self):
        constructor = Mock(return_value=object())
        with patch.object(module, "OpenAI", constructor), patch.dict(os.environ, {"OPENAI_API_KEY": "test-only"}):
            module._client()
        constructor.assert_called_once_with(api_key="test-only", max_retries=0)


if __name__ == "__main__":
    unittest.main()
