"""TKI-7: orchestration, mobile adapter, feature-flag routing, cache
versioning, and admin diagnostic route tests.

Pure/in-memory + mocked-DB where a live Development database is not
available, matching the established convention.
"""
import os

os.environ.setdefault("SESSION_SECRET", "test-session-secret")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")
os.environ.setdefault("TOKEN_ENCRYPTION_KEY", "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("WHOOP_CLIENT_ID", "test-client-id")
os.environ.setdefault("WHOOP_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("WHOOP_REDIRECT_URI", "https://development.example/whoop/callback")
os.environ.setdefault("APPLE_HEALTH_INGEST_KEY", "test-ingest-key")

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

import main
from training_engine_flag import (
    training_intelligence_mobile_enabled, training_intelligence_shadow_compare_enabled,
    resolve_effective_training_engine, ENGINE_B3, ENGINE_TRAINING_INTELLIGENCE,
    MOBILE_ENV_VAR_NAME, SHADOW_COMPARE_ENV_VAR_NAME,
)
from training_intelligence.calibration.mobile_adapter import adapt_calibrated_to_workout_schema


AS_OF = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)


def _calibrated_result(family="Upper Push", verdict="QUESTIONABLE"):
    return {
        "status": "ok", "as_of": AS_OF.isoformat(), "goal_mode": "general_fitness",
        "selected_session_family": family,
        "readiness": {"recovery_score": 80.0, "readiness_band": "good", "training_category": "Good"},
        "local_readiness": {"Chest": "READY", "Shoulders": "READY"},
        "personal_capacity_reference": {"source": "exact_family", "confidence": "HIGH"},
        "feasible_range": {"lower_bound_working_sets": 8, "upper_bound_working_sets": 14,
                            "binding_constraints": [{"constraint": "historical_capacity", "binding": True}]},
        "dose": {"working_sets": 10, "delivered_sets": 10, "dose_shortfall": 0, "shortfall_reason": None},
        "exercises": [
            {"movement_id": "m1", "movement_name": "Bench Press", "accessory": None,
             "primary_muscles": ["Chest"], "secondary_muscles": ["Triceps"],
             "is_bilateral": True, "is_two_sided": False, "is_alternating": False,
             "working_sets": 5, "target_reps_per_set": 8, "prescribed_resistance_lb": 60.0,
             "rep_range": {"minimum": 8, "maximum": 12}, "target_rir": {"minimum": 1, "maximum": 3},
             "rest_seconds": {"minimum": 120, "maximum": 180}, "progression_state": "HOLD",
             "progression_action": "HOLD", "progression_reason": "x", "confidence": "MEDIUM",
             "performance_state": "STABLE", "comparable_history": {},
             "progression_evidence": {"evidence_tier": 1, "exact_mode_sessions": 4, "compatible_mode_sessions": 0,
                                       "load_match_quality": "exact", "effort_quality": "HIGH"},
             "workload": {"confidence": "HIGH"}, "workload_multiplier": 1, "multiplier_source": "x",
             "estimated_volume": 2400.0},
        ],
        "estimated_total_volume": 2400.0,
        "workload_sanity_v3": {"status": "WITHIN_PERSONAL_RANGE", "confidence": "HIGH",
                                "absolute_workload_ratio": 1.0, "workload_per_set_ratio": 1.0,
                                "gap_classification": "NONE", "justification_sufficient": True,
                                "justifications": []},
        "quality_v3": {"verdict": verdict, "reasons": ["x"]},
    }


class MobileAdapterTests(unittest.TestCase):
    def test_adapts_to_b3_shape_with_ti_namespace(self):
        result = _calibrated_result()
        workout = adapt_calibrated_to_workout_schema(AS_OF, result, {"stored": True, "snapshot": {"decision_id": "abc"}})
        self.assertEqual(workout["status"], "ok")
        session = workout["session"]
        self.assertEqual(session["session_type"], "Upper Push")
        self.assertEqual(session["total_sets"], 5)
        self.assertEqual(session["estimated_total_volume"], 2400.0)
        ti = session["training_intelligence"]
        self.assertEqual(ti["engine_source"], "training_intelligence")
        self.assertEqual(ti["decision_id"], "abc")
        self.assertEqual(ti["quality_verdict"], "QUESTIONABLE")

    def test_estimated_volume_uses_calibrated_field_not_naive_recompute(self):
        # resistance*reps*sets would be 60*8*5=2400 here coincidentally -
        # use a case where they diverge to prove the calibrated field wins.
        result = _calibrated_result()
        result["exercises"][0]["estimated_volume"] = 9999.0  # cable-aware, workload_multiplier=2 in reality
        workout = adapt_calibrated_to_workout_schema(AS_OF, result, None)
        self.assertEqual(workout["session"]["exercises"][0]["estimated_volume"], 9999.0)

    def test_non_ok_status_returns_error_shape(self):
        workout = adapt_calibrated_to_workout_schema(AS_OF, {"status": "error", "reason": "x"}, None)
        self.assertEqual(workout["status"], "error")

    def test_evidence_provenance_namespace_present_per_exercise(self):
        result = _calibrated_result()
        workout = adapt_calibrated_to_workout_schema(AS_OF, result, None)
        evidence = workout["session"]["exercises"][0]["training_intelligence_evidence"]
        self.assertEqual(evidence["evidence_tier"], 1)
        self.assertEqual(evidence["effort_quality"], "HIGH")


class FeatureFlagTests(unittest.TestCase):
    def test_default_false_absent(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(MOBILE_ENV_VAR_NAME, None)
            self.assertFalse(training_intelligence_mobile_enabled())

    def test_enabled_when_true(self):
        with patch.dict(os.environ, {MOBILE_ENV_VAR_NAME: "true"}):
            self.assertTrue(training_intelligence_mobile_enabled())

    def test_shadow_compare_default_false(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(SHADOW_COMPARE_ENV_VAR_NAME, None)
            self.assertFalse(training_intelligence_shadow_compare_enabled())

    def test_effective_engine_prefers_mobile_flag(self):
        with patch.dict(os.environ, {MOBILE_ENV_VAR_NAME: "true"}):
            self.assertEqual(resolve_effective_training_engine(), ENGINE_TRAINING_INTELLIGENCE)

    def test_effective_engine_falls_back_to_legacy_flag_when_mobile_absent(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(MOBILE_ENV_VAR_NAME, None)
            os.environ.pop("TRAINING_PRESCRIPTION_ENGINE", None)
            self.assertEqual(resolve_effective_training_engine(), ENGINE_B3)


class CacheVersioningTests(unittest.TestCase):
    def test_three_distinct_engine_partitions(self):
        import todays_plan_store
        offsets = todays_plan_store.ENGINE_PLAN_VERSION_OFFSET
        self.assertEqual(len(set(offsets.values())), 3)
        self.assertIn(ENGINE_TRAINING_INTELLIGENCE, offsets)

    def test_training_intelligence_partition_distinct_from_others(self):
        import todays_plan_store
        ti_version = todays_plan_store._effective_plan_version(ENGINE_TRAINING_INTELLIGENCE)
        b3_version = todays_plan_store._effective_plan_version(ENGINE_B3)
        self.assertNotEqual(ti_version, b3_version)


class BuildWorkoutRoutingTests(unittest.TestCase):
    """Section 37: old-vs-new feature-flag routing tests for
    todays_plan._build_workout()."""

    def test_flag_off_calls_legacy_path_unchanged(self):
        import todays_plan
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(MOBILE_ENV_VAR_NAME, None)
            with patch("todays_plan.build_daily_workout_prescription", return_value={"status": "ok", "session": {}}) as legacy_mock:
                workout = todays_plan._build_workout()
        legacy_mock.assert_called_once()
        self.assertEqual(workout["status"], "ok")

    def test_flag_on_calls_assembled_engine(self):
        import todays_plan
        result = _calibrated_result()
        with patch.dict(os.environ, {MOBILE_ENV_VAR_NAME: "true"}):
            with patch(
                "training_intelligence.calibration.orchestrator.build_training_intelligence_prescription",
                return_value=(result, {"stored": True, "snapshot": {"decision_id": "xyz"}}),
            ) as orchestrator_mock:
                workout = todays_plan._build_workout()
        orchestrator_mock.assert_called_once()
        self.assertEqual(workout["status"], "ok")
        self.assertEqual(workout["session"]["training_intelligence"]["decision_id"], "xyz")

    def test_flag_on_falls_back_to_legacy_on_exception(self):
        import todays_plan
        with patch.dict(os.environ, {MOBILE_ENV_VAR_NAME: "true"}):
            with patch(
                "training_intelligence.calibration.orchestrator.build_training_intelligence_prescription",
                side_effect=RuntimeError("boom"),
            ):
                with patch("todays_plan.build_daily_workout_prescription", return_value={"status": "ok", "session": {}}) as legacy_mock:
                    workout = todays_plan._build_workout()
        legacy_mock.assert_called_once()
        self.assertEqual(workout["status"], "ok")

    def test_flag_on_shadow_compare_computes_legacy_too_without_using_it(self):
        import todays_plan
        result = _calibrated_result()
        with patch.dict(os.environ, {MOBILE_ENV_VAR_NAME: "true", SHADOW_COMPARE_ENV_VAR_NAME: "true"}):
            with patch(
                "training_intelligence.calibration.orchestrator.build_training_intelligence_prescription",
                return_value=(result, None),
            ):
                with patch("todays_plan.build_daily_workout_prescription", return_value={"status": "ok", "session": {"total_sets": 3}}) as legacy_mock:
                    workout = todays_plan._build_workout()
        legacy_mock.assert_called_once()
        # the TKI result, not the legacy one, is what's returned to the caller
        self.assertEqual(workout["session"]["session_type"], "Upper Push")


class AdminRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app, base_url="https://testserver")

    def _login(self):
        response = self.client.post("/admin/login", data={"password": "test-admin-password"}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)

    def test_unauthenticated_request_rejected(self):
        response = self.client.get("/training-intelligence/current")
        self.assertEqual(response.status_code, 401)

    def test_authenticated_request_returns_diagnostic_shape(self):
        self._login()
        fake_diagnostic = {
            "as_of": AS_OF.isoformat(), "training_intelligence": {"status": "ok"},
            "legacy": {"status": "ok"}, "comparison": {}, "decision_provenance": {},
            "quality_verdict": "SUPPORTED",
        }
        with patch(
            "training_intelligence.calibration.orchestrator.training_intelligence_diagnostic",
            return_value=fake_diagnostic,
        ):
            response = self.client.get("/training-intelligence/current")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["quality_verdict"], "SUPPORTED")
        self.assertIn("training_intelligence", body)
        self.assertIn("legacy", body)


if __name__ == "__main__":
    unittest.main()
