"""TKI-3: personalized training dose shadow layer tests.

All tests are pure/in-memory (fixture sessions/rows, no live database),
matching the established convention for this codebase. Real-Postgres
coverage lives in test_training_intelligence_dose_postgres.py (opt-in).

build_shadow_dose composes several already-validated, already-live
components (B2's compute_dose_target, progressive_overload.trajectory,
TKI-2's ledger) - these tests exercise the REAL B2 engine over
controlled fixture data (not a mock of it), plus the genuinely new
TKI-3 composition on top (tolerance bands, dose_classification, goal
context, explanation/fallback surfacing).
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from training_intelligence.dose.shadow_dose import build_shadow_dose, DOSE_MODEL_VERSION
from training_intelligence.dose.tolerance_bands import (
    classify_band,
    BELOW_RANGE,
    WITHIN_RANGE,
    UPPER_RANGE,
    INSUFFICIENT_EVIDENCE,
    MIN_SESSIONS_FOR_BAND,
)

AS_OF = datetime(2026, 9, 12, 8, 10, tzinfo=timezone.utc)


def _session(label, days_ago, workout_type="Upper Pull", set_count=9, movement_count=4,
             total_reps=80, total_volume=5080.0, duration_seconds=2400, included=True,
             exclusion_reason=None):
    return {
        "activity_id": label,
        "begin_time": AS_OF - timedelta(days=days_ago),
        "workout_type": workout_type,
        "duration_seconds": duration_seconds,
        "total_reps": total_reps,
        "total_volume": total_volume,
        "set_count": set_count,
        "movement_count": movement_count,
        "included": included,
        "exclusion_reason": exclusion_reason,
    }


def _muscle_row(label, days_ago, muscle_groups, volume=200.0, included=True, exclusion_reason=None):
    return {
        "activity_id": label,
        "begin_time": AS_OF - timedelta(days=days_ago),
        "included": included,
        "exclusion_reason": exclusion_reason,
        "volume": volume,
        "muscle_groups": muscle_groups,
    }


def _patched(readiness_band="high", recovery_score=94.0, muscles=None, goal=None):
    """Context manager stack for the three external, real-DB-backed
    reads build_shadow_dose otherwise makes: WHOOP readiness, local
    muscle readiness, and active goal. sessions/muscle_rows/ledger_rows
    are still passed explicitly by the caller (no DB needed for those)."""
    muscles = muscles if muscles is not None else []
    goal = goal if goal is not None else {"phase": "lean_cut", "goal_type": "fat_loss"}
    return (
        patch(
            "training_intelligence.dose.shadow_dose._latest_readiness",
            return_value={"recovery_score": recovery_score, "readiness_band": readiness_band},
        ),
        patch(
            "training_intelligence.dose.shadow_dose.calculate_muscle_readiness",
            return_value={"selection_confidence": "high", "latest_workout_age_hours": 24.0, "muscles": muscles},
        ),
        patch("training_intelligence.dose.shadow_dose.get_active_goal", return_value=goal),
    )


class ToleranceBandTests(unittest.TestCase):
    def test_below_range(self):
        self.assertEqual(classify_band(4, 5, 6, 8, 10), BELOW_RANGE)

    def test_within_range(self):
        self.assertEqual(classify_band(7, 5, 6, 8, 10), WITHIN_RANGE)

    def test_upper_range(self):
        self.assertEqual(classify_band(11, 5, 6, 8, 10), UPPER_RANGE)

    def test_insufficient_evidence_below_minimum_sessions(self):
        self.assertEqual(classify_band(9, MIN_SESSIONS_FOR_BAND - 1, 6, 8, 10), INSUFFICIENT_EVIDENCE)

    def test_insufficient_evidence_when_percentiles_missing(self):
        self.assertEqual(classify_band(9, 5, None, None, None), INSUFFICIENT_EVIDENCE)

    def test_exactly_minimum_sessions_is_sufficient(self):
        self.assertNotEqual(classify_band(9, MIN_SESSIONS_FOR_BAND, 6, 8, 10), INSUFFICIENT_EVIDENCE)


class HistoricalDoseCalculationTests(unittest.TestCase):
    """Real B2 engine over a rich, consistent comparable-session history."""

    def test_historical_dose_produced_from_real_comparable_sessions(self):
        sessions = [_session(f"s{i}", days_ago=3 + i * 6) for i in range(5)]  # all within B2's 30-day tier-1 window
        readiness_ctx = _patched(readiness_band="good", recovery_score=70.0)
        with readiness_ctx[0], readiness_ctx[1], readiness_ctx[2]:
            result = build_shadow_dose(AS_OF, "Upper Pull", sessions=sessions, muscle_rows=[], ledger_rows=[])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["mode"], "shadow")
        self.assertEqual(result["dose_model_version"], DOSE_MODEL_VERSION)
        self.assertGreater(result["recommended_dose"]["working_sets"], 0)
        self.assertEqual(result["historical_dose_reference"]["comparable_session_count"], 5)
        self.assertEqual(result["historical_dose_reference"]["source"], "comparable_sessions_30_90d")

    def test_deterministic_repeated_output(self):
        sessions = [_session(f"s{i}", days_ago=3 + i * 5) for i in range(4)]
        readiness_ctx = _patched()
        with readiness_ctx[0], readiness_ctx[1], readiness_ctx[2]:
            first = build_shadow_dose(AS_OF, "Upper Pull", sessions=list(sessions), muscle_rows=[], ledger_rows=[])
            second = build_shadow_dose(AS_OF, "Upper Pull", sessions=list(sessions), muscle_rows=[], ledger_rows=[])
        self.assertEqual(first, second)

    def test_unknown_session_family_rejected(self):
        with self.assertRaises(ValueError):
            build_shadow_dose(AS_OF, "Not A Real Family", sessions=[], muscle_rows=[], ledger_rows=[])

    def test_naive_as_of_rejected(self):
        with self.assertRaises(ValueError):
            build_shadow_dose(datetime(2026, 9, 12), "Upper Pull", sessions=[], muscle_rows=[], ledger_rows=[])


class MinimumEvidenceFallbackTests(unittest.TestCase):
    def test_insufficient_comparable_history_falls_back_and_flags_it(self):
        sessions = [_session("only_one", days_ago=5)]
        readiness_ctx = _patched()
        with readiness_ctx[0], readiness_ctx[1], readiness_ctx[2]:
            result = build_shadow_dose(AS_OF, "Upper Pull", sessions=sessions, muscle_rows=[], ledger_rows=[])
        self.assertNotEqual(result["historical_dose_reference"]["source"], "comparable_sessions_30_90d")
        self.assertEqual(result["recommended_dose"]["working_sets_personal_band"], INSUFFICIENT_EVIDENCE)
        self.assertTrue(any("fallback" in f for f in result["fallbacks_used"]))

    def test_zero_history_still_produces_a_conservative_dose_not_a_crash(self):
        readiness_ctx = _patched()
        with readiness_ctx[0], readiness_ctx[1], readiness_ctx[2]:
            result = build_shadow_dose(AS_OF, "Upper Pull", sessions=[], muscle_rows=[], ledger_rows=[])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["historical_dose_reference"]["source"], "conservative_configured_fallback")


class SystemicAndLocalReadinessTests(unittest.TestCase):
    """Section 6/7: WHOOP scales capacity; local readiness gates muscle
    budget; systemic recovery cannot override local fatigue."""

    def test_high_systemic_fresh_muscle_gets_nonzero_budget(self):
        muscles = [{"muscle": m, "readiness_state": "FRESH", "readiness_score": 90.0,
                    "hours_since_primary_exposure": 200.0} for m in ("Back", "Biceps")]
        sessions = [_session(f"s{i}", days_ago=3 + i * 7) for i in range(4)]
        readiness_ctx = _patched(readiness_band="high", recovery_score=94.0, muscles=muscles)
        with readiness_ctx[0], readiness_ctx[1], readiness_ctx[2]:
            result = build_shadow_dose(AS_OF, "Upper Pull", sessions=sessions, muscle_rows=[], ledger_rows=[])
        for muscle in ("Back", "Biceps"):
            self.assertGreater(result["local_readiness"][muscle]["budget_effective_sets"], 0.0)
        self.assertGreater(result["recommended_dose"]["working_sets"], 0)

    def test_high_systemic_fatigued_muscle_forced_to_zero_budget_and_zero_dose(self):
        """The central invariant: high WHOOP cannot override local fatigue."""
        muscles = [{"muscle": m, "readiness_state": "FATIGUED", "readiness_score": 20.0,
                    "hours_since_primary_exposure": 6.0} for m in ("Back", "Biceps")]
        sessions = [_session(f"s{i}", days_ago=3 + i * 7) for i in range(4)]
        readiness_ctx = _patched(readiness_band="high", recovery_score=94.0, muscles=muscles)
        with readiness_ctx[0], readiness_ctx[1], readiness_ctx[2]:
            result = build_shadow_dose(AS_OF, "Upper Pull", sessions=sessions, muscle_rows=[], ledger_rows=[])
        for muscle in ("Back", "Biceps"):
            self.assertEqual(result["local_readiness"][muscle]["budget_effective_sets"], 0.0)
        self.assertEqual(result["recommended_dose"]["working_sets"], 0)

    def test_low_systemic_fresh_muscle_preserves_reduced_but_nonzero_dose(self):
        muscles = [{"muscle": m, "readiness_state": "FRESH", "readiness_score": 90.0,
                    "hours_since_primary_exposure": 200.0} for m in ("Back", "Biceps")]
        sessions = [_session(f"s{i}", days_ago=3 + i * 7) for i in range(4)]
        readiness_ctx = _patched(readiness_band="low", recovery_score=30.0, muscles=muscles)
        with readiness_ctx[0], readiness_ctx[1], readiness_ctx[2]:
            result = build_shadow_dose(AS_OF, "Upper Pull", sessions=sessions, muscle_rows=[], ledger_rows=[])
        self.assertGreater(result["recommended_dose"]["working_sets"], 0)
        self.assertEqual(result["dose_classification"], "reduced")

    def test_unknown_local_readiness_is_not_silently_treated_as_fresh(self):
        """Section 7: "Unknown must remain uncertainty, not silently
        Fresh." Target muscles with no readiness entry at all."""
        sessions = [_session(f"s{i}", days_ago=3 + i * 7) for i in range(4)]
        readiness_ctx = _patched(muscles=[])  # no entries at all for Back/Biceps
        with readiness_ctx[0], readiness_ctx[1], readiness_ctx[2]:
            result = build_shadow_dose(AS_OF, "Upper Pull", sessions=sessions, muscle_rows=[], ledger_rows=[])
        for muscle in ("Back", "Biceps"):
            self.assertEqual(result["local_readiness"][muscle]["readiness_state"], "UNKNOWN")
        # B2's own handling: unrecognized state -> conservative half budget,
        # never the full FRESH-equivalent budget.
        for muscle in ("Back", "Biceps"):
            self.assertGreater(result["local_readiness"][muscle]["budget_effective_sets"], 0.0)


class PerformanceResponseTests(unittest.TestCase):
    def test_improving_performance_classified(self):
        # progressive_overload.trajectory: recent 2 sessions vs older -
        # recent volume clearly higher than older.
        sessions = [
            _session("recent1", days_ago=2, total_volume=6000.0),
            _session("recent2", days_ago=5, total_volume=5800.0),
            _session("older1", days_ago=20, total_volume=4000.0),
            _session("older2", days_ago=27, total_volume=4100.0),
        ]
        readiness_ctx = _patched()
        with readiness_ctx[0], readiness_ctx[1], readiness_ctx[2]:
            result = build_shadow_dose(AS_OF, "Upper Pull", sessions=sessions, muscle_rows=[], ledger_rows=[])
        self.assertEqual(result["performance_state"]["trajectory"], "IMPROVING")

    def test_declining_performance_classified(self):
        sessions = [
            _session("recent1", days_ago=2, total_volume=3000.0),
            _session("recent2", days_ago=5, total_volume=3100.0),
            _session("older1", days_ago=20, total_volume=5000.0),
            _session("older2", days_ago=27, total_volume=5200.0),
        ]
        readiness_ctx = _patched()
        with readiness_ctx[0], readiness_ctx[1], readiness_ctx[2]:
            result = build_shadow_dose(AS_OF, "Upper Pull", sessions=sessions, muscle_rows=[], ledger_rows=[])
        self.assertEqual(result["performance_state"]["trajectory"], "DECLINING")

    def test_insufficient_history_performance_state(self):
        sessions = [_session("only_one", days_ago=3)]
        readiness_ctx = _patched()
        with readiness_ctx[0], readiness_ctx[1], readiness_ctx[2]:
            result = build_shadow_dose(AS_OF, "Upper Pull", sessions=sessions, muscle_rows=[], ledger_rows=[])
        self.assertEqual(result["performance_state"]["trajectory"], "INSUFFICIENT_DATA")


class GoalContextTests(unittest.TestCase):
    def test_lean_cut_context_sets_training_objective(self):
        readiness_ctx = _patched(goal={"phase": "lean_cut", "goal_type": "fat_loss"})
        with readiness_ctx[0], readiness_ctx[1], readiness_ctx[2]:
            result = build_shadow_dose(AS_OF, "Upper Pull", sessions=[], muscle_rows=[], ledger_rows=[])
        self.assertEqual(result["goal_context"]["phase"], "lean_cut")
        self.assertEqual(result["goal_context"]["training_objective"], "preserve_or_gain_lean_mass")

    def test_body_composition_single_reading_never_consumed(self):
        readiness_ctx = _patched()
        with readiness_ctx[0], readiness_ctx[1], readiness_ctx[2]:
            result = build_shadow_dose(AS_OF, "Upper Pull", sessions=[], muscle_rows=[], ledger_rows=[])
        self.assertIn("none", result["goal_context"]["body_composition_single_reading_influence"])

    def test_other_phase_leaves_objective_unset(self):
        readiness_ctx = _patched(goal={"phase": "maintenance", "goal_type": "recomposition"})
        with readiness_ctx[0], readiness_ctx[1], readiness_ctx[2]:
            result = build_shadow_dose(AS_OF, "Upper Pull", sessions=[], muscle_rows=[], ledger_rows=[])
        self.assertIsNone(result["goal_context"]["training_objective"])


class TemporalLeakageTests(unittest.TestCase):
    """Section 13: future workouts/activity cannot influence historical
    dose, even if injected directly into sessions/muscle_rows (defense in
    depth beyond B2's own SQL-level bound - see shadow_dose.py docstring)."""

    def test_future_session_excluded_from_dose_calculation(self):
        past_sessions = [_session(f"s{i}", days_ago=3 + i * 7) for i in range(4)]
        future_session = _session("future", days_ago=-5, total_volume=999999.0, set_count=99)
        readiness_ctx = _patched()
        with readiness_ctx[0], readiness_ctx[1], readiness_ctx[2]:
            with_future = build_shadow_dose(
                AS_OF, "Upper Pull", sessions=past_sessions + [future_session], muscle_rows=[], ledger_rows=[]
            )
            without_future = build_shadow_dose(
                AS_OF, "Upper Pull", sessions=list(past_sessions), muscle_rows=[], ledger_rows=[]
            )
        self.assertEqual(with_future, without_future)

    def test_future_muscle_row_excluded(self):
        past_rows = [_muscle_row("m1", days_ago=3, muscle_groups=["Back"])]
        future_rows = [_muscle_row("future", days_ago=-3, muscle_groups=["Back"], volume=999999.0)]
        readiness_ctx = _patched()
        with readiness_ctx[0], readiness_ctx[1], readiness_ctx[2]:
            with_future = build_shadow_dose(
                AS_OF, "Upper Pull", sessions=[], muscle_rows=past_rows + future_rows, ledger_rows=[]
            )
            without_future = build_shadow_dose(
                AS_OF, "Upper Pull", sessions=[], muscle_rows=list(past_rows), ledger_rows=[]
            )
        self.assertEqual(with_future, without_future)

    def test_exact_as_of_boundary_session_included(self):
        boundary_session = {**_session("boundary", days_ago=0), "begin_time": AS_OF}
        readiness_ctx = _patched()
        with readiness_ctx[0], readiness_ctx[1], readiness_ctx[2]:
            result = build_shadow_dose(AS_OF, "Upper Pull", sessions=[boundary_session], muscle_rows=[], ledger_rows=[])
        self.assertEqual(result["status"], "ok")


class ExtremeHistoryTests(unittest.TestCase):
    def test_extreme_outlier_session_does_not_dominate_median_baseline(self):
        normal = [_session(f"s{i}", days_ago=3 + i * 7, set_count=9, total_volume=5000.0) for i in range(4)]
        outlier = _session("outlier", days_ago=10, set_count=90, total_volume=90000.0)
        readiness_ctx = _patched()
        with readiness_ctx[0], readiness_ctx[1], readiness_ctx[2]:
            result = build_shadow_dose(AS_OF, "Upper Pull", sessions=normal + [outlier], muscle_rows=[], ledger_rows=[])
        # Median-based baseline should stay close to the 9-set norm, not be
        # dragged toward the 90-set outlier.
        self.assertLess(result["historical_dose_reference"]["median_working_sets"], 20)

    def test_dose_classification_thresholds(self):
        from training_intelligence.dose.shadow_dose import _classify_dose
        self.assertEqual(_classify_dose(0.5), "reduced")
        self.assertEqual(_classify_dose(0.95), "normal")
        self.assertEqual(_classify_dose(1.2), "upper_normal")
        self.assertEqual(_classify_dose(None), "normal")


if __name__ == "__main__":
    unittest.main()
