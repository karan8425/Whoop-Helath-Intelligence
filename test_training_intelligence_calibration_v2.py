"""TKI-5.2: capacity + workload calibration tests
(training_intelligence.calibration.*).

Pure/in-memory (fixture Tonal set/workout/movement rows, no live
database), matching the established convention. Real-Postgres coverage
for the temporal-correctness claims lives in
test_training_intelligence_calibration_v2_postgres.py (opt-in).
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from training_intelligence.dose.goal_policy import (
    GOAL_MODES, LEAN_CUT, LEAN_BULK, STRENGTH, MAINTENANCE, GENERAL_FITNESS, RECOVERY,
)
from training_intelligence.selection.shadow_selection import REST_FAMILY_NAME
from training_intelligence.calibration.history import (
    valid_rows, multipliers, sessions_from_rows, comparable_sessions, envelope,
    pattern, distribution, quantile, unassisted, mode as row_mode,
)
from training_intelligence.calibration.capacity import capacity_reference, feasible_capacity, choose_dose
from training_intelligence.calibration.progression import prescribe, matched_history, movement_capacity
from training_intelligence.calibration.shadow import (
    build_calibrated_shadow_prescription, select_pool, workload_sanity, quality_verdict,
)

AS_OF = datetime(2026, 9, 12, 8, 10, tzinfo=timezone.utc)
BACK_ID = "back-movement-uuid"
CURL_ID = "curl-movement-uuid"


def _row(activity, set_index, days_ago, movement_id=BACK_ID, muscle_groups=("Back", "Biceps"),
         rep_count=10, base_weight=60.0, volume=None, eccentric=False, chains=False,
         spotter=False, included=True, is_generic=False, custom_movement=False,
         is_bilateral=True, is_two_sided=False, is_alternating=False, warm_up=False,
         rir=2.0, name="Barbell Bent Over Row", accessory="StraightBar", duration=10,
         volume_ratio=2.0):
    begin = AS_OF - timedelta(days=days_ago)
    if volume is None:
        volume = base_weight * rep_count * volume_ratio
    return {
        "activity_id": activity, "begin_time": begin, "end_time": begin + timedelta(seconds=duration),
        "duration_seconds": duration, "set_index": set_index, "movement_id": movement_id,
        "rep_count": rep_count, "base_weight": base_weight, "avg_weight": base_weight,
        "volume": volume, "raw_data": {"warmUp": warm_up, "repsInReserve": rir},
        "eccentric": eccentric, "chains": chains, "progressive": False, "burnout": False, "flex": False,
        "spotter": spotter, "struggling_score": None, "inconsistency_score": None,
        "name": name, "muscle_groups": list(muscle_groups), "accessory": accessory,
        "is_bilateral": is_bilateral, "is_two_sided": is_two_sided, "is_alternating": is_alternating,
        "is_generic": is_generic, "custom_movement": custom_movement, "included": included,
    }


def _session_rows(activity, days_ago, n_sets=4, **kwargs):
    return [_row(activity, i, days_ago, **kwargs) for i in range(n_sets)]


def _rich_two_cable_history(movement_id=BACK_ID, n_sessions=4, days_start=5, gap=6, name="Barbell Bent Over Row"):
    rows = []
    for i in range(n_sessions):
        rows += _session_rows(f"row-session-{i}", days_start + i * gap, n_sets=3,
                               movement_id=movement_id, name=name, volume_ratio=2.0)
    return rows


class ValidRowsTests(unittest.TestCase):
    def test_future_rows_excluded(self):
        rows = _rich_two_cable_history() + [_row("future", 0, -5)]
        cleaned = valid_rows(rows, AS_OF)
        self.assertTrue(all(r["begin_time"] <= AS_OF for r in cleaned))

    def test_warm_up_excluded(self):
        rows = [_row("w1", 0, 2, warm_up=True)]
        self.assertEqual(valid_rows(rows, AS_OF), [])

    def test_generic_and_custom_excluded(self):
        rows = [_row("g1", 0, 2, is_generic=True), _row("c1", 0, 2, custom_movement=True)]
        self.assertEqual(valid_rows(rows, AS_OF), [])

    def test_excluded_from_training_analysis_dropped(self):
        rows = [_row("x1", 0, 2, included=False)]
        self.assertEqual(valid_rows(rows, AS_OF), [])

    def test_malformed_records_dropped(self):
        rows = [_row("bad", 0, 2, rep_count=0), _row("bad2", 0, 2, base_weight=0), _row("bad3", 0, 2, volume=0)]
        self.assertEqual(valid_rows(rows, AS_OF), [])

    def test_multi_user_guard_raises(self):
        rows = [dict(_row("a", 0, 2), user_id="user-1"), dict(_row("b", 0, 2), user_id="user-2")]
        with self.assertRaises(ValueError):
            valid_rows(rows, AS_OF)

    def test_unmapped_muscle_groups_dropped(self):
        rows = [_row("u1", 0, 2, muscle_groups=["Nonexistent"])]
        self.assertEqual(valid_rows(rows, AS_OF), [])


class WorkloadMultiplierTests(unittest.TestCase):
    def test_two_cable_ratio_detected_with_sufficient_evidence(self):
        rows = _rich_two_cable_history(volume_ratio=2.0) if False else []
        rows = []
        for i in range(3):
            rows += _session_rows(f"s{i}", 2 + i * 3, n_sets=3, volume_ratio=2.0)
        result = multipliers(rows, AS_OF)
        self.assertEqual(result[BACK_ID]["workload_multiplier"], 2)
        self.assertEqual(result[BACK_ID]["multiplier_source"], "historical_standard_mode_ratio")
        self.assertIn(result[BACK_ID]["confidence"], ("MEDIUM", "HIGH"))

    def test_one_cable_ratio_not_doubled(self):
        rows = []
        for i in range(3):
            rows += _session_rows(f"s{i}", 2 + i * 3, n_sets=3, volume_ratio=1.0)
        result = multipliers(rows, AS_OF)
        self.assertEqual(result[BACK_ID]["workload_multiplier"], 1)

    def test_insufficient_evidence_falls_back_with_warning(self):
        rows = _session_rows("only-one", 2, n_sets=2, volume_ratio=2.0)
        result = multipliers(rows, AS_OF)
        self.assertEqual(result[BACK_ID]["workload_multiplier"], 1)
        self.assertEqual(result[BACK_ID]["multiplier_source"], "product_policy_fallback")
        self.assertEqual(result[BACK_ID]["confidence"], "LOW")
        self.assertIsNotNone(result[BACK_ID]["warning"])

    def test_no_blanket_bilateral_multiplier(self):
        """A bilateral-flagged movement whose OWN observed ratio is 1
        must not be force-doubled just because is_bilateral is True."""
        rows = []
        for i in range(3):
            rows += _session_rows(f"s{i}", 2 + i * 3, n_sets=3, volume_ratio=1.0, is_bilateral=True)
        result = multipliers(rows, AS_OF)
        self.assertEqual(result[BACK_ID]["workload_multiplier"], 1)

    def test_eccentric_mode_excluded_from_standard_ratio(self):
        rows = []
        for i in range(3):
            rows += _session_rows(f"s{i}", 2 + i * 3, n_sets=3, volume_ratio=2.0)
        rows += _session_rows("ecc", 1, n_sets=3, volume_ratio=1.0, eccentric=True)
        result = multipliers(rows, AS_OF)
        # Still resolves to 2 - the eccentric-mode rows must not dilute
        # the standard-mode-only ratio.
        self.assertEqual(result[BACK_ID]["workload_multiplier"], 2)


class ComparableSessionHierarchyTests(unittest.TestCase):
    def test_exact_family_used_when_sufficient(self):
        rows = []
        for i in range(4):
            # Two distinct movements, distinct primary muscles, same
            # activity - satisfies "Upper Pull"'s minimum_eligible=2
            # classification threshold (a single movement's own primary
            # + secondary is not enough).
            rows.append(_row(f"pull{i}", 0, 2 + i * 5, movement_id=BACK_ID,
                              muscle_groups=("Back",), name="Barbell Bent Over Row"))
            rows.append(_row(f"pull{i}", 1, 2 + i * 5, movement_id=CURL_ID,
                              muscle_groups=("Biceps",), name="Hammer Curl"))
        sessions = sessions_from_rows(rows, AS_OF)
        comparable, source = comparable_sessions(sessions, "Upper Pull", AS_OF)
        self.assertEqual(source, "exact_family")

    def test_falls_back_to_general_history_when_family_sparse(self):
        rows = []
        for i in range(4):
            rows += _session_rows(f"squat{i}", 2 + i * 5, n_sets=3, movement_id="quad-uuid",
                                   muscle_groups=("Quads", "Glutes"), name="Goblet Squat")
        sessions = sessions_from_rows(rows, AS_OF)
        comparable, source = comparable_sessions(sessions, "Upper Pull", AS_OF)
        self.assertIn(source, ("general_personal_history", "muscle_region"))

    def test_no_history_falls_back_to_product_policy(self):
        comparable, source = comparable_sessions([], "Upper Pull", AS_OF)
        self.assertEqual(source, "product_policy_fallback")
        self.assertEqual(comparable, [])


class CapacityModelTests(unittest.TestCase):
    def _rich_family_sessions(self, n=8, sets_per_session=15):
        rows = []
        for i in range(n):
            rows += _session_rows(f"pull{i}", 3 + i * 4, n_sets=sets_per_session, movement_id=BACK_ID,
                                   muscle_groups=("Back", "Biceps"))
        return sessions_from_rows(rows, AS_OF)

    def test_recent_low_stimulus_does_not_collapse_long_term_capacity(self):
        """Core invariant (section 6): a training gap must not, by
        itself, make historical_capacity collapse toward zero."""
        sessions = self._rich_family_sessions(n=8, sets_per_session=15)
        # Zero recent (last 90 days) sessions - all pushed far into the past.
        old_rows = []
        for i in range(8):
            old_rows += _session_rows(f"old{i}", 200 + i * 10, n_sets=15, movement_id=BACK_ID,
                                       muscle_groups=("Back", "Biceps"))
        old_sessions = sessions_from_rows(old_rows, AS_OF)
        ref = capacity_reference(old_sessions, "Upper Pull", AS_OF)
        self.assertGreaterEqual(ref["median_sets"], 10)
        self.assertIsNone(ref["recent_median_sets"])

    def test_capacity_reference_uses_percentiles_not_single_session(self):
        sessions = self._rich_family_sessions()
        ref = capacity_reference(sessions, "Upper Pull", AS_OF)
        self.assertLessEqual(ref["p25_sets"], ref["median_sets"])
        self.assertLessEqual(ref["median_sets"], ref["p75_sets"])
        self.assertGreaterEqual(ref["comparable_session_count"], 8)

    def test_no_history_uses_versioned_fallback_not_crash(self):
        ref = capacity_reference([], "Upper Pull", AS_OF)
        self.assertEqual(ref["confidence"], "LOW")
        self.assertIsNotNone(ref["median_sets"])

    def test_personal_frequency_derived_from_real_gaps(self):
        sessions = self._rich_family_sessions()
        ref = capacity_reference(sessions, "Upper Pull", AS_OF)
        self.assertEqual(ref["frequency_source"], "personal_inter_session_gaps")
        self.assertGreater(ref["personal_frequency_per_week"], 0)


class FeasibleCapacityTests(unittest.TestCase):
    def _capacity(self, low=13.0, high=17.0):
        return {"p25_sets": low, "p75_sets": high}

    def test_local_fatigue_still_constrains_hard(self):
        sessions = []
        feasible_fresh = feasible_capacity(self._capacity(), sessions, AS_OF, "high", {"Back": "READY"})
        feasible_fatigued = feasible_capacity(self._capacity(), sessions, AS_OF, "high", {"Back": "FATIGUED"})
        self.assertLess(feasible_fatigued["upper_bound_working_sets"], feasible_fresh["upper_bound_working_sets"])
        self.assertEqual(feasible_fatigued["upper_bound_working_sets"], 0)

    def test_high_recovery_cannot_exceed_demonstrated_capacity(self):
        feasible = feasible_capacity(self._capacity(low=13, high=17), sessions=[], as_of=AS_OF,
                                      readiness_band="high", local_states={"Back": "READY"})
        self.assertLessEqual(feasible["upper_bound_working_sets"], 17)

    def test_unknown_local_readiness_is_not_fresh(self):
        feasible_unknown = feasible_capacity(self._capacity(), [], AS_OF, "high", {"Back": "UNKNOWN"})
        feasible_fresh = feasible_capacity(self._capacity(), [], AS_OF, "high", {"Back": "READY"})
        self.assertLessEqual(feasible_unknown["upper_bound_working_sets"], feasible_fresh["upper_bound_working_sets"])

    def test_binding_constraints_report_provenance(self):
        feasible = feasible_capacity(self._capacity(), [], AS_OF, "moderate", {"Back": "READY"})
        names = {c["constraint"] for c in feasible["binding_constraints"]}
        self.assertIn("historical_capacity", names)
        self.assertIn("systemic_readiness", names)

    def test_prolonged_absence_reduces_range_but_not_to_zero(self):
        old_sessions = sessions_from_rows(
            _session_rows("old", 300, n_sets=10, movement_id=BACK_ID, muscle_groups=("Back", "Biceps")), AS_OF)
        feasible = feasible_capacity(self._capacity(low=13, high=17), old_sessions, AS_OF, "high", {"Back": "READY"})
        self.assertGreater(feasible["upper_bound_working_sets"], 0)
        self.assertLess(feasible["upper_bound_working_sets"], 17)
        states = {c["constraint"]: c for c in feasible["binding_constraints"]}
        self.assertTrue(states["detraining_uncertainty"]["binding"])

    def test_session_structure_cap_applied_when_supplied(self):
        feasible = feasible_capacity(self._capacity(low=13, high=17), [], AS_OF, "high", {"Back": "READY"}, structure_cap=6)
        self.assertLessEqual(feasible["upper_bound_working_sets"], 6)


class GoalPostureTests(unittest.TestCase):
    def test_goal_mode_selects_posture_inside_feasible_range_not_beyond(self):
        feasible = {"lower_bound_working_sets": 10, "upper_bound_working_sets": 16}
        for mode in GOAL_MODES:
            dose = choose_dose(feasible, mode)
            self.assertGreaterEqual(dose, feasible["lower_bound_working_sets"])
            self.assertLessEqual(dose, feasible["upper_bound_working_sets"])

    def test_goal_mode_cannot_invent_capacity_beyond_range(self):
        feasible = {"lower_bound_working_sets": 4, "upper_bound_working_sets": 6}
        doses = {mode: choose_dose(feasible, mode) for mode in GOAL_MODES}
        self.assertTrue(all(4 <= d <= 6 for d in doses.values()))


class ProgressionComparatorTests(unittest.TestCase):
    def test_paired_load_matched_evidence(self):
        rows = _rich_two_cable_history()
        profile = {"movement_id": BACK_ID, "name": "Barbell Bent Over Row", "muscle_groups": ["Back", "Biceps"]}
        result = prescribe(profile, rows, AS_OF, "good", 3)
        self.assertIn(result["progression_state"], ("HOLD", "PROGRESS_REPS", "PROGRESS_LOAD"))
        self.assertGreater(result["comparable_performance"]["matched_set_count"], 0)

    def test_warm_up_sets_excluded_from_progression(self):
        rows = _session_rows("s1", 2, n_sets=3, warm_up=True)
        matched = matched_history(rows, BACK_ID, AS_OF)
        self.assertEqual(matched, [])

    def test_no_cross_mode_progression_claim(self):
        """Comparable history recorded under a non-standard mode must
        never silently anchor a standard-mode prescription."""
        rows = _session_rows("s1", 2, n_sets=3, eccentric=True)
        matched = matched_history(rows, BACK_ID, AS_OF)
        self.assertEqual(matched, [])
        profile = {"movement_id": BACK_ID, "name": "Barbell Bent Over Row", "muscle_groups": ["Back"]}
        result = prescribe(profile, rows, AS_OF, "good", 3)
        self.assertEqual(result["progression_state"], "REBUILD")
        self.assertFalse(result["cross_mode_fallback"])

    def test_mode_filtering_happens_before_sample_limit(self):
        """Section 14: retrieving eligible history, THEN filtering mode,
        THEN limiting sample count - never truncate before mode
        filtering (which would produce false REBUILD outcomes)."""
        rows = []
        # 6 old eccentric-mode sessions (would fill the sample-count
        # window if mode filtering happened after truncation).
        for i in range(6):
            rows += _session_rows(f"ecc{i}", 10 + i, n_sets=2, eccentric=True)
        # 3 real standard-mode sessions, further back in time.
        for i in range(3):
            rows += _session_rows(f"std{i}", 20 + i * 3, n_sets=2, volume_ratio=2.0)
        matched = matched_history(rows, BACK_ID, AS_OF)
        self.assertTrue(all(not r["eccentric"] for r in matched))
        self.assertGreater(len(matched), 0)

    def test_assisted_reps_excluded_from_progression(self):
        rows = _session_rows("s1", 2, n_sets=3, base_weight=60.0)
        for r in rows:
            r["avg_weight"] = 40.0  # >5% below base -> assisted, per unassisted()
        matched = matched_history(rows, BACK_ID, AS_OF)
        self.assertEqual(matched, [])


class WorkloadEnvelopeAndSanityTests(unittest.TestCase):
    def test_within_range_status(self):
        reference = {"90": {"normalized_workload": {"sample_count": 5, "median": 10000, "p25": 8000, "p75": 12000}}}
        exercises = [{"estimated_volume": 9000, "workload": {"confidence": "MEDIUM"}}]
        result = workload_sanity(exercises, reference, [])
        self.assertEqual(result["status"], "WITHIN_PERSONAL_RANGE")

    def test_below_range_flagged_without_justification(self):
        reference = {"90": {"normalized_workload": {"sample_count": 5, "median": 10000, "p25": 8000, "p75": 12000}}}
        exercises = [{"estimated_volume": 3000, "workload": {"confidence": "MEDIUM"}}]
        result = workload_sanity(exercises, reference, [])
        self.assertEqual(result["status"], "BELOW_PERSONAL_RANGE")
        self.assertIsNone(result["accepted_reason_for_deviation"])

    def test_below_range_accepted_with_justification(self):
        reference = {"90": {"normalized_workload": {"sample_count": 5, "median": 10000, "p25": 8000, "p75": 12000}}}
        exercises = [{"estimated_volume": 3000, "workload": {"confidence": "MEDIUM"}}]
        reasons = [{"constraint": "local_readiness", "binding": True}]
        result = workload_sanity(exercises, reference, reasons)
        self.assertIsNotNone(result["accepted_reason_for_deviation"])

    def test_insufficient_reference_data_reported_explicitly(self):
        empty_window = {"normalized_workload": {"sample_count": 0, "median": None, "p25": None, "p75": None}}
        reference = {"90": empty_window, "365": empty_window, "30": empty_window}
        exercises = [{"estimated_volume": 3000, "workload": {"confidence": "MEDIUM"}}]
        result = workload_sanity(exercises, reference, [])
        self.assertEqual(result["status"], "INSUFFICIENT_DATA")

    def test_does_not_auto_inflate_to_median(self):
        reference = {"90": {"normalized_workload": {"sample_count": 5, "median": 10000, "p25": 8000, "p75": 12000}}}
        exercises = [{"estimated_volume": 2000, "workload": {"confidence": "MEDIUM"}}]
        result = workload_sanity(exercises, reference, [])
        self.assertEqual(result["estimated_workload"], 2000)  # never bumped toward the median


class QualityVerdictTests(unittest.TestCase):
    def _exercise(self, sets=3, confidence="HIGH", muscles=("Back",)):
        return {"working_sets": sets, "confidence": confidence, "primary_muscles": list(muscles), "secondary_muscles": []}

    def test_contradicted_when_dose_exceeds_feasible_upper(self):
        exercises = [self._exercise(sets=20)]
        feasible = {"upper_bound_working_sets": 10}
        sanity = {"status": "WITHIN_PERSONAL_RANGE", "accepted_reason_for_deviation": None}
        result = quality_verdict(exercises, feasible, sanity, {}, target=10)
        self.assertEqual(result["verdict"], "CONTRADICTED")

    def test_contradicted_when_fatigued_muscle_loaded(self):
        exercises = [self._exercise(sets=5, muscles=("Back",))]
        feasible = {"upper_bound_working_sets": 10}
        sanity = {"status": "WITHIN_PERSONAL_RANGE", "accepted_reason_for_deviation": None}
        result = quality_verdict(exercises, feasible, sanity, {"Back": "FATIGUED"}, target=5)
        self.assertEqual(result["verdict"], "CONTRADICTED")

    def test_supported_when_all_consistent(self):
        exercises = [self._exercise(sets=5, confidence="HIGH")]
        feasible = {"upper_bound_working_sets": 10}
        sanity = {"status": "WITHIN_PERSONAL_RANGE", "accepted_reason_for_deviation": None}
        result = quality_verdict(exercises, feasible, sanity, {"Back": "READY"}, target=5)
        self.assertEqual(result["verdict"], "SUPPORTED")

    def test_questionable_when_unexplained_deviation(self):
        exercises = [self._exercise(sets=5, confidence="HIGH")]
        feasible = {"upper_bound_working_sets": 10}
        sanity = {"status": "BELOW_PERSONAL_RANGE", "accepted_reason_for_deviation": None}
        result = quality_verdict(exercises, feasible, sanity, {"Back": "READY"}, target=5)
        self.assertEqual(result["verdict"], "QUESTIONABLE")


class ColdStartTests(unittest.TestCase):
    def _run(self, rows=None, profiles=None, muscles=None, readiness_band="good", recovery_score=70.0, goal=None):
        rows = rows if rows is not None else []
        profiles = profiles if profiles is not None else []
        muscles = muscles if muscles is not None else [
            {"muscle": m, "readiness_state": "READY", "hours_since_primary_exposure": 100.0}
            for m in ("Chest", "Back", "Shoulders", "Biceps", "Triceps", "Core", "Glutes", "Hamstrings", "Quads")
        ]
        readiness = {"recovery_score": recovery_score, "readiness_band": readiness_band}
        muscle_readiness = {"selection_confidence": "high", "latest_workout_age_hours": 24.0, "muscles": muscles}
        with patch(
            "training_intelligence.selection.shadow_selection._latest_readiness", return_value=readiness,
        ), patch(
            "training_intelligence.selection.shadow_selection.calculate_muscle_readiness", return_value=muscle_readiness,
        ), patch(
            "training_intelligence.selection.shadow_selection.get_active_goal", return_value=goal or {},
        ), patch(
            "training_intelligence.selection.shadow_selection.load_session_history", return_value=[],
        ), patch(
            "training_intelligence.selection.shadow_selection._load_muscle_set_rows", return_value=[],
        ), patch(
            "training_intelligence.selection.shadow_selection.load_rows", return_value=[],
        ), patch(
            "training_intelligence.calibration.shadow._latest_readiness", return_value=readiness,
        ), patch(
            "training_intelligence.calibration.shadow.calculate_muscle_readiness", return_value=muscle_readiness,
        ), patch(
            "training_intelligence.calibration.shadow.build_movement_performance_profiles",
            return_value={"profiles": profiles},
        ):
            return build_calibrated_shadow_prescription(AS_OF, rows=rows, profiles=profiles)

    def test_zero_history_does_not_crash(self):
        result = self._run()
        self.assertEqual(result["status"], "ok")

    def test_zero_history_produces_valid_dose_zero_or_fallback(self):
        result = self._run()
        self.assertIn("dose", result)
        self.assertIsInstance(result["dose"]["working_sets"], int)

    def test_sparse_history_below_min_sessions_does_not_crash(self):
        rows = _session_rows("s1", 2, n_sets=3)
        result = self._run(rows=rows)
        self.assertEqual(result["status"], "ok")

    def test_unknown_readiness_band_does_not_crash(self):
        result = self._run(readiness_band="unknown", recovery_score=None)
        self.assertEqual(result["status"], "ok")


class RestOutcomeTests(unittest.TestCase):
    def test_rest_family_produces_zero_dose_no_exercises(self):
        muscles = [
            {"muscle": m, "readiness_state": "FATIGUED", "hours_since_primary_exposure": 2.0}
            for m in ("Chest", "Back", "Shoulders", "Biceps", "Triceps", "Core", "Glutes", "Hamstrings", "Quads")
        ]
        readiness = {"recovery_score": 5.0, "readiness_band": "very_low"}
        muscle_readiness = {"selection_confidence": "high", "latest_workout_age_hours": 24.0, "muscles": muscles}
        with patch(
            "training_intelligence.selection.shadow_selection._latest_readiness", return_value=readiness,
        ), patch(
            "training_intelligence.selection.shadow_selection.calculate_muscle_readiness", return_value=muscle_readiness,
        ), patch(
            "training_intelligence.selection.shadow_selection.get_active_goal", return_value={},
        ), patch(
            "training_intelligence.selection.shadow_selection.load_session_history", return_value=[],
        ), patch(
            "training_intelligence.selection.shadow_selection._load_muscle_set_rows", return_value=[],
        ), patch(
            "training_intelligence.selection.shadow_selection.load_rows", return_value=[],
        ), patch(
            "training_intelligence.calibration.shadow._latest_readiness", return_value=readiness,
        ), patch(
            "training_intelligence.calibration.shadow.calculate_muscle_readiness", return_value=muscle_readiness,
        ):
            result = build_calibrated_shadow_prescription(AS_OF, rows=[], profiles=[])
        self.assertEqual(result["selected_session_family"], REST_FAMILY_NAME)
        self.assertEqual(result["exercises"], [])
        self.assertEqual(result["dose"]["dose_shortfall"], 0)


class NoHardcodedConstantsTests(unittest.TestCase):
    def test_no_hardcoded_tonnage_target_in_source(self):
        import training_intelligence.calibration.shadow as shadow_module
        import training_intelligence.calibration.capacity as capacity_module
        import inspect
        source = inspect.getsource(shadow_module) + inspect.getsource(capacity_module)
        self.assertNotIn("20000", source)
        self.assertNotIn("20,000", source)

    def test_no_hardcoded_recovery_percentage(self):
        import training_intelligence.calibration.capacity as capacity_module
        import inspect
        source = inspect.getsource(capacity_module)
        self.assertNotIn("0.94", source)
        self.assertNotIn("94.0", source)

    def test_no_hardcoded_session_family(self):
        import training_intelligence.calibration.shadow as shadow_module
        import inspect
        source = inspect.getsource(shadow_module)
        self.assertNotIn('"Upper Push"', source)
        self.assertNotIn("'Upper Push'", source)


class DeterminismTests(unittest.TestCase):
    def test_repeated_multipliers_call_deterministic(self):
        rows = _rich_two_cable_history()
        first = multipliers(rows, AS_OF)
        second = multipliers(rows, AS_OF)
        self.assertEqual(first, second)

    def test_repeated_capacity_reference_deterministic(self):
        rows = _rich_two_cable_history()
        sessions = sessions_from_rows(rows, AS_OF)
        first = capacity_reference(sessions, "Upper Pull", AS_OF)
        second = capacity_reference(sessions, "Upper Pull", AS_OF)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
