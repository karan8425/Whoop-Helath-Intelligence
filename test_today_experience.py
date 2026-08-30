from copy import deepcopy
import importlib
import sys
import types
import unittest

from daily_coaching_summary import build_daily_coaching_summary


sys.modules.setdefault(
    "todays_plan",
    types.SimpleNamespace(build_todays_plan=lambda: {}),
)
today_experience = importlib.import_module("today_experience")


def _plan():
    return {
        "status": "ok",
        "version": "1.2",
        "plan_date": "2026-08-30",
        "available_sections": [
            "training", "nutrition", "hydration", "sleep",
        ],
        "training": {
            "status": "ok",
            "available": True,
            "category": "Active Recovery",
            "recovery_score": 32,
            "session_type": "Recovery Session",
            "primary_focus": ["Mobility"],
            "total_sets": 6,
            "exercise_count": 3,
            "exercises": [{"name": "Full detail must not leak"}],
            "action": {"label": "View Workout", "destination": "training"},
        },
        "nutrition": {
            "status": "ok",
            "available": True,
            "phase": "lean_cut",
            "calories": 1520,
            "protein_g": 185,
            "carbs_g": 150,
            "fat_g": 20,
            "current_weight_lb": 194.0,
            "intake_tracking_status": "not_connected",
            "priority": "Follow the configured lean-cut prescription.",
            "activity": {
                "status": "on_track",
                "average_steps_7d": 10100,
                "target_steps": 10000,
                "percentage_of_target": 101.0,
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
            "sleep_target_display": "9h 30m",
            "time_in_bed_target_display": "10h 20m",
            "recommended_bedtime": "9:40 PM",
            "wake_time": "8:00 AM",
            "recovery_score": 32,
            "recovery_band": "red",
            "recent_sleep_average": 6.17,
            "trend_summary": "Recent sleep remains materially below target.",
        },
        "hydration": {
            "status": "ok",
            "available": True,
            "daily_target_display": "100 oz",
            "priority": "Spread hydration across the day.",
        },
    }


class TodayExperienceTests(unittest.TestCase):
    def setUp(self):
        self.plan = _plan()
        self.coaching = build_daily_coaching_summary(self.plan)
        self.result = today_experience.build_today_experience(
            plan=self.plan,
            coaching_summary=self.coaching,
        )

    def test_contains_all_required_cards(self):
        self.assertEqual(
            set(self.result["cards"]),
            {
                "recovery", "training", "nutrition", "sleep",
                "hydration", "activity", "goal_progress",
            },
        )

    def test_training_card_excludes_exercises(self):
        self.assertNotIn("exercises", self.result["cards"]["training"])
        self.assertEqual(
            self.result["cards"]["training"]["action"],
            {"label": "View Workout", "destination": "training"},
        )

    def test_coaching_fields_exactly_match_canonical_summary(self):
        for key in ("overall_state", "headline", "summary", "top_actions"):
            self.assertEqual(self.result[key], self.coaching[key])

    def test_nutrition_targets_are_unchanged(self):
        card = self.result["cards"]["nutrition"]
        self.assertEqual(card["calories"], 1520)
        self.assertEqual(card["protein_g"], 185)
        self.assertEqual(card["carbs_g"], 150)
        self.assertEqual(card["fat_g"], 20)
        self.assertEqual(card["intake_tracking_status"], "not_connected")

    def test_sleep_preserves_schedule(self):
        card = self.result["cards"]["sleep"]
        self.assertEqual(card["recommended_bedtime"], "9:40 PM")
        self.assertEqual(card["wake_time"], "8:00 AM")

    def test_on_track_activity_remains_on_track(self):
        card = self.result["cards"]["activity"]
        self.assertEqual(card["status"], "on_track")
        self.assertIn("on track", card["status_text"])

    def test_baseline_goal_does_not_claim_progress_or_regression(self):
        card = self.result["cards"]["goal_progress"]
        self.assertEqual(card["status"], "baseline_building")
        self.assertIn("classification is withheld", card["summary"])

    def test_builder_does_not_mutate_todays_plan(self):
        plan = _plan()
        original = deepcopy(plan)
        today_experience.build_today_experience(plan=plan)
        self.assertEqual(plan, original)


if __name__ == "__main__":
    unittest.main()
