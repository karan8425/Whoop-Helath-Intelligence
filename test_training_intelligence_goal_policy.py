"""Goal Policy Architecture tests.

Covers both the standalone registry (training_intelligence.dose.goal_policy)
and its integration into build_shadow_dose - specifically the corrected
architecture:

  1. A goal-AGNOSTIC feasible dose range (recommended_dose implied by
     build_shadow_dose's `feasible_dose_range`) is established FIRST, from
     history/comparable-sessions/systemic readiness/local readiness/recent
     performance ONLY - identical across every goal mode for the same
     state.
  2. Goal policy then selects a POSITION inside that already-established
     range (range_position_fraction) - it never rewrites the range, never
     invents capacity, never overrides local fatigue (a fatigue-collapsed
     range stays collapsed regardless of goal mode), and can never push
     the final recommendation outside [lower_bound, upper_bound].
  3. The final goal-adjusted recommended_dose.working_sets MAY differ
     across goal modes for the identical history/readiness state - this
     is the intended behavior now, not a foundation-safety violation.
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from training_intelligence.dose.goal_policy import (
    GOAL_MODES,
    GOAL_POLICIES,
    GOAL_POLICY_VERSION,
    get_goal_policy,
    normalize_goal_mode,
    resolve_goal_mode,
    LEAN_CUT,
    LEAN_BULK,
    STRENGTH,
    MAINTENANCE,
    GENERAL_FITNESS,
    RECOVERY,
)
from training_intelligence.dose.shadow_dose import build_shadow_dose

AS_OF = datetime(2026, 9, 12, 8, 10, tzinfo=timezone.utc)


def _session(label, days_ago, workout_type="Upper Pull", set_count=20, movement_count=4,
             total_reps=160, total_volume=10000.0, duration_seconds=2400):
    return {
        "activity_id": label,
        "begin_time": AS_OF - timedelta(days=days_ago),
        "workout_type": workout_type,
        "duration_seconds": duration_seconds,
        "total_reps": total_reps,
        "total_volume": total_volume,
        "set_count": set_count,
        "movement_count": movement_count,
        "included": True,
        "exclusion_reason": None,
    }


def _muscle_row(label, days_ago, muscle, volume=200.0):
    return {
        "activity_id": label,
        "begin_time": AS_OF - timedelta(days=days_ago),
        "included": True,
        "exclusion_reason": None,
        "volume": volume,
        "muscle_groups": [muscle],
    }


def _rich_fixture():
    """4 comparable "Upper Pull" sessions (set_count=20) plus dense
    Back/Biceps muscle-set history within the 14-day window, so the
    per-muscle readiness budget is generous enough that it is NOT the
    binding constraint - only then does the WHOOP-band-driven feasible
    range have real width for different goal-mode fractions to select
    different rounded working-set values within."""
    sessions = [_session(f"s{i}", days_ago=3 + i * 6) for i in range(4)]
    muscle_rows = []
    for i in range(20):
        day = 1 + i * 0.6
        muscle_rows.append(_muscle_row(f"back{i}", day, "Back"))
        muscle_rows.append(_muscle_row(f"bi{i}", day, "Biceps"))
    return sessions, muscle_rows


def _patched_readiness_and_muscles(recovery_score=40.0, readiness_band="low"):
    muscles = [
        {"muscle": "Back", "readiness_state": "FRESH", "readiness_score": 90.0,
         "hours_since_primary_exposure": 200.0},
        {"muscle": "Biceps", "readiness_state": "FRESH", "readiness_score": 90.0,
         "hours_since_primary_exposure": 200.0},
    ]
    return (
        patch(
            "training_intelligence.dose.shadow_dose._latest_readiness",
            return_value={"recovery_score": recovery_score, "readiness_band": readiness_band},
        ),
        patch(
            "training_intelligence.dose.shadow_dose.calculate_muscle_readiness",
            return_value={"selection_confidence": "high", "latest_workout_age_hours": 24.0, "muscles": muscles},
        ),
    )


class GoalPolicyRegistryTests(unittest.TestCase):
    def test_all_six_required_modes_are_defined(self):
        for mode in (LEAN_CUT, LEAN_BULK, STRENGTH, MAINTENANCE, GENERAL_FITNESS, RECOVERY):
            self.assertIn(mode, GOAL_MODES)
            self.assertIn(mode, GOAL_POLICIES)

    def test_every_policy_defines_all_required_dimensions(self):
        required_fields = (
            "training_objective", "range_position_fraction", "stimulus_posture",
            "volume_posture", "progression_emphasis", "fatigue_tolerance",
            "intensity_volume_tradeoff", "maintenance_vs_growth_priority",
        )
        for mode, policy in GOAL_POLICIES.items():
            for field in required_fields:
                self.assertIn(field, policy, f"{mode} missing {field}")
                self.assertIsNotNone(policy[field], f"{mode}.{field} is None")

    def test_range_position_fractions_are_within_zero_one(self):
        for mode, policy in GOAL_POLICIES.items():
            self.assertGreaterEqual(policy["range_position_fraction"], 0.0, mode)
            self.assertLessEqual(policy["range_position_fraction"], 1.0, mode)

    def test_aliases_resolve_to_canonical_modes(self):
        self.assertEqual(normalize_goal_mode("hypertrophy_gain"), LEAN_BULK)
        self.assertEqual(normalize_goal_mode("return_to_training"), RECOVERY)
        self.assertEqual(normalize_goal_mode(LEAN_CUT), LEAN_CUT)

    def test_unknown_mode_normalizes_to_none_never_guessed(self):
        self.assertIsNone(normalize_goal_mode("made_up_mode"))

    def test_get_goal_policy_unresolved_mode_is_explicit_not_a_default(self):
        policy = get_goal_policy(None)
        self.assertIsNone(policy["training_objective"])
        self.assertIsNone(policy["range_position_fraction"])
        policy_unknown = get_goal_policy("not_a_real_mode")
        self.assertIsNone(policy_unknown["training_objective"])

    def test_resolve_goal_mode_prefers_goal_type_over_phase(self):
        goal = {"phase": "maintenance", "goal_type": "build_muscle"}
        self.assertEqual(resolve_goal_mode(goal), LEAN_BULK)

    def test_resolve_goal_mode_falls_back_to_phase(self):
        goal = {"phase": "lean_bulk", "goal_type": None}
        self.assertEqual(resolve_goal_mode(goal), LEAN_BULK)

    def test_resolve_goal_mode_none_for_unrecognized_goal(self):
        goal = {"phase": "unknown", "goal_type": "unknown"}
        self.assertIsNone(resolve_goal_mode(goal))

    def test_resolve_goal_mode_handles_missing_goal(self):
        self.assertIsNone(resolve_goal_mode({}))
        self.assertIsNone(resolve_goal_mode(None))

    def test_strength_and_recovery_have_real_goal_type_gap_documented(self):
        from training_intelligence.dose.goal_policy import _GOAL_TYPE_TO_MODE
        self.assertNotIn(STRENGTH, _GOAL_TYPE_TO_MODE.values())
        self.assertNotIn(RECOVERY, _GOAL_TYPE_TO_MODE.values())

    def test_lean_bulk_targets_upper_range_lean_cut_lower_middle(self):
        """The assignment's own directional expectation: lean_bulk should
        sit closer to the range ceiling than lean_cut."""
        self.assertGreater(
            GOAL_POLICIES[LEAN_BULK]["range_position_fraction"],
            GOAL_POLICIES[LEAN_CUT]["range_position_fraction"],
        )

    def test_recovery_targets_the_lowest_fraction_of_all_modes(self):
        recovery_fraction = GOAL_POLICIES[RECOVERY]["range_position_fraction"]
        for mode, policy in GOAL_POLICIES.items():
            if mode != RECOVERY:
                self.assertLessEqual(recovery_fraction, policy["range_position_fraction"], mode)


class FeasibleRangeFoundationTests(unittest.TestCase):
    """Section 1 of the corrected architecture: the feasible range itself
    must be identical across every goal mode, for the same state."""

    def test_feasible_range_identical_across_all_six_goal_modes(self):
        sessions, muscle_rows = _rich_fixture()
        readiness_ctx = _patched_readiness_and_muscles()
        ranges = {}
        with readiness_ctx[0], readiness_ctx[1], patch(
            "training_intelligence.dose.shadow_dose.get_active_goal", return_value={}
        ):
            for mode in GOAL_MODES:
                result = build_shadow_dose(
                    AS_OF, "Upper Pull", sessions=list(sessions), muscle_rows=list(muscle_rows),
                    ledger_rows=[], goal_mode_override=mode,
                )
                ranges[mode] = result["feasible_dose_range"]
        reference = ranges[LEAN_CUT]
        for mode in GOAL_MODES:
            self.assertEqual(ranges[mode], reference, f"feasible_dose_range differed for {mode}")

    def test_other_numeric_fields_stay_goal_agnostic_too(self):
        sessions, muscle_rows = _rich_fixture()
        readiness_ctx = _patched_readiness_and_muscles()
        results = {}
        with readiness_ctx[0], readiness_ctx[1], patch(
            "training_intelligence.dose.shadow_dose.get_active_goal", return_value={}
        ):
            for mode in GOAL_MODES:
                results[mode] = build_shadow_dose(
                    AS_OF, "Upper Pull", sessions=list(sessions), muscle_rows=list(muscle_rows),
                    ledger_rows=[], goal_mode_override=mode,
                )
        reference = results[LEAN_CUT]
        goal_agnostic_keys = (
            "local_readiness", "systemic_capacity", "dose_classification",
            "historical_dose_reference", "performance_state",
        )
        for mode in GOAL_MODES:
            for key in goal_agnostic_keys:
                self.assertEqual(results[mode][key], reference[key], f"{key} differed for {mode}")
            # recommended_dose's own goal-agnostic reference sub-field
            # must also be identical - only .working_sets may vary.
            self.assertEqual(
                results[mode]["recommended_dose"]["goal_agnostic_reference_working_sets"],
                reference["recommended_dose"]["goal_agnostic_reference_working_sets"],
            )
            self.assertEqual(
                results[mode]["recommended_dose"]["exercise_count"],
                reference["recommended_dose"]["exercise_count"],
            )

    def test_fatigued_muscle_collapses_range_to_zero_regardless_of_goal_mode(self):
        """Goal policy must never override local fatigue: a
        SUPPRESSED/FATIGUED-collapsed range stays at zero for every mode."""
        muscles = [
            {"muscle": "Back", "readiness_state": "SUPPRESSED", "readiness_score": 10.0,
             "hours_since_primary_exposure": 4.0},
            {"muscle": "Biceps", "readiness_state": "FATIGUED", "readiness_score": 20.0,
             "hours_since_primary_exposure": 6.0},
        ]
        sessions, muscle_rows = _rich_fixture()
        with patch(
            "training_intelligence.dose.shadow_dose._latest_readiness",
            return_value={"recovery_score": 94.0, "readiness_band": "high"},
        ), patch(
            "training_intelligence.dose.shadow_dose.calculate_muscle_readiness",
            return_value={"selection_confidence": "high", "latest_workout_age_hours": 24.0, "muscles": muscles},
        ), patch(
            "training_intelligence.dose.shadow_dose.get_active_goal", return_value={}
        ):
            for mode in GOAL_MODES:
                result = build_shadow_dose(
                    AS_OF, "Upper Pull", sessions=list(sessions), muscle_rows=list(muscle_rows),
                    ledger_rows=[], goal_mode_override=mode,
                )
                self.assertEqual(result["feasible_dose_range"]["lower_bound_working_sets"], 0)
                self.assertEqual(result["feasible_dose_range"]["upper_bound_working_sets"], 0)
                self.assertEqual(result["recommended_dose"]["working_sets"], 0, mode)


class GoalAdjustedDoseTests(unittest.TestCase):
    """Section 2/3: goal policy selects a position within the range, and
    the final recommendation may legitimately differ by goal mode."""

    def test_goal_mode_never_exceeds_feasible_range(self):
        sessions, muscle_rows = _rich_fixture()
        readiness_ctx = _patched_readiness_and_muscles()
        with readiness_ctx[0], readiness_ctx[1], patch(
            "training_intelligence.dose.shadow_dose.get_active_goal", return_value={}
        ):
            for mode in GOAL_MODES:
                result = build_shadow_dose(
                    AS_OF, "Upper Pull", sessions=list(sessions), muscle_rows=list(muscle_rows),
                    ledger_rows=[], goal_mode_override=mode,
                )
                low = result["feasible_dose_range"]["lower_bound_working_sets"]
                high = result["feasible_dose_range"]["upper_bound_working_sets"]
                working_sets = result["recommended_dose"]["working_sets"]
                self.assertGreaterEqual(working_sets, low, mode)
                self.assertLessEqual(working_sets, high, mode)

    def test_lean_cut_lean_bulk_strength_maintenance_can_produce_different_final_doses(self):
        """Required by the assignment: at minimum lean_cut, lean_bulk,
        strength, and maintenance, same user/state, same underlying
        feasible range, final goal-adjusted dose CAN differ."""
        sessions, muscle_rows = _rich_fixture()
        readiness_ctx = _patched_readiness_and_muscles()
        working_sets_by_mode = {}
        with readiness_ctx[0], readiness_ctx[1], patch(
            "training_intelligence.dose.shadow_dose.get_active_goal", return_value={}
        ):
            for mode in (LEAN_CUT, LEAN_BULK, STRENGTH, MAINTENANCE):
                result = build_shadow_dose(
                    AS_OF, "Upper Pull", sessions=list(sessions), muscle_rows=list(muscle_rows),
                    ledger_rows=[], goal_mode_override=mode,
                )
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["goal_context"]["goal_mode"], mode)
                working_sets_by_mode[mode] = result["recommended_dose"]["working_sets"]
        # Not every pair need differ (rounding can coincide), but the
        # four required modes must not all collapse to one identical
        # value - that would mean goal policy is not actually doing
        # anything, which is exactly what this milestone corrects.
        self.assertGreater(len(set(working_sets_by_mode.values())), 1, working_sets_by_mode)
        # And lean_bulk (upper posture) must be >= lean_cut (lower-middle
        # posture) for the identical range - directionally consistent
        # with their range_position_fraction ordering.
        self.assertGreaterEqual(working_sets_by_mode[LEAN_BULK], working_sets_by_mode[LEAN_CUT])

    def test_no_goal_resolved_uses_range_midpoint(self):
        sessions, muscle_rows = _rich_fixture()
        readiness_ctx = _patched_readiness_and_muscles()
        with readiness_ctx[0], readiness_ctx[1], patch(
            "training_intelligence.dose.shadow_dose.get_active_goal", return_value={}
        ):
            result = build_shadow_dose(
                AS_OF, "Upper Pull", sessions=list(sessions), muscle_rows=list(muscle_rows), ledger_rows=[]
            )
        low = result["feasible_dose_range"]["lower_bound_working_sets"]
        high = result["feasible_dose_range"]["upper_bound_working_sets"]
        self.assertEqual(result["recommended_dose"]["working_sets"], round((low + high) / 2.0))
        self.assertIsNone(result["goal_context"]["goal_mode"])

    def test_goal_mode_does_change_the_policy_framing(self):
        sessions, muscle_rows = _rich_fixture()
        readiness_ctx = _patched_readiness_and_muscles()
        with readiness_ctx[0], readiness_ctx[1], patch(
            "training_intelligence.dose.shadow_dose.get_active_goal", return_value={}
        ):
            lean_cut = build_shadow_dose(
                AS_OF, "Upper Pull", sessions=list(sessions), muscle_rows=list(muscle_rows),
                ledger_rows=[], goal_mode_override=LEAN_CUT,
            )
            lean_bulk = build_shadow_dose(
                AS_OF, "Upper Pull", sessions=list(sessions), muscle_rows=list(muscle_rows),
                ledger_rows=[], goal_mode_override=LEAN_BULK,
            )
        self.assertNotEqual(lean_cut["goal_context"]["policy"], lean_bulk["goal_context"]["policy"])
        self.assertEqual(lean_cut["goal_context"]["policy"]["maintenance_vs_growth_priority"], "maintenance")
        self.assertEqual(lean_bulk["goal_context"]["policy"]["maintenance_vs_growth_priority"], "growth")

    def test_explicit_override_takes_precedence_over_resolved_goal(self):
        readiness_ctx = _patched_readiness_and_muscles()
        with readiness_ctx[0], readiness_ctx[1], patch(
            "training_intelligence.dose.shadow_dose.get_active_goal",
            return_value={"phase": "lean_cut", "goal_type": "lose_body_fat"},
        ):
            result = build_shadow_dose(
                AS_OF, "Upper Pull", sessions=[], muscle_rows=[], ledger_rows=[],
                goal_mode_override=STRENGTH,
            )
        self.assertEqual(result["goal_context"]["goal_mode"], STRENGTH)
        self.assertEqual(result["goal_context"]["goal_mode_source"], "override")

    def test_no_override_resolves_from_active_goal_and_reports_its_source(self):
        readiness_ctx = _patched_readiness_and_muscles()
        with readiness_ctx[0], readiness_ctx[1], patch(
            "training_intelligence.dose.shadow_dose.get_active_goal",
            return_value={"phase": "lean_bulk", "goal_type": "build_muscle"},
        ):
            result = build_shadow_dose(AS_OF, "Upper Pull", sessions=[], muscle_rows=[], ledger_rows=[])
        self.assertEqual(result["goal_context"]["goal_mode"], LEAN_BULK)
        self.assertEqual(result["goal_context"]["goal_mode_source"], "resolved_from_active_goal")

    def test_deterministic_repeated_output_per_goal_mode(self):
        sessions, muscle_rows = _rich_fixture()
        readiness_ctx = _patched_readiness_and_muscles()
        with readiness_ctx[0], readiness_ctx[1], patch(
            "training_intelligence.dose.shadow_dose.get_active_goal", return_value={}
        ):
            first = build_shadow_dose(
                AS_OF, "Upper Pull", sessions=list(sessions), muscle_rows=list(muscle_rows),
                ledger_rows=[], goal_mode_override=STRENGTH,
            )
            second = build_shadow_dose(
                AS_OF, "Upper Pull", sessions=list(sessions), muscle_rows=list(muscle_rows),
                ledger_rows=[], goal_mode_override=STRENGTH,
            )
        self.assertEqual(first, second)

    def test_goal_policy_version_surfaced(self):
        readiness_ctx = _patched_readiness_and_muscles()
        with readiness_ctx[0], readiness_ctx[1], patch(
            "training_intelligence.dose.shadow_dose.get_active_goal", return_value={}
        ):
            result = build_shadow_dose(
                AS_OF, "Upper Pull", sessions=[], muscle_rows=[], ledger_rows=[],
                goal_mode_override=RECOVERY,
            )
        self.assertEqual(result["goal_context"]["goal_policy_version"], GOAL_POLICY_VERSION)


if __name__ == "__main__":
    unittest.main()
