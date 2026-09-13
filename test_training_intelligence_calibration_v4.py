"""TKI-5.4: progression evidence V2 (mode compatibility, tiered
evidence, RIR reliability) + justification V2 (counterfactual binding
test, strength, large-deviation sufficiency) + workload_sanity_v3.

Pure/in-memory, matching the established convention (no live database;
opt-in Postgres coverage lives in a *_postgres.py module).
"""
import unittest
from datetime import datetime, timedelta, timezone

from training_intelligence.calibration.mode_compatibility import classify, is_usable_evidence
from training_intelligence.calibration.progression_v2 import (
    rir_reliability, evidence, prescribe_v2,
)
from training_intelligence.calibration.justification_v2 import (
    counterfactual_effects, justification_sufficient, LARGE_DEVIATION_RATIO,
)
from training_intelligence.calibration.workload_v2 import workload_sanity_v3, classify_gap
from training_intelligence.calibration.capacity import capacity_reference, feasible_capacity, choose_dose
from training_intelligence.calibration.history import sessions_from_rows, multipliers
from training_intelligence.calibration.legacy_fixtures import sep12_legacy_fixture_v3_analysis

AS_OF = datetime(2026, 9, 12, 8, 10, tzinfo=timezone.utc)
MOVE_ID = "move-uuid-1"


def _row(activity, set_index, days_ago, movement_id=MOVE_ID, muscle_groups=("Chest", "Shoulders"),
         rep_count=10, base_weight=60.0, volume=None, eccentric=False, chains=False, progressive=False,
         burnout=False, flex=False, spotter=False, included=True, is_generic=False, custom_movement=False,
         is_bilateral=True, is_two_sided=False, is_alternating=False, warm_up=False,
         rir=2.0, name="Standing Incline Press", accessory="StraightBar", duration=10, volume_ratio=1.0):
    begin = AS_OF - timedelta(days=days_ago)
    if volume is None:
        volume = base_weight * rep_count * volume_ratio
    return {
        "activity_id": activity, "begin_time": begin, "end_time": begin + timedelta(seconds=duration),
        "duration_seconds": duration, "set_index": set_index, "movement_id": movement_id,
        "rep_count": rep_count, "base_weight": base_weight, "avg_weight": base_weight,
        "volume": volume, "raw_data": {"warmUp": warm_up, "repsInReserve": rir},
        "eccentric": eccentric, "chains": chains, "progressive": progressive, "burnout": burnout, "flex": flex,
        "spotter": spotter, "struggling_score": None, "inconsistency_score": None,
        "name": name, "muscle_groups": list(muscle_groups), "accessory": accessory,
        "is_bilateral": is_bilateral, "is_two_sided": is_two_sided, "is_alternating": is_alternating,
        "is_generic": is_generic, "custom_movement": custom_movement, "included": included,
    }


def _session(activity, days_ago, n_sets=3, **kwargs):
    return [_row(activity, i, days_ago, **kwargs) for i in range(n_sets)]


class ModeCompatibilityTests(unittest.TestCase):
    def test_standard_is_exact(self):
        self.assertEqual(classify("standard"), "EXACT")

    def test_eccentric_chains_progressive_are_partial(self):
        for m in ("eccentric", "chains", "progressive", "eccentric+chains", "chains+progressive"):
            self.assertEqual(classify(m), "PARTIAL", m)

    def test_burnout_and_flex_are_unknown(self):
        for m in ("burnout", "flex", "eccentric+burnout", "chains+flex"):
            self.assertEqual(classify(m), "UNKNOWN", m)

    def test_non_standard_target_is_always_unknown(self):
        self.assertEqual(classify("standard", target_mode="eccentric"), "UNKNOWN")

    def test_usable_evidence_flags(self):
        self.assertTrue(is_usable_evidence("standard"))
        self.assertTrue(is_usable_evidence("chains"))
        self.assertFalse(is_usable_evidence("burnout"))


class RirReliabilityTests(unittest.TestCase):
    def test_no_rows_is_unusable(self):
        self.assertEqual(rir_reliability([]), "UNUSABLE")

    def test_high_availability_no_outliers_is_high(self):
        rows = [_row("a", i, 5, rir=2.0) for i in range(10)]
        self.assertEqual(rir_reliability(rows), "HIGH")

    def test_low_availability_is_unusable(self):
        rows = [_row("a", i, 5, rir=(2.0 if i < 3 else None)) for i in range(10)]
        self.assertEqual(rir_reliability(rows), "UNUSABLE")

    def test_outliers_downgrade_to_low(self):
        rows = [_row("a", i, 5, rir=(99.0 if i < 3 else 2.0)) for i in range(10)]
        self.assertEqual(rir_reliability(rows), "LOW")


class EvidenceTierTests(unittest.TestCase):
    def test_tier1_exact_mode_matched_load(self):
        rows = []
        for i in range(4):
            rows += _session(f"s{i}", 5 + i * 6, base_weight=60.0)
        ev = evidence(rows, MOVE_ID, AS_OF)
        self.assertEqual(ev["evidence_tier"], 1)
        self.assertEqual(ev["exact_mode_sessions"], 4)

    def test_tier3_cross_mode_fallback_used_when_exact_mode_sparse(self):
        rows = _session("standard1", 5, base_weight=60.0)  # only 1 exact session
        for i in range(5):
            rows += _session(f"ecc{i}", 10 + i * 6, base_weight=60.0, eccentric=True)
        ev = evidence(rows, MOVE_ID, AS_OF)
        self.assertEqual(ev["evidence_tier"], 3)
        self.assertGreaterEqual(ev["compatible_mode_sessions"], 4)
        self.assertIn("mode_compatible_fallback_used", ev["reason_codes"])

    def test_incompatible_mode_never_contributes_evidence(self):
        rows = []
        for i in range(6):
            rows += _session(f"b{i}", 5 + i * 6, base_weight=60.0, burnout=True)
        ev = evidence(rows, MOVE_ID, AS_OF)
        # burnout-only history is UNKNOWN compatibility - never silently used
        self.assertEqual(ev["evidence_tier"], 5)
        self.assertEqual(ev["exact_mode_sessions"], 0)
        self.assertEqual(ev["compatible_mode_sessions"], 0)

    def test_tier5_when_truly_no_history(self):
        ev = evidence([], MOVE_ID, AS_OF)
        self.assertEqual(ev["evidence_tier"], 5)
        self.assertEqual(ev["pool"], [])

    def test_adjacent_tonal_load_widens_pool_only_when_exact_load_insufficient(self):
        # 1 session at 60lb (recent anchor), 3 older sessions at 66lb (10%
        # away - outside tier1's ~5% window but inside tier2's 15% one).
        rows = _session("recent", 3, base_weight=60.0)
        for i in range(3):
            rows += _session(f"older{i}", 20 + i * 6, base_weight=66.0)
        ev = evidence(rows, MOVE_ID, AS_OF)
        self.assertIn(ev["evidence_tier"], (1, 2))
        if ev["evidence_tier"] == 2:
            self.assertGreaterEqual(len({r["activity_id"] for r in ev["pool"]}), 3)


class PrescribeV2Tests(unittest.TestCase):
    def _profile(self):
        return {"movement_id": MOVE_ID, "name": "Standing Incline Press", "muscle_groups": ["Chest", "Shoulders"]}

    def test_reduce_requires_matched_evidence_not_just_fewer_sets(self):
        # Declining rep trend WITH real matched-load evidence -> REDUCE allowed.
        rows = []
        for i, reps in enumerate([12, 12, 8, 8]):  # most-recent-first: recent sessions worse
            rows += _session(f"s{i}", 5 + i * 6, base_weight=60.0, rep_count=reps)
        p = prescribe_v2(self._profile(), rows, AS_OF, "low", 4)
        self.assertIn(p["progression_state"], ("REDUCE", "HOLD"))
        # never REDUCE from a single low-set-count session alone - here we
        # gave 4 full matched sessions, so this is legitimate evidence, not
        # an inferred decline from mere session-size variation.
        self.assertGreaterEqual(p["progression_evidence"]["matched_working_sets"], 6)

    def test_progress_load_requires_reliable_effort(self):
        rows = []
        for i in range(4):
            rows += _session(f"s{i}", 5 + i * 6, base_weight=60.0, rep_count=12, rir=1.0)
        p = prescribe_v2(self._profile(), rows, AS_OF, "high", 4)
        if p["progression_state"] == "PROGRESS_LOAD":
            self.assertIn(p["progression_evidence"]["effort_quality"], ("HIGH", "MEDIUM"))

    def test_unreliable_rir_downgrades_to_rep_only_progression(self):
        rows = []
        for i in range(4):
            rows += _session(f"s{i}", 5 + i * 6, base_weight=60.0, rep_count=12, rir=None)
        p = prescribe_v2(self._profile(), rows, AS_OF, "high", 4)
        self.assertNotEqual(p["progression_evidence"]["effort_quality"], "HIGH")
        self.assertNotIn("effort_verified_ceiling", p["progression_evidence"]["reason_codes"])

    def test_cold_start_zero_history_is_rebuild_low(self):
        p = prescribe_v2(self._profile(), [], AS_OF, "moderate", 4)
        self.assertEqual(p["progression_state"], "REBUILD")
        self.assertEqual(p["confidence"], "LOW")
        self.assertEqual(p["progression_evidence"]["evidence_tier"], 5)


class JustificationCounterfactualTests(unittest.TestCase):
    def _sessions(self, family="Upper Push"):
        rows = []
        set_counts = [2, 3, 3, 4, 5, 6]
        for i, n in enumerate(set_counts):
            rows += _session(f"h{i}", 10 + i * 10, n_sets=n, muscle_groups=("Chest", "Shoulders"))
        relationships = multipliers(rows, AS_OF)
        return sessions_from_rows(rows, AS_OF, relationships)

    def test_ready_muscles_never_cited_as_local_readiness_justification(self):
        capacity = capacity_reference(self._sessions(), "Upper Push", AS_OF)
        local_states = {"Chest": "READY", "Shoulders": "READY"}
        feasible = feasible_capacity(capacity, self._sessions(), AS_OF, "high", local_states)
        dose = choose_dose(feasible, "general_fitness")
        effects = counterfactual_effects(capacity, self._sessions(), AS_OF, "high", local_states,
                                          None, "general_fitness", feasible, dose)
        self.assertNotIn("local_readiness", [e["reason"] for e in effects])

    def test_recovering_muscle_is_binding_and_quantified(self):
        capacity = capacity_reference(self._sessions(), "Upper Push", AS_OF)
        local_states = {"Chest": "RECOVERING", "Shoulders": "READY"}
        feasible = feasible_capacity(capacity, self._sessions(), AS_OF, "high", local_states)
        dose = choose_dose(feasible, "general_fitness")
        effects = counterfactual_effects(capacity, self._sessions(), AS_OF, "high", local_states,
                                          None, "general_fitness", feasible, dose)
        entry = next(e for e in effects if e["reason"] == "local_readiness")
        self.assertTrue(entry["binding"])
        self.assertNotEqual(entry["observed_effect"]["set_delta"], 0)

    def test_goal_posture_not_binding_when_range_collapsed_to_a_point(self):
        capacity = {"p25_sets": 10, "p75_sets": 10, "capacity_policy_version": 1, "source": "exact_family",
                    "comparable_session_count": 5, "median_sets": 10, "recent_median_sets": 10,
                    "historical_upper_typical": 10, "confidence": "HIGH", "personal_frequency_per_week": 3,
                    "frequency_source": "x", "evidence_limit": "x"}
        feasible = feasible_capacity(capacity, self._sessions(), AS_OF, "high", {"Chest": "READY"})
        self.assertEqual(feasible["lower_bound_working_sets"], feasible["upper_bound_working_sets"])
        dose = choose_dose(feasible, "recovery")
        effects = counterfactual_effects(capacity, self._sessions(), AS_OF, "high", {"Chest": "READY"},
                                          None, "recovery", feasible, dose)
        entry = next(e for e in effects if e["reason"] == "reduced_goal_posture")
        self.assertFalse(entry["binding"])

    def test_goal_posture_binding_when_range_has_width(self):
        capacity = capacity_reference(self._sessions(), "Upper Push", AS_OF)
        feasible = feasible_capacity(capacity, self._sessions(), AS_OF, "high", {"Chest": "READY", "Shoulders": "READY"})
        self.assertGreater(feasible["upper_bound_working_sets"], feasible["lower_bound_working_sets"])
        dose = choose_dose(feasible, "recovery")
        effects = counterfactual_effects(capacity, self._sessions(), AS_OF, "high", {"Chest": "READY", "Shoulders": "READY"},
                                          None, "recovery", feasible, dose)
        entry = next(e for e in effects if e["reason"] == "reduced_goal_posture")
        self.assertTrue(entry["binding"])
        self.assertEqual(entry["strength"], "STRONG")


class JustificationSufficiencyTests(unittest.TestCase):
    def test_weak_alone_insufficient_for_large_deviation(self):
        justifications = [{"reason": "session_structure", "binding": True, "strength": "WEAK"}]
        self.assertFalse(justification_sufficient(justifications, 0.3))

    def test_strong_alone_sufficient_for_large_deviation(self):
        justifications = [{"reason": "systemic_readiness", "binding": True, "strength": "STRONG"}]
        self.assertTrue(justification_sufficient(justifications, 0.3))

    def test_two_moderate_sufficient_for_large_deviation(self):
        justifications = [{"reason": "systemic_readiness", "binding": True, "strength": "MODERATE"},
                           {"reason": "local_readiness", "binding": True, "strength": "MODERATE"}]
        self.assertTrue(justification_sufficient(justifications, 0.3))

    def test_single_moderate_insufficient_for_large_deviation(self):
        justifications = [{"reason": "systemic_readiness", "binding": True, "strength": "MODERATE"}]
        self.assertFalse(justification_sufficient(justifications, 0.3))

    def test_single_moderate_sufficient_for_small_deviation(self):
        justifications = [{"reason": "systemic_readiness", "binding": True, "strength": "MODERATE"}]
        self.assertTrue(justification_sufficient(justifications, 0.8))

    def test_no_binding_justification_is_insufficient(self):
        justifications = [{"reason": "reduced_goal_posture", "binding": False, "strength": "NONE"}]
        self.assertFalse(justification_sufficient(justifications, 0.9))

    def test_large_deviation_threshold_is_named_constant(self):
        self.assertEqual(LARGE_DEVIATION_RATIO, 0.5)


class GapClassificationTests(unittest.TestCase):
    def test_single_cause_maps_directly(self):
        self.assertEqual(classify_gap(["dose_contribution"]), "DOSE_DRIVEN")
        self.assertEqual(classify_gap(["resistance_or_rep_contribution"]), "INTENSITY_DRIVEN")
        self.assertEqual(classify_gap(["exercise_count_contribution"]), "COMPOSITION_DRIVEN")
        self.assertEqual(classify_gap(["movement_mix_or_semantics_contribution"]), "SEMANTICS_DRIVEN")

    def test_multiple_causes_are_mixed_never_forced_to_one(self):
        self.assertEqual(classify_gap(["dose_contribution", "resistance_or_rep_contribution"]), "MIXED")

    def test_no_evidence_is_insufficient(self):
        self.assertEqual(classify_gap(["insufficient_comparable_evidence"]), "INSUFFICIENT_EVIDENCE")

    def test_within_range_is_none(self):
        self.assertEqual(classify_gap(["none_below_threshold"]), "NONE")


class Sep12V3FixtureTests(unittest.TestCase):
    def test_systemic_and_local_readiness_structurally_ruled_out(self):
        result = sep12_legacy_fixture_v3_analysis()
        self.assertIn("systemic_readiness", result["justifications_structurally_ruled_out"])
        self.assertIn("local_readiness", result["justifications_structurally_ruled_out"])

    def test_goal_posture_and_structure_left_unresolved_not_fabricated(self):
        result = sep12_legacy_fixture_v3_analysis()
        self.assertIn("reduced_goal_posture", result["justifications_unresolved_not_fabricated"])
        self.assertIn("session_structure", result["justifications_unresolved_not_fabricated"])

    def test_classification_and_verdict_not_forced_to_pass(self):
        result = sep12_legacy_fixture_v3_analysis()
        self.assertEqual(result["classification"], "UNREPRODUCIBLE_LEGACY_STATE")
        self.assertEqual(result["quality_verdict"], "QUESTIONABLE")


if __name__ == "__main__":
    unittest.main()
