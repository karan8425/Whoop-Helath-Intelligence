import unittest

from integrations.tonal.training_priority import _score_session_templates
from integrations.tonal.training_dose import whoop_capacity_multiplier, muscle_budget


MUSCLES = ["Chest", "Back", "Shoulders", "Biceps", "Triceps", "Core", "Glutes", "Hamstrings", "Quads"]


def readiness():
    fresh = {"Chest", "Biceps", "Core"}
    return {
        "selection_confidence": "high",
        "muscles": [{
            "muscle": m,
            "readiness_state": "FRESH" if m in fresh else "RECOVERING",
            "readiness_score": 100 if m in fresh else 72,
        } for m in MUSCLES],
    }


def ranked():
    # History still matters, but the explicit local-readiness component makes
    # a recovering muscle require strong evidence to beat a fresh candidate.
    history = {"Chest": 55, "Biceps": 55, "Back": 80, "Hamstrings": 80, "Quads": 80,
               "Shoulders": 40, "Triceps": 40, "Core": 40, "Glutes": 40}
    state_bonus = {"FRESH": 70, "RECOVERING": -25}
    states = {x["muscle"]: x["readiness_state"] for x in readiness()["muscles"]}
    rows = [{"muscle": m, "priority_score": history[m] + state_bonus[states[m]]} for m in MUSCLES]
    return sorted(rows, key=lambda x: x["priority_score"], reverse=True)


class HierarchyCorrectionTests(unittest.TestCase):
    def test_today_case_fresh_chest_biceps_beat_recovering_back_legs(self):
        scores = _score_session_templates(ranked(), readiness(), [])
        self.assertEqual(scores[0]["session_type"], "Chest + Biceps")
        self.assertGreater(scores[0]["score"], next(x["score"] for x in scores if x["session_type"] == "Lower Body"))

    def test_same_local_state_has_same_focus_for_all_whoop_states(self):
        focuses = []
        doses = []
        for band, recovery in (("high", 90), ("moderate", 55), ("low", 38)):
            focuses.append(_score_session_templates(ranked(), readiness(), [])[0]["session_type"])
            doses.append(whoop_capacity_multiplier(band, recovery))
        self.assertEqual(focuses, ["Chest + Biceps"] * 3)
        self.assertGreater(doses[0], doses[1])
        self.assertGreater(doses[1], doses[2])

    def test_whoop_not_compounded_inside_muscle_budget(self):
        baseline = {"windows": {14: {"effective_sets_per_week": 12}}}
        low = muscle_budget("Chest", {"readiness_state": "READY"}, baseline, 0.45, 0.8)
        high = muscle_budget("Chest", {"readiness_state": "READY"}, baseline, 1.15, 1.08)
        self.assertEqual(low["budget_effective_sets"], high["budget_effective_sets"])

    def test_low_recovery_floor_is_personal_baseline_fraction(self):
        self.assertGreaterEqual(whoop_capacity_multiplier("low", 38), 0.30)
        self.assertLessEqual(whoop_capacity_multiplier("low", 38), 0.50)
        self.assertLess(whoop_capacity_multiplier("low", 38), whoop_capacity_multiplier("moderate", 55))

    def test_regional_fat_is_not_an_input_to_selection(self):
        self.assertNotIn("fat", _score_session_templates.__code__.co_names)


if __name__ == "__main__":
    unittest.main()
