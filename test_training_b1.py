import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch


db_stub = types.ModuleType("db")
db_stub.get_conn = Mock()
sys.modules.setdefault("db", db_stub)

from integrations.tonal.muscle_readiness import calculate_muscle_readiness
from integrations.tonal.training_priority import (
    _score_session_templates,
    _session_from_templates,
)
from integrations.tonal import daily_sync


NOW = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
MUSCLES = ("Chest", "Back", "Shoulders", "Biceps", "Triceps", "Core", "Glutes", "Hamstrings", "Quads")


def rows(muscles, sets, hours, *, included=True, supplemental=False, volume=500):
    return [
        {
            "activity_id": f"a-{hours}",
            "begin_time": NOW - timedelta(hours=hours),
            "included": included,
            "exclusion_reason": "Atypical abbreviated freestyle session" if supplemental else None,
            "volume": volume,
            "muscle_groups": muscles,
        }
        for _ in range(sets)
    ]


def state(result, muscle):
    return next(item for item in result["muscles"] if item["muscle"] == muscle)


def readiness(states=None, confidence="high"):
    states = states or {}
    return {
        "selection_confidence": confidence,
        "muscles": [
            {
                "muscle": muscle,
                "readiness_state": states.get(muscle, "FRESH"),
                "readiness_score": 90,
            }
            for muscle in MUSCLES
        ],
    }


def ranked():
    return [{"muscle": muscle, "priority_score": 50} for muscle in MUSCLES]


class MuscleReadinessTests(unittest.TestCase):
    def test_heavy_lower_yesterday_suppresses_lower(self):
        data = rows(["Quads", "Glutes", "Hamstrings"], 8, 20)
        result = calculate_muscle_readiness(NOW, data, NOW - timedelta(hours=20))
        self.assertEqual(state(result, "Quads")["readiness_state"], "SUPPRESSED")

    def test_heavy_shoulders_yesterday_leaves_rested_lower_eligible(self):
        result = calculate_muscle_readiness(NOW, rows(["Shoulders", "Triceps"], 8, 20), NOW - timedelta(hours=20))
        self.assertIn(state(result, "Quads")["readiness_state"], ("FRESH", "READY"))

    def test_hamstrings_suppressed_inside_24h(self):
        result = calculate_muscle_readiness(NOW, rows(["Hamstrings", "Glutes"], 4, 12), NOW - timedelta(hours=12))
        self.assertEqual(state(result, "Hamstrings")["readiness_state"], "SUPPRESSED")

    def test_substantial_quads_suppressed_inside_48h(self):
        result = calculate_muscle_readiness(NOW, rows(["Quads", "Glutes"], 7, 36), NOW - timedelta(hours=36))
        self.assertEqual(state(result, "Quads")["readiness_state"], "SUPPRESSED")

    def test_secondary_exposure_has_smaller_penalty(self):
        result = calculate_muscle_readiness(NOW, rows(["Chest", "Shoulders"], 4, 12), NOW - timedelta(hours=12))
        self.assertGreater(state(result, "Shoulders")["readiness_score"], state(result, "Chest")["readiness_score"])

    def test_small_supplemental_session_does_not_suppress(self):
        result = calculate_muscle_readiness(NOW, rows(["Quads"], 4, 8, included=False, supplemental=True), NOW - timedelta(hours=8))
        self.assertNotEqual(state(result, "Quads")["readiness_state"], "SUPPRESSED")

    def test_no_activity_three_days_allows_balanced_selection(self):
        result = calculate_muscle_readiness(NOW, [], NOW - timedelta(days=3))
        self.assertTrue(all(item["readiness_state"] in ("FRESH", "READY") for item in result["muscles"]))

    def test_stale_history_reduces_confidence(self):
        result = calculate_muscle_readiness(NOW, [], NOW - timedelta(days=8))
        self.assertEqual(result["selection_confidence"], "low")
        self.assertTrue(all(item["readiness_state"] != "FRESH" for item in result["muscles"]))

    def test_unmapped_sets_are_ignored(self):
        result = calculate_muscle_readiness(NOW, rows([], 3, 10), NOW - timedelta(hours=10))
        self.assertEqual(result["unmapped_sets_ignored"], 3)

    def test_low_whoop_does_not_change_local_state(self):
        result = calculate_muscle_readiness(NOW, rows(["Back"], 4, 12), NOW - timedelta(hours=12))
        self.assertEqual(state(result, "Back")["readiness_state"], "SUPPRESSED")


class SessionSelectionTests(unittest.TestCase):
    def test_repeated_focus_receives_rotation_penalty(self):
        scores = _score_session_templates(ranked(), readiness(), [{"focus": "Lower Body"}, {"focus": "Lower Body"}])
        lower = next(item for item in scores if item["session_type"] == "Lower Body")
        self.assertGreater(lower["rotation_penalty"], 0)

    def test_actual_suppression_beats_recommendation_history(self):
        states = {"Glutes": "SUPPRESSED", "Hamstrings": "SUPPRESSED", "Quads": "SUPPRESSED"}
        scores = _score_session_templates(ranked(), readiness(states), [{"focus": "Upper Push"}])
        lower = next(item for item in scores if item["session_type"] == "Lower Body")
        self.assertFalse(lower["eligible"])

    def test_high_whoop_cannot_restore_suppressed_muscle(self):
        states = {"Quads": "SUPPRESSED", "Glutes": "SUPPRESSED"}
        scores = _score_session_templates(ranked(), readiness(states), [])
        self.assertFalse(next(item for item in scores if item["session_type"] == "Lower Body")["eligible"])

    def test_template_winner_is_score_driven(self):
        scores = _score_session_templates(ranked(), readiness(), [])
        winner = _session_from_templates(scores, ranked())
        self.assertIn(winner["session_type"], {item["session_type"] for item in scores if item["eligible"]})


class CacheAndContractTests(unittest.TestCase):
    def test_meaningful_sync_invalidates_today_cache(self):
        with patch.object(daily_sync, "automation_credentials", return_value=("e", "p")), patch.object(daily_sync, "_start_audit", return_value=1), patch.object(daily_sync, "_stored_latest_workout", side_effect=[None, NOW]), patch.object(daily_sync, "authenticate", return_value={"id_token": "t"}), patch.object(daily_sync, "get_user_info", return_value={"id": "u"}), patch.object(daily_sync, "get_movements", return_value=[]), patch.object(daily_sync, "get_all_workouts", return_value=[]), patch.object(daily_sync, "get_strength_history", return_value=[]), patch.object(daily_sync, "_material_training_change", return_value=True), patch.object(daily_sync, "sync_movements", return_value=0), patch.object(daily_sync, "sync_workouts_and_sets", return_value={"workouts": 0, "sets": 0}), patch.object(daily_sync, "sync_records", return_value=0), patch.object(daily_sync, "_finish_audit"), patch("todays_plan_store.invalidate_todays_plan") as invalidate:
            result = daily_sync.run_sync({})
        invalidate.assert_called_once()
        self.assertTrue(result["today_cache_invalidated"])

    def test_idempotent_sync_does_not_invalidate_cache(self):
        with patch.object(daily_sync, "automation_credentials", return_value=("e", "p")), patch.object(daily_sync, "_start_audit", return_value=1), patch.object(daily_sync, "_stored_latest_workout", return_value=NOW), patch.object(daily_sync, "authenticate", return_value={"id_token": "t"}), patch.object(daily_sync, "get_user_info", return_value={"id": "u"}), patch.object(daily_sync, "get_movements", return_value=[]), patch.object(daily_sync, "get_all_workouts", return_value=[]), patch.object(daily_sync, "get_strength_history", return_value=[]), patch.object(daily_sync, "_material_training_change", return_value=False), patch.object(daily_sync, "sync_movements", return_value=0), patch.object(daily_sync, "sync_workouts_and_sets", return_value={"workouts": 0, "sets": 0}), patch.object(daily_sync, "sync_records", return_value=0), patch.object(daily_sync, "_finish_audit"), patch("todays_plan_store.invalidate_todays_plan") as invalidate:
            result = daily_sync.run_sync({})
        invalidate.assert_not_called()
        self.assertFalse(result["today_cache_invalidated"])

    def test_today_training_card_remains_backward_compatible(self):
        session = _session_from_templates(
            _score_session_templates(ranked(), readiness(), []),
            ranked(),
        )
        self.assertIn("session_type", session)
        self.assertIn("primary_focus", session)
        self.assertIn("secondary_focus", session)

    def test_sep5_replay_lower_suppression(self):
        data = rows(["Hamstrings", "Shoulders"], 6, 23) + rows(["Glutes", "Quads"], 8, 44)
        result = calculate_muscle_readiness(NOW, data, NOW - timedelta(hours=23))
        states = {item["muscle"]: item["readiness_state"] for item in result["muscles"]}
        scores = _score_session_templates(ranked(), readiness(states), [{"focus": "Lower Body"}] * 3)
        self.assertFalse(next(item for item in scores if item["session_type"] == "Lower Body")["eligible"])


if __name__ == "__main__":
    unittest.main()
