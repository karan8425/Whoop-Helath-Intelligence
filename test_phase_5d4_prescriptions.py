import importlib
import sys
import types
import unittest


sys.modules.setdefault(
    "goals",
    types.SimpleNamespace(get_active_goal=lambda: None),
)
sys.modules.setdefault(
    "goal_progress",
    types.SimpleNamespace(goal_progress=lambda **kwargs: {}),
)
sys.modules.setdefault(
    "apple_health_trends",
    types.SimpleNamespace(apple_health_trends=lambda: {}),
)
sys.modules.setdefault(
    "db",
    types.SimpleNamespace(get_conn=lambda: None),
)

nutrition = importlib.import_module("nutrition_prescription")
sleep = importlib.import_module("sleep_prescription")


class NutritionPrescriptionTests(unittest.TestCase):
    def test_lean_cut_targets_and_macro_derived_calories(self):
        result = nutrition.build_nutrition_prescription(
            goal={"phase": "lean_cut"},
            trends={"body_composition": {}},
            progress={"activity": {}, "direction": "insufficient_data"},
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["protein_target_g"], 185)
        self.assertEqual(result["protein_range_g"], [180, 185])
        self.assertEqual(result["fat_target_g"], 20)
        self.assertEqual(result["carbohydrate_target_g"], 150)
        self.assertEqual(result["calorie_target"], 1520)
        self.assertEqual(result["macro_calorie_check"], 1520)
        self.assertEqual(result["adherence_status"], "not_connected")
        self.assertEqual(
            result["intake_tracking"]["status"],
            "not_connected",
        )


class SleepPrescriptionTests(unittest.TestCase):
    @staticmethod
    def _rows(recovery=75, sleep_need=None):
        rows = []
        for day in range(8, 0, -1):
            rows.append({
                "metric_date": None,
                "has_sleep": True,
                "has_recovery": True,
                "recovery_score": recovery,
                "sleep_duration_hours": 8.0,
                "sleep_performance_percentage": 90,
                "sleep_consistency_percentage": 85,
                "sleep_efficiency_percentage": 92,
                "sleep_need_hours": sleep_need if day == 8 else None,
            })
        return rows

    def test_whoop_sleep_need_is_the_anchor(self):
        result = sleep.build_sleep_prescription(
            rows=self._rows(sleep_need=8.25)
        )

        self.assertEqual(result["target_sleep_hours"], 8.25)
        self.assertEqual(result["target_sleep_minutes"], 495)
        self.assertEqual(result["sleep_need_source"], "whoop_sleep_need")
        self.assertEqual(result["recovery_band"], "green")

    def test_whoop_sleep_need_above_nine_is_not_fallback_capped(self):
        result = sleep.build_sleep_prescription(
            rows=self._rows(recovery=32, sleep_need=9.55)
        )

        self.assertEqual(result["target_sleep_hours"], 9.5)
        self.assertEqual(result["target_sleep_minutes"], 570)
        self.assertEqual(result["sleep_need_source"], "whoop_sleep_need")

    def test_extreme_and_malformed_whoop_need_are_defensive(self):
        extreme = sleep.build_sleep_prescription(
            rows=self._rows(sleep_need=99)
        )
        malformed = sleep.build_sleep_prescription(
            rows=self._rows(sleep_need="not-a-number")
        )

        self.assertEqual(extreme["target_sleep_hours"], 10.5)
        self.assertEqual(
            malformed["sleep_need_source"],
            "personal_history_fallback",
        )
        self.assertLessEqual(malformed["target_sleep_hours"], 9.0)

    def test_personal_history_fallback_without_whoop_need(self):
        result = sleep.build_sleep_prescription(rows=self._rows())

        self.assertEqual(result["target_sleep_hours"], 8.0)
        self.assertEqual(
            result["sleep_need_source"],
            "personal_history_fallback",
        )
        self.assertGreaterEqual(result["target_sleep_hours"], 7.5)
        self.assertLessEqual(result["target_sleep_hours"], 9.0)

    def test_poor_recovery_cannot_reduce_sleep(self):
        good = sleep.build_sleep_prescription(rows=self._rows(recovery=85))
        poor = sleep.build_sleep_prescription(rows=self._rows(recovery=20))

        self.assertGreaterEqual(
            poor["target_sleep_minutes"],
            good["target_sleep_minutes"],
        )

        whoop_need = sleep.build_sleep_prescription(
            rows=self._rows(recovery=20, sleep_need=9.55)
        )
        self.assertGreaterEqual(
            whoop_need["target_sleep_hours"],
            9.5,
        )


class TodaysPlanIntegrationTests(unittest.TestCase):
    def test_plan_contains_nutrition_and_sleep_without_changing_training(self):
        sys.modules.setdefault(
            "hydration_prescription",
            types.SimpleNamespace(
                build_hydration_prescription=lambda **kwargs: {
                    "status": "not_ready"
                }
            ),
        )
        sys.modules.setdefault(
            "integrations.tonal.workout_prescription",
            types.SimpleNamespace(
                build_daily_workout_prescription=lambda: {
                    "status": "not_ready",
                    "reason": "test training unchanged",
                }
            ),
        )
        plan_module = importlib.import_module("todays_plan")

        original = {
            name: getattr(plan_module, name)
            for name in (
                "get_active_goal",
                "apple_health_trends",
                "goal_progress",
                "build_daily_workout_prescription",
                "build_nutrition_prescription",
                "build_hydration_prescription",
                "build_sleep_prescription",
            )
        }
        progress = {"direction": "insufficient_data"}
        plan_module.get_active_goal = lambda: {"phase": "lean_cut"}
        plan_module.apple_health_trends = lambda: {}
        plan_module.goal_progress = lambda **kwargs: progress
        plan_module.build_daily_workout_prescription = lambda: {
            "status": "not_ready",
            "reason": "test training unchanged",
        }
        plan_module.build_nutrition_prescription = lambda **kwargs: {
            "status": "ok",
            "phase": "lean_cut",
            "calorie_target": 1520,
            "protein_target_g": 185,
            "protein_range_g": [180, 185],
            "carbohydrate_target_g": 150,
            "fat_target_g": 20,
            "macros": {"protein_g": 185, "carbs_g": 150, "fat_g": 20},
            "goal_progress": kwargs["progress"],
            "intake_tracking": {"status": "not_connected"},
            "rationale": ["configured"],
        }
        plan_module.build_hydration_prescription = lambda **kwargs: {
            "status": "not_ready"
        }
        plan_module.build_sleep_prescription = lambda **kwargs: {
            "status": "ok",
            "target_sleep_hours": 8.25,
            "target_sleep_minutes": 495,
            "sleep_need_source": "whoop_sleep_need",
            "recovery_score": 75,
            "recovery_band": "green",
            "rationale": ["WHOOP anchor"],
        }

        try:
            result = plan_module.build_todays_plan()
        finally:
            for name, value in original.items():
                setattr(plan_module, name, value)

        self.assertEqual(result["nutrition"]["calorie_target"], 1520)
        self.assertEqual(result["sleep"]["target_sleep_minutes"], 495)
        self.assertEqual(result["training"]["reason"], "test training unchanged")
        self.assertIs(result["nutrition"]["goal_progress"], progress)


if __name__ == "__main__":
    unittest.main()
