from copy import deepcopy
import importlib
import sys
import types
import unittest

from daily_coaching_summary import build_daily_coaching_summary


def _production_scenario():
    return {
        "status": "ok",
        "plan_date": "2026-08-30",
        "training": {
            "status": "ok",
            "available": True,
            "category": "Active Recovery",
            "session_type": "Recovery Session",
            "recovery_score": 32,
        },
        "nutrition": {
            "status": "ok",
            "available": True,
            "phase": "lean_cut",
            "calorie_target": 1520,
            "protein_target_g": 185,
            "intake_tracking_status": "not_connected",
            "activity": {
                "status": "on_track",
                "average_steps_7d": 10000,
                "target_steps": 10000,
            },
            "goal_progress": {
                "direction": "insufficient_data",
                "phase_age_days": 0,
            },
        },
        "sleep": {
            "status": "ok",
            "available": True,
            "target_sleep_hours": 9.5,
            "target_sleep_minutes": 570,
            "recent_sleep_average": 6.17,
            "recovery_score": 32,
            "recovery_band": "red",
        },
        "hydration": {
            "status": "ok",
            "available": True,
            "daily_target_display": "100 oz",
        },
    }


class DailyCoachingSummaryTests(unittest.TestCase):
    def test_red_recovery_and_sleep_gap_are_first(self):
        result = build_daily_coaching_summary(_production_scenario())

        self.assertEqual(result["overall_state"], "recovery_first")
        self.assertEqual(result["top_priorities"][0]["area"], "recovery_sleep")
        self.assertIn("sleep", result["headline"].lower())
        self.assertIn(
            "nutrition",
            {item["area"] for item in result["top_priorities"]},
        )

    def test_active_recovery_is_preserved_and_actionable(self):
        plan = _production_scenario()
        result = build_daily_coaching_summary(plan)

        self.assertEqual(result["training"], plan["training"])
        self.assertEqual(result["training"]["category"], "Active Recovery")
        self.assertIn("active-recovery", result["top_actions"][0])
        self.assertIn("do not chase progression", result["top_actions"][0])

    def test_on_track_activity_is_not_escalated(self):
        result = build_daily_coaching_summary(_production_scenario())

        areas = {item["area"] for item in result["top_priorities"]}
        self.assertNotIn("activity", areas)
        self.assertFalse(any("activity" in warning.lower() for warning in result["warnings"]))

    def test_insufficient_goal_progress_is_baseline_building(self):
        result = build_daily_coaching_summary(_production_scenario())

        self.assertEqual(result["goal_progress"]["status"], "baseline_building")
        self.assertIn("classification is withheld", result["summary"])

    def test_nutrition_adherence_is_not_inferred(self):
        result = build_daily_coaching_summary(_production_scenario())

        self.assertEqual(
            result["nutrition"]["intake_tracking_status"],
            "not_connected",
        )
        self.assertTrue(any("adherence is unknown" in item for item in result["warnings"]))

    def test_priority_and_action_limits(self):
        result = build_daily_coaching_summary(_production_scenario())

        self.assertLessEqual(len(result["top_priorities"]), 3)
        self.assertGreaterEqual(len(result["top_actions"]), 1)
        self.assertLessEqual(len(result["top_actions"]), 2)

    def test_summary_does_not_mutate_todays_plan(self):
        plan = _production_scenario()
        original = deepcopy(plan)

        build_daily_coaching_summary(plan)

        self.assertEqual(plan, original)

    def test_daily_health_payload_exposes_canonical_summary(self):
        plan = _production_scenario()
        sys.modules.setdefault(
            "openai",
            types.SimpleNamespace(OpenAI=object),
        )
        sys.modules.setdefault(
            "todays_plan",
            types.SimpleNamespace(build_todays_plan=lambda: plan),
        )
        module = importlib.import_module("daily_health_intelligence")
        original = module.build_todays_plan
        module.build_todays_plan = lambda: plan
        try:
            payload = module.build_daily_health_ai_payload()
        finally:
            module.build_todays_plan = original

        self.assertEqual(
            payload["daily_coaching_summary"]["overall_state"],
            "recovery_first",
        )


if __name__ == "__main__":
    unittest.main()
