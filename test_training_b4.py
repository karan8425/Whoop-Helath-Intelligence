import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from activity_plan import build_activity_plan, calculate_step_target


EASTERN = ZoneInfo("America/New_York")


def context(steps=2500, runs=0, aerobic=4, resting=60, maximum=190):
    return {"today_row": datetime(2026, 9, 7).date(), "steps_today": steps,
            "avg_7": 5000, "avg_14": 4800, "avg_30": 4500, "avg_90": 4200,
            "n_7": 7, "n_14": 14, "n_30": 30, "n_90": 90,
            "strength_avg": 5200, "non_strength_avg": 4100,
            "aerobic_30": aerobic, "runs_30": runs,
            "resting_hr": resting, "profile_max_hr": maximum,
            "observed_max_hr": maximum - 5 if maximum is not None else None}


def strength(recovery=38, session="Lower Body", available=True, signal="PLAN_WORKING"):
    return {"available": available, "recovery_score": recovery, "session_type": session,
            "total_sets": 8, "training_b3": {"body_composition_strategy": {
                "training_strategy_signal": signal}}}


class TrainingB4Tests(unittest.TestCase):
    def plan(self, **kwargs):
        return build_activity_plan(goal={"phase": "lean_cut", "daily_step_target": 9000},
                                   context=kwargs.pop("context", context()),
                                   strength=kwargs.pop("strength", strength()),
                                   now=kwargs.pop("now", datetime(2026, 9, 7, 10, tzinfo=EASTERN)))

    def test_personalized_target_is_bounded_progression(self):
        out = calculate_step_target(context(), {"phase": "lean_cut", "daily_step_target": 12000}, "moderate")
        self.assertLessEqual(out["recommended"], 5500)

    def test_steps_remaining(self):
        p=self.plan(); self.assertEqual(p["steps_remaining"], max(0, p["step_target"] - 2500))
    def test_above_target_has_no_session(self):
        p = self.plan(context=context(8000)); self.assertEqual(p["steps_remaining"], 0); self.assertEqual(p["sessions"], [])
    def test_low_recovery_avoids_jog(self): self.assertNotIn("JOG", {s["modality"] for s in self.plan(context=context(runs=8))["sessions"]})
    def test_high_recovery_can_jog_with_history(self):
        p = self.plan(context=context(500, runs=4), strength=strength(90, "Upper Push"),
                      now=datetime(2026, 9, 7, 17, tzinfo=EASTERN)); self.assertIn("ZONE2_JOG", {s["modality"] for s in p["sessions"]})
    def test_lower_body_avoids_jog(self):
        p = self.plan(context=context(500, runs=4), strength=strength(90, "Lower Body")); self.assertNotIn("JOG", {s["modality"] for s in p["sessions"]})
    def test_upper_body_allows_zone2(self):
        p = self.plan(context=context(500), strength=strength(90, "Upper Push")); self.assertTrue(any(s["modality"].startswith("ZONE2") for s in p["sessions"]))
    def test_rest_day_can_receive_larger_walk(self):
        rest = self.plan(context=context(0), strength=strength(90, None, False))
        lower = self.plan(context=context(0), strength=strength(90, "Lower Body"))
        self.assertGreaterEqual(sum(s["duration_minutes"] for s in rest["sessions"]), sum(s["duration_minutes"] for s in lower["sessions"]))
    def test_running_requires_history(self): self.assertFalse(self.plan()["conditioning_history"]["running_supported"])
    def test_running_supported(self): self.assertTrue(self.plan(context=context(runs=2))["conditioning_history"]["running_supported"])
    def test_zone2_hr_available(self):
        p=self.plan(context=context(500)); self.assertEqual(p["sessions"][0]["target_hr_range"], None)
    def test_zone2_hr_personalized_when_used(self):
        p = self.plan(context=context(500), strength=strength(90, "Upper Push")); self.assertEqual(p["sessions"][0]["target_hr_range"]["minimum"], 138)
    def test_zone2_talk_test_without_hr(self):
        c=context(500, resting=None, maximum=None); p=self.plan(context=c, strength=strength(90,"Upper Push")); self.assertEqual(p["zone2_methodology"]["methodology"], "talk test")
    def test_late_large_gap_is_not_chased(self):
        p=self.plan(context=context(200), now=datetime(2026,9,7,21,tzinfo=EASTERN)); self.assertIsNotNone(p["late_day_guidance"]); self.assertLessEqual(sum(s["duration_minutes"] for s in p["sessions"]),20)
    def test_plan_working_does_not_escalate_unbounded(self): self.assertLessEqual(self.plan()["step_target"], 5500)
    def test_lean_mass_risk_caps_at_baseline(self):
        p=self.plan(strength=strength(70,"Upper Push",True,"LEAN_MASS_RISK")); self.assertLessEqual(p["step_target"],4800)
    def test_nutrition_is_not_intake(self): self.assertTrue(self.plan()["goal_context"]["nutrition_is_prescription_not_intake"])
    def test_regional_fat_is_not_an_input(self): self.assertNotIn("regional_fat", build_activity_plan.__code__.co_names)


if __name__ == "__main__": unittest.main()
