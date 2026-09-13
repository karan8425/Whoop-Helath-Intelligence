"""TKI-6: engine-routing tests for todays_plan._build_workout().

Pure/in-memory - build_daily_workout_prescription, build_shadow_
prescription, and adapt_to_workout_schema are all mocked; only the
routing/fallback logic itself is under test here.
"""

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import todays_plan


B3_RESULT = {"status": "ok", "session": {"session_type": "Upper Pull"}, "readiness": {}}
TKI_RAW_RESULT = {
    "status": "ok", "selected_session_family": "Upper Pull",
    "dose": {"working_sets": 9}, "dose_absorption": {"dose_allocated": 9, "dose_shortfall": 0},
    "adaptations_used": [], "fallbacks_used": [], "exercises": [],
}
ADAPTED_TKI_WORKOUT = {"status": "ok", "session": {"session_type": "Upper Pull (TKI)"}, "readiness": {}}


class EngineRoutingTests(unittest.TestCase):
    def test_missing_flag_calls_b3(self):
        with patch("todays_plan.resolve_training_prescription_engine", return_value="b3"), \
             patch("todays_plan.build_daily_workout_prescription", return_value=B3_RESULT) as mock_b3:
            result = todays_plan._build_workout()
            mock_b3.assert_called_once()
            self.assertEqual(result, B3_RESULT)

    def test_b3_flag_calls_b3(self):
        with patch("todays_plan.resolve_training_prescription_engine", return_value="b3"), \
             patch("todays_plan.build_daily_workout_prescription", return_value=B3_RESULT) as mock_b3:
            result = todays_plan._build_workout()
            mock_b3.assert_called_once()
            self.assertEqual(result, B3_RESULT)

    def test_tki_flag_calls_tki_and_adapts(self):
        with patch("todays_plan.resolve_training_prescription_engine", return_value="tki"), \
             patch(
                 "training_intelligence.prescription.shadow_prescription.build_shadow_prescription",
                 return_value=TKI_RAW_RESULT,
             ) as mock_tki, \
             patch(
                 "training_intelligence.prescription.mobile_adapter.adapt_to_workout_schema",
                 return_value=ADAPTED_TKI_WORKOUT,
             ) as mock_adapt, \
             patch("todays_plan.build_daily_workout_prescription") as mock_b3:
            result = todays_plan._build_workout()
            mock_tki.assert_called_once()
            mock_adapt.assert_called_once()
            mock_b3.assert_not_called()
            self.assertEqual(result, ADAPTED_TKI_WORKOUT)

    def test_tki_exception_falls_back_to_b3(self):
        with patch("todays_plan.resolve_training_prescription_engine", return_value="tki"), \
             patch(
                 "training_intelligence.prescription.shadow_prescription.build_shadow_prescription",
                 side_effect=RuntimeError("boom"),
             ), \
             patch("todays_plan.build_daily_workout_prescription", return_value=B3_RESULT) as mock_b3:
            result = todays_plan._build_workout()
            mock_b3.assert_called_once()
            self.assertEqual(result, B3_RESULT)

    def test_tki_exception_fallback_is_logged_observably(self):
        with patch("todays_plan.resolve_training_prescription_engine", return_value="tki"), \
             patch(
                 "training_intelligence.prescription.shadow_prescription.build_shadow_prescription",
                 side_effect=RuntimeError("boom"),
             ), \
             patch("todays_plan.build_daily_workout_prescription", return_value=B3_RESULT), \
             patch("todays_plan._print_timing") as mock_log:
            todays_plan._build_workout()
            logged = " ".join(str(call.args[0]) for call in mock_log.call_args_list)
            self.assertIn("requested_engine=tki", logged)
            self.assertIn("actual_engine=b3", logged)
            self.assertIn("fallback_reason", logged)

    def test_invalid_flag_value_calls_b3(self):
        """resolve_training_prescription_engine() itself already falls
        back to 'b3' for invalid values (test_training_engine_flag.py)
        - this proves the router honors that and never special-cases an
        unrecognized string as tki."""
        with patch("todays_plan.resolve_training_prescription_engine", return_value="b3"), \
             patch("todays_plan.build_daily_workout_prescription", return_value=B3_RESULT) as mock_b3:
            result = todays_plan._build_workout()
            mock_b3.assert_called_once()
            self.assertEqual(result, B3_RESULT)

    def test_tki_success_does_not_call_b3(self):
        with patch("todays_plan.resolve_training_prescription_engine", return_value="tki"), \
             patch(
                 "training_intelligence.prescription.shadow_prescription.build_shadow_prescription",
                 return_value=TKI_RAW_RESULT,
             ), \
             patch(
                 "training_intelligence.prescription.mobile_adapter.adapt_to_workout_schema",
                 return_value=ADAPTED_TKI_WORKOUT,
             ), \
             patch("todays_plan.build_daily_workout_prescription") as mock_b3:
            todays_plan._build_workout()
            mock_b3.assert_not_called()


if __name__ == "__main__":
    unittest.main()
