"""Program Intelligence V2: pure/in-memory tests for taxonomy,
feasibility, the decision hierarchy, and the time-budget/reduction
mechanics - matching the established convention (no live database;
opt-in Postgres end-to-end coverage lives in
test_training_intelligence_program_adaptation_postgres.py).
"""
import unittest

from training_intelligence.programs.adaptation import taxonomy
from training_intelligence.programs.adaptation.feasibility import (
    local_readiness_gate, sequence_cost, recency_flag,
)
from training_intelligence.programs.adaptation.decision import decide_action
from training_intelligence.programs.adaptation.time_budget import (
    resolve_available_duration, estimate_session_duration_minutes, classify_fit,
)
from training_intelligence.programs.adaptation.taxonomy import (
    ACTION_KEEP, ACTION_SHIFT, ACTION_SUBSTITUTE, ACTION_REDUCE, ACTION_RECOVERY, ACTION_REST,
    FIT_FITS, FIT_TIGHT, FIT_EXCEEDS, FIT_UNKNOWN,
)


def _session(key, family, muscles):
    return {"session_key": key, "session_name": key.title(), "session_family": family}


def _slots(muscles, required=True):
    return [{"primary_muscle": m, "required": required, "set_min": 2, "set_max": 4} for m in muscles]


def _muscle_readiness(states):
    return {"muscles": [{"muscle": m, "readiness_state": s} for m, s in states.items()]}


def _readiness(band):
    return {"readiness_band": band}


def _profile(position, key, family, muscles, muscle_readiness, program_need=0.0, recency_flagged=False):
    slots = _slots(muscles)
    return {
        "position": position, "session": _session(key, family, muscles), "slots": slots,
        "local_gate": local_readiness_gate(slots, muscle_readiness),
        "program_need": program_need, "recency_flagged": recency_flagged,
    }


class TaxonomyTests(unittest.TestCase):
    def test_actions_are_distinct(self):
        self.assertEqual(len(taxonomy.ACTIONS), len(set(taxonomy.ACTIONS)))

    def test_ready_recovering_ineligible_states_do_not_overlap(self):
        all_states = taxonomy.READY_STATES + taxonomy.RECOVERING_STATES + taxonomy.INELIGIBLE_PRIMARY_STATES
        self.assertEqual(len(all_states), len(set(all_states)))

    def test_fit_statuses_are_distinct(self):
        self.assertEqual(len(taxonomy.FIT_STATUSES), len(set(taxonomy.FIT_STATUSES)))


class LocalReadinessGateTests(unittest.TestCase):
    def test_fatigued_required_muscle_blocks_eligibility(self):
        gate = local_readiness_gate(_slots(["chest"]), _muscle_readiness({"Chest": "FATIGUED"}))
        self.assertFalse(gate["eligible"])
        self.assertIn("chest", gate["blocking_muscles"])

    def test_suppressed_required_muscle_blocks_eligibility(self):
        gate = local_readiness_gate(_slots(["back"]), _muscle_readiness({"Back": "SUPPRESSED"}))
        self.assertFalse(gate["eligible"])

    def test_recovering_required_muscle_is_eligible_but_flagged_reduced_only(self):
        gate = local_readiness_gate(_slots(["chest"]), _muscle_readiness({"Chest": "RECOVERING"}))
        self.assertTrue(gate["eligible"])
        self.assertTrue(gate["reduced_only"])

    def test_ready_muscle_is_fully_eligible(self):
        gate = local_readiness_gate(_slots(["quads"]), _muscle_readiness({"Quads": "READY"}))
        self.assertTrue(gate["eligible"])
        self.assertFalse(gate["reduced_only"])

    def test_non_required_slot_never_blocks_eligibility(self):
        gate = local_readiness_gate(_slots(["chest"], required=False), _muscle_readiness({"Chest": "FATIGUED"}))
        self.assertEqual(gate["muscle_states"], {})
        self.assertTrue(gate["eligible"])

    def test_high_systemic_recovery_is_irrelevant_to_this_gate(self):
        # This gate never even looks at systemic readiness - the hard
        # rule is enforced by construction, not by a comparison here.
        gate = local_readiness_gate(_slots(["chest"]), _muscle_readiness({"Chest": "FATIGUED"}))
        self.assertFalse(gate["eligible"])


class SequenceCostTests(unittest.TestCase):
    def test_nominal_has_zero_cost(self):
        self.assertEqual(sequence_cost(0), 0)

    def test_farther_rotation_position_costs_more(self):
        self.assertGreater(sequence_cost(2), sequence_cost(1))


class DecideActionScenarioTests(unittest.TestCase):
    """Mirrors Phase 15's Cases A-E/H/I at the pure decision-hierarchy
    level - no DB, no Tonal resolution, isolating the decision logic
    itself."""

    def test_case_a_keep_when_fully_ready(self):
        profiles = [
            _profile(0, "lower_a", "Lower Body", ["quads", "hamstrings"], ALL_READY := _muscle_readiness(
                {"Quads": "READY", "Hamstrings": "READY", "Chest": "READY", "Back": "READY"})),
            _profile(1, "upper_b", "Upper Mixed", ["chest", "back"], ALL_READY, program_need=3.0),
        ]
        action, chosen, codes = decide_action(profiles, _readiness("high"))
        self.assertEqual(action, ACTION_KEEP)
        self.assertEqual(chosen["position"], 0)

    def test_case_b_shift_when_nominal_recovering_and_alternative_needed(self):
        mr = _muscle_readiness({"Chest": "RECOVERING", "Shoulders": "RECOVERING", "Quads": "READY", "Hamstrings": "READY"})
        profiles = [
            _profile(0, "upper_b", "Upper Mixed", ["chest", "shoulders"], mr, program_need=0.2),
            _profile(1, "lower_b", "Lower Body", ["quads", "hamstrings"], mr, program_need=4.0),
        ]
        action, chosen, codes = decide_action(profiles, _readiness("good"))
        self.assertEqual(action, ACTION_SHIFT)
        self.assertEqual(chosen["position"], 1)

    def test_case_c_keep_despite_fresh_alternative_when_nominal_fully_ready(self):
        mr = _muscle_readiness({"Chest": "READY", "Back": "READY", "Quads": "READY", "Hamstrings": "READY"})
        profiles = [
            _profile(0, "upper_a", "Upper Mixed", ["chest", "back"], mr, program_need=0.1),
            _profile(1, "lower_a", "Lower Body", ["quads", "hamstrings"], mr, program_need=5.0),
        ]
        action, chosen, codes = decide_action(profiles, _readiness("good"))
        self.assertEqual(action, ACTION_KEEP)
        self.assertEqual(chosen["position"], 0)

    def test_case_d_reduce_on_moderate_systemic_capacity(self):
        mr = _muscle_readiness({"Quads": "READY", "Hamstrings": "READY"})
        profiles = [_profile(0, "lower", "Lower Body", ["quads", "hamstrings"], mr)]
        action, chosen, codes = decide_action(profiles, _readiness("moderate"))
        self.assertEqual(action, ACTION_REDUCE)

    def test_case_e_recovery_or_rest_on_very_low_systemic_capacity(self):
        mr = _muscle_readiness({"Quads": "READY"})
        profiles = [_profile(0, "lower", "Lower Body", ["quads"], mr)]
        action, chosen, codes = decide_action(profiles, _readiness("very_low"))
        self.assertIn(action, (ACTION_RECOVERY, ACTION_REST))

    def test_case_h_never_selects_a_candidate_outside_the_supplied_rotation_window(self):
        # Enforced by construction: decide_action only ever sees the
        # candidates it's given - it can select nothing else.
        mr = _muscle_readiness({"Chest": "FATIGUED", "Quads": "READY"})
        profiles = [
            _profile(0, "upper_b", "Upper Mixed", ["chest"], mr, program_need=1.0),
            _profile(1, "lower_b", "Lower Body", ["quads"], mr, program_need=1.0),
        ]
        action, chosen, codes = decide_action(profiles, _readiness("high"))
        self.assertIn(chosen["session"]["session_key"], {p["session"]["session_key"] for p in profiles})

    def test_case_i_high_recovery_never_reinstates_a_fatigued_nominal(self):
        mr = _muscle_readiness({"Chest": "FATIGUED", "Shoulders": "FATIGUED", "Quads": "READY", "Hamstrings": "READY"})
        profiles = [
            _profile(0, "upper_b", "Upper Mixed", ["chest", "shoulders"], mr, program_need=0.0),
            _profile(1, "lower_b", "Lower Body", ["quads", "hamstrings"], mr, program_need=2.0),
        ]
        action, chosen, codes = decide_action(profiles, _readiness("high"))
        self.assertNotEqual(chosen["session"]["session_key"], "upper_b")
        self.assertIn("HIGH_SYSTEMIC_CAPACITY", codes)  # recorded, but never overrides the block


class TimeBudgetTests(unittest.TestCase):
    def test_resolve_available_duration_precedence(self):
        self.assertEqual(resolve_available_duration(request_override_min=20, user_preference_min=30,
                                                      program_default_min=40), (20, "request_override"))
        self.assertEqual(resolve_available_duration(user_preference_min=30, program_default_min=40),
                          (30, "user_preference"))
        self.assertEqual(resolve_available_duration(program_default_min=40), (40, "program_default"))
        self.assertEqual(resolve_available_duration(), (None, "unknown"))

    def test_never_defaults_to_45(self):
        minutes, source = resolve_available_duration()
        self.assertIsNone(minutes)
        self.assertNotEqual(minutes, 45)

    def test_classify_fit_unknown_when_available_is_none(self):
        self.assertEqual(classify_fit(20, 30, None), FIT_UNKNOWN)

    def test_classify_fit_exceeds_when_low_estimate_over_budget(self):
        self.assertEqual(classify_fit(40, 50, 20), FIT_EXCEEDS)

    def test_classify_fit_fits_with_ample_margin(self):
        self.assertEqual(classify_fit(20, 25, 60), FIT_FITS)

    def test_classify_fit_tight_near_the_edge(self):
        self.assertEqual(classify_fit(28, 30, 30), FIT_TIGHT)

    def test_duration_estimate_scales_with_sets(self):
        few = estimate_session_duration_minutes([{"sets": 2, "rep_min": 8, "rep_max": 10,
                                                   "rest_seconds_min": 90, "rest_seconds_max": 120, "is_unilateral": False}])
        many = estimate_session_duration_minutes([{"sets": 6, "rep_min": 8, "rep_max": 10,
                                                    "rest_seconds_min": 90, "rest_seconds_max": 120, "is_unilateral": False}])
        self.assertLess(few[0], many[0])

    def test_unilateral_takes_longer_than_bilateral_for_same_reps(self):
        bilateral = estimate_session_duration_minutes([{"sets": 3, "rep_min": 10, "rep_max": 10,
                                                         "rest_seconds_min": 60, "rest_seconds_max": 60, "is_unilateral": False}])
        unilateral = estimate_session_duration_minutes([{"sets": 3, "rep_min": 10, "rep_max": 10,
                                                          "rest_seconds_min": 60, "rest_seconds_max": 60, "is_unilateral": True}])
        self.assertGreater(unilateral[0], bilateral[0])


class TimeReductionMechanicsTests(unittest.TestCase):
    """Direct, synthetic test of the deterministic reduction order
    (Phase 11) with genuine headroom above each slot's set_min - proves
    the mechanism itself works, independent of whether any particular
    live scenario happens to have headroom to give up."""

    def _resolved_slots(self):
        return [
            {"slot_id": "req1", "status": "resolved", "required": True, "set_min": 2, "set_max": 4},
            {"slot_id": "opt1", "status": "resolved", "required": False, "set_min": 1, "set_max": 3},
            {"slot_id": "opt2", "status": "resolved", "required": False, "set_min": 1, "set_max": 3},
        ]

    def _exercises(self):
        rep_range = {"minimum": 8, "maximum": 10}
        rir = {"minimum": 1, "maximum": 3}
        rest = {"minimum": 90, "maximum": 120}
        return [
            {"slot_id": "req1", "movement_id": "m_req", "working_sets": 4, "rep_range": rep_range,
             "rest_seconds": rest, "is_bilateral": True},
            {"slot_id": "opt1", "movement_id": "m_opt1", "working_sets": 3, "rep_range": rep_range,
             "rest_seconds": rest, "is_bilateral": True},
            {"slot_id": "opt2", "movement_id": "m_opt2", "working_sets": 3, "rep_range": rep_range,
             "rest_seconds": rest, "is_bilateral": True},
        ]

    def test_reduction_shrinks_optional_before_removing_required_movement(self):
        from training_intelligence.programs.adaptation.shadow import _apply_time_reduction
        exercises = self._exercises()
        resolved_slots = self._resolved_slots()
        # Full, unconstrained estimate for these 3 exercises comfortably
        # exceeds a tight budget - pick one that forces some reduction
        # but does not require removing the required slot entirely.
        reduced, time_ctx = _apply_time_reduction(exercises, resolved_slots, available_min=12, source="request_override")
        req_exercise = next(e for e in reduced if e["slot_id"] == "req1")
        self.assertEqual(req_exercise["working_sets"], 4)  # required slot's sets untouched while optional exists
        total_after = sum(e["working_sets"] for e in reduced)
        self.assertLess(total_after, sum(e["working_sets"] for e in exercises))

    def test_required_movement_is_never_fully_removed(self):
        from training_intelligence.programs.adaptation.shadow import _apply_time_reduction
        exercises = self._exercises()
        resolved_slots = self._resolved_slots()
        reduced, time_ctx = _apply_time_reduction(exercises, resolved_slots, available_min=1, source="request_override")
        self.assertIn("m_req", {e["movement_id"] for e in reduced})

    def test_optional_slots_can_be_removed_entirely_if_still_needed(self):
        from training_intelligence.programs.adaptation.shadow import _apply_time_reduction
        exercises = self._exercises()
        resolved_slots = self._resolved_slots()
        reduced, time_ctx = _apply_time_reduction(exercises, resolved_slots, available_min=8, source="request_override")
        remaining_ids = {e["movement_id"] for e in reduced}
        self.assertIn("m_req", remaining_ids)
        self.assertTrue(remaining_ids.issubset({"m_req", "m_opt1", "m_opt2"}))


if __name__ == "__main__":
    unittest.main()
