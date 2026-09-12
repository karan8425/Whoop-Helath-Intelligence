"""Goal Policy Architecture tests.

Covers both the standalone registry (training_intelligence.dose.goal_policy)
and its integration into build_shadow_dose - specifically the central
requirement that changing goal mode changes POLICY output (framing) while
the personalized dose model itself remains fully goal-agnostic: the same
historical/readiness state must produce byte-identical numeric dose output
(working_sets, muscle budgets, WHOOP multiplier, everything under
recommended_dose/local_readiness/systemic_capacity) regardless of which of
the six goal modes is supplied - only `goal_context` may differ.
"""

import unittest
from datetime import datetime, timezone
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


def _session(label, days_ago, workout_type="Upper Pull", set_count=9, movement_count=4,
             total_reps=80, total_volume=5080.0, duration_seconds=2400):
    from datetime import timedelta
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


def _patched_readiness_and_muscles():
    muscles = [
        {"muscle": "Back", "readiness_state": "FRESH", "readiness_score": 90.0,
         "hours_since_primary_exposure": 200.0},
        {"muscle": "Biceps", "readiness_state": "FRESH", "readiness_score": 90.0,
         "hours_since_primary_exposure": 200.0},
    ]
    return (
        patch(
            "training_intelligence.dose.shadow_dose._latest_readiness",
            return_value={"recovery_score": 70.0, "readiness_band": "good"},
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
            "training_objective", "stimulus_posture", "volume_posture",
            "progression_emphasis", "fatigue_tolerance",
            "intensity_volume_tradeoff", "maintenance_vs_growth_priority",
        )
        for mode, policy in GOAL_POLICIES.items():
            for field in required_fields:
                self.assertIn(field, policy, f"{mode} missing {field}")
                self.assertIsNotNone(policy[field], f"{mode}.{field} is None")

    def test_aliases_resolve_to_canonical_modes(self):
        self.assertEqual(normalize_goal_mode("hypertrophy_gain"), LEAN_BULK)
        self.assertEqual(normalize_goal_mode("return_to_training"), RECOVERY)
        self.assertEqual(normalize_goal_mode(LEAN_CUT), LEAN_CUT)

    def test_unknown_mode_normalizes_to_none_never_guessed(self):
        self.assertIsNone(normalize_goal_mode("made_up_mode"))

    def test_get_goal_policy_unresolved_mode_is_explicit_not_a_default(self):
        policy = get_goal_policy(None)
        self.assertIsNone(policy["training_objective"])
        policy_unknown = get_goal_policy("not_a_real_mode")
        self.assertIsNone(policy_unknown["training_objective"])

    def test_resolve_goal_mode_prefers_goal_type_over_phase(self):
        # goal_type is the more specific/authoritative field.
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
        """These two modes are not reachable from any real goal_type in
        the live health_goal_profiles schema today - only via explicit
        override. This test documents (and would catch a silent
        regression of) that gap, not a bug."""
        from training_intelligence.dose.goal_policy import _GOAL_TYPE_TO_MODE
        self.assertNotIn(STRENGTH, _GOAL_TYPE_TO_MODE.values())
        self.assertNotIn(RECOVERY, _GOAL_TYPE_TO_MODE.values())


class GoalModeDoseAgnosticismTests(unittest.TestCase):
    """The central requirement: changing goal mode changes policy output,
    never the numeric dose, for the same historical/readiness state."""

    def test_goal_mode_does_not_change_the_numeric_dose(self):
        sessions = [_session(f"s{i}", days_ago=3 + i * 6) for i in range(4)]
        results = {}
        readiness_ctx = _patched_readiness_and_muscles()
        with readiness_ctx[0], readiness_ctx[1], patch(
            "training_intelligence.dose.shadow_dose.get_active_goal", return_value={}
        ):
            for mode in GOAL_MODES:
                results[mode] = build_shadow_dose(
                    AS_OF, "Upper Pull", sessions=list(sessions), muscle_rows=[], ledger_rows=[],
                    goal_mode_override=mode,
                )

        numeric_keys = ("recommended_dose", "local_readiness", "systemic_capacity",
                        "dose_classification", "historical_dose_reference", "performance_state")
        reference = results[LEAN_CUT]
        for mode in GOAL_MODES:
            for key in numeric_keys:
                self.assertEqual(
                    results[mode][key], reference[key],
                    f"goal mode {mode} changed {key} - the dose model must remain goal-agnostic",
                )

    def test_goal_mode_does_change_the_policy_framing(self):
        sessions = [_session(f"s{i}", days_ago=3 + i * 6) for i in range(4)]
        readiness_ctx = _patched_readiness_and_muscles()
        with readiness_ctx[0], readiness_ctx[1], patch(
            "training_intelligence.dose.shadow_dose.get_active_goal", return_value={}
        ):
            lean_cut = build_shadow_dose(
                AS_OF, "Upper Pull", sessions=list(sessions), muscle_rows=[], ledger_rows=[],
                goal_mode_override=LEAN_CUT,
            )
            lean_bulk = build_shadow_dose(
                AS_OF, "Upper Pull", sessions=list(sessions), muscle_rows=[], ledger_rows=[],
                goal_mode_override=LEAN_BULK,
            )
        self.assertNotEqual(
            lean_cut["goal_context"]["policy"], lean_bulk["goal_context"]["policy"]
        )
        self.assertEqual(lean_cut["goal_context"]["policy"]["maintenance_vs_growth_priority"], "maintenance")
        self.assertEqual(lean_bulk["goal_context"]["policy"]["maintenance_vs_growth_priority"], "growth")

    def test_lean_cut_lean_bulk_strength_maintenance_all_produce_valid_distinct_policies(self):
        """Required by the assignment: at minimum lean_cut, lean_bulk,
        strength, and maintenance, same user/state, each a valid and
        distinct policy output."""
        sessions = [_session(f"s{i}", days_ago=3 + i * 6) for i in range(4)]
        readiness_ctx = _patched_readiness_and_muscles()
        seen_policies = []
        with readiness_ctx[0], readiness_ctx[1], patch(
            "training_intelligence.dose.shadow_dose.get_active_goal", return_value={}
        ):
            for mode in (LEAN_CUT, LEAN_BULK, STRENGTH, MAINTENANCE):
                result = build_shadow_dose(
                    AS_OF, "Upper Pull", sessions=list(sessions), muscle_rows=[], ledger_rows=[],
                    goal_mode_override=mode,
                )
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["goal_context"]["goal_mode"], mode)
                self.assertIsNotNone(result["goal_context"]["training_objective"])
                seen_policies.append(result["goal_context"]["policy"])
        # No two of the four required modes share an identical policy dict.
        for i in range(len(seen_policies)):
            for j in range(i + 1, len(seen_policies)):
                self.assertNotEqual(seen_policies[i], seen_policies[j])

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
        sessions = [_session(f"s{i}", days_ago=3 + i * 6) for i in range(4)]
        readiness_ctx = _patched_readiness_and_muscles()
        with readiness_ctx[0], readiness_ctx[1], patch(
            "training_intelligence.dose.shadow_dose.get_active_goal", return_value={}
        ):
            first = build_shadow_dose(
                AS_OF, "Upper Pull", sessions=list(sessions), muscle_rows=[], ledger_rows=[],
                goal_mode_override=STRENGTH,
            )
            second = build_shadow_dose(
                AS_OF, "Upper Pull", sessions=list(sessions), muscle_rows=[], ledger_rows=[],
                goal_mode_override=STRENGTH,
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
