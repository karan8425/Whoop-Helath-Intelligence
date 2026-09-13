"""TKI-6: schema-adapter field-mapping tests
(training_intelligence.prescription.mobile_adapter).

Pure/in-memory - the adapter takes an already-built TKI-5.1 result
(never calls build_shadow_prescription itself), so no database/mocking
of the shadow engine is required here.
"""

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from training_intelligence.prescription.mobile_adapter import adapt_to_workout_schema
from training_intelligence.selection.shadow_selection import REST_FAMILY_NAME

AS_OF = datetime(2026, 9, 12, 8, 0, tzinfo=timezone.utc)


def _tki_exercise(movement_id="m1", name="Barbell Bent Over Row", sets=3, reps=10,
                   resistance=57.5, progression_state="HOLD", role="primary"):
    return {
        "movement_id": movement_id,
        "movement_name": name,
        "role": role,
        "primary_muscles": ["Back"],
        "secondary_muscles": ["Biceps"],
        "working_sets": sets,
        "rep_range": {"minimum": 8, "maximum": 12},
        "target_reps_per_set": reps,
        "prescribed_resistance_lb": resistance,
        "target_rir": {"minimum": 1, "maximum": 3},
        "rest_seconds": {"minimum": 120, "maximum": 180},
        "progression_action": "HOLD",
        "progression_state": progression_state,
        "progression_reason": "Repeat productive work.",
        "goal_posture_applied": 0.5,
        "comparable_history": {
            "count": 6, "smart_weight_mode": "standard",
            "latest_sets": sets, "latest_total_reps": reps * sets,
            "latest_resistance_lb": resistance,
        },
        "performance_state": "STABLE",
        "confidence": "HIGH",
        "is_bilateral": True,
        "is_two_sided": False,
        "absorption_cap": 5,
        "initial_allocation": sets,
    }


def _tki_result(exercises=None, family="Upper Pull", target_sets=9, allocated=9, shortfall=0,
                 shortfall_reason=None, adaptations=None, fallbacks=None):
    exercises = exercises if exercises is not None else [_tki_exercise()]
    return {
        "status": "ok",
        "selected_session_family": family,
        "goal_mode": "lean_cut",
        "dose": {
            "working_sets": target_sets,
            "exercise_count": len(exercises),
            "feasible_range": {"lower_bound_working_sets": target_sets, "upper_bound_working_sets": target_sets},
        } if family != REST_FAMILY_NAME else None,
        "dose_absorption": {
            "dose_target": target_sets, "dose_allocated": allocated,
            "dose_shortfall": shortfall, "shortfall_reason": shortfall_reason,
        } if family != REST_FAMILY_NAME else None,
        "exercises": exercises,
        "adaptations_used": adaptations or [],
        "fallbacks_used": fallbacks or [],
        "explanation_factors": ["Session family selected from TKI-4."],
        "data_quality": {"selection_confidence": "high"},
    }


class AdapterShapeTests(unittest.TestCase):
    def _adapt(self, tki_result):
        with patch(
            "training_intelligence.prescription.mobile_adapter._latest_readiness",
            return_value={"recovery_score": 70.0, "readiness_band": "good", "training_category": "Normal"},
        ):
            return adapt_to_workout_schema(AS_OF, tki_result)

    def test_top_level_shape_matches_b3(self):
        workout = self._adapt(_tki_result())
        self.assertEqual(set(workout.keys()), {"status", "generated_at", "readiness", "session", "progression_policy"})
        self.assertEqual(workout["status"], "ok")

    def test_movement_name_mapping(self):
        workout = self._adapt(_tki_result())
        self.assertEqual(workout["session"]["exercises"][0]["name"], "Barbell Bent Over Row")

    def test_sets_mapping(self):
        workout = self._adapt(_tki_result(exercises=[_tki_exercise(sets=5)]))
        self.assertEqual(workout["session"]["exercises"][0]["sets"], 5)

    def test_reps_mapping(self):
        workout = self._adapt(_tki_result(exercises=[_tki_exercise(reps=11)]))
        self.assertEqual(workout["session"]["exercises"][0]["reps_per_set"], 11)

    def test_resistance_mapping(self):
        workout = self._adapt(_tki_result(exercises=[_tki_exercise(resistance=62.5)]))
        self.assertEqual(workout["session"]["exercises"][0]["target_weight_lb"], 62.5)

    def test_exercise_ordering_preserved(self):
        exercises = [_tki_exercise("m1", "First"), _tki_exercise("m2", "Second"), _tki_exercise("m3", "Third")]
        workout = self._adapt(_tki_result(exercises=exercises))
        self.assertEqual([e["name"] for e in workout["session"]["exercises"]], ["First", "Second", "Third"])

    def test_session_type_mapping(self):
        workout = self._adapt(_tki_result(family="Lower Body"))
        self.assertEqual(workout["session"]["session_type"], "Lower Body")

    def test_missing_resistance_produces_none_volume_not_zero(self):
        exercise = _tki_exercise(resistance=None)
        workout = self._adapt(_tki_result(exercises=[exercise]))
        self.assertIsNone(workout["session"]["exercises"][0]["estimated_volume"])

    def test_estimated_total_volume_formula_matches_b3_semantics(self):
        # B3: sum(resistance * reps * sets), rounded to 1 decimal, None treated as 0.
        exercises = [_tki_exercise("m1", "A", sets=2, reps=8, resistance=70.0),
                     _tki_exercise("m2", "B", sets=3, reps=10, resistance=50.0)]
        workout = self._adapt(_tki_result(exercises=exercises))
        expected = round((70.0 * 8 * 2) + (50.0 * 10 * 3), 1)
        self.assertEqual(workout["session"]["estimated_total_volume"], expected)

    def test_rest_family_produces_valid_empty_session(self):
        workout = self._adapt(_tki_result(family=REST_FAMILY_NAME, exercises=[]))
        self.assertEqual(workout["session"]["exercises"], [])
        self.assertEqual(workout["session"]["total_sets"], 0)
        self.assertEqual(workout["session"]["estimated_total_volume"], 0.0)

    def test_smart_weight_hardware_historical_context_always_present(self):
        """These are non-optional STRUCTS on the iOS side - must always
        be emitted as objects, never omitted, even with all-null inner
        fields."""
        workout = self._adapt(_tki_result())
        exercise = workout["session"]["exercises"][0]
        self.assertIn("smart_weight", exercise)
        self.assertIn("hardware_context", exercise)
        self.assertIn("historical_context", exercise)
        self.assertIsInstance(exercise["smart_weight"], dict)
        self.assertIsInstance(exercise["hardware_context"], dict)
        self.assertIsInstance(exercise["historical_context"], dict)

    def test_target_set_range_present(self):
        workout = self._adapt(_tki_result(target_sets=9))
        self.assertEqual(workout["session"]["target_set_range"]["target"], 9)

    def test_progression_policy_present_and_non_empty(self):
        workout = self._adapt(_tki_result())
        self.assertIn("progression_ladder", workout["progression_policy"])
        self.assertTrue(workout["progression_policy"]["progression_ladder"])

    def test_movement_id_and_name_never_none(self):
        """iOS TrainingExercise.movementId/name are non-optional - must
        never be null."""
        workout = self._adapt(_tki_result())
        exercise = workout["session"]["exercises"][0]
        self.assertIsNotNone(exercise["movement_id"])
        self.assertIsNotNone(exercise["name"])

    def test_muscle_groups_never_none(self):
        workout = self._adapt(_tki_result())
        exercise = workout["session"]["exercises"][0]
        self.assertIsInstance(exercise["muscle_groups"], list)

    def test_deterministic(self):
        result = _tki_result()
        first = self._adapt(result)
        second = self._adapt(result)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
