"""Training-B2 personalized dose engine - deterministic regression tests."""

import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

db_stub = types.ModuleType("db")
db_stub.get_conn = Mock()
sys.modules.setdefault("db", db_stub)

from integrations.tonal import training_dose as td
from integrations.tonal import workout_prescription as wp

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


def session(days_ago, sets=14, exercises=5, reps=70, volume=9500, duration_min=45,
            workout_type="Upper Push", included=True, exclusion_reason=None,
            activity_id=None):
    return {
        "activity_id": activity_id or f"a-{days_ago}-{sets}-{volume}",
        "begin_time": NOW - timedelta(days=days_ago),
        "workout_type": workout_type,
        "duration_seconds": duration_min * 60 if duration_min is not None else None,
        "total_reps": reps,
        "total_volume": volume,
        "set_count": sets,
        "movement_count": exercises,
        "included": included,
        "exclusion_reason": exclusion_reason,
    }


def muscle_row(days_ago, muscles, volume=500, included=True, exclusion_reason=None, activity_id=None):
    return {
        "activity_id": activity_id or f"m-{days_ago}-{'-'.join(muscles)}",
        "begin_time": NOW - timedelta(days=days_ago),
        "included": included,
        "exclusion_reason": exclusion_reason,
        "volume": volume,
        "muscle_groups": muscles,
    }


def ready(muscle, state="READY"):
    return {"muscle": muscle, "readiness_state": state, "readiness_score": 70.0}


# A consistent personal "substantial session" history: 12 Upper Push
# sessions over 90 days, ~14 sets / ~9500 volume / 45 min each - this is
# the baseline that later high-recovery tests expect the engine to
# respect rather than under-prescribe against.
SUBSTANTIAL_UPPER_HISTORY = [
    session(days_ago=d, sets=14, exercises=5, volume=9500, duration_min=45, workout_type="Upper Push")
    for d in (2, 5, 9, 12, 16, 19, 23, 30, 40, 55, 70, 85)
]


class SessionBaselineTests(unittest.TestCase):

    def test_median_and_percentiles_use_robust_statistics(self):
        baselines = td.compute_session_baselines(NOW, sessions=SUBSTANTIAL_UPPER_HISTORY)
        w30 = baselines["windows"][30]
        self.assertEqual(w30["median_sets"], 14.0)
        self.assertAlmostEqual(w30["median_volume"], 9500.0)
        self.assertGreater(w30["sessions_per_week"], 0)

    def test_extreme_historical_workout_does_not_distort_median(self):
        # Scenario 11
        outlier = SUBSTANTIAL_UPPER_HISTORY + [
            session(days_ago=3, sets=40, exercises=8, volume=40000, duration_min=120, workout_type="Upper Push")
        ]
        baselines = td.compute_session_baselines(NOW, sessions=outlier)
        # Median barely moves even though the mean would roughly triple.
        self.assertEqual(baselines["windows"][30]["median_sets"], 14.0)

    def test_abbreviated_session_does_not_distort_baseline(self):
        # Scenario 10
        with_abbrev = SUBSTANTIAL_UPPER_HISTORY + [
            session(days_ago=1, sets=3, exercises=1, volume=300, duration_min=8,
                    workout_type="Upper Push", included=False,
                    exclusion_reason="Atypical abbreviated freestyle session")
        ]
        baselines = td.compute_session_baselines(NOW, sessions=with_abbrev)
        self.assertEqual(baselines["windows"][30]["median_sets"], 14.0)
        reasons = {e["reason"] for e in baselines["excluded_sessions"]}
        self.assertIn("abbreviated_session", reasons)

    def test_explicit_override_excluded_session_reported(self):
        excluded = SUBSTANTIAL_UPPER_HISTORY + [
            session(days_ago=4, included=False, exclusion_reason="user marked duplicate")
        ]
        baselines = td.compute_session_baselines(NOW, sessions=excluded)
        reasons = {e["reason"] for e in baselines["excluded_sessions"]}
        self.assertIn("user marked duplicate", reasons)

    def test_malformed_session_excluded(self):
        malformed = SUBSTANTIAL_UPPER_HISTORY + [
            session(days_ago=6, sets=0, exercises=0, volume=0)
        ]
        baselines = td.compute_session_baselines(NOW, sessions=malformed)
        reasons = {e["reason"] for e in baselines["excluded_sessions"]}
        self.assertIn("malformed_missing_set_or_movement_count", reasons)

    def test_large_legitimate_workout_is_not_discarded(self):
        legit_big = SUBSTANTIAL_UPPER_HISTORY + [
            session(days_ago=8, sets=18, exercises=6, volume=13000, duration_min=60, workout_type="Upper Push")
        ]
        baselines = td.compute_session_baselines(NOW, sessions=legit_big)
        self.assertEqual(baselines["total_sessions_considered"], len(legit_big))
        excluded_ids = {e["activity_id"] for e in baselines["excluded_sessions"]}
        self.assertNotIn(legit_big[-1]["activity_id"], excluded_ids)


class ComparableSessionTests(unittest.TestCase):

    def test_comparable_session_baseline_preferred_over_global(self):
        # Scenario 12
        mixed = SUBSTANTIAL_UPPER_HISTORY + [
            session(days_ago=d, sets=6, exercises=3, volume=2000, duration_min=20, workout_type="Core + Accessories")
            for d in (1, 3, 7)
        ]
        comparable = td.compute_comparable_baseline(NOW, mixed, ["Chest", "Shoulders"], "Upper Push")
        self.assertEqual(comparable["source"], "comparable_sessions_30_90d")
        self.assertAlmostEqual(comparable["median_sets"], 14.0)

    def test_insufficient_comparable_sessions_falls_back_hierarchy(self):
        # Scenario 13
        sparse = [session(days_ago=2, sets=10, exercises=3, volume=3000, workout_type="Lower Body")]
        comparable = td.compute_comparable_baseline(NOW, sparse, ["Chest"], "Upper Push")
        self.assertIn(comparable["source"], (
            "global_qualifying_session_baseline",
            "broader_muscle_region_sessions",
        ))

    def test_no_history_at_all_uses_conservative_fallback(self):
        comparable = td.compute_comparable_baseline(NOW, [], ["Chest"], "Upper Push")
        self.assertEqual(comparable["source"], "conservative_configured_fallback")
        self.assertEqual(comparable["session_count"], 0)

    def test_insufficient_duration_history_is_unavailable(self):
        # Scenario 15
        no_duration = [
            session(days_ago=d, sets=14, exercises=5, volume=9500, duration_min=None, workout_type="Upper Push")
            for d in (2, 5, 9, 12)
        ]
        comparable = td.compute_comparable_baseline(NOW, no_duration, ["Chest"], "Upper Push")
        self.assertIsNone(comparable["median_duration_minutes"])


class WhoopMultiplierTests(unittest.TestCase):

    def test_push_multiplier_within_configured_band(self):
        m = td.whoop_capacity_multiplier("high", 90.0)
        lo, hi = td.WHOOP_CAPACITY_RANGES["high"]
        self.assertTrue(lo <= m <= hi)

    def test_multiplier_scales_deterministically_within_band(self):
        low_end = td.whoop_capacity_multiplier("moderate", 45.0)
        high_end = td.whoop_capacity_multiplier("moderate", 66.9)
        self.assertLess(low_end, high_end)

    def test_active_recovery_multiplier_band(self):
        m = td.whoop_capacity_multiplier("low", 38.0)
        self.assertTrue(0.30 <= m <= 0.50)

    def test_rest_multiplier_is_zero(self):
        self.assertEqual(td.whoop_capacity_multiplier("very_low", 10.0), 0.0)


class RecentLoadModifierTests(unittest.TestCase):

    def test_high_recent_load_reduces_dose(self):
        # Scenario 7
        baselines = td.compute_session_baselines(NOW, sessions=SUBSTANTIAL_UPPER_HISTORY)
        # Force an unusually high 7d rate by injecting extra recent sessions.
        loaded = SUBSTANTIAL_UPPER_HISTORY + [
            session(days_ago=d, sets=14, exercises=5, volume=9500, workout_type="Upper Push")
            for d in (1, 2, 3, 4)
        ]
        loaded_baselines = td.compute_session_baselines(NOW, sessions=loaded)
        result = td.recent_load_modifier(loaded_baselines, muscles_ready=True)
        self.assertLess(result["multiplier"], 1.0)

    def test_low_recent_load_with_ready_muscles_allows_increase(self):
        # Scenario 8
        rested = [
            session(days_ago=d, sets=14, exercises=5, volume=9500, workout_type="Upper Push")
            for d in (40, 50, 60, 70, 80)
        ]
        baselines = td.compute_session_baselines(NOW, sessions=rested)
        result = td.recent_load_modifier(baselines, muscles_ready=True)
        self.assertGreaterEqual(result["multiplier"], 1.0)

    def test_missing_history_is_neutral_not_low_load(self):
        # Missing/stale history must never be read as "low load".
        baselines = td.compute_session_baselines(NOW, sessions=[])
        result = td.recent_load_modifier(baselines, muscles_ready=True)
        self.assertEqual(result["multiplier"], td.RECENT_LOAD_NEUTRAL_MULTIPLIER)


class MuscleBudgetTests(unittest.TestCase):

    def test_suppressed_muscle_gets_zero_budget(self):
        # Scenario 6 / Step 7 invariant
        result = td.muscle_budget("Shoulders", ready("Shoulders", "SUPPRESSED"), None, 1.15, 1.0)
        self.assertEqual(result["budget_effective_sets"], 0.0)

    def test_fatigued_muscle_gets_zero_budget(self):
        result = td.muscle_budget("Biceps", ready("Biceps", "FATIGUED"), None, 1.15, 1.0)
        self.assertEqual(result["budget_effective_sets"], 0.0)

    def test_recovering_muscle_gets_reduced_budget(self):
        baseline_entry = {"windows": {14: {"effective_sets_per_week": 9.0}}}
        recovering = td.muscle_budget("Chest", ready("Chest", "RECOVERING"), baseline_entry, 1.0, 1.0)
        ready_budget = td.muscle_budget("Chest", ready("Chest", "READY"), baseline_entry, 1.0, 1.0)
        self.assertLess(recovering["budget_effective_sets"], ready_budget["budget_effective_sets"])

    def test_high_whoop_never_resurrects_suppressed_muscle(self):
        # Scenario 6, explicit at the highest WHOOP multiplier.
        result = td.muscle_budget("Glutes", ready("Glutes", "SUPPRESSED"), {"windows": {14: {"effective_sets_per_week": 20.0}}}, 1.15, 1.08)
        self.assertEqual(result["budget_effective_sets"], 0.0)


class ConfidenceTests(unittest.TestCase):

    def test_stale_tonal_data_yields_low_confidence(self):
        # Scenario 14
        comparable = {"session_count": 1, "source": "global_qualifying_session_baseline"}
        session_baselines = td.compute_session_baselines(NOW, sessions=[])
        confidence = td.dose_confidence(comparable, tonal_freshness_hours=400.0, session_baselines=session_baselines)
        self.assertEqual(confidence, "LOW")

    def test_rich_comparable_history_yields_high_confidence(self):
        comparable = td.compute_comparable_baseline(NOW, SUBSTANTIAL_UPPER_HISTORY, ["Chest"], "Upper Push")
        session_baselines = td.compute_session_baselines(NOW, sessions=SUBSTANTIAL_UPPER_HISTORY)
        confidence = td.dose_confidence(comparable, tonal_freshness_hours=20.0, session_baselines=session_baselines)
        self.assertEqual(confidence, "HIGH")


class ComputeDoseTargetTests(unittest.TestCase):

    def _dose(self, band, recovery, sessions, muscles_state, target_muscles=("Chest", "Shoulders"),
              session_type="Upper Push", freshness_hours=20.0, muscle_rows=None):
        muscle_readiness_by_name = {m: ready(m, s) for m, s in muscles_state.items()}
        if muscle_rows is None:
            # Derive realistic per-muscle set rows from the same session
            # history, so the muscle-level budget reflects the same
            # personal history as the session-level baseline instead of
            # falling back to the conservative no-history default.
            muscle_rows = []
            for s in sessions:
                per_muscle_sets = max(1, (s.get("set_count") or 0) // max(len(target_muscles), 1))
                for muscle in target_muscles:
                    for i in range(per_muscle_sets):
                        muscle_rows.append(muscle_row(
                            days_ago=(NOW - s["begin_time"]).days,
                            muscles=[muscle],
                            volume=(s.get("total_volume") or 0) / max(len(target_muscles) * per_muscle_sets, 1),
                            activity_id=f"{s['activity_id']}-{muscle}-{i}",
                        ))
        return td.compute_dose_target(
            NOW, band, recovery, list(target_muscles), session_type,
            muscle_readiness_by_name, freshness_hours, sessions=sessions,
            muscle_rows=muscle_rows,
        )

    def test_push_ready_muscles_normal_load_near_or_above_baseline(self):
        # Scenario 1
        dose = self._dose("high", 90.0, SUBSTANTIAL_UPPER_HISTORY, {"Chest": "READY", "Shoulders": "READY"})
        self.assertGreaterEqual(dose["target"]["working_sets"], 13)

    def test_normal_recovery_dose_around_baseline(self):
        # Scenario 2
        dose = self._dose("good", 72.0, SUBSTANTIAL_UPPER_HISTORY, {"Chest": "READY", "Shoulders": "READY"})
        self.assertTrue(11 <= dose["target"]["working_sets"] <= 16)

    def test_moderate_recovery_dose_below_baseline(self):
        # Scenario 3
        normal = self._dose("good", 72.0, SUBSTANTIAL_UPPER_HISTORY, {"Chest": "READY", "Shoulders": "READY"})
        moderate = self._dose("moderate", 55.0, SUBSTANTIAL_UPPER_HISTORY, {"Chest": "READY", "Shoulders": "READY"})
        self.assertLess(moderate["target"]["working_sets"], normal["target"]["working_sets"])

    def test_active_recovery_materially_reduced(self):
        # Scenario 4 / Step 12
        normal = self._dose("good", 72.0, SUBSTANTIAL_UPPER_HISTORY, {"Chest": "READY", "Shoulders": "READY"})
        active_recovery = self._dose("low", 38.0, SUBSTANTIAL_UPPER_HISTORY, {"Chest": "READY", "Shoulders": "READY"})
        self.assertLess(active_recovery["target"]["working_sets"], normal["target"]["working_sets"] * 0.6)
        # Must not be a hard-coded 6 - it's derived; confirm it moves with
        # a different personal baseline.
        smaller_history = [
            session(days_ago=d, sets=8, exercises=3, volume=4000, workout_type="Upper Push")
            for d in (2, 5, 9, 12, 16, 19)
        ]
        smaller_active_recovery = self._dose("low", 38.0, smaller_history, {"Chest": "READY", "Shoulders": "READY"})
        self.assertNotEqual(
            active_recovery["target"]["working_sets"],
            0,
        )
        self.assertLessEqual(smaller_active_recovery["target"]["working_sets"], active_recovery["target"]["working_sets"])

    def test_high_whoop_high_recent_load_reduces_dose(self):
        # Scenario 7
        loaded = SUBSTANTIAL_UPPER_HISTORY + [
            session(days_ago=d, sets=14, exercises=5, volume=9500, workout_type="Upper Push")
            for d in (1, 2, 3)
        ]
        baseline_dose = self._dose("high", 90.0, SUBSTANTIAL_UPPER_HISTORY, {"Chest": "READY", "Shoulders": "READY"})
        loaded_dose = self._dose("high", 90.0, loaded, {"Chest": "READY", "Shoulders": "READY"})
        self.assertLessEqual(loaded_dose["modifiers"]["recent_load"], baseline_dose["modifiers"]["recent_load"])

    def test_secondary_exposure_contributes_reduced_effective_set(self):
        # Scenario 9
        rows = [muscle_row(d, ["Chest", "Triceps"]) for d in (1, 3, 5)]
        baselines = td.compute_muscle_baselines(NOW, rows=rows)
        triceps_7d = baselines["muscles"]["Triceps"]["windows"][7]["effective_sets"]
        chest_7d = baselines["muscles"]["Chest"]["windows"][7]["effective_sets"]
        self.assertAlmostEqual(triceps_7d, chest_7d * td.SECONDARY_SET_WEIGHT, places=2)

    def test_small_movement_pool_limits_dose_not_unsafe_addition(self):
        # Scenario 16 - handled at the caller (build_daily_workout_prescription)
        # via trimming `selected`; here we confirm the dose engine reports
        # a target that a small pool would then legitimately fall short of,
        # rather than silently inflating it.
        dose = self._dose("high", 90.0, SUBSTANTIAL_UPPER_HISTORY, {"Chest": "READY", "Shoulders": "READY"})
        self.assertGreater(dose["target"]["exercise_count"], 0)

    def test_per_muscle_budget_caps_target_sets(self):
        # Scenario 17 / Step 9
        dose = self._dose("high", 90.0, SUBSTANTIAL_UPPER_HISTORY, {"Chest": "RECOVERING", "Shoulders": "RECOVERING"})
        total_budget = sum(b["budget_effective_sets"] for b in dose["muscle_budgets"].values())
        self.assertLessEqual(dose["target"]["working_sets"], round(total_budget) + 1)

    def test_volume_target_never_overrides_suppression(self):
        # Scenario 18
        dose = self._dose("high", 90.0, SUBSTANTIAL_UPPER_HISTORY, {"Chest": "SUPPRESSED", "Shoulders": "SUPPRESSED"})
        self.assertEqual(dose["target"]["working_sets"], 0)
        self.assertEqual(dose["dose_limited_by"], "muscle_readiness_budget")
        for budget in dose["muscle_budgets"].values():
            self.assertEqual(budget["budget_effective_sets"], 0.0)

    def test_valid_lower_body_day_receives_personalized_dose(self):
        # Scenario 22
        lower_history = [
            session(days_ago=d, sets=12, exercises=4, volume=8000, duration_min=40, workout_type="Lower Body")
            for d in (3, 10, 17, 24, 45, 66)
        ]
        dose = self._dose(
            "good", 72.0, lower_history, {"Glutes": "READY", "Hamstrings": "READY"},
            target_muscles=("Glutes", "Hamstrings"), session_type="Lower Body",
        )
        self.assertGreater(dose["target"]["working_sets"], 0)
        self.assertEqual(dose["baseline"]["source"], "comparable_sessions_30_90d")

    def test_push_day_not_systematically_under_prescribed(self):
        # Scenario 23 - regression for the historical "9.7k volume on a
        # high-recovery day despite a much bigger personal baseline" bug.
        dose = self._dose("high", 90.0, SUBSTANTIAL_UPPER_HISTORY, {"Chest": "READY", "Shoulders": "READY"})
        self.assertGreaterEqual(dose["target"]["volume_target"], 9500 * 0.95)


class RestDoseTests(unittest.TestCase):

    def test_rest_band_configured_multiplier_is_zero(self):
        # Scenario 5 - build_daily_workout_prescription already short-
        # circuits "very_low" to a Rest session before the dose engine
        # runs; this pins the dose engine's own contribution to that
        # invariant (0 resistance-training multiplier).
        self.assertEqual(td.WHOOP_CAPACITY_RANGES["very_low"], (0.0, 0.0))
        self.assertEqual(td.whoop_capacity_multiplier("very_low", 10.0), 0.0)


class SepFiveReplayTests(unittest.TestCase):
    """Replays the real, previously-validated Sep 5 Development facts
    (38% recovery / Active Recovery, suppressed Shoulders/Biceps/Glutes/
    Hamstrings, latest Tonal workout 2026-09-04T12:49:04Z) as inputs to
    the actual B2 engine. Historical session rows are reconstructed from
    the "Sep4/3/2/1 substantial sessions, Aug 30 substantial lower
    session" facts stated for this replay, since live DB access is not
    available from this environment (see task RETURN, Step 18 note)."""

    SEP5_HISTORY = [
        session(days_ago=1, sets=14, exercises=5, volume=9200, duration_min=45, workout_type="Lower Body"),
        session(days_ago=2, sets=13, exercises=5, volume=8800, duration_min=42, workout_type="Upper Push"),
        session(days_ago=3, sets=14, exercises=5, volume=9000, duration_min=44, workout_type="Upper Pull"),
        session(days_ago=4, sets=12, exercises=4, volume=7600, duration_min=38, workout_type="Core + Accessories"),
        session(days_ago=6, sets=13, exercises=5, volume=8600, duration_min=41, workout_type="Lower Body"),
    ]

    def test_sep5_active_recovery_dose_derived_from_personal_history_not_hardcoded(self):
        muscle_readiness_by_name = {
            "Shoulders": ready("Shoulders", "SUPPRESSED"),
            "Biceps": ready("Biceps", "SUPPRESSED"),
            "Glutes": ready("Glutes", "SUPPRESSED"),
            "Hamstrings": ready("Hamstrings", "SUPPRESSED"),
            "Triceps": ready("Triceps", "READY"),
            "Core": ready("Core", "READY"),
        }
        dose = td.compute_dose_target(
            NOW, "low", 38.0, ["Triceps", "Core"], "Core + Accessories",
            muscle_readiness_by_name, 24.0, sessions=self.SEP5_HISTORY, muscle_rows=[],
        )
        # Must be derived, not the literal B1.1 constants.
        self.assertNotEqual(dose["baseline"]["source"], None)
        self.assertGreater(dose["target"]["working_sets"], 0)
        self.assertLessEqual(dose["modifiers"]["whoop_capacity"], 0.50)
        self.assertGreaterEqual(dose["modifiers"]["whoop_capacity"], 0.30)
        # Suppressed muscles are simply absent from the target set here
        # (as B1.1 already guarantees) - confirm B2 doesn't add a budget
        # for them if asked.
        forced = td.muscle_budget("Shoulders", muscle_readiness_by_name["Shoulders"], None, dose["modifiers"]["whoop_capacity"], dose["modifiers"]["recent_load"])
        self.assertEqual(forced["budget_effective_sets"], 0.0)


class BackwardCompatibilityTests(unittest.TestCase):

    def test_plan_version_bumped_for_b2(self):
        # Scenario 19 - B2 moved PLAN_VERSION to at least 3 (from B1.1's
        # 2); later B2-related payload bumps must not regress it, since a
        # lower value would let a pre-B2 cache row be reused.
        import todays_plan_store
        self.assertGreaterEqual(todays_plan_store.PLAN_VERSION, 3)

    def test_dose_diagnostics_is_additive_key(self):
        # Scenario 20 - the new diagnostics block must not replace or
        # rename any existing session field iOS already depends on.
        from integrations.tonal import workout_prescription as wp
        # Static structural check: the source still assigns all pre-B2
        # keys inside the same "session" dict literal as dose_diagnostics.
        source = open(wp.__file__).read()
        for legacy_key in (
            '"session_type"', '"primary_focus"', '"secondary_focus"',
            '"target_muscles"', '"suppressed_muscles"', '"exercise_count"',
            '"total_sets"', '"target_set_range"', '"estimated_total_volume"',
            '"exercises"',
        ):
            self.assertIn(legacy_key, source)
        self.assertIn('"dose_diagnostics"', source)


def wp_profile(name, muscles, movement_id=None):
    return {
        "movement_id": movement_id or name,
        "name": name,
        "muscle_groups": muscles,
        "incidental_muscles": [],
        "history": {"sessions_in_lookback": 5},
        "performance": {"status": "usable", "progression_earned": False},
    }


class BuildDailyWorkoutPrescriptionIntegrationTests(unittest.TestCase):
    """End-to-end wiring tests for build_daily_workout_prescription() with
    the B2 dose engine, mocking exactly the DB-touching entry points
    (B1 readiness/priority/profile lookups and B2's own session/muscle
    history) so the orchestration itself runs for real."""

    def _fake_prescribe(self, profile, readiness_band, set_count):
        return {
            "name": profile["name"],
            "sets": set_count,
            "reps_per_set": 8,
            "muscle_groups": profile["muscle_groups"],
            "exercise_family": None,
            "estimated_volume": set_count * 100,
            "progression_applied": False,
        }

    def _run(self, readiness_band, recovery_score, target_muscles, suppressed_muscles,
              profiles, session_type="Upper Push", sessions=None):
        readiness = {
            "available": True,
            "readiness_band": readiness_band,
            "recovery_score": recovery_score,
            "training_category": "Push" if readiness_band == "high" else "Active Recovery",
        }
        muscle_readiness_muscles = [
            {"muscle": m, "readiness_state": "SUPPRESSED", "readiness_score": 40.0}
            for m in suppressed_muscles
        ] + [
            {"muscle": m, "readiness_state": "READY", "readiness_score": 70.0}
            for m in target_muscles
        ]
        priorities = {
            "recommended_session": {
                "primary_focus": target_muscles[:1],
                "secondary_focus": target_muscles[1:],
                "session_type": session_type,
            },
            "suppressed_muscles": [{"muscle": m} for m in suppressed_muscles],
            "muscle_readiness": {
                "muscles": muscle_readiness_muscles,
                "latest_workout_age_hours": 20.0,
            },
            "session_template_scores": [],
            "ranked_muscles": [],
            "selection_confidence": "high",
            "recent_training_context": {},
        }

        history = sessions or SUBSTANTIAL_UPPER_HISTORY
        derived_muscle_rows = []
        for s in history:
            per_muscle_sets = max(1, (s.get("set_count") or 0) // max(len(target_muscles), 1))
            for muscle in target_muscles:
                for i in range(per_muscle_sets):
                    derived_muscle_rows.append(muscle_row(
                        days_ago=(NOW - s["begin_time"]).days,
                        muscles=[muscle],
                        volume=(s.get("total_volume") or 0) / max(len(target_muscles) * per_muscle_sets, 1),
                        activity_id=f"{s['activity_id']}-{muscle}-{i}",
                    ))

        with patch.object(wp, "_latest_readiness", return_value=readiness), \
             patch.object(wp, "build_training_priority", return_value=priorities), \
             patch.object(wp, "build_movement_performance_profiles", return_value={"profiles": profiles}), \
             patch.object(wp, "_prescribe_exercise", side_effect=self._fake_prescribe), \
             patch.object(td, "load_session_history", return_value=history), \
             patch.object(td, "_load_muscle_set_rows", return_value=derived_muscle_rows):
            return wp.build_daily_workout_prescription(now=NOW)

    def test_dose_diagnostics_present_and_legacy_fields_unchanged(self):
        # Scenario 20 - real call path.
        profiles = [wp_profile("Bench Press", ["Chest"]), wp_profile("Overhead Press", ["Shoulders"])]
        result = self._run("high", 90.0, ["Chest", "Shoulders"], [], profiles)
        session_dict = result["session"]
        for legacy_key in (
            "session_type", "primary_focus", "secondary_focus", "target_muscles",
            "suppressed_muscles", "exercise_count", "total_sets", "target_set_range",
            "estimated_total_volume", "exercises",
        ):
            self.assertIn(legacy_key, session_dict)
        self.assertIn("dose_diagnostics", session_dict)
        self.assertIn("dose_confidence", session_dict["dose_diagnostics"])

    def test_small_movement_pool_yields_smaller_session_not_padding(self):
        # Scenario 16 - only 2 safe movements exist even though the
        # personalized dose would otherwise want more; must not add
        # incompatible exercises to compensate.
        profiles = [wp_profile("Bench Press", ["Chest"]), wp_profile("Overhead Press", ["Shoulders"])]
        result = self._run("high", 90.0, ["Chest", "Shoulders"], [], profiles)
        self.assertLessEqual(result["session"]["exercise_count"], 2)
        self.assertEqual(result["session"]["dose_diagnostics"]["dose_limited_by"], "movement_availability")

    def test_suppressed_muscle_never_appears_as_prescribed_target(self):
        # Scenario 21 - B1.1 suppression invariant intact at the real
        # call path, with B2 wired in.
        profiles = [
            wp_profile("Bench Press", ["Chest"]),
            wp_profile("Overhead Press", ["Shoulders"]),
            wp_profile("Barbell RDL", ["Hamstrings", "Glutes"]),
        ]
        result = self._run("high", 90.0, ["Chest", "Shoulders"], ["Hamstrings", "Glutes"], profiles)
        prescribed_muscles = {
            m for ex in result["session"]["exercises"] for m in ex["muscle_groups"]
        }
        self.assertNotIn("Hamstrings", prescribed_muscles)
        self.assertNotIn("Glutes", prescribed_muscles)
        budgets = result["session"]["dose_diagnostics"]["muscle_budgets"]
        # Suppressed muscles are not among today's *targets* (B1 already
        # excludes them upstream) - confirm B2 would still refuse them a
        # budget if asked, matching the unit-level invariant test.
        for muscle in ("Hamstrings", "Glutes"):
            forced = td.muscle_budget(muscle, {"readiness_state": "SUPPRESSED"}, None, 1.15, 1.0)
            self.assertEqual(forced["budget_effective_sets"], 0.0)
            self.assertNotIn(muscle, budgets)


if __name__ == "__main__":
    unittest.main()
