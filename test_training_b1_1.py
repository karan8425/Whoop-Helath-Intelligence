import sys
import types
import unittest
from unittest.mock import Mock


db_stub = types.ModuleType("db")
db_stub.get_conn = Mock()
sys.modules.setdefault("db", db_stub)

from integrations.tonal.workout_prescription import (
    SESSION_RULES,
    _movement_eligibility,
    _select_movements,
    _set_allocation,
)
import todays_plan_store


def profile(name, muscles, *, status="usable", incidental=None, movement_id=None):
    return {
        "movement_id": movement_id or name,
        "name": name,
        "muscle_groups": muscles,
        "incidental_muscles": incidental or [],
        "history": {"sessions_in_lookback": 5},
        "performance": {"status": status, "progression_earned": False},
    }


class MovementEligibilityTests(unittest.TestCase):
    def test_rdl_excluded_when_hamstrings_glutes_suppressed(self):
        candidate = profile("Barbell RDL", ["Hamstrings", "Glutes", "Abs"])
        selected = _select_movements([candidate], ["Back", "Triceps", "Chest"], [], ["Hamstrings", "Glutes"])
        self.assertEqual(selected, [])

    def test_split_squat_excluded_when_glutes_suppressed(self):
        candidate = profile("Split Squat", ["Quads", "Glutes", "Obliques"])
        selected = _select_movements([candidate], ["Quads"], [], ["Glutes"])
        self.assertEqual(selected, [])

    def test_squat_row_excluded_when_lower_dominates(self):
        candidate = profile("Squat with Row", ["Glutes", "Quads", "Back"])
        selected = _select_movements([candidate], ["Back"], [], ["Glutes"])
        self.assertEqual(selected, [])

    def test_compatible_back_movement_preferred(self):
        good = profile("Neutral Pulldown", ["Back", "Biceps"])
        bad = profile("Squat with Row", ["Glutes", "Quads", "Back"])
        selected = _select_movements([bad, good], ["Back"], [], ["Glutes"])
        self.assertEqual(selected, [good])

    def test_suppressed_primary_never_eligible(self):
        eligible, _ = _movement_eligibility(profile("Curl", ["Biceps"]), ["Biceps"], ["Biceps"])
        self.assertFalse(eligible)

    def test_material_suppressed_secondary_excluded(self):
        eligible, _ = _movement_eligibility(profile("Row", ["Back", "Biceps"]), ["Back"], ["Biceps"])
        self.assertFalse(eligible)

    def test_explicit_incidental_secondary_can_remain(self):
        candidate = profile("Row", ["Back", "Biceps"], incidental=["Biceps"])
        eligible, _ = _movement_eligibility(candidate, ["Back"], ["Biceps"])
        self.assertTrue(eligible)

    def test_insufficient_pool_returns_smaller_workout(self):
        good = profile("Chest Press", ["Chest", "Triceps"])
        bad = profile("Deadlift", ["Hamstrings", "Glutes"])
        selected = _select_movements([good, bad], ["Chest", "Back"], [], ["Hamstrings", "Glutes"])
        self.assertEqual(selected, [good])

    def test_target_muscle_must_be_primary(self):
        eligible, _ = _movement_eligibility(profile("Squat Row", ["Glutes", "Back"]), ["Back"], [])
        self.assertFalse(eligible)

    def test_unusable_history_is_excluded(self):
        eligible, _ = _movement_eligibility(profile("Pulldown", ["Back"], status="insufficient"), ["Back"], [])
        self.assertFalse(eligible)

    def test_active_recovery_ceiling(self):
        selected = [profile(str(i), ["Back"], movement_id=str(i)) for i in range(3)]
        allocations = _set_allocation(selected, "low")
        self.assertLessEqual(len(selected), SESSION_RULES["low"]["max_exercises"])
        self.assertLessEqual(sum(allocations), SESSION_RULES["low"]["max_sets"])
        self.assertEqual(SESSION_RULES["low"]["target_rir"], "3-4")

    def test_high_recovery_does_not_change_suppression(self):
        candidate = profile("Chest Press", ["Chest"])
        for _band in ("low", "moderate", "good", "high"):
            eligible, _ = _movement_eligibility(candidate, ["Chest"], ["Chest"])
            self.assertFalse(eligible)

    def test_non_suppressed_lower_body_remains_valid(self):
        lower = [
            profile("Goblet Squat", ["Quads", "Glutes"]),
            profile("Barbell RDL", ["Hamstrings", "Glutes"]),
        ]
        selected = _select_movements(lower, ["Quads", "Hamstrings"], [], [])
        self.assertEqual(len(selected), 2)

    def test_cache_version_rejects_pre_b1_rows(self):
        self.assertEqual(todays_plan_store.PLAN_VERSION, 2)


if __name__ == "__main__":
    unittest.main()
