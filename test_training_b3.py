import unittest
from datetime import date, timedelta

from body_composition_strategy import classify
from integrations.tonal.progressive_overload import comparable_history, prescribe, trajectory
from todays_plan import _training_card


def session(reps, load=50, volume=None, mode="standard", sets=3):
    return {
        "set_count": sets, "total_reps": reps,
        "total_volume": volume if volume is not None else reps * load,
        "median_base_weight": load, "mode_counts": {mode: sets},
    }


def profile(sessions, name="Bench Press", muscles=None):
    return {"name": name, "muscle_groups": muscles or ["Chest", "Triceps"], "recent_sessions": sessions}


class ProgressionTests(unittest.TestCase):
    def test_rep_progression(self):
        out = prescribe(profile([session(30), session(29), session(28)]), "high", 3)
        self.assertEqual(out["progression_state"], "PROGRESS_REPS")

    def test_load_progression_after_upper_range_mastery(self):
        out = prescribe(profile([session(36), session(36), session(35)]), "high", 3)
        self.assertEqual(out["progression_state"], "PROGRESS_LOAD")
        self.assertEqual(out["target_resistance_lb"], 51.0)

    def test_hold_at_moderate_capacity(self):
        out = prescribe(profile([session(30), session(30), session(30)]), "moderate", 3)
        self.assertEqual(out["progression_state"], "HOLD")

    def test_rebuild_with_insufficient_history(self):
        self.assertEqual(prescribe(profile([session(30)]), "high", 3)["progression_state"], "REBUILD")

    def test_multi_session_decline_reduces_on_low_capacity(self):
        out = prescribe(profile([session(24), session(25), session(34), session(35)]), "low", 4)
        self.assertEqual(out["progression_state"], "REDUCE")
        self.assertEqual(out["sets"], 3)

    def test_single_poor_session_does_not_establish_decline(self):
        self.assertEqual(trajectory([session(20), session(32)]), "INSUFFICIENT_DATA")

    def test_smart_weight_modes_are_not_silently_mixed(self):
        sessions, _ = comparable_history(profile([
            session(30, mode="eccentric"), session(30, mode="standard"), session(31, mode="eccentric")
        ]))
        self.assertEqual(len(sessions), 2)

    def test_isolation_has_shorter_rest_and_lower_rir(self):
        out = prescribe(profile([session(30), session(31)], name="Biceps Curl"), "high", 3)
        self.assertLess(out["rest_seconds"]["maximum"], 180)
        self.assertEqual(out["target_rir"]["minimum"], 0)


class BodyStrategyTests(unittest.TestCase):
    def rows(self, old, new):
        today = date(2026, 9, 7)
        return [{"date": today - timedelta(days=25), "value": old}, {"date": today, "value": new}]

    def goal(self):
        return {"goal_type": "lose_body_fat"}

    def test_plan_working(self):
        out = classify(self.goal(), {"fat_mass": self.rows(40, 37), "lean_mass": self.rows(145, 145)})
        self.assertEqual(out["training_strategy_signal"], "PLAN_WORKING")

    def test_lean_mass_risk_requires_performance_decline(self):
        out = classify(self.goal(), {"fat_mass": self.rows(40, 37), "lean_mass": self.rows(145, 141)}, "DECLINING")
        self.assertEqual(out["training_strategy_signal"], "LEAN_MASS_RISK")

    def test_possible_recomposition(self):
        out = classify(self.goal(), {"weight": self.rows(185, 185), "body_fat_percentage": self.rows(21, 20), "lean_mass": self.rows(145, 147)})
        self.assertEqual(out["training_strategy_signal"], "POSSIBLE_RECOMPOSITION")

    def test_one_measurement_is_insufficient(self):
        out = classify(self.goal(), {"fat_mass": [{"date": date(2026, 9, 7), "value": 38}]})
        self.assertEqual(out["classification"], "INSUFFICIENT_DATA")

    def test_regional_fat_never_drives_selection(self):
        out = classify(self.goal(), {})
        self.assertEqual(out["regional_fat"]["status"], "regional_history_not_available")
        self.assertNotIn("Core", str(out))


class ApiPassThroughTests(unittest.TestCase):
    def test_today_card_preserves_b3_contract(self):
        movement = {
            "movement_id": "m1", "name": "Bench Press", "muscle_groups": ["Chest"],
            "smart_weight": {}, "hardware_context": {}, "historical_context": {},
            "rep_range": {"minimum": 8, "maximum": 12},
            "rir_range": {"minimum": 1, "maximum": 2},
            "rest_seconds": {"minimum": 120, "maximum": 180},
            "progression_state": "PROGRESS_REPS", "progression_label": "ADD REPS",
        }
        card = _training_card({
            "status": "ok", "readiness": {}, "progression_policy": {},
            "session": {"exercises": [movement], "training_b3": {"dose_classification": "HIGH_PRODUCTIVE_DOSE"}},
        })
        self.assertEqual(card["exercises"][0]["progression_state"], "PROGRESS_REPS")
        self.assertEqual(card["training_b3"]["dose_classification"], "HIGH_PRODUCTIVE_DOSE")

    def test_volume_repair_contract_is_pinned_in_engine(self):
        from pathlib import Path
        source = Path(__file__).with_name("integrations").joinpath(
            "tonal", "workout_prescription.py"
        ).read_text()
        self.assertIn("B3 final volume repair", source)
        self.assertIn("exercise[\"sets\"] -= removable", source)


if __name__ == "__main__":
    unittest.main()
