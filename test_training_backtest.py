import unittest

from training_backtest_metrics import aggregate


class AggregateMetricsTests(unittest.TestCase):
    def test_aggregate_keeps_observational_metrics_structured(self):
        day = {
            "replay_date": "2026-07-15",
            "data_quality": {"status": "COMPLETE", "missing_sources": []},
            "recommendation": {"training_status": "Normal", "session_type": "Upper Body",
                               "selected_muscles": ["Chest"], "target_sets": 12,
                               "exercises": [{"progression_state": "HOLD"}],
                               "activity_plan": {"step_target": 7000}},
            "inputs": {"training": {"ranked_muscles": [{"muscle": "Chest", "readiness_state": "READY"}]},
                       "whoop": {"readiness_band": "good"}},
            "actual_outcome": {"workouts": [{"muscles": ["Chest"]}], "actual_steps": 7200},
        }
        result = aggregate([day])
        self.assertEqual(1, result["coverage"]["evaluable_days"])
        self.assertEqual(1, result["coverage"]["complete_days"])
        self.assertEqual({"READY": 1}, result["muscle_selection"]["readiness_state_distribution"])
        self.assertEqual({"HOLD": 1}, result["progression_state_distribution"])
        self.assertEqual(1.0, result["activity"]["target_attainment_rate"])


if __name__ == "__main__":
    unittest.main()
