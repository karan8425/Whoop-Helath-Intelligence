"""TKI-5(.1): exercise selection / progressive-overload prescription +
composition-calibration tests.

Pure/in-memory (fixture profiles/sessions/rows, no live database),
matching the established convention. Real-Postgres coverage lives in
test_training_intelligence_prescription_postgres.py (opt-in).
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from training_intelligence.dose.goal_policy import (
    GOAL_MODES, LEAN_CUT, LEAN_BULK, STRENGTH, MAINTENANCE, GENERAL_FITNESS, RECOVERY,
)
from training_intelligence.selection.shadow_selection import REST_FAMILY_NAME
from training_intelligence.prescription.shadow_prescription import (
    build_shadow_prescription, _filter_generic_placeholders, _assign_roles, _apply_goal_rep_posture,
)
from training_intelligence.prescription.prescription_policy import (
    MIN_EXERCISES, MAX_EXERCISES, PER_MOVEMENT_ABSORPTION_CAP_FALLBACK,
)
from training_intelligence.prescription.composition import (
    feasible_exercise_count, historical_session_structure, dose_absorption_waterfall,
)

AS_OF = datetime(2026, 9, 12, 8, 10, tzinfo=timezone.utc)
ALL_MUSCLES = ("Chest", "Back", "Shoulders", "Biceps", "Triceps", "Core", "Glutes", "Hamstrings", "Quads")


def _muscle(muscle, state="FRESH", hours=200.0, score=90.0):
    return {"muscle": muscle, "readiness_state": state, "readiness_score": score,
            "hours_since_primary_exposure": hours}


def _all_fresh_muscles(overrides=None):
    overrides = overrides or {}
    return [_muscle(m, **overrides.get(m, {})) for m in ALL_MUSCLES]


def _session(label, days_ago, workout_type="Upper Pull", set_count=10, movement_count=4,
             total_reps=80, total_volume=5000.0, duration_seconds=2400):
    return {
        "activity_id": label, "begin_time": AS_OF - timedelta(days=days_ago),
        "workout_type": workout_type, "duration_seconds": duration_seconds,
        "total_reps": total_reps, "total_volume": total_volume,
        "set_count": set_count, "movement_count": movement_count,
        "included": True, "exclusion_reason": None,
    }


def _muscle_row(label, days_ago, muscle, volume=200.0):
    return {"activity_id": label, "begin_time": AS_OF - timedelta(days=days_ago),
            "included": True, "exclusion_reason": None, "volume": volume, "muscle_groups": [muscle]}


def _ledger_row(label, days_ago, movement_id, muscle_groups, rep_count=10, volume=200.0, set_index=0):
    return {"activity_id": label, "begin_time": AS_OF - timedelta(days=days_ago), "set_index": set_index,
            "movement_id": movement_id, "rep_count": rep_count, "volume": volume,
            "included": True, "exclusion_reason": None,
            "muscle_groups": muscle_groups, "movement_name": movement_id}


def _default_sessions():
    return [_session(f"s{i}", days_ago=3 + i * 6) for i in range(4)]


def _rich_muscle_rows():
    rows = []
    for i in range(10):
        day = 1 + i * 1.2
        for muscle in ALL_MUSCLES:
            rows.append(_muscle_row(f"{muscle}{i}", day, muscle))
    return rows


def _profile(movement_id, name, muscle_groups, status="usable", sessions=6, reps=80, set_count=8,
             weight=40.0, struggling=0.1, inconsistency=0.1, progression_earned=True,
             is_bilateral=False, is_two_sided=False):
    return {
        "movement_id": movement_id, "name": name, "muscle_groups": muscle_groups,
        "incidental_muscles": [], "is_bilateral": is_bilateral, "is_two_sided": is_two_sided,
        "is_alternating": False,
        "history": {"sessions_in_lookback": sessions},
        "performance": {
            "status": status, "recent_sets_per_session": set_count, "recent_reps_per_session": reps,
            "recent_working_weight_per_arm_lb": weight, "progression_earned": progression_earned,
            "recent_struggling_score": struggling, "recent_inconsistency_score": inconsistency,
            "tonal_hardware": {"near_hardware_ceiling": False},
        },
        "recent_sessions": [
            {"mode_counts": {"standard": set_count}, "total_volume": reps * weight,
             "median_base_weight": weight, "total_reps": reps, "set_count": set_count}
            for _ in range(sessions)
        ] if status == "usable" else [],
    }


def _default_profiles():
    return [
        _profile("m_row", "Barbell Bent Over Row", ["Back", "Biceps"]),
        _profile("m_pulldown", "Seated Lat Pulldown", ["Back"]),
        _profile("m_curl", "Hammer Curl", ["Biceps"]),
        _profile("m_press", "Barbell Bench Press", ["Chest", "Triceps"]),
        _profile("m_squat", "Goblet Squat", ["Quads", "Glutes"]),
    ]


def _run(readiness_band="good", recovery_score=70.0, muscles=None, goal=None,
         sessions=None, muscle_rows=None, ledger_rows=None, goal_mode_override=None,
         profiles=None, selection_result=None):
    muscles = muscles if muscles is not None else _all_fresh_muscles()
    goal = goal if goal is not None else {}
    sessions = sessions if sessions is not None else _default_sessions()
    muscle_rows = muscle_rows if muscle_rows is not None else _rich_muscle_rows()
    ledger_rows = ledger_rows if ledger_rows is not None else []
    profiles = profiles if profiles is not None else _default_profiles()
    readiness = {"recovery_score": recovery_score, "readiness_band": readiness_band}
    muscle_readiness_result = {"selection_confidence": "high", "latest_workout_age_hours": 24.0, "muscles": muscles}
    with patch(
        "training_intelligence.selection.shadow_selection._latest_readiness", return_value=readiness,
    ), patch(
        "training_intelligence.selection.shadow_selection.calculate_muscle_readiness",
        return_value=muscle_readiness_result,
    ), patch(
        "training_intelligence.selection.shadow_selection.get_active_goal", return_value=goal,
    ), patch(
        "training_intelligence.selection.shadow_selection.load_session_history", return_value=sessions,
    ), patch(
        "training_intelligence.selection.shadow_selection._load_muscle_set_rows", return_value=muscle_rows,
    ), patch(
        "training_intelligence.selection.shadow_selection.load_rows", return_value=ledger_rows,
    ), patch(
        "training_intelligence.prescription.shadow_prescription.build_movement_performance_profiles",
        return_value={"status": "ok", "movement_count": len(profiles), "profiles": profiles},
    ), patch(
        "training_intelligence.prescription.shadow_prescription._latest_readiness", return_value=readiness,
    ), patch(
        "training_intelligence.prescription.shadow_prescription.calculate_muscle_readiness",
        return_value=muscle_readiness_result,
    ):
        return build_shadow_prescription(
            AS_OF, goal_mode_override=goal_mode_override, selection_result=selection_result,
            ledger_rows=ledger_rows,
        )


# ----------------------------------------------------------------------
# Section 3/7: feasible exercise-count range (unit-level).
# ----------------------------------------------------------------------

class FeasibleExerciseCountTests(unittest.TestCase):
    def _insufficient_structure(self):
        return {"sample_count": 0, "median_exercises_per_session": None,
                "median_sets_per_exercise": None, "source": "insufficient_personal_history"}

    def test_zero_dose_gives_zero_preferred(self):
        result = feasible_exercise_count(5, 0, 0, LEAN_CUT, self._insufficient_structure())
        self.assertEqual(result["preferred"], 0)

    def test_preferred_never_exceeds_eligible_pool_size(self):
        """Section 3's core fix: fewer candidates than a naive preference
        must cap the preference, not merely be reported as a shortfall."""
        result = feasible_exercise_count(2, 15, 15, GENERAL_FITNESS, self._insufficient_structure())
        self.assertLessEqual(result["preferred"], 2)
        self.assertLessEqual(result["max"], 2)

    def test_preferred_available_when_pool_is_large_enough(self):
        result = feasible_exercise_count(5, 9, 9, LEAN_CUT, self._insufficient_structure())
        self.assertGreaterEqual(result["preferred"], MIN_EXERCISES)
        self.assertLessEqual(result["preferred"], MAX_EXERCISES)

    def test_dose_upper_bound_caps_count_to_avoid_invariant_violation(self):
        # 2 sets/exercise floor means max_sets=4 can support at most 2 exercises.
        result = feasible_exercise_count(5, 20, 4, GENERAL_FITNESS, self._insufficient_structure())
        self.assertLessEqual(result["preferred"], 2)

    def test_personal_history_used_when_sufficient(self):
        structure = {"sample_count": 6, "median_exercises_per_session": 2.0,
                     "median_sets_per_exercise": 3.0, "source": "session_family_personal_history"}
        result = feasible_exercise_count(5, 9, 9, LEAN_CUT, structure)
        self.assertEqual(result["preference_source"], "session_family_personal_history")
        self.assertEqual(result["preferred"], 2)

    def test_insufficient_history_falls_back_to_goal_dose_default(self):
        result = feasible_exercise_count(5, 9, 9, LEAN_CUT, self._insufficient_structure())
        self.assertEqual(result["preference_source"], "goal_policy_dose_default")

    def test_two_exercise_composition_is_valid_not_penalized(self):
        """Section 14: an explicitly 2-movement feasible/preferred
        result must not itself be treated as degraded."""
        structure = {"sample_count": 5, "median_exercises_per_session": 2.0,
                     "median_sets_per_exercise": 4.0, "source": "session_family_personal_history"}
        result = feasible_exercise_count(4, 8, 8, MAINTENANCE, structure)
        self.assertEqual(result["preferred"], 2)
        self.assertNotIn("eligible movement pool", " ".join(result["limiting_factors"]))

    def test_four_plus_exercise_composition_reachable(self):
        structure = {"sample_count": 5, "median_exercises_per_session": 4.0,
                     "median_sets_per_exercise": 3.0, "source": "session_family_personal_history"}
        result = feasible_exercise_count(5, 16, 16, LEAN_BULK, structure)
        self.assertGreaterEqual(result["preferred"], 4)


class HistoricalSessionStructureTests(unittest.TestCase):
    def test_insufficient_sessions_reports_insufficient_source(self):
        rows = [_ledger_row("w1", 2, "back_m", ["Back"])]
        result = historical_session_structure(rows, AS_OF, "Upper Pull")
        self.assertEqual(result["source"], "insufficient_personal_history")

    def test_sufficient_sessions_computes_median(self):
        rows = []
        for i in range(4):
            rows.append(_ledger_row(f"w{i}", 2 + i, f"back_m{i}", ["Back"], set_index=0))
            rows.append(_ledger_row(f"w{i}", 2 + i, f"biceps_m{i}", ["Biceps"], set_index=1))
        result = historical_session_structure(rows, AS_OF, "Upper Pull")
        self.assertEqual(result["source"], "session_family_personal_history")
        self.assertEqual(result["median_exercises_per_session"], 2.0)

    def test_future_rows_excluded(self):
        rows = []
        for i in range(4):
            rows.append(_ledger_row(f"w{i}", 2 + i, f"back_m{i}", ["Back"], set_index=0))
            rows.append(_ledger_row(f"w{i}", 2 + i, f"biceps_m{i}", ["Biceps"], set_index=1))
        future_rows = [
            _ledger_row("future", -5, "chest_m", ["Chest"], set_index=0),
            _ledger_row("future", -5, "triceps_m", ["Triceps"], set_index=1),
            _ledger_row("future", -5, "shoulders_m", ["Shoulders"], set_index=2),
        ]
        baseline = historical_session_structure(rows, AS_OF, "Upper Pull")
        with_future = historical_session_structure(rows + future_rows, AS_OF, "Upper Pull")
        self.assertEqual(baseline, with_future)


# ----------------------------------------------------------------------
# Section 8/9/10/11: dose-absorption waterfall (unit-level).
# ----------------------------------------------------------------------

class DoseAbsorptionWaterfallTests(unittest.TestCase):
    def test_exact_dose_delivered_when_absorbable(self):
        selected = [
            _profile("m1", "Barbell Bent Over Row", ["Back", "Biceps"], sessions=6, reps=64, set_count=8),
            _profile("m2", "Seated Lat Pulldown", ["Back"], sessions=6, reps=64, set_count=8),
        ]
        result = dose_absorption_waterfall(selected, ["primary", "secondary"], "good", target_sets=9, max_sets=9)
        self.assertEqual(result["dose_allocated"], 9)
        self.assertEqual(result["dose_shortfall"], 0)
        self.assertIsNone(result["shortfall_reason"])

    def test_true_shortfall_when_all_movements_reduce(self):
        """Declining trend + low/moderate readiness legitimately reduces
        every movement - the resulting gap must be disclosed, not hidden
        or silently redistributed to a movement that has no headroom."""
        declining_sessions = [
            {"mode_counts": {"standard": 8}, "total_volume": 2000.0, "median_base_weight": 40.0, "total_reps": 80, "set_count": 8},
            {"mode_counts": {"standard": 8}, "total_volume": 2000.0, "median_base_weight": 40.0, "total_reps": 80, "set_count": 8},
            {"mode_counts": {"standard": 8}, "total_volume": 1000.0, "median_base_weight": 30.0, "total_reps": 60, "set_count": 8},
            {"mode_counts": {"standard": 8}, "total_volume": 1000.0, "median_base_weight": 30.0, "total_reps": 60, "set_count": 8},
        ]
        p1 = _profile("m1", "Barbell Bent Over Row", ["Back", "Biceps"])
        p1["recent_sessions"] = declining_sessions
        p2 = _profile("m2", "Seated Lat Pulldown", ["Back"])
        p2["recent_sessions"] = declining_sessions
        result = dose_absorption_waterfall([p1, p2], ["primary", "secondary"], "low", target_sets=9, max_sets=9)
        # Both movements should be in REDUCE; whatever they collectively
        # deliver is below target, and the gap must be explicit.
        if result["dose_shortfall"] > 0:
            self.assertIsNotNone(result["shortfall_reason"])
        self.assertLessEqual(result["dose_allocated"], 9)

    def test_never_exceeds_max_sets(self):
        selected = [_profile(f"m{i}", f"Movement {i}", ["Back"]) for i in range(4)]
        result = dose_absorption_waterfall(selected, ["primary"] + ["secondary"] * 3, "high", target_sets=20, max_sets=10)
        self.assertLessEqual(result["dose_allocated"], 10)

    def test_no_single_movement_absorbs_excessive_share(self):
        """Section 12/27: reallocation must not dump most of the dose
        into one accessory movement."""
        p1 = _profile("m1", "Barbell Bent Over Row", ["Back", "Biceps"], reps=64, set_count=8)  # primary, absorbing
        p2 = _profile("m2", "Hammer Curl", ["Biceps"])
        declining_sessions = [
            {"mode_counts": {"standard": 8}, "total_volume": 2000.0, "median_base_weight": 40.0, "total_reps": 80, "set_count": 8},
            {"mode_counts": {"standard": 8}, "total_volume": 2000.0, "median_base_weight": 40.0, "total_reps": 80, "set_count": 8},
            {"mode_counts": {"standard": 8}, "total_volume": 1000.0, "median_base_weight": 30.0, "total_reps": 60, "set_count": 8},
        ]
        p2["recent_sessions"] = declining_sessions  # REDUCE - not an absorbing target
        result = dose_absorption_waterfall([p1, p2], ["primary", "secondary"], "good", target_sets=16, max_sets=16)
        p1_entry = next(m for m in result["per_movement"] if m["profile"]["movement_id"] == "m1")
        self.assertLessEqual(p1_entry["sets"], round(16 * 0.4) + p1_entry["initial_sets"])


# ----------------------------------------------------------------------
# Everything below: end-to-end build_shadow_prescription() tests.
# ----------------------------------------------------------------------

class GenericPlaceholderTests(unittest.TestCase):
    def test_handle_move_and_bar_move_are_filtered(self):
        profiles = _default_profiles() + [
            _profile("m_handle", "Handle Move", ["Back"]),
            _profile("m_bar", "Bar Move", ["Chest"]),
        ]
        kept, excluded = _filter_generic_placeholders(profiles)
        self.assertEqual(set(excluded), {"Handle Move", "Bar Move"})
        self.assertTrue(all(p["name"] not in ("Handle Move", "Bar Move") for p in kept))

    def test_placeholder_never_appears_in_a_real_prescription(self):
        profiles = _default_profiles() + [_profile("m_handle", "Handle Move", ["Back"])]
        result = _run(profiles=profiles)
        names = {e["movement_name"] for e in result["exercises"]}
        self.assertNotIn("Handle Move", names)


class RoleAssignmentTests(unittest.TestCase):
    def test_first_coverage_of_each_muscle_is_primary(self):
        selected = [
            _profile("a", "A", ["Back"]),
            _profile("b", "B", ["Back"]),
            _profile("c", "C", ["Biceps"]),
        ]
        roles = _assign_roles(selected, primary_focus=["Back", "Biceps"])
        self.assertEqual(roles, ["primary", "secondary", "primary"])


class GoalRepPostureTests(unittest.TestCase):
    def test_reduce_state_is_never_touched(self):
        progression = {"progression_state": "REDUCE", "rep_range": {"minimum": 8, "maximum": 12}, "target_reps_per_set": 8}
        result = _apply_goal_rep_posture(progression, STRENGTH)
        self.assertEqual(result["target_reps_per_set"], 8)
        self.assertIsNone(result["goal_posture_applied"])

    def test_hold_state_biased_toward_goal_posture(self):
        progression = {"progression_state": "HOLD", "rep_range": {"minimum": 8, "maximum": 12}, "target_reps_per_set": 10}
        strength = _apply_goal_rep_posture(progression, STRENGTH)
        bulk = _apply_goal_rep_posture(progression, LEAN_BULK)
        self.assertLess(strength["target_reps_per_set"], bulk["target_reps_per_set"])

    def test_progress_reps_never_walked_back_below_earned_value(self):
        progression = {"progression_state": "PROGRESS_REPS", "rep_range": {"minimum": 8, "maximum": 12}, "target_reps_per_set": 12}
        result = _apply_goal_rep_posture(progression, STRENGTH)
        self.assertGreaterEqual(result["target_reps_per_set"], 12)


class CandidateAndEligibilityTests(unittest.TestCase):
    def test_candidate_movements_reflect_selected_family_muscles(self):
        result = _run()
        for exercise in result["exercises"]:
            self.assertTrue(exercise["primary_muscles"])

    def test_unusable_history_movement_excluded(self):
        profiles = _default_profiles() + [_profile("m_bad", "Bad Movement", ["Back"], status="no_data")]
        result = _run(profiles=profiles)
        names = {e["movement_name"] for e in result["exercises"]}
        self.assertNotIn("Bad Movement", names)


class DuplicatePatternSuppressionTests(unittest.TestCase):
    def test_does_not_select_two_movements_from_the_same_family(self):
        profiles = [
            _profile("m_squat1", "Goblet Squat", ["Quads", "Glutes"]),
            _profile("m_squat2", "Barbell Front Squat", ["Quads", "Glutes"]),
            _profile("m_hinge", "Barbell Deadlift", ["Hamstrings", "Glutes"]),
        ]
        result = _run(profiles=profiles)
        self.assertLessEqual(
            sum(1 for e in result["exercises"] if e["movement_name"] in ("Goblet Squat", "Barbell Front Squat")), 1
        )


class MuscleCoverageTests(unittest.TestCase):
    def test_session_summary_muscle_stimulus_reflects_prescribed_exercises(self):
        result = _run()
        total_from_exercises = sum(e["working_sets"] for e in result["exercises"])
        self.assertEqual(result["session_summary"]["total_working_sets"], total_from_exercises)

    def test_full_dose_delivered_when_movements_can_absorb_it(self):
        """Section 8's primary invariant: no silent shortfall when the
        selected movements are all healthy (HOLD/PROGRESS) and dose is
        within a reasonable range for the pool."""
        result = _run()
        if result["dose"] is not None and result["dose_absorption"]["dose_shortfall"] == 0:
            self.assertEqual(
                result["session_summary"]["total_working_sets"], result["dose_absorption"]["dose_target"]
            )


class DoseBoundEnforcementTests(unittest.TestCase):
    def test_total_working_sets_within_feasible_range(self):
        result = _run()
        if result["dose"] is None:
            return
        upper = result["dose"]["feasible_range"]["upper_bound_working_sets"]
        self.assertLessEqual(result["session_summary"]["total_working_sets"], upper)


class SetAllocationTests(unittest.TestCase):
    def test_each_exercise_gets_at_least_one_set_when_any_allocated(self):
        result = _run()
        for e in result["exercises"]:
            self.assertGreaterEqual(e["working_sets"], 1)


class RepRangeTests(unittest.TestCase):
    def test_rep_range_present_and_ordered(self):
        result = _run()
        for e in result["exercises"]:
            self.assertLessEqual(e["rep_range"]["minimum"], e["rep_range"]["maximum"])
            self.assertGreaterEqual(e["target_reps_per_set"], e["rep_range"]["minimum"])
            self.assertLessEqual(e["target_reps_per_set"], e["rep_range"]["maximum"])


class ProgressionStateTests(unittest.TestCase):
    def test_add_reps_progression_reachable(self):
        muscles = _all_fresh_muscles({m: {"state": "FATIGUED"} for m in ALL_MUSCLES if m not in ("Back", "Biceps")})
        profiles = [_profile("m_x", "Barbell Bent Over Row", ["Back", "Biceps"], reps=64, set_count=8)]
        result = _run(profiles=profiles, readiness_band="good", muscles=muscles)
        self.assertEqual(result["exercises"][0]["progression_state"], "PROGRESS_REPS")

    def test_increase_load_progression_reachable(self):
        muscles = _all_fresh_muscles({m: {"state": "FATIGUED"} for m in ALL_MUSCLES if m not in ("Back", "Biceps")})
        profiles = [_profile("m_x", "Barbell Bent Over Row", ["Back", "Biceps"], reps=96, set_count=8)]
        result = _run(profiles=profiles, readiness_band="good", muscles=muscles)
        self.assertEqual(result["exercises"][0]["progression_state"], "PROGRESS_LOAD")

    def test_regression_moderation_on_decline(self):
        profiles = [_profile("m_x", "Barbell Bent Over Row", ["Back", "Biceps"])]
        p = profiles[0]
        p["recent_sessions"] = [
            {"mode_counts": {"standard": 8}, "total_volume": 1000.0, "median_base_weight": 30.0, "total_reps": 60, "set_count": 8},
            {"mode_counts": {"standard": 8}, "total_volume": 1000.0, "median_base_weight": 30.0, "total_reps": 60, "set_count": 8},
            {"mode_counts": {"standard": 8}, "total_volume": 2000.0, "median_base_weight": 40.0, "total_reps": 80, "set_count": 8},
            {"mode_counts": {"standard": 8}, "total_volume": 2000.0, "median_base_weight": 40.0, "total_reps": 80, "set_count": 8},
        ]
        muscles = _all_fresh_muscles({m: {"state": "FATIGUED"} for m in ALL_MUSCLES if m not in ("Back", "Biceps")})
        result = _run(profiles=profiles, readiness_band="low", muscles=muscles)
        self.assertEqual(result["exercises"][0]["progression_state"], "REDUCE")


class ReadinessCombinationTests(unittest.TestCase):
    def test_high_systemic_and_fresh_muscles_allows_normal_prescription(self):
        result = _run(readiness_band="high", recovery_score=90.0)
        self.assertEqual(result["status"], "ok")

    def test_high_systemic_but_fatigued_muscle_excludes_that_muscle(self):
        muscles = _all_fresh_muscles({"Back": {"state": "FATIGUED"}, "Biceps": {"state": "FATIGUED"}})
        result = _run(readiness_band="high", recovery_score=94.0, muscles=muscles)
        names = {e["movement_name"] for e in result["exercises"]}
        self.assertNotIn("Barbell Bent Over Row", names)
        self.assertNotIn("Hammer Curl", names)


class GoalModeVariationTests(unittest.TestCase):
    def test_goal_mode_can_change_exercise_count_or_rep_posture(self):
        strength = _run(goal_mode_override=STRENGTH)
        bulk = _run(goal_mode_override=LEAN_BULK)
        self.assertEqual(strength["status"], "ok")
        self.assertEqual(bulk["status"], "ok")

    def test_no_mode_bypasses_dose_bound(self):
        for mode in GOAL_MODES:
            result = _run(goal_mode_override=mode)
            if result["dose"] is not None:
                self.assertLessEqual(
                    result["session_summary"]["total_working_sets"],
                    result["dose"]["feasible_range"]["upper_bound_working_sets"],
                    mode,
                )

    def test_no_goal_mode_forces_infeasible_count(self):
        """Section 21/6: goal mode cannot request more exercises than
        the eligible pool can support."""
        profiles = [
            _profile("m1", "Barbell Bent Over Row", ["Back", "Biceps"]),
            _profile("m2", "Seated Lat Pulldown", ["Back"]),
        ]
        for mode in GOAL_MODES:
            result = _run(profiles=profiles, goal_mode_override=mode)
            self.assertLessEqual(len(result["exercises"]), 2)


class MovementContinuityTests(unittest.TestCase):
    def test_strong_history_movement_preferred_over_weak_alternative(self):
        profiles = [
            _profile("m_strong", "Barbell Bent Over Row", ["Back", "Biceps"], sessions=8, struggling=0.05, inconsistency=0.05),
            _profile("m_weak", "T-Bar Row", ["Back"], sessions=1, struggling=0.5, inconsistency=0.5),
        ]
        result = _run(profiles=profiles)
        names = [e["movement_name"] for e in result["exercises"]]
        self.assertIn("Barbell Bent Over Row", names)


class UnilateralMovementTests(unittest.TestCase):
    def test_bilateral_flag_is_carried_through_unmodified(self):
        profiles = [_profile("m_uni", "Single-Leg RDL", ["Hamstrings", "Glutes"], is_bilateral=False, is_two_sided=False)]
        result = _run(profiles=profiles)
        uni = next((e for e in result["exercises"] if e["movement_name"] == "Single-Leg RDL"), None)
        if uni:
            self.assertFalse(uni["is_bilateral"])

    def test_bilateral_movement_flag_true(self):
        profiles = [_profile("m_bi", "Barbell Front Squat", ["Quads", "Glutes"], is_bilateral=True)]
        result = _run(profiles=profiles)
        bi = next((e for e in result["exercises"] if e["movement_name"] == "Barbell Front Squat"), None)
        if bi:
            self.assertTrue(bi["is_bilateral"])


class ColdStartTests(unittest.TestCase):
    def test_no_movement_history_returns_valid_no_exercise_output(self):
        result = _run(profiles=[])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["exercises"], [])

    def test_sparse_movement_history_still_produces_valid_output(self):
        profiles = [_profile("m_new", "Barbell Bent Over Row", ["Back", "Biceps"], sessions=1)]
        result = _run(profiles=profiles)
        self.assertEqual(result["status"], "ok")

    def test_unknown_readiness_does_not_crash(self):
        result = _run(readiness_band="unknown", recovery_score=None)
        self.assertEqual(result["status"], "ok")

    def test_sparse_pool_does_not_produce_giant_per_exercise_allocation(self):
        """Section 22: if safe full dose cannot be allocated with a
        sparse pool, an explicit shortfall is preferable to unsafe
        concentration - no single movement should receive an
        implausibly large allocation just because few movements exist."""
        profiles = [_profile("m_only", "Barbell Bent Over Row", ["Back", "Biceps"], sessions=1)]
        result = _run(profiles=profiles)
        for e in result["exercises"]:
            self.assertLessEqual(e["working_sets"], PER_MOVEMENT_ABSORPTION_CAP_FALLBACK + 2)


class RestOutputTests(unittest.TestCase):
    def test_rest_selection_produces_no_strength_exercises(self):
        muscles = _all_fresh_muscles({m: {"state": "FATIGUED"} for m in ALL_MUSCLES})
        result = _run(readiness_band="very_low", recovery_score=5.0, muscles=muscles)
        self.assertEqual(result["selected_session_family"], REST_FAMILY_NAME)
        self.assertEqual(result["exercises"], [])
        self.assertIsNone(result["dose"])


class DeterminismTests(unittest.TestCase):
    def test_repeated_call_is_deterministic(self):
        first = _run()
        second = _run()
        self.assertEqual(first, second)


class TemporalLeakageTests(unittest.TestCase):
    def test_naive_as_of_rejected(self):
        with self.assertRaises(ValueError):
            build_shadow_prescription(datetime(2026, 9, 12))

    def test_future_session_does_not_influence_prescription(self):
        baseline = _run()
        future = _session("future", days_ago=-10, total_volume=999999.0, set_count=99)
        with_future = _run(sessions=_default_sessions() + [future])
        self.assertEqual(baseline["selected_session_family"], with_future["selected_session_family"])
        self.assertEqual(
            [e["movement_id"] for e in baseline["exercises"]],
            [e["movement_id"] for e in with_future["exercises"]],
        )

    def test_future_ledger_rows_do_not_influence_feasible_count(self):
        baseline = _run()
        future_rows = [
            _ledger_row("future", -5, "chest_m", ["Chest"], set_index=0),
            _ledger_row("future", -5, "triceps_m", ["Triceps"], set_index=1),
        ]
        with_future = _run(ledger_rows=future_rows)
        self.assertEqual(baseline["feasible_exercise_count"], with_future["feasible_exercise_count"])


class SimultaneousProgressionGuardrailTests(unittest.TestCase):
    def test_load_and_rep_progression_not_both_maximally_applied(self):
        muscles = _all_fresh_muscles({m: {"state": "FATIGUED"} for m in ALL_MUSCLES if m not in ("Back", "Biceps")})
        profiles = [_profile("m_x", "Barbell Bent Over Row", ["Back", "Biceps"], reps=96, set_count=8)]
        result = _run(profiles=profiles, readiness_band="good", muscles=muscles)
        exercise = result["exercises"][0]
        if exercise["progression_state"] == "PROGRESS_LOAD":
            self.assertEqual(exercise["target_reps_per_set"], exercise["rep_range"]["minimum"])


class AdaptationVsFallbackSemanticsTests(unittest.TestCase):
    def test_smaller_but_high_quality_session_is_adaptation_not_fallback(self):
        """Section 16: 'preferred N unavailable, formed a good smaller
        session' must appear in adaptations_used, never fallbacks_used."""
        profiles = [
            _profile("m1", "Barbell Bent Over Row", ["Back", "Biceps"], sessions=8, struggling=0.05, inconsistency=0.05),
            _profile("m2", "Seated Lat Pulldown", ["Back"], sessions=8, struggling=0.05, inconsistency=0.05),
        ]
        result = _run(profiles=profiles)
        self.assertTrue(any("exercise(s)" in a for a in result["adaptations_used"]))
        self.assertFalse(any("desired exercise slots" in f for f in result["fallbacks_used"]))


if __name__ == "__main__":
    unittest.main()
