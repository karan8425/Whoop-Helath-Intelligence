import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

import training_replay
from training_replay import ReplayContext, replay_day


def _training(now, sources):
    cutoff = now
    tonal = [row for row in sources["tonal"] if row["at"] <= cutoff]
    whoop = [row for row in sources["whoop"] if row["at"] <= cutoff]
    body = [row for row in sources["body"] if row["at"] <= cutoff]
    return {
        "status": "ok",
        "generated_at": now.isoformat(),
        "readiness": {"recovery_score": whoop[-1]["score"], "readiness_band": "good", "training_category": "Normal"},
        "session": {"session_type": "Upper Body", "primary_focus": ["Chest"], "secondary_focus": ["Triceps"],
                    "exercise_count": 1, "total_sets": len(tonal) + 3,
                    "estimated_total_volume": body[-1]["weight"] * 10, "exercises": []},
    }


class ReplayIsolationTests(unittest.TestCase):
    def setUp(self):
        self.context = ReplayContext.morning(date(2026, 7, 15))
        before = datetime(2026, 7, 14, tzinfo=timezone.utc)
        self.sources = {
            "tonal": [{"at": before}],
            "whoop": [{"at": before, "score": 72}],
            "body": [{"at": before, "weight": 180}],
            "apple": [{"at": before, "steps": 7000}],
            "goals": [{"at": before, "daily_step_target": 7000}],
        }

    def run_replay(self):
        with patch.object(training_replay, "_data_quality", return_value={"status": "COMPLETE", "sources": {}, "missing_sources": []}), \
             patch.object(training_replay, "get_active_goal", side_effect=lambda as_of: [row for row in self.sources["goals"] if row["at"] <= as_of][-1]), \
             patch.object(training_replay, "build_daily_workout_prescription", side_effect=lambda now: _training(now, self.sources)), \
             patch.object(training_replay, "load_activity_context", side_effect=lambda now: {"today_row": True, "steps_today": 0, "avg_14": [row for row in self.sources["apple"] if row["at"] <= now.astimezone(timezone.utc)][-1]["steps"], "n_14": 14}), \
             patch.object(training_replay, "build_activity_plan", side_effect=lambda **kwargs: {"step_target": kwargs["goal"]["daily_step_target"]}), \
             patch.object(training_replay, "_actual_outcome", return_value={"workout_performed": False, "workouts": [], "actual_steps": 7000}):
            return replay_day(self.context, include_details=False)

    def test_future_tonal_whoop_body_and_apple_data_do_not_change_recommendation(self):
        first = self.run_replay()["recommendation"]
        future = datetime(2026, 7, 16, tzinfo=timezone.utc)
        self.sources["tonal"].append({"at": future})
        self.sources["whoop"].append({"at": future, "score": 99})
        self.sources["body"].append({"at": future, "weight": 999})
        self.sources["apple"].append({"at": future, "steps": 99999})
        self.sources["goals"].append({"at": future, "daily_step_target": 15000})
        self.assertEqual(first, self.run_replay()["recommendation"])

    def test_frozen_replay_clock_is_deterministic(self):
        self.assertEqual(self.run_replay()["recommendation"], self.run_replay()["recommendation"])

    def test_outcome_is_not_passed_to_recommendation_engine(self):
        with patch.object(training_replay, "_actual_outcome") as outcome:
            outcome.return_value = {"workout_performed": True, "workouts": [], "actual_steps": 1}
            with patch.object(self, "run_replay", wraps=self.run_replay):
                pass
        self.assertEqual(self.context.as_of.hour, 11)  # 07:00 EDT expressed in UTC.

    def test_context_uses_new_york_morning_cutoff(self):
        self.assertEqual("2026-07-15T11:00:00+00:00", self.context.as_of.isoformat())


class QueryBoundaryTests(unittest.TestCase):
    def test_whoop_loader_has_availability_cutoff(self):
        from integrations.tonal import workout_prescription
        executed = []

        class Cursor:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def execute(self, sql, params): executed.append((" ".join(sql.split()), params))
            def fetchone(self): return None
        class Connection:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def cursor(self): return Cursor()

        cutoff = datetime(2026, 7, 15, 11, tzinfo=timezone.utc)
        with patch.object(workout_prescription, "get_conn", return_value=Connection()):
            workout_prescription._latest_readiness(now=cutoff)
        self.assertIn("source_updated_at <= %s", executed[0][0])
        self.assertEqual((cutoff.date(), cutoff), executed[0][1])


if __name__ == "__main__":
    unittest.main()
