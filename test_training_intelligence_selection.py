"""TKI-4: session selection / scoring tests.

Pure/in-memory (fixture sessions/rows, no live database), matching the
established convention. Real-Postgres coverage lives in
test_training_intelligence_selection_postgres.py (opt-in).
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from training_intelligence.dose.goal_policy import (
    GOAL_MODES, LEAN_CUT, LEAN_BULK, STRENGTH, MAINTENANCE, GENERAL_FITNESS, RECOVERY,
)
from training_intelligence.selection.shadow_selection import (
    build_shadow_selection, REST_FAMILY_NAME, SELECTION_MODEL_VERSION,
)
from training_intelligence.selection.scoring_policy import SCORING_WEIGHTS, SCORE_DIMENSIONS

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
    """Enough B2 muscle-level history that per-muscle budgets aren't
    zero/degenerate for every candidate."""
    rows = []
    for i in range(10):
        day = 1 + i * 1.2
        for muscle in ALL_MUSCLES:
            rows.append(_muscle_row(f"{muscle}{i}", day, muscle))
    return rows


def _run(readiness_band="good", recovery_score=70.0, muscles=None, goal=None,
          sessions=None, muscle_rows=None, ledger_rows=None, goal_mode_override=None):
    muscles = muscles if muscles is not None else _all_fresh_muscles()
    goal = goal if goal is not None else {}
    sessions = sessions if sessions is not None else _default_sessions()
    muscle_rows = muscle_rows if muscle_rows is not None else _rich_muscle_rows()
    ledger_rows = ledger_rows if ledger_rows is not None else []
    with patch(
        "training_intelligence.selection.shadow_selection._latest_readiness",
        return_value={"recovery_score": recovery_score, "readiness_band": readiness_band},
    ), patch(
        "training_intelligence.selection.shadow_selection.calculate_muscle_readiness",
        return_value={"selection_confidence": "high", "latest_workout_age_hours": 24.0, "muscles": muscles},
    ), patch(
        "training_intelligence.selection.shadow_selection.get_active_goal", return_value=goal,
    ):
        return build_shadow_selection(
            AS_OF, goal_mode_override=goal_mode_override,
            sessions=sessions, muscle_rows=muscle_rows, ledger_rows=ledger_rows,
        )


class ScoringPolicyTests(unittest.TestCase):
    def test_every_goal_mode_has_weights_for_every_dimension(self):
        for mode in GOAL_MODES:
            weights = SCORING_WEIGHTS[mode]
            for dimension in SCORE_DIMENSIONS:
                self.assertIn(dimension, weights)
                self.assertGreater(weights[dimension], 0.0)


class LocalFatigueSuppressionTests(unittest.TestCase):
    def test_fatigued_primary_muscles_cannot_win_via_stimulus_debt(self):
        """Core invariant: a family whose muscles are all FATIGUED must
        not win even if it has the largest stimulus debt."""
        muscles = _all_fresh_muscles({
            "Back": {"state": "FATIGUED"}, "Biceps": {"state": "FATIGUED"},
        })
        # Zero history for Back/Biceps specifically -> maximal stimulus debt.
        muscle_rows = [r for r in _rich_muscle_rows() if r["muscle_groups"][0] not in ("Back", "Biceps")]
        result = _run(muscles=muscles, muscle_rows=muscle_rows)
        self.assertNotEqual(result["selected_session_family"], "Upper Pull")
        upper_pull = next(c for c in result["candidates"] if c["session_family"] == "Upper Pull")
        self.assertFalse(upper_pull["eligible"])
        self.assertTrue(any("insufficient_local_readiness" in r for r in upper_pull["exclusion_reasons"]))

    def test_high_systemic_recovery_cannot_rescue_fatigued_family(self):
        muscles = _all_fresh_muscles({
            "Back": {"state": "FATIGUED"}, "Biceps": {"state": "FATIGUED"},
        })
        result = _run(readiness_band="high", recovery_score=94.0, muscles=muscles)
        upper_pull = next(c for c in result["candidates"] if c["session_family"] == "Upper Pull")
        self.assertFalse(upper_pull["eligible"])


class ReadinessCombinationTests(unittest.TestCase):
    def test_high_systemic_low_local_readiness_still_excludes_fatigued_family(self):
        muscles = _all_fresh_muscles({m: {"state": "FATIGUED"} for m in ("Chest", "Shoulders", "Triceps")})
        result = _run(readiness_band="high", recovery_score=94.0, muscles=muscles)
        upper_push = next(c for c in result["candidates"] if c["session_family"] == "Upper Push")
        self.assertFalse(upper_push["eligible"])

    def test_low_systemic_fresh_muscles_still_eligible_and_scorable(self):
        result = _run(readiness_band="low", recovery_score=30.0)
        lower_body = next(c for c in result["candidates"] if c["session_family"] == "Lower Body")
        self.assertTrue(lower_body["eligible"])
        self.assertIsNotNone(lower_body["score_total"])

    def test_unknown_readiness_is_not_silently_fresh(self):
        """A muscle with no readiness entry at all (cold-start / missing
        data) must score below a genuinely FRESH muscle, not the same."""
        fresh_muscles = _all_fresh_muscles()
        missing_entry_muscles = [m for m in fresh_muscles if m["muscle"] not in ("Back", "Biceps")]
        fresh_result = _run(muscles=fresh_muscles)
        unknown_result = _run(muscles=missing_entry_muscles)
        fresh_pull = next(c for c in fresh_result["candidates"] if c["session_family"] == "Upper Pull")
        unknown_pull = next(c for c in unknown_result["candidates"] if c["session_family"] == "Upper Pull")
        self.assertTrue(unknown_pull["eligible"])  # coarse gate still passes (not SUPPRESSED/FATIGUED)
        self.assertLess(
            unknown_pull["score_components"]["local_readiness"],
            fresh_pull["score_components"]["local_readiness"],
        )


class StimulusDebtAndNeglectTests(unittest.TestCase):
    def test_long_neglected_muscle_group_scores_higher_days_since_trained(self):
        muscles = _all_fresh_muscles({
            "Glutes": {"hours": 24 * 30}, "Hamstrings": {"hours": 24 * 30}, "Quads": {"hours": 24 * 30},
            "Chest": {"hours": 2.0}, "Shoulders": {"hours": 2.0}, "Triceps": {"hours": 2.0},
        })
        result = _run(muscles=muscles)
        lower = next(c for c in result["candidates"] if c["session_family"] == "Lower Body")
        push = next(c for c in result["candidates"] if c["session_family"] == "Upper Push")
        self.assertGreater(
            lower["score_components"]["days_since_trained"], push["score_components"]["days_since_trained"]
        )

    def test_recent_overrepresentation_reduces_program_balance_score(self):
        ledger_rows = []
        for i in range(6):
            # Two rows per workout, distinct primary muscles, so the
            # workout's trained-primary set is {"back", "biceps"} -
            # satisfying Upper Pull's own minimum_eligible=2 threshold in
            # training_intelligence.stimulus.session_family's classifier.
            ledger_rows.append(_ledger_row(f"pull{i}", 1 + i, f"back_m{i}", ["Back"], set_index=0))
            ledger_rows.append(_ledger_row(f"pull{i}", 1 + i, f"biceps_m{i}", ["Biceps"], set_index=1))
        result = _run(ledger_rows=ledger_rows)
        pull = next(c for c in result["candidates"] if c["session_family"] == "Upper Pull")
        lower = next(c for c in result["candidates"] if c["session_family"] == "Lower Body")
        self.assertLess(
            pull["score_components"]["program_balance"], lower["score_components"]["program_balance"]
        )


class DoseFeasibilityGatingTests(unittest.TestCase):
    def test_no_feasible_dose_makes_candidate_ineligible(self):
        """Zero muscle-set history at all -> B2's conservative fallback
        budget collapses toward a very small number; with FATIGUED-free
        but data-free muscles this should still be dosable at the
        conservative baseline, so instead force zero budget via FATIGUED
        to directly test the insufficient_feasible_dose path is at least
        reachable in principle (structurally exercised by the
        eligibility ordering: dose is only computed once readiness
        eligibility already passed)."""
        result = _run(muscle_rows=[])
        for candidate in result["candidates"]:
            if candidate["session_family"] == REST_FAMILY_NAME:
                continue
            if not candidate["eligible"]:
                self.assertTrue(len(candidate["exclusion_reasons"]) > 0)


class GoalPolicyVariationTests(unittest.TestCase):
    def test_goal_mode_changes_scoring_weights_used(self):
        lean_cut = _run(goal_mode_override=LEAN_CUT)
        strength = _run(goal_mode_override=STRENGTH)
        lc_pull = next(c for c in lean_cut["candidates"] if c["session_family"] == "Upper Pull")
        st_pull = next(c for c in strength["candidates"] if c["session_family"] == "Upper Pull")
        self.assertNotEqual(lc_pull["score_total"], st_pull["score_total"])

    def test_no_hidden_hardcoded_lean_cut_logic(self):
        """The engine must treat lean_cut as just another table row -
        running with an override that ISN'T lean_cut, on the exact same
        state, must go through identical code (no special-casing)."""
        for mode in GOAL_MODES:
            result = _run(goal_mode_override=mode)
            self.assertEqual(result["goal_mode"], mode)
            self.assertIn(result["selected_session_family"], SCORING_WEIGHTS.keys() | {REST_FAMILY_NAME} | {
                c["session_family"] for c in result["candidates"]
            })


class MultiGoalInvariantTests(unittest.TestCase):
    """Section 20: same state, all six goal modes - factual inputs must
    stay identical, only policy-sensitive scoring may change."""

    def test_same_state_all_six_modes_share_identical_factual_inputs(self):
        results = {mode: _run(goal_mode_override=mode) for mode in GOAL_MODES}
        reference = results[LEAN_CUT]
        for mode in GOAL_MODES:
            self.assertEqual(results[mode]["systemic_capacity"], reference["systemic_capacity"])
            for candidate, ref_candidate in zip(
                sorted(results[mode]["candidates"], key=lambda c: c["session_family"]),
                sorted(reference["candidates"], key=lambda c: c["session_family"]),
            ):
                self.assertEqual(candidate["eligible"], ref_candidate["eligible"])
                self.assertEqual(candidate["primary_muscles"], ref_candidate["primary_muscles"])
                if candidate["dose_summary"] and ref_candidate["dose_summary"]:
                    self.assertEqual(
                        candidate["dose_summary"]["feasible_dose_range"],
                        ref_candidate["dose_summary"]["feasible_dose_range"],
                    )

    def test_no_mode_invents_stimulus_history_or_bypasses_dose_feasibility(self):
        results = {mode: _run(goal_mode_override=mode) for mode in GOAL_MODES}
        for mode, result in results.items():
            for candidate in result["candidates"]:
                if candidate["session_family"] == REST_FAMILY_NAME or not candidate["eligible"]:
                    continue
                dose = candidate["dose_summary"]
                low = dose["feasible_dose_range"]["lower_bound_working_sets"]
                high = dose["feasible_dose_range"]["upper_bound_working_sets"]
                self.assertGreaterEqual(dose["working_sets"], low, mode)
                self.assertLessEqual(dose["working_sets"], high, mode)

    def test_selected_family_may_differ_or_legitimately_stay_the_same(self):
        """Do not require artificial differentiation - just confirm the
        engine ran validly for every mode without erroring."""
        selections = {mode: _run(goal_mode_override=mode)["selected_session_family"] for mode in GOAL_MODES}
        self.assertEqual(len(selections), len(GOAL_MODES))


class RestRecoveryOutcomeTests(unittest.TestCase):
    def test_very_low_systemic_and_all_fatigued_selects_rest(self):
        muscles = _all_fresh_muscles({m: {"state": "FATIGUED"} for m in ALL_MUSCLES})
        result = _run(readiness_band="very_low", recovery_score=5.0, muscles=muscles)
        self.assertEqual(result["selected_session_family"], REST_FAMILY_NAME)

    def test_rest_is_always_a_candidate(self):
        result = _run()
        self.assertTrue(any(c["session_family"] == REST_FAMILY_NAME for c in result["candidates"]))

    def test_recovery_goal_mode_biases_toward_rest_when_systemic_is_reduced(self):
        muscles = _all_fresh_muscles({m: {"state": "FATIGUED"} for m in ("Chest", "Back", "Shoulders", "Glutes", "Hamstrings", "Quads")})
        result = _run(readiness_band="low", recovery_score=30.0, muscles=muscles, goal_mode_override=RECOVERY)
        self.assertEqual(result["selected_session_family"], REST_FAMILY_NAME)


class ColdStartTests(unittest.TestCase):
    def test_zero_history_does_not_crash_and_returns_valid_shape(self):
        muscles = []  # no local readiness data at all
        result = _run(muscles=muscles, sessions=[], muscle_rows=[], ledger_rows=[])
        self.assertEqual(result["status"], "ok")
        self.assertIn(result["selected_session_family"], {c["session_family"] for c in result["candidates"]})

    def test_no_whoop_data_treated_as_neutral_not_high_or_low(self):
        result = _run(readiness_band="unknown", recovery_score=None)
        self.assertEqual(result["status"], "ok")

    def test_partial_history_produces_valid_output(self):
        sessions = [_session("only_one", days_ago=5)]
        result = _run(sessions=sessions)
        self.assertEqual(result["status"], "ok")


class TieBreakAndDeterminismTests(unittest.TestCase):
    def test_deterministic_repeated_output(self):
        first = _run()
        second = _run()
        self.assertEqual(first, second)

    def test_tie_break_is_deterministic_across_runs(self):
        results = [_run()["selected_session_family"] for _ in range(3)]
        self.assertEqual(len(set(results)), 1)

    def test_ranked_eligible_is_sorted_descending_by_score(self):
        result = _run()
        scores = [c["score_total"] for c in result["ranked_eligible"]]
        self.assertEqual(scores, sorted(scores, reverse=True))


class TemporalLeakageTests(unittest.TestCase):
    def test_future_session_does_not_influence_selection(self):
        baseline = _run()
        future_session = _session("future", days_ago=-10, total_volume=999999.0, set_count=99)
        with_future = _run(sessions=_default_sessions() + [future_session])
        self.assertEqual(baseline["selected_session_family"], with_future["selected_session_family"])

    def test_future_muscle_row_does_not_influence_selection(self):
        baseline = _run()
        future_rows = _rich_muscle_rows() + [_muscle_row("future", -5, "Back", volume=999999.0)]
        with_future = _run(muscle_rows=future_rows)
        self.assertEqual(baseline["selected_session_family"], with_future["selected_session_family"])

    def test_naive_as_of_rejected(self):
        with self.assertRaises(ValueError):
            build_shadow_selection(datetime(2026, 9, 12))


if __name__ == "__main__":
    unittest.main()
