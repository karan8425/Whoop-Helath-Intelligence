import io
import sys
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from unittest.mock import Mock, call, patch

db_stub = types.ModuleType("db")
db_stub.get_conn = Mock()
sys.modules.setdefault("db", db_stub)
requests_stub = types.ModuleType("requests")
requests_stub.Timeout = type("Timeout", (Exception,), {})
requests_stub.ConnectionError = type("ConnectionError", (Exception,), {})
requests_stub.request = Mock()
sys.modules.setdefault("requests", requests_stub)

from integrations.tonal import daily_sync
from integrations.tonal.client import (
    TonalAuthenticationError,
    TonalConfigurationError,
    automation_credentials,
)
from integrations.tonal.sync_tonal import get_all_workouts


NOW = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)


class Response:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}

    def json(self):
        return self._payload


class TonalCredentialTests(unittest.TestCase):
    def test_missing_automation_credentials_is_sanitized(self):
        with self.assertRaisesRegex(
            TonalConfigurationError,
            "automation credentials are not configured",
        ):
            automation_credentials({})

    def test_credentials_are_never_logged(self):
        secret_email = "private@example.test"
        secret_password = "do-not-print"
        output = io.StringIO()
        with patch.object(
            daily_sync,
            "automation_credentials",
            return_value=(secret_email, secret_password),
        ), patch.object(daily_sync, "_start_audit", return_value=1), patch.object(
            daily_sync, "_stored_latest_workout", return_value=None
        ), patch.object(
            daily_sync,
            "authenticate",
            side_effect=TonalAuthenticationError("rejected"),
        ), patch.object(daily_sync, "_finish_audit"):
            with redirect_stdout(output), redirect_stderr(output):
                with self.assertRaises(RuntimeError):
                    daily_sync.run_sync({})
        text = output.getvalue()
        self.assertNotIn(secret_email, text)
        self.assertNotIn(secret_password, text)


class TonalRetryTests(unittest.TestCase):
    @patch("integrations.tonal.client.requests.request")
    def test_transient_429_retries(self, request):
        from integrations.tonal.client import _request_with_retry

        request.side_effect = [Response(429), Response(200)]
        response = _request_with_retry("GET", "https://example.test", sleep=Mock())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(request.call_count, 2)

    @patch("integrations.tonal.client.requests.request")
    def test_transient_5xx_retries(self, request):
        from integrations.tonal.client import _request_with_retry

        request.side_effect = [Response(503), Response(502), Response(200)]
        response = _request_with_retry("GET", "https://example.test", sleep=Mock())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(request.call_count, 3)

    @patch("integrations.tonal.client.requests.request")
    def test_authentication_failure_does_not_retry(self, request):
        from integrations.tonal.client import authenticate

        request.return_value = Response(401)
        with self.assertRaises(TonalAuthenticationError):
            authenticate("email", "password", sleep=Mock())
        self.assertEqual(request.call_count, 1)


class TonalPaginationTests(unittest.TestCase):
    def test_workout_pagination(self):
        first = [{"beginTime": f"2026-01-{(i % 28) + 1:02d}"} for i in range(100)]
        second = [{"beginTime": "2026-09-05"}]
        responses = [
            Response(200, first, {"pg-total": "101"}),
            Response(200, second, {"pg-total": "101"}),
        ]
        with patch(
            "integrations.tonal.sync_tonal.tonal_get",
            side_effect=responses,
        ) as tonal_get:
            workouts = get_all_workouts("token", "user")
        self.assertEqual(len(workouts), 101)
        self.assertEqual(tonal_get.call_count, 2)
        self.assertEqual(
            tonal_get.call_args_list[1].kwargs["headers"]["pg-offset"],
            "100",
        )


class TonalOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.workouts = [
            {
                "beginTime": "2026-09-05T10:00:00Z",
                "workoutSetActivity": [{"movementId": "m1"}, {"movementId": "m2"}],
            }
        ]
        self.audit = patch.object(daily_sync, "_finish_audit").start()
        self.addCleanup(patch.stopall)
        patch.object(daily_sync, "automation_credentials", return_value=("e", "p")).start()
        patch.object(daily_sync, "_start_audit", return_value=7).start()
        patch.object(daily_sync, "authenticate", return_value={"id_token": "token"}).start()
        patch.object(daily_sync, "get_user_info", return_value={"id": "user"}).start()
        patch.object(daily_sync, "get_movements", return_value=[{"id": "m1"}]).start()
        patch.object(daily_sync, "get_all_workouts", return_value=self.workouts).start()
        patch.object(daily_sync, "get_strength_history", return_value=[{"id": "s1"}]).start()
        patch.object(daily_sync, "_material_training_change", return_value=False).start()
        patch.object(daily_sync, "sync_movements", return_value=1).start()
        patch.object(
            daily_sync,
            "sync_workouts_and_sets",
            return_value={"workouts": 1, "sets": 2},
        ).start()
        patch.object(daily_sync, "sync_records", return_value=1).start()

    def test_successful_complete_orchestration_and_audit(self):
        with patch.object(daily_sync, "_stored_latest_workout", side_effect=[None, NOW]):
            result = daily_sync.run_sync({})
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["sets_received"], 2)
        self.assertEqual(self.audit.call_args.args[:2], (7, "completed"))
        self.assertNotIn("status", self.audit.call_args.kwargs)

    def test_repeated_run_remains_successful(self):
        for _ in range(2):
            with patch.object(daily_sync, "_stored_latest_workout", side_effect=[NOW, NOW]):
                self.assertEqual(daily_sync.run_sync({})["status"], "completed")
        self.assertEqual(self.audit.call_count, 2)

    def test_source_store_mismatch_fails_run(self):
        old = datetime(2026, 9, 4, tzinfo=timezone.utc)
        with patch.object(daily_sync, "_stored_latest_workout", side_effect=[old, old, old]):
            with self.assertRaisesRegex(RuntimeError, "failed during processing"):
                daily_sync.run_sync({})
        self.assertEqual(self.audit.call_args.args[:2], (7, "failed"))

    def test_parser_failure_is_recorded_safely(self):
        secret = "payload-secret"
        daily_sync.sync_workouts_and_sets.side_effect = ValueError(secret)
        with patch.object(daily_sync, "_stored_latest_workout", side_effect=[None, None]):
            with self.assertRaises(RuntimeError) as raised:
                daily_sync.run_sync({})
        self.assertNotIn(secret, str(raised.exception))
        audit_kwargs = self.audit.call_args.kwargs
        self.assertEqual(audit_kwargs["error_class"], "ValueError")
        self.assertNotIn(secret, audit_kwargs["error_message_sanitized"])

    def test_audit_never_receives_token_or_password(self):
        with patch.object(daily_sync, "_stored_latest_workout", side_effect=[None, NOW]):
            daily_sync.run_sync({})
        audit_text = repr(self.audit.call_args)
        self.assertNotIn("token", audit_text)
        self.assertNotIn("password", audit_text)


if __name__ == "__main__":
    unittest.main()
