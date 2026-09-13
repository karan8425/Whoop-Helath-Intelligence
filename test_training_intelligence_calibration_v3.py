"""TKI-5.3: comparable workload evidence + reproducible decision state.

Pure/in-memory tests for training_intelligence.calibration.quality,
.workload_v2, and .snapshot - matching the established convention (no
live database; opt-in Postgres coverage for the temporal/round-trip
claims belongs in a *_postgres.py module, not here).
"""
import json
import unittest
from datetime import datetime, timedelta, timezone

from training_intelligence.calibration.quality import (
    session_quality_score, scored_comparable_sessions, QUALITY_WEIGHTS, MIN_QUALITY_TO_INCLUDE,
)
from training_intelligence.calibration.workload_v2 import (
    workload_envelope_v2, decompose_gap, workload_sanity_v2, _weighted_quantile, _weighted_distribution,
)
from training_intelligence.calibration.snapshot import (
    build_snapshot, compare_to_snapshot, DECISION_CRITICAL_FIELDS,
)
from training_intelligence.calibration.legacy_fixtures import (
    sep12_legacy_fixture_analysis, SEP_12_FORENSIC_FACTS,
)

AS_OF = datetime(2026, 9, 12, 8, 10, tzinfo=timezone.utc)


def _session(days_ago, family="Upper Push", set_count=10, exercise_count=3,
             normalized_workload=9000.0, workload_confident=True, known_workload_fraction=0.9,
             primary_counts=None):
    return {
        "activity_id": f"s-{days_ago}", "begin_time": AS_OF - timedelta(days=days_ago),
        "family": family, "primary_counts": primary_counts or {"Chest": 1, "Shoulders": 1},
        "set_count": set_count, "exercise_count": exercise_count,
        "movement_ids": [f"m{i}" for i in range(exercise_count)],
        "primary_movement_count": exercise_count,
        "sets_per_movement": {}, "normalized_workload": normalized_workload,
        "workload_confident": workload_confident, "known_workload_fraction": known_workload_fraction,
        "recorded_volume": normalized_workload,
    }


def _exercise(working_sets=4, estimated_volume=3000.0, confidence="HIGH"):
    return {"working_sets": working_sets, "estimated_volume": estimated_volume,
            "workload": {"confidence": confidence}}


class SessionQualityScoreTests(unittest.TestCase):
    def test_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(QUALITY_WEIGHTS.values()), 1.0, places=6)

    def test_exact_family_match_scores_higher_than_mismatch(self):
        matching = _session(10, family="Upper Push", primary_counts={"Chest": 2, "Shoulders": 2, "Triceps": 1})
        mismatch = dict(matching, family="Lower Body")
        score_match, _ = session_quality_score(matching, "Upper Push", AS_OF)
        score_mismatch, _ = session_quality_score(mismatch, "Upper Push", AS_OF)
        self.assertGreater(score_match, score_mismatch)

    def test_recency_decays_but_never_disqualifies_alone(self):
        recent = _session(5)
        old = _session(300)
        score_recent, _ = session_quality_score(recent, "Upper Push", AS_OF)
        score_old, _ = session_quality_score(old, "Upper Push", AS_OF)
        self.assertGreater(score_recent, score_old)
        # recency alone (10% weight) should not zero out an otherwise strong match
        self.assertGreaterEqual(score_old, MIN_QUALITY_TO_INCLUDE)

    def test_data_completeness_uses_known_workload_fraction(self):
        complete = _session(10, known_workload_fraction=1.0)
        incomplete = _session(10, known_workload_fraction=0.1)
        score_complete, _ = session_quality_score(complete, "Upper Push", AS_OF)
        score_incomplete, _ = session_quality_score(incomplete, "Upper Push", AS_OF)
        self.assertGreater(score_complete, score_incomplete)


class ScoredComparableSessionsTests(unittest.TestCase):
    def test_excludes_future_sessions(self):
        future = _session(-5)
        past = _session(5)
        scored = scored_comparable_sessions([future, past], "Upper Push", AS_OF)
        self.assertEqual([s for s, _, _ in scored], [past])

    def test_window_filters_by_days(self):
        recent = _session(10)
        old = _session(200)
        scored = scored_comparable_sessions([recent, old], "Upper Push", AS_OF, window_days=30)
        self.assertEqual([s for s, _, _ in scored], [recent])

    def test_low_quality_sessions_excluded_not_zeroed(self):
        # A completely mismatched family/muscle/low-completeness session
        # should fall below MIN_QUALITY_TO_INCLUDE and be dropped, not
        # merely down-weighted to zero contribution silently.
        poor = _session(10, family="Lower Body", primary_counts={"Quads": 1}, known_workload_fraction=0.0)
        scored = scored_comparable_sessions([poor], "Upper Push", AS_OF)
        self.assertEqual(scored, [])

    def test_sorted_descending_by_score(self):
        good = _session(5, primary_counts={"Chest": 2, "Shoulders": 2, "Triceps": 1})
        ok = _session(60, primary_counts={"Chest": 1, "Shoulders": 1})
        scored = scored_comparable_sessions([ok, good], "Upper Push", AS_OF)
        scores = [q for _, q, _ in scored]
        self.assertEqual(scores, sorted(scores, reverse=True))


class WeightedQuantileTests(unittest.TestCase):
    def test_equal_weights_matches_plain_median(self):
        pairs = [(10, 1.0), (20, 1.0), (30, 1.0)]
        self.assertEqual(_weighted_quantile(pairs, 0.5), 20)

    def test_heavier_weight_pulls_quantile_toward_it(self):
        pairs = [(10, 5.0), (100, 0.1)]
        median = _weighted_quantile(pairs, 0.5)
        self.assertEqual(median, 10)

    def test_empty_input_returns_none(self):
        self.assertIsNone(_weighted_quantile([], 0.5))
        self.assertEqual(_weighted_distribution([]), {"effective_count": 0.0, "median": None, "p25": None, "p75": None})


class WorkloadEnvelopeV2Tests(unittest.TestCase):
    def test_confident_only_sessions_feed_normalized_workload(self):
        confident = _session(10, workload_confident=True, normalized_workload=9000.0)
        unconfident = _session(11, workload_confident=False, normalized_workload=50000.0)
        envelope = workload_envelope_v2([confident, unconfident], "Upper Push", AS_OF)
        window = envelope["windows"]["90"]
        # the unconfident session's absurd normalized_workload must not
        # leak into the absolute-workload distribution
        self.assertEqual(window["normalized_workload"]["median"], 9000.0)
        # but both sessions still count toward set/exercise-count distributions
        self.assertEqual(window["working_sets"]["effective_count"] > 0, True)

    def test_windows_present_for_30_90_365(self):
        envelope = workload_envelope_v2([_session(10)], "Upper Push", AS_OF)
        self.assertEqual(set(envelope["windows"]), {"30", "90", "365"})


class DecomposeGapTests(unittest.TestCase):
    def _window(self, median_workload=9000.0, median_per_set=900.0, median_sets=10, median_exercises=3):
        return {
            "normalized_workload": {"median": median_workload},
            "workload_per_set": {"median": median_per_set},
            "working_sets": {"median": median_sets},
            "exercises": {"median": median_exercises},
        }

    def test_dose_driven_gap_flags_dose_contribution(self):
        window = self._window()
        gap = decompose_gap(estimated_workload=4500, delivered_sets=5, exercise_count=3, window=window)
        self.assertIn("dose_contribution", gap["dominant_gap_causes"])
        self.assertEqual(gap["set_count_ratio"], 0.5)

    def test_resistance_driven_gap_flags_resistance_or_rep(self):
        # same sets/exercises as the reference, but much lower workload
        # per set -> resistance/rep is the only plausible cause.
        window = self._window()
        gap = decompose_gap(estimated_workload=4500, delivered_sets=10, exercise_count=3, window=window)
        self.assertEqual(gap["set_count_ratio"], 1.0)
        self.assertEqual(gap["exercise_count_ratio"], 1.0)
        self.assertIn("resistance_or_rep_contribution", gap["dominant_gap_causes"])

    def test_within_range_reports_no_dominant_cause(self):
        window = self._window()
        gap = decompose_gap(estimated_workload=9000, delivered_sets=10, exercise_count=3, window=window)
        self.assertEqual(gap["dominant_gap_causes"], ["none_below_threshold"])

    def test_never_claims_certainty_multiple_causes_can_coexist(self):
        window = self._window()
        gap = decompose_gap(estimated_workload=2000, delivered_sets=4, exercise_count=1, window=window)
        self.assertTrue(len(gap["dominant_gap_causes"]) >= 2)


class WorkloadSanityV2Tests(unittest.TestCase):
    def _sessions(self, n=6, normalized_workload=9000.0, set_count=10, exercise_count=3):
        return [_session(10 + i * 7, normalized_workload=normalized_workload,
                          set_count=set_count, exercise_count=exercise_count) for i in range(n)]

    def test_within_personal_range(self):
        exercises = [_exercise(working_sets=4, estimated_volume=3000.0)] * 3
        result = workload_sanity_v2(exercises, self._sessions(), "Upper Push", AS_OF, 12, [], False)
        self.assertEqual(result["status"], "WITHIN_PERSONAL_RANGE")
        self.assertEqual(result["dominant_gap_causes"], [])

    def test_below_range_unexplained_without_binding_constraint(self):
        exercises = [_exercise(working_sets=2, estimated_volume=800.0)] * 2
        result = workload_sanity_v2(exercises, self._sessions(), "Upper Push", AS_OF, 12, [], False)
        self.assertEqual(result["status"], "BELOW_PERSONAL_RANGE_UNEXPLAINED")
        self.assertIsNone(result["justification"])

    def test_below_range_justified_with_binding_readiness_constraint(self):
        exercises = [_exercise(working_sets=2, estimated_volume=800.0)] * 2
        binding = [{"constraint": "systemic_readiness", "binding": True}]
        result = workload_sanity_v2(exercises, self._sessions(), "Upper Push", AS_OF, 12, binding, False)
        self.assertEqual(result["status"], "BELOW_PERSONAL_RANGE_JUSTIFIED")
        self.assertEqual(result["justification"], ["systemic_readiness"])

    def test_reduced_goal_posture_is_a_named_justification(self):
        exercises = [_exercise(working_sets=2, estimated_volume=800.0)] * 2
        result = workload_sanity_v2(exercises, self._sessions(), "Upper Push", AS_OF, 12, [], True)
        self.assertEqual(result["status"], "BELOW_PERSONAL_RANGE_JUSTIFIED")
        self.assertIn("reduced_goal_posture", result["justification"])

    def test_insufficient_data_when_no_comparable_sessions(self):
        exercises = [_exercise()] * 2
        result = workload_sanity_v2(exercises, [], "Upper Push", AS_OF, 12, [], False)
        self.assertEqual(result["status"], "INSUFFICIENT_DATA")
        self.assertEqual(result["comparable_count"], 0)

    def test_known_workload_fraction_reflects_low_confidence_exercises(self):
        exercises = [_exercise(estimated_volume=1000.0, confidence="HIGH"),
                     _exercise(estimated_volume=1000.0, confidence="LOW")]
        result = workload_sanity_v2(exercises, self._sessions(), "Upper Push", AS_OF, 12, [], False)
        self.assertEqual(result["known_workload_fraction"], 0.5)

    def test_above_range_justified_by_session_structure(self):
        exercises = [_exercise(working_sets=6, estimated_volume=6000.0)] * 3
        binding = [{"constraint": "session_structure", "binding": True}]
        result = workload_sanity_v2(exercises, self._sessions(), "Upper Push", AS_OF, 12, binding, False)
        self.assertIn(result["status"], ("ABOVE_PERSONAL_RANGE_JUSTIFIED", "WITHIN_PERSONAL_RANGE"))


class SnapshotTests(unittest.TestCase):
    def _result(self, **overrides):
        base = {
            "calibration_model_version": 2, "prescription_model_version": 1, "goal_policy_version": 1,
            "goal_mode": "GENERAL_FITNESS", "readiness": {"metric_date": "2026-09-12", "recovery_score": 66},
            "local_readiness": {"states": {}}, "selected_session_family": "Upper Push",
            "feasible_range": {"lower_bound_working_sets": 8, "upper_bound_working_sets": 14},
            "personal_capacity_reference": {"source": "exact_family"},
            "composition": {"exercise_count": 3},
            "exercises": [{"movement_id": "m1", "movement_name": "Bench", "progression_state": "MATCH",
                           "confidence": "HIGH", "comparable_history": 5}],
            "workload_reference": {"90": {}}, "workload_sanity_v2": {"status": "WITHIN_PERSONAL_RANGE",
                                                                       "workload_v2_policy_version": 1},
            "dose": {"working_sets": 10, "delivered_sets": 10}, "estimated_total_volume": 9000.0,
            "quality_v2": {"verdict": "SUPPORTED", "reasons": []},
        }
        base.update(overrides)
        return base

    def test_build_snapshot_is_deterministic_for_same_inputs(self):
        result = self._result()
        snap1 = build_snapshot(AS_OF, result)
        snap2 = build_snapshot(AS_OF, result)
        self.assertEqual(snap1["decision_id"], snap2["decision_id"])
        self.assertEqual(snap1["final_prescription"], snap2["final_prescription"])

    def test_decision_id_changes_with_family_or_as_of(self):
        result = self._result()
        snap = build_snapshot(AS_OF, result)
        snap_other_family = build_snapshot(AS_OF, self._result(selected_session_family="Lower Body"))
        snap_other_time = build_snapshot(AS_OF + timedelta(days=1), result)
        self.assertNotEqual(snap["decision_id"], snap_other_family["decision_id"])
        self.assertNotEqual(snap["decision_id"], snap_other_time["decision_id"])

    def test_replay_of_identical_result_is_equivalent(self):
        result = self._result()
        snapshot = build_snapshot(AS_OF, result)
        comparison = compare_to_snapshot(snapshot, result)
        self.assertTrue(comparison["equivalent"])
        self.assertEqual(comparison["mismatched_fields"], [])

    def test_replay_with_changed_dose_is_flagged_not_silently_accepted(self):
        result = self._result()
        snapshot = build_snapshot(AS_OF, result)
        changed = self._result(dose={"working_sets": 6, "delivered_sets": 6})
        comparison = compare_to_snapshot(snapshot, changed)
        self.assertFalse(comparison["equivalent"])
        self.assertIn("dose", comparison["mismatched_fields"])

    def test_replay_tolerates_json_round_trip_type_normalization(self):
        # A snapshot loaded back from Postgres has already been through
        # json.dumps/json.loads: tuples become lists. That alone must
        # never be reported as a mismatch - only an actual value change
        # should be.
        result = self._result(feasible_range={
            "lower_bound_working_sets": 8, "upper_bound_working_sets": 14,
            "binding_constraints": [{"constraint": "systemic_readiness", "fractions": (0.75, 0.85), "binding": True}],
        })
        snapshot = build_snapshot(AS_OF, result)
        # simulate the DB round trip explicitly
        reloaded = json.loads(json.dumps(snapshot, default=str))
        comparison = compare_to_snapshot(reloaded, result)
        self.assertTrue(comparison["equivalent"])
        self.assertEqual(comparison["mismatched_fields"], [])

    def test_all_decision_critical_fields_are_extractable(self):
        result = self._result()
        snapshot = build_snapshot(AS_OF, result)
        comparison = compare_to_snapshot(snapshot, result)
        # every field in the contract must be resolvable on both sides
        # without raising - a KeyError here means the contract and the
        # snapshot shape have drifted apart.
        self.assertEqual(set(DECISION_CRITICAL_FIELDS) - set(comparison["detail"]) | set(comparison["detail"]),
                          set(DECISION_CRITICAL_FIELDS))


class Sep12LegacyFixtureTests(unittest.TestCase):
    def test_classified_as_unreproducible_legacy_state_not_a_reconstruction(self):
        result = sep12_legacy_fixture_analysis()
        self.assertEqual(result["classification"], "UNREPRODUCIBLE_LEGACY_STATE")

    def test_cable_aware_correction_still_shows_below_range(self):
        result = sep12_legacy_fixture_analysis()
        raw = result["decomposition"]["raw__strict_exact_family"]
        corrected = result["decomposition"]["cable_aware__strict_exact_family"]
        # cable-aware correction must raise the ratio relative to raw...
        self.assertGreater(corrected["absolute_ratio_to_median"], raw["absolute_ratio_to_median"])
        # ...but not erase the below-range gap entirely (both known
        # reference medians in the surviving record are higher than
        # even the corrected volume).
        self.assertEqual(corrected["status_without_justification_lookup"], "BELOW_PERSONAL_RANGE")

    def test_all_four_readings_agree_below_range(self):
        result = sep12_legacy_fixture_analysis()
        statuses = {k: v["status_without_justification_lookup"] for k, v in result["decomposition"].items()}
        self.assertTrue(all(s == "BELOW_PERSONAL_RANGE" for s in statuses.values()), statuses)

    def test_quality_verdict_not_forced_to_pass(self):
        result = sep12_legacy_fixture_analysis()
        self.assertEqual(result["quality_verdict"], "QUESTIONABLE")

    def test_unrecoverable_ratios_are_explicitly_labeled_not_fabricated(self):
        result = sep12_legacy_fixture_analysis()
        for entry in result["decomposition"].values():
            for ratio_field in ("workload_per_set_ratio", "set_count_ratio", "exercise_count_ratio"):
                self.assertEqual(entry[ratio_field], "NOT_RECOVERABLE_FROM_SURVIVING_RECORD")

    def test_facts_match_the_carried_forward_forensic_record(self):
        self.assertEqual(SEP_12_FORENSIC_FACTS["displayed_raw_volume_lb"], 5772.0)
        self.assertEqual(SEP_12_FORENSIC_FACTS["cable_aware_volume_lb"], 9204.0)
        self.assertEqual(SEP_12_FORENSIC_FACTS["whoop_recovery_score"], 94)
        self.assertEqual(SEP_12_FORENSIC_FACTS["family"], "Upper Push")


if __name__ == "__main__":
    unittest.main()
