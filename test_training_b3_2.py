import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from integrations.tonal import training_priority as tp


MUSCLES = ("Chest", "Back", "Shoulders", "Biceps", "Triceps", "Core", "Glutes", "Hamstrings", "Quads")


def analytics(gaps=None):
    gaps = gaps or {}
    muscle_rows = {muscle: {"primary_sessions": 1, "secondary_sessions": 0,
                            "primary_sets": 6, "secondary_sets": 0,
                            "days_since_primary_training": gaps.get(muscle, 3),
                            "last_primary_trained_at": None}
                   for muscle in MUSCLES}
    return {"windows": {"7": {"muscles": muscle_rows}}, "latest_strength_scores": None}


def readiness(states=None):
    states = states or {}
    return {"selection_confidence": "high", "latest_workout_age_hours": 12,
            "latest_tonal_workout_at": "2026-07-14T11:00:00+00:00",
            "muscles": [{"muscle": muscle, "readiness_state": states.get(muscle, "READY"),
                         "readiness_score": 80, "effective_sets_7d": 0}
                        for muscle in MUSCLES]}


class ProgramBalanceCalibrationTests(unittest.TestCase):
    def priority(self, gaps=None, states=None, history=None, calibration="balanced"):
        with patch.object(tp, "strength_analytics", return_value=analytics(gaps)), \
             patch.object(tp, "calculate_muscle_readiness", return_value=readiness(states)):
            return tp.build_training_priority(
                now=datetime(2026, 7, 15, 11, tzinfo=timezone.utc),
                recommendation_history=history or [], calibration=calibration)

    def test_neglect_accumulates_smoothly_and_is_bounded(self):
        ten = self.priority({"Back": 10})
        twenty = self.priority({"Back": 20})
        score10 = next(row for row in ten["ranked_muscles"] if row["muscle"] == "Back")["program_coverage_score"]
        score20 = next(row for row in twenty["ranked_muscles"] if row["muscle"] == "Back")["program_coverage_score"]
        self.assertGreater(score20, score10)
        self.assertLess(score20, tp.CALIBRATION_PROFILES["balanced"]["coverage_max"])

    def test_actual_training_reduces_coverage_pressure(self):
        neglected = self.priority({"Back": 20})
        trained = self.priority({"Back": 1})
        def score(result):
            return next(row for row in result["ranked_muscles"] if row["muscle"] == "Back")["program_coverage_score"]
        self.assertGreater(score(neglected), score(trained))

    def test_repeat_overlap_favors_equally_suitable_alternative(self):
        ranked = [{"muscle": muscle, "priority_score": 100} for muscle in MUSCLES]
        history = [{"focus": "Lower Body", "selected_muscles": ["Glutes", "Hamstrings", "Quads"]}]
        scores = tp._score_session_templates(ranked, readiness(), history, tp.CALIBRATION_PROFILES["balanced"])
        lower = next(row for row in scores if row["session_type"] == "Lower Body")
        upper = next(row for row in scores if row["session_type"] == "Upper Push")
        self.assertLess(lower["score"], upper["score"])

    def test_no_hard_rotation_when_only_prior_region_is_safe(self):
        states = {muscle: "FATIGUED" for muscle in MUSCLES}
        states.update({"Glutes": "READY", "Hamstrings": "READY", "Quads": "READY"})
        result = self.priority(states=states, history=[{"focus": "Lower Body", "selected_muscles": ["Glutes", "Hamstrings", "Quads"]}])
        self.assertEqual("Lower Body", result["recommended_session"]["session_type"])

    def test_fatigued_and_suppressed_never_become_template_eligible(self):
        ranked = [{"muscle": muscle, "priority_score": 9999} for muscle in MUSCLES]
        states = readiness({"Back": "SUPPRESSED", "Biceps": "FATIGUED"})
        scores = tp._score_session_templates(ranked, states, [], tp.CALIBRATION_PROFILES["balanced"])
        pull = next(row for row in scores if row["session_type"] == "Upper Pull")
        self.assertFalse(pull["eligible"])

    def test_ready_beats_recovering_when_need_is_comparable(self):
        result = self.priority(states={"Back": "READY", "Glutes": "RECOVERING"})
        rows = {row["muscle"]: row for row in result["ranked_muscles"]}
        self.assertGreater(rows["Back"]["priority_score"], rows["Glutes"]["priority_score"])

    def test_recovering_can_win_with_materially_stronger_need(self):
        result = self.priority(gaps={"Glutes": 25, "Back": 1},
                               states={"Glutes": "RECOVERING", "Back": "READY"})
        rows = {row["muscle"]: row for row in result["ranked_muscles"]}
        self.assertGreater(rows["Glutes"]["program_coverage_score"], rows["Back"]["program_coverage_score"])


if __name__ == "__main__":
    unittest.main()
