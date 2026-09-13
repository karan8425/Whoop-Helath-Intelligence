"""TKI-6: feature-flag resolution tests (training_engine_flag.py)."""

import unittest
from unittest.mock import patch

from training_engine_flag import resolve_training_prescription_engine, ENGINE_B3, ENGINE_TKI


class TrainingEngineFlagTests(unittest.TestCase):
    def test_missing_env_var_defaults_to_b3(self):
        with patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("TRAINING_PRESCRIPTION_ENGINE", None)
            self.assertEqual(resolve_training_prescription_engine(), ENGINE_B3)

    def test_explicit_b3(self):
        with patch.dict("os.environ", {"TRAINING_PRESCRIPTION_ENGINE": "b3"}):
            self.assertEqual(resolve_training_prescription_engine(), ENGINE_B3)

    def test_explicit_tki(self):
        with patch.dict("os.environ", {"TRAINING_PRESCRIPTION_ENGINE": "tki"}):
            self.assertEqual(resolve_training_prescription_engine(), ENGINE_TKI)

    def test_case_and_whitespace_insensitive(self):
        with patch.dict("os.environ", {"TRAINING_PRESCRIPTION_ENGINE": "  TKI  "}):
            self.assertEqual(resolve_training_prescription_engine(), ENGINE_TKI)

    def test_invalid_value_falls_back_to_b3(self):
        with patch.dict("os.environ", {"TRAINING_PRESCRIPTION_ENGINE": "gpt5"}):
            self.assertEqual(resolve_training_prescription_engine(), ENGINE_B3)

    def test_invalid_value_logs_a_warning(self):
        with patch.dict("os.environ", {"TRAINING_PRESCRIPTION_ENGINE": "bogus"}):
            with patch("builtins.print") as mock_print:
                resolve_training_prescription_engine()
                self.assertTrue(mock_print.called)
                message = mock_print.call_args[0][0]
                self.assertIn("TRAINING_PRESCRIPTION_ENGINE_INVALID", message)

    def test_valid_value_does_not_log(self):
        with patch.dict("os.environ", {"TRAINING_PRESCRIPTION_ENGINE": "tki"}):
            with patch("builtins.print") as mock_print:
                resolve_training_prescription_engine()
                self.assertFalse(mock_print.called)


if __name__ == "__main__":
    unittest.main()
