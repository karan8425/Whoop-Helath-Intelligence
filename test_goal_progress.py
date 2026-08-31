import importlib
import sys
import types
import unittest


sys.modules.setdefault(
    "goals",
    types.SimpleNamespace(get_active_goal=lambda: None),
)
sys.modules.setdefault(
    "apple_health_trends",
    types.SimpleNamespace(apple_health_trends=lambda: {}),
)
sys.modules.setdefault(
    "body_composition_progress",
    types.SimpleNamespace(body_composition_progress=lambda: {}),
)
sys.modules.setdefault(
    "integrations.tonal.strength_adherence",
    types.SimpleNamespace(
        strength_adherence=lambda target: {
            "status": "not_connected",
            "target_sessions_per_week": target,
        }
    ),
)

goal_progress_module = importlib.import_module("goal_progress")


class GoalProgressDerivedCompositionTests(unittest.TestCase):
    def setUp(self):
        self.goal = {
            "phase": "lean_cut",
            "phase_start_date": "2026-08-30",
            "phase_start_weight_lb": 200,
            "phase_start_body_fat_percentage": 20,
            "target_weight_lb": 180,
            "target_body_fat_percentage": 15,
            "daily_step_target": None,
            "strength_sessions_per_week": 3,
            "protein_target_grams": 180,
        }
        self.trends = {
            "body_composition": {
                "weight": {
                    "available": True,
                    "current_value": 88,
                },
                "body_fat_percentage": {
                    "available": True,
                    "current_value": 19,
                    "windows": {},
                },
            },
            "activity": {},
        }

    @staticmethod
    def _metric(start, current, target, progress, raw):
        available = target is not None
        return {
            "phase_start_value": start,
            "goal_current_value": current,
            "target_value": target,
            "goal_direction": "decrease" if available else None,
            "progress": {
                "available": available,
                "progress_percentage": progress,
                "raw_progress_percentage": raw,
                "state": "in_progress" if available else "insufficient_data",
            },
            "horizons": {
                "Goal": {
                    "status": "progressing" if available else "insufficient_data",
                },
            },
        }

    def test_exposes_day_zero_derived_goal_values(self):
        body_progress = {
            "metrics": {
                "fat_mass": self._metric(40, 39.5, 27, 3.8, 3.8),
                "lean_mass": self._metric(160, 159.2, 153, 11.4, 11.4),
            },
        }

        original = goal_progress_module.body_composition_progress
        calls = []

        def source_of_truth():
            calls.append(True)
            return body_progress

        goal_progress_module.body_composition_progress = source_of_truth
        try:
            result = goal_progress_module.goal_progress(
                self.goal,
                self.trends,
            )
        finally:
            goal_progress_module.body_composition_progress = original

        self.assertIn("fat_mass", result)
        self.assertIn("lean_mass", result)
        self.assertEqual(calls, [True])
        self.assertEqual(result["fat_mass"]["current_lb"], 39.5)
        self.assertEqual(result["lean_mass"]["current_lb"], 159.2)
        self.assertEqual(result["fat_mass"]["progress_percentage"], 3.8)

    def test_no_target_keeps_progress_unavailable(self):
        body_progress = {
            "metrics": {
                "fat_mass": self._metric(40, 39.5, None, None, None),
                "lean_mass": self._metric(160, 159.2, None, None, None),
            },
        }

        result = goal_progress_module.goal_progress(
            self.goal,
            self.trends,
            body_progress,
        )

        for key in ("fat_mass", "lean_mass"):
            self.assertIsNone(result[key]["target_lb"])
            self.assertIsNone(result[key]["progress_percentage"])
            self.assertIsNone(result[key]["raw_progress_percentage"])
            self.assertEqual(result[key]["state"], "insufficient_data")

    def test_existing_body_fat_and_weight_behavior_is_unchanged(self):
        result = goal_progress_module.goal_progress(
            self.goal,
            self.trends,
            {"metrics": {}},
        )

        self.assertEqual(result["body_fat"]["current_percentage"], 19)
        self.assertEqual(result["body_fat"]["progress_percentage"], 20)
        self.assertEqual(result["weight"]["current_lb"], 194)
        self.assertEqual(result["weight"]["progress_percentage"], 30)

    def test_exposes_tonal_strength_adherence_without_changing_protein(self):
        strength = {
            "status": "target_met",
            "sessions_7d": 3,
            "qualifying_sessions_7d": 3,
            "supplemental_sessions_7d": 0,
            "total_strength_activities_7d": 3,
            "target_sessions_per_week": 3,
            "percentage_of_target": 100.0,
            "remaining_sessions": 0,
            "window_start_date": "2026-08-24",
            "window_end_date": "2026-08-30",
        }

        result = goal_progress_module.goal_progress(
            self.goal,
            self.trends,
            {"metrics": {}},
            strength,
        )

        self.assertEqual(result["strength"], strength)
        self.assertEqual(
            result["protein"],
            {
                "status": "not_connected",
                "target_grams_per_day": 180,
            },
        )
        self.assertIn(
            "Strength adherence uses distinct qualifying Tonal sessions",
            result["data_notes"][-2],
        )
        self.assertIn(
            "supplemental strength activity",
            result["data_notes"][-2],
        )
        self.assertIn("Protein adherence", result["data_notes"][-1])


if __name__ == "__main__":
    unittest.main()
