"""TKI-4.1: monotony / rolling-representation / starvation-protection
calibration tests.

Pure/in-memory (fixture sessions/rows, no live database), matching the
established convention. Real-Postgres coverage for the temporal-
correctness claims lives in
test_training_intelligence_calibration_postgres.py (opt-in).

Two layers are tested here:
  - training_intelligence.selection.monotony's pure functions directly
    (unit-level: exact penalty/adjustment values for known inputs).
  - build_shadow_selection() end-to-end (integration-level: the
    calibration layer actually changes ranking/selection when and only
    when it should, and never breaks an existing TKI-4 invariant).
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from training_intelligence.dose.goal_policy import (
    LEAN_CUT, LEAN_BULK, STRENGTH, MAINTENANCE, GENERAL_FITNESS, RECOVERY,
)
from training_intelligence.selection.calibration_policy import (
    CONSECUTIVE_REPEAT_FREE_STREAK,
    CONSECUTIVE_REPEAT_PENALTY_PER_DAY,
    CONSECUTIVE_REPEAT_PENALTY_CAP,
    MONOTONY_TOLERANCE_MULTIPLIER,
    TOTAL_CALIBRATION_ADJUSTMENT_CAP,
)
from training_intelligence.selection.monotony import (
    consecutive_repeat_streak,
    repeat_penalty,
    rolling_representation_adjustment,
    starvation_bonus,
    compute_monotony_adjustment,
)
from training_intelligence.selection.shadow_selection import (
    build_shadow_selection, REST_FAMILY_NAME,
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


def _family_ledger_rows(family_movements, n_per_day=1, start_days_ago=1, prefix="w"):
    """Real ledger rows classifying as a specific family for N consecutive
    real days - used to build rolling-representation/starvation fixtures.
    All rows for a given day share ONE activity_id (one workout) with
    distinct movement_ids/set_index per row, so session_family.py's
    classifier sees the intended multi-muscle workout (matching the
    established fixture pattern - see test_training_intelligence_selection.py's
    test_recent_overrepresentation_reduces_program_balance_score)."""
    rows = []
    for day_offset in range(n_per_day):
        days_ago = start_days_ago + day_offset
        activity_id = f"{prefix}_{day_offset}"
        for idx, (movement_id, muscles) in enumerate(family_movements):
            rows.append(_ledger_row(
                activity_id, days_ago, f"{movement_id}_{day_offset}", muscles, set_index=idx,
            ))
    return rows


def _run(readiness_band="good", recovery_score=70.0, muscles=None, goal=None,
         sessions=None, muscle_rows=None, ledger_rows=None, goal_mode_override=None,
         recent_selections=None):
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
            recent_selections=recent_selections,
        )


# ----------------------------------------------------------------------
# Unit-level: monotony.py's pure functions.
# ----------------------------------------------------------------------

class ConsecutiveRepeatStreakTests(unittest.TestCase):
    def test_no_history_is_zero_streak(self):
        self.assertEqual(consecutive_repeat_streak("Lower Body", []), 0)

    def test_single_match_is_streak_one(self):
        history = [(AS_OF - timedelta(days=1), "Lower Body")]
        self.assertEqual(consecutive_repeat_streak("Lower Body", history), 1)

    def test_streak_breaks_on_first_mismatch(self):
        history = [
            (AS_OF - timedelta(days=1), "Lower Body"),
            (AS_OF - timedelta(days=2), "Lower Body"),
            (AS_OF - timedelta(days=3), "Upper Push"),
            (AS_OF - timedelta(days=4), "Lower Body"),
        ]
        self.assertEqual(consecutive_repeat_streak("Lower Body", history), 2)

    def test_rest_day_breaks_a_lifting_streak(self):
        history = [
            (AS_OF - timedelta(days=1), REST_FAMILY_NAME),
            (AS_OF - timedelta(days=2), "Lower Body"),
            (AS_OF - timedelta(days=3), "Lower Body"),
        ]
        self.assertEqual(consecutive_repeat_streak("Lower Body", history), 0)


class RepeatPenaltyTests(unittest.TestCase):
    def test_first_repeat_allowed_no_or_tiny_penalty(self):
        """Section 20: 'first repeat allowed' - selecting a family again
        immediately after a single prior selection costs nothing, since
        CONSECUTIVE_REPEAT_FREE_STREAK covers exactly one repeat."""
        history = [(AS_OF - timedelta(days=1), "Lower Body")]
        penalty, streak = repeat_penalty("Lower Body", history, LEAN_CUT)
        self.assertEqual(streak, 1)
        self.assertEqual(penalty, 0.0)

    def test_penalty_increases_progressively_with_streak_length(self):
        """Section 20: 'repeated selections progressively dampened'."""
        penalties = []
        for streak_len in range(1, 6):
            history = [(AS_OF - timedelta(days=d), "Lower Body") for d in range(1, streak_len + 1)]
            penalty, _ = repeat_penalty("Lower Body", history, LEAN_CUT)
            penalties.append(penalty)
        for earlier, later in zip(penalties, penalties[1:]):
            self.assertLessEqual(earlier, later)
        self.assertLess(penalties[0], penalties[-1])

    def test_penalty_is_capped(self):
        history = [(AS_OF - timedelta(days=d), "Lower Body") for d in range(1, 40)]
        penalty, _ = repeat_penalty("Lower Body", history, LEAN_CUT)
        self.assertLessEqual(penalty, CONSECUTIVE_REPEAT_PENALTY_CAP)

    def test_goal_mode_changes_tolerance_without_converging(self):
        """Section 8/20: goal-specific repetition differences - strength
        and hypertrophy(lean_bulk) must be MORE tolerant (lower penalty)
        than general_fitness for the identical streak."""
        history = [(AS_OF - timedelta(days=d), "Lower Body") for d in range(1, 5)]
        strength_penalty, _ = repeat_penalty("Lower Body", history, STRENGTH)
        bulk_penalty, _ = repeat_penalty("Lower Body", history, LEAN_BULK)
        general_penalty, _ = repeat_penalty("Lower Body", history, GENERAL_FITNESS)
        self.assertLess(strength_penalty, general_penalty)
        self.assertLess(bulk_penalty, general_penalty)
        self.assertNotEqual(strength_penalty, bulk_penalty)  # no forced convergence

    def test_rest_day_resets_penalty_for_next_lifting_choice(self):
        history_no_rest = [(AS_OF - timedelta(days=d), "Lower Body") for d in range(1, 5)]
        history_with_rest_yesterday = [(AS_OF - timedelta(days=1), REST_FAMILY_NAME)] + history_no_rest
        penalty_no_rest, _ = repeat_penalty("Lower Body", history_no_rest, LEAN_CUT)
        penalty_with_rest, _ = repeat_penalty("Lower Body", history_with_rest_yesterday, LEAN_CUT)
        self.assertGreater(penalty_no_rest, 0.0)
        self.assertEqual(penalty_with_rest, 0.0)


class RollingRepresentationTests(unittest.TestCase):
    def test_overrepresented_family_gets_negative_adjustment(self):
        # 10 real sessions in the last 7/14/30 days, all "Upper Pull".
        rows = _family_ledger_rows(
            [("back_m", ["Back"]), ("biceps_m", ["Biceps"])], n_per_day=10, start_days_ago=1,
        )
        family_windows = {
            days: __import__(
                "training_intelligence.stimulus.session_family", fromlist=["session_family_windows"]
            ).session_family_windows(rows, AS_OF, (days,))[days]
            for days in (7, 14, 30)
        }
        adjustment = rolling_representation_adjustment("Upper Pull", family_windows, eligible_family_count=7)
        self.assertLess(adjustment, 0.0)

    def test_underrepresented_family_gets_positive_adjustment(self):
        rows = _family_ledger_rows(
            [("back_m", ["Back"]), ("biceps_m", ["Biceps"])], n_per_day=10, start_days_ago=1,
        )
        from training_intelligence.stimulus.session_family import session_family_windows
        family_windows = {days: session_family_windows(rows, AS_OF, (days,))[days] for days in (7, 14, 30)}
        adjustment = rolling_representation_adjustment("Lower Body", family_windows, eligible_family_count=7)
        self.assertGreater(adjustment, 0.0)

    def test_cold_start_no_history_is_neutral(self):
        family_windows = {7: {}, 14: {}, 30: {}}
        adjustment = rolling_representation_adjustment("Lower Body", family_windows, eligible_family_count=7)
        self.assertEqual(adjustment, 0.0)

    def test_balanced_history_does_not_force_equal_distribution(self):
        """A family sitting almost exactly at its fair share should see
        an adjustment close to zero, not a nudge toward exact equality."""
        from training_intelligence.stimulus.session_family import session_family_windows
        rows = []
        rows += _family_ledger_rows(
            [("back_m", ["Back"]), ("biceps_m", ["Biceps"])], n_per_day=2, start_days_ago=1, prefix="pull",
        )
        rows += _family_ledger_rows(
            [("chest_m", ["Chest"]), ("shoulders_m", ["Shoulders"])], n_per_day=2, start_days_ago=8, prefix="push",
        )
        family_windows = {days: session_family_windows(rows, AS_OF, (days,))[days] for days in (7, 14, 30)}
        adjustment = rolling_representation_adjustment("Upper Pull", family_windows, eligible_family_count=2)
        self.assertLess(abs(adjustment), 0.03)


class StarvationBonusTests(unittest.TestCase):
    def _dosed_eligible_candidate(self, family, upper_bound):
        return {
            "session_family": family, "eligible": True,
            "dose": {"feasible_dose_range": {"upper_bound_working_sets": upper_bound}},
        }

    def test_absent_family_with_meaningful_dose_gets_bonus(self):
        candidate = self._dosed_eligible_candidate("Core + Accessories", upper_bound=10)
        # Enough REAL signal elsewhere in the window (section 15's guard)
        # that "Core + Accessories has zero sessions" is a meaningful
        # claim, not a cold-start artifact.
        family_windows = {30: {"Upper Pull": {"sessions": 5}, "Lower Body": {"sessions": 4}}}
        self.assertGreater(starvation_bonus("Core + Accessories", candidate, family_windows), 0.0)

    def test_starvation_requires_enough_real_signal_to_fire(self):
        """Section 15 cold-start guard: near-zero total real training
        anywhere in the window must not itself be read as starvation."""
        candidate = self._dosed_eligible_candidate("Core + Accessories", upper_bound=10)
        family_windows = {30: {}}  # zero real sessions anywhere - true cold start
        self.assertEqual(starvation_bonus("Core + Accessories", candidate, family_windows), 0.0)

    def test_starvation_cannot_bypass_ineligibility(self):
        candidate = {"session_family": "Upper Pull", "eligible": False, "dose": None}
        family_windows = {30: {"Lower Body": {"sessions": 5}, "Upper Push": {"sessions": 4}}}
        self.assertEqual(starvation_bonus("Upper Pull", candidate, family_windows), 0.0)

    def test_present_family_gets_no_starvation_bonus(self):
        candidate = self._dosed_eligible_candidate("Upper Pull", upper_bound=10)
        family_windows = {30: {"Upper Pull": {"sessions": 1}, "Lower Body": {"sessions": 4}}}
        self.assertEqual(starvation_bonus("Upper Pull", candidate, family_windows), 0.0)

    def test_low_feasible_dose_does_not_qualify_for_starvation_bonus(self):
        candidate = self._dosed_eligible_candidate("Core + Accessories", upper_bound=3)
        family_windows = {30: {"Upper Pull": {"sessions": 5}, "Lower Body": {"sessions": 4}}}
        self.assertEqual(starvation_bonus("Core + Accessories", candidate, family_windows), 0.0)


# ----------------------------------------------------------------------
# Integration-level: build_shadow_selection() with the calibration layer.
# ----------------------------------------------------------------------

class CalibrationIntegrationTests(unittest.TestCase):
    def test_strong_stimulus_debt_can_still_overcome_repeat_penalty(self):
        """Soft, not a hard ban: a candidate with a strong enough v1
        score_total lead over its nearest competitor still wins even
        with a real, nonzero consecutive-repeat penalty applied - the
        penalty is a soft tax, not a disqualification."""
        muscles = _all_fresh_muscles({
            "Glutes": {"hours": 24 * 60}, "Hamstrings": {"hours": 24 * 60}, "Quads": {"hours": 24 * 60},
        })
        muscle_rows = [r for r in _rich_muscle_rows() if r["muscle_groups"][0] not in ("Glutes", "Hamstrings", "Quads")]
        # Real recent ledger stimulus for every OTHER muscle (well-serviced
        # -> low debt), none for Glutes/Hamstrings/Quads (well-neglected
        # -> maximal relative debt) - gives Lower Body a genuine, sizeable
        # stimulus_debt-driven lead over its nearest competitor (Full
        # Body, which shares those same 3 muscles but dilutes them with
        # 4 well-serviced ones of its own).
        ledger_rows = []
        for i in range(8):
            for idx, muscle in enumerate(("Chest", "Back", "Shoulders", "Biceps", "Triceps")):
                ledger_rows.append(_ledger_row(f"up{i}", 1 + i % 10, f"{muscle}_m{i}", [muscle], set_index=idx))
        history = [(AS_OF - timedelta(days=d), "Lower Body") for d in range(1, 3)]  # streak=2, moderate penalty
        result = _run(muscles=muscles, muscle_rows=muscle_rows, ledger_rows=ledger_rows, recent_selections=history)
        lower = next(c for c in result["candidates"] if c["session_family"] == "Lower Body")
        self.assertGreater(lower["monotony"]["repeat_penalty"], 0.0)
        self.assertEqual(result["selected_session_family"], "Lower Body")

    def test_fatigued_alternative_does_not_force_diversity(self):
        """If the ONLY eligible lifting family has a repeat streak, and
        every alternative is fatigued/ineligible, monotony must not (and
        cannot) force a switch to a candidate that doesn't exist."""
        muscles = _all_fresh_muscles({m: {"state": "FATIGUED"} for m in ALL_MUSCLES if m not in ("Glutes", "Hamstrings", "Quads")})
        history = [(AS_OF - timedelta(days=d), "Lower Body") for d in range(1, 10)]
        result = _run(muscles=muscles, recent_selections=history)
        self.assertEqual(result["selected_session_family"], "Lower Body")

    def test_no_recent_selections_means_zero_repeat_penalty(self):
        result = _run(recent_selections=None)
        for candidate in result["candidates"]:
            if candidate["eligible"] and candidate["session_family"] != REST_FAMILY_NAME:
                self.assertEqual(candidate["monotony"]["repeat_penalty"], 0.0)

    def test_rest_is_exempt_from_monotony_layer(self):
        history = [(AS_OF - timedelta(days=d), REST_FAMILY_NAME) for d in range(1, 10)]
        result = _run(recent_selections=history)
        rest = next(c for c in result["candidates"] if c["session_family"] == REST_FAMILY_NAME)
        self.assertIsNone(rest["monotony"])
        self.assertEqual(rest["score_total_calibrated"], rest["score_total"])

    def test_future_recent_selection_entry_does_not_influence_selection(self):
        """as_of-safety for the caller-supplied recent_selections list."""
        baseline = _run()
        history_with_future = [(AS_OF + timedelta(days=1), "Lower Body")]
        with_future = _run(recent_selections=history_with_future)
        self.assertEqual(baseline["selected_session_family"], with_future["selected_session_family"])
        self.assertEqual(
            [c["score_total_calibrated"] for c in baseline["ranked_eligible"]],
            [c["score_total_calibrated"] for c in with_future["ranked_eligible"]],
        )

    def test_deterministic_with_calibration_applied(self):
        history = [(AS_OF - timedelta(days=d), "Lower Body") for d in range(1, 5)]
        first = _run(recent_selections=history)
        second = _run(recent_selections=history)
        self.assertEqual(first, second)

    def test_ranked_eligible_sorted_by_calibrated_score(self):
        history = [(AS_OF - timedelta(days=d), "Lower Body") for d in range(1, 5)]
        result = _run(recent_selections=history)
        scores = [c["score_total_calibrated"] for c in result["ranked_eligible"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_cold_start_zero_history_calibration_is_neutral(self):
        result = _run(muscles=[], sessions=[], muscle_rows=[], ledger_rows=[], recent_selections=[])
        self.assertEqual(result["status"], "ok")
        for candidate in result["candidates"]:
            if candidate["eligible"] and candidate["session_family"] != REST_FAMILY_NAME:
                self.assertEqual(candidate["monotony"]["monotony_adjustment_total"], 0.0)


class CoreAccessoriesTaxonomyDispositionTests(unittest.TestCase):
    """Section 7 (audit): EVERY ONE of Core + Accessories' 4 muscles
    (Core, Biceps, Triceps, Shoulders) is also a primary muscle of at
    least one other, typically larger/"compound"-tagged family (Core via
    Full Body; Biceps via Upper Pull; Triceps/Shoulders via Upper Push) -
    it is the only SESSION_TEMPLATES family with NO muscle unique to it.
    This is a durable, checked characterization of *why* it structurally
    struggles to win (its own local_readiness/days_since_trained/
    stimulus_debt signal is diluted by muscles that are usually already
    serviced elsewhere), not a mandate to change SESSION_TEMPLATES (out
    of scope - that is a B3 taxonomy, unmodified)."""

    def test_core_accessories_has_no_muscle_unique_to_it(self):
        from integrations.tonal.training_priority import SESSION_TEMPLATES
        core_muscles = set(SESSION_TEMPLATES["Core + Accessories"]["muscles"])
        other_muscles = set()
        for family, spec in SESSION_TEMPLATES.items():
            if family == "Core + Accessories":
                continue
            other_muscles |= set(spec["muscles"])
        overlap = core_muscles & other_muscles
        unique = core_muscles - other_muscles
        self.assertEqual(unique, set())
        self.assertEqual(overlap, core_muscles)


if __name__ == "__main__":
    unittest.main()
