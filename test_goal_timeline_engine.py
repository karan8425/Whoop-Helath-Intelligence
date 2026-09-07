"""Deterministic Goal Setting V2 timeline + compatibility engine."""

import math
import unittest
from datetime import date, timedelta

import goal_pace_config as cfg
import goal_timeline_engine as e


CUR = {"weight_lb": 184.0, "body_fat_percentage": 22.0}


def _t(w=175.0, bf=15.0):
    return {"target_weight_lb": w, "target_body_fat_percentage": bf}


class CompatibilityTests(unittest.TestCase):

    def test_compatible_targets(self):
        # 184@22 -> lean 143.5 ; 172@19 -> lean 139.3 ; delta ~ -4.2 => borderline.
        # pick a target with matched lean mass:
        c = e.compatibility_check(CUR, {"target_weight_lb": 172.0,
                                        "target_body_fat_percentage": 16.5})
        self.assertEqual(c["state"], "compatible")
        self.assertIsNotNone(c["current"]["lean_mass_lb"])
        self.assertIsNotNone(c["target_implied"]["lean_mass_lb"])

    def test_target_implies_lean_mass_loss(self):
        c = e.compatibility_check(CUR, {"target_weight_lb": 150.0,
                                        "target_body_fat_percentage": 18.0})
        self.assertEqual(c["state"], "potential_lean_mass_loss")
        self.assertLess(c["lean_mass_delta_lb"], -cfg.LEAN_MASS_TOLERANCE_LB)

    def test_target_implies_lean_mass_gain(self):
        c = e.compatibility_check(CUR, {"target_weight_lb": 185.0,
                                        "target_body_fat_percentage": 12.0})
        self.assertEqual(c["state"], "requires_lean_mass_gain")
        self.assertGreater(c["lean_mass_delta_lb"], cfg.LEAN_MASS_TOLERANCE_LB)

    def test_missing_body_fat_is_insufficient_data(self):
        c = e.compatibility_check({"weight_lb": 184.0}, _t())
        self.assertEqual(c["state"], "insufficient_data")

    def test_missing_target_weight_is_insufficient_data(self):
        c = e.compatibility_check(CUR, {"target_body_fat_percentage": 15.0})
        self.assertEqual(c["state"], "insufficient_data")

    def test_mathematically_inconsistent_target(self):
        c = e.compatibility_check(CUR, {"target_weight_lb": 175.0,
                                        "target_body_fat_percentage": 75.0})
        self.assertEqual(c["state"], "mathematically_inconsistent")


class TimelineOptionTests(unittest.TestCase):

    def test_three_bands_present_and_ordered(self):
        tl = e.timeline_options(CUR, _t())
        for band in ("comfortable", "recommended", "faster"):
            self.assertIn(band, tl["options"])
        weeks = [tl["options"][b]["estimated_weeks"]
                 for b in ("comfortable", "recommended", "faster")]
        self.assertGreaterEqual(weeks[0], weeks[1])
        self.assertGreaterEqual(weeks[1], weeks[2])

    def test_comfortable_rate_matches_config(self):
        tl = e.timeline_options(CUR, _t())
        self.assertEqual(
            tl["options"]["comfortable"]["weekly_rate_percent"],
            round(cfg.COMFORTABLE_WEEKLY_RATE * 100, 2),
        )

    def test_date_calculation_correctness(self):
        tl = e.timeline_options(CUR, _t())
        opt = tl["options"]["recommended"]
        expected_weeks = math.ceil(
            abs(175.0 - 184.0) / (cfg.RECOMMENDED_WEEKLY_RATE * 184.0)
        )
        self.assertEqual(opt["estimated_weeks"], expected_weeks)
        expected_date = (date.today() + timedelta(days=expected_weeks * 7)).isoformat()
        self.assertEqual(opt["estimated_target_date"], expected_date)

    def test_weekly_rate_calculation(self):
        tl = e.timeline_options(CUR, _t())
        opt = tl["options"]["recommended"]
        self.assertAlmostEqual(
            abs(opt["required_average_weekly_change_lb"]) * opt["estimated_weeks"],
            9.0, delta=0.5,
        )

    def test_priority_shifts_recommended_band(self):
        self.assertEqual(
            e.timeline_options(CUR, _t(), "preserve_build")["recommended_band"],
            "comfortable",
        )
        self.assertEqual(
            e.timeline_options(CUR, _t(), "balanced")["recommended_band"],
            "recommended",
        )
        self.assertEqual(
            e.timeline_options(CUR, _t(), "faster")["recommended_band"],
            "faster",
        )

    def test_missing_target_weight_is_insufficient(self):
        tl = e.timeline_options(CUR, {"target_body_fat_percentage": 15.0})
        self.assertEqual(tl["status"], "insufficient_data")

    def test_trivial_change_reads_as_maintain(self):
        tl = e.timeline_options(CUR, _t(w=184.3))
        self.assertEqual(tl["status"], "maintain")


class CustomDateTests(unittest.TestCase):

    def test_custom_date_inside_supported_range(self):
        far = (date.today() + timedelta(days=300)).isoformat()
        r = e.evaluate_custom_date(CUR, _t(), far)
        self.assertIn(r["timeline_status"],
                      ("comfortable", "recommended", "faster"))
        self.assertIsNotNone(r["required_weekly_change_lb"])
        self.assertLess(r["required_weekly_change_lb"], 0)  # loss

    def test_custom_date_outside_supported_range_returns_guardrail(self):
        # 9 lb over ~3 weeks -> ~1.6%/wk -> outside ceiling
        soon = (date.today() + timedelta(days=21)).isoformat()
        r = e.evaluate_custom_date(CUR, _t(), soon)
        self.assertEqual(r["timeline_status"], "outside_supported_range")
        self.assertIn("faster rate of change than this plan supports",
                      r["message"])
        self.assertIsNotNone(r["recommended_target_date"])

    def test_faster_but_supported_band(self):
        # tune a date that lands between FASTER and MAXIMUM
        weeks = 9.0 / (0.010 * 184.0)          # ~1.0%/week target rate
        d = (date.today() + timedelta(days=int(weeks * 7))).isoformat()
        r = e.evaluate_custom_date(CUR, _t(), d)
        self.assertIn(r["timeline_status"],
                      ("faster", "faster_but_supported"))

    def test_past_date_rejected(self):
        past = (date.today() - timedelta(days=5)).isoformat()
        r = e.evaluate_custom_date(CUR, _t(), past)
        self.assertEqual(r["timeline_status"], "outside_supported_range")

    def test_missing_data_custom_date(self):
        r = e.evaluate_custom_date({"weight_lb": 184.0}, {}, "2027-01-01")
        self.assertEqual(r["timeline_status"], "insufficient_data")

    def test_maximum_rate_guardrail_is_the_ceiling(self):
        # exactly at the ceiling -> supported; just past -> not
        w = CUR["weight_lb"]
        weeks_at_ceiling = 9.0 / (cfg.MAXIMUM_SUPPORTED_WEEKLY_RATE * w)
        at = (date.today() + timedelta(days=math.ceil(weeks_at_ceiling * 7) + 1)).isoformat()
        past = (date.today() + timedelta(days=math.floor(weeks_at_ceiling * 7) - 2)).isoformat()
        self.assertNotEqual(
            e.evaluate_custom_date(CUR, _t(), at)["timeline_status"],
            "outside_supported_range",
        )
        self.assertEqual(
            e.evaluate_custom_date(CUR, _t(), past)["timeline_status"],
            "outside_supported_range",
        )


class PreviewShapeTests(unittest.TestCase):

    def test_preview_payload_shape(self):
        p = e.build_preview(goal_type="lose_body_fat", current=CUR, target=_t(),
                            lean_mass_priority="preserve_build")
        for key in ("goal_type", "current", "target", "compatibility",
                    "timeline_options", "recommended_band", "custom_timeline",
                    "methodology"):
            self.assertIn(key, p)
        self.assertIsNone(p["custom_timeline"])
        self.assertEqual(p["recommended_band"], "comfortable")

    def test_preview_with_custom_date_populates_custom_timeline(self):
        d = (date.today() + timedelta(days=250)).isoformat()
        p = e.build_preview(goal_type="lose_body_fat", current=CUR, target=_t(),
                            custom_target_date=d)
        self.assertIsNotNone(p["custom_timeline"])
        self.assertEqual(p["custom_timeline"]["chosen_date"], d)


if __name__ == "__main__":
    unittest.main()
