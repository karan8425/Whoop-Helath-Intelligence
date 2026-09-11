import asyncio
import base64
import hashlib
import hmac
import json
import sys
import time
import types
import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch


# --------------------------------------------------------------------------
# Isolate the webhook module from FastAPI / DB / pipeline imports.
#
# Other test files import whoop_webhook (directly or via main) under their own
# stubs and leave it cached in sys.modules. To stay order-independent this
# file force-reimports whoop_webhook against its own stubs, keeps a private
# reference, then restores sys.modules to its pre-load state so sibling test
# files are unaffected. Every collaborator is still re-patched per test.
# --------------------------------------------------------------------------

_SAVED_MODULES = {
    name: sys.modules.get(name)
    for name in ("fastapi", "daily_job", "whoop_webhook_store", "whoop_webhook")
}


class _HTTPException(Exception):
    def __init__(self, status_code=None, detail=None):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class _BackgroundTasks:
    def __init__(self):
        self.tasks = []

    def add_task(self, func, *args, **kwargs):
        self.tasks.append((func, args, kwargs))


class _Router:
    def post(self, *args, **kwargs):
        return lambda function: function


def _module(name, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


_module(
    "fastapi",
    APIRouter=lambda: _Router(),
    BackgroundTasks=_BackgroundTasks,
    HTTPException=_HTTPException,
    Request=object,
)
_module("daily_job", run_daily_pipeline=Mock(return_value={"status": "completed"}))
_module(
    "whoop_webhook_store",
    init_whoop_webhook_tables=Mock(),
    store_webhook_event=Mock(return_value=1),
    mark_pipeline_started=Mock(),
    mark_pipeline_completed=Mock(),
    mark_pipeline_skipped=Mock(),
    mark_pipeline_failed=Mock(),
    pipeline_lock=Mock(),
    take_superseded_skips=Mock(return_value=0),
)

sys.modules.pop("whoop_webhook", None)
import whoop_webhook  # noqa: E402  (force-reimported against the stubs above)

for _name, _original in _SAVED_MODULES.items():
    if _original is None:
        sys.modules.pop(_name, None)
    else:
        sys.modules[_name] = _original


TEST_SECRET = "unit-test-whoop-client-secret"


def _fresh_timestamp():
    return str(int(time.time() * 1000))


def _sign(timestamp, body):
    digest = hmac.new(
        TEST_SECRET.encode("utf-8"),
        timestamp.encode("utf-8") + body,
        hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def _request(payload, *, timestamp=None, signature=None, headers=None):
    body = json.dumps(payload).encode("utf-8")
    timestamp = timestamp if timestamp is not None else _fresh_timestamp()
    if headers is None:
        headers = {
            "X-WHOOP-Signature-Timestamp": timestamp,
            "X-WHOOP-Signature": (
                signature if signature is not None else _sign(timestamp, body)
            ),
        }
    request = Mock()
    request.headers = headers
    request.body = Mock(return_value=body)

    async def _body():
        return body

    request.body = _body
    return request


def _call(payload, **kwargs):
    request = _request(payload, **kwargs)
    background = _BackgroundTasks()
    with patch.object(whoop_webhook, "WHOOP_CLIENT_SECRET", TEST_SECRET):
        result = asyncio.run(
            whoop_webhook.receive_whoop_webhook(request, background)
        )
    return result, background


RECOVERY_PAYLOAD = {
    "type": "recovery.updated",
    "trace_id": "trace-sep6",
    # WHOOP v2: recovery.updated carries the associated SLEEP uuid as id.
    "id": "1170a900-5a8c-47e1-b07e-404f28fd4a22",
    "user_id": 25298070,
}
SLEEP_PAYLOAD = {
    "type": "sleep.updated",
    "trace_id": "trace-sep6",
    "id": "1170a900-5a8c-47e1-b07e-404f28fd4a22",
    "user_id": 25298070,
}
WORKOUT_PAYLOAD = {
    "type": "workout.updated",
    "trace_id": "trace-workout",
    "id": "workout-1",
    "user_id": 25298070,
}


class SignatureValidationTests(unittest.TestCase):

    def setUp(self):
        whoop_webhook.store_webhook_event.reset_mock(return_value=True)
        whoop_webhook.store_webhook_event.return_value = 1

    def test_valid_signature_is_accepted(self):
        result, background = _call(RECOVERY_PAYLOAD)
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(len(background.tasks), 1)

    def test_missing_headers_rejected_401(self):
        with self.assertRaises(_HTTPException) as ctx:
            _call(RECOVERY_PAYLOAD, headers={})
        self.assertEqual(ctx.exception.status_code, 401)

    def test_tampered_signature_rejected_401(self):
        with self.assertRaises(_HTTPException) as ctx:
            _call(RECOVERY_PAYLOAD, signature="not-the-real-signature")
        self.assertEqual(ctx.exception.status_code, 401)

    def test_body_tamper_after_signing_rejected_401(self):
        ts = _fresh_timestamp()
        good_sig = _sign(ts, json.dumps(RECOVERY_PAYLOAD).encode("utf-8"))
        tampered = dict(RECOVERY_PAYLOAD, user_id=999)
        with self.assertRaises(_HTTPException) as ctx:
            _call(tampered, timestamp=ts, signature=good_sig)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_missing_secret_returns_503(self):
        request = _request(RECOVERY_PAYLOAD)
        with patch.object(whoop_webhook, "WHOOP_CLIENT_SECRET", ""):
            with self.assertRaises(_HTTPException) as ctx:
                asyncio.run(
                    whoop_webhook.receive_whoop_webhook(
                        request, _BackgroundTasks()
                    )
                )
        self.assertEqual(ctx.exception.status_code, 503)

    def test_expired_timestamp_rejected_401(self):
        stale = str(int((time.time() - 3600) * 1000))
        with self.assertRaises(_HTTPException) as ctx:
            _call(RECOVERY_PAYLOAD, timestamp=stale)
        self.assertEqual(ctx.exception.status_code, 401)


class EventRoutingTests(unittest.TestCase):

    def setUp(self):
        for name in (
            "store_webhook_event",
            "init_whoop_webhook_tables",
        ):
            getattr(whoop_webhook, name).reset_mock()
        whoop_webhook.store_webhook_event.return_value = 1
        whoop_webhook.run_daily_pipeline.reset_mock()

    def test_recovery_schedules_immediate_pipeline(self):
        result, background = _call(RECOVERY_PAYLOAD)
        self.assertEqual(result["trigger_mode"], "immediate")
        func, args, _ = background.tasks[0]
        self.assertIs(func, whoop_webhook._run_immediate_pipeline)
        self.assertEqual(args, (1, "trace-sep6", "recovery.updated"))

    def test_sleep_schedules_immediate_pipeline_no_delay(self):
        result, background = _call(SLEEP_PAYLOAD)
        self.assertEqual(result["trigger_mode"], "immediate")
        func, args, _ = background.tasks[0]
        self.assertIs(func, whoop_webhook._run_immediate_pipeline)
        self.assertEqual(args, (1, "trace-sep6", "sleep.updated"))

    def test_workout_schedules_immediate_pipeline(self):
        result, background = _call(WORKOUT_PAYLOAD)
        self.assertEqual(result["trigger_mode"], "immediate")
        func, _, _ = background.tasks[0]
        self.assertIs(func, whoop_webhook._run_immediate_pipeline)

    def test_v2_recovery_resource_id_is_the_sleep_uuid_passed_through(self):
        _call(RECOVERY_PAYLOAD)
        _, kwargs = whoop_webhook.store_webhook_event.call_args
        self.assertEqual(
            kwargs["resource_id"], "1170a900-5a8c-47e1-b07e-404f28fd4a22"
        )
        self.assertEqual(kwargs["event_type"], "recovery.updated")

    def test_exact_duplicate_is_ignored_without_scheduling(self):
        whoop_webhook.store_webhook_event.return_value = None
        result, background = _call(RECOVERY_PAYLOAD)
        self.assertEqual(result["status"], "duplicate_ignored")
        self.assertEqual(background.tasks, [])

    def test_same_trace_id_different_type_both_processed(self):
        whoop_webhook.store_webhook_event.side_effect = [10, 11]
        _, sleep_bg = _call(SLEEP_PAYLOAD)
        _, recovery_bg = _call(RECOVERY_PAYLOAD)
        whoop_webhook.store_webhook_event.side_effect = None
        self.assertEqual(sleep_bg.tasks[0][1], (10, "trace-sep6", "sleep.updated"))
        self.assertEqual(
            recovery_bg.tasks[0][1], (11, "trace-sep6", "recovery.updated")
        )

    def test_handler_does_not_run_pipeline_inline(self):
        _call(RECOVERY_PAYLOAD)
        whoop_webhook.run_daily_pipeline.assert_not_called()

    def test_missing_trace_id_rejected_400(self):
        with self.assertRaises(_HTTPException) as ctx:
            _call({"type": "recovery.updated", "id": "x"})
        self.assertEqual(ctx.exception.status_code, 400)


class ExecutePipelineOnceTests(unittest.TestCase):

    def setUp(self):
        for name in (
            "mark_pipeline_started",
            "mark_pipeline_completed",
            "mark_pipeline_skipped",
            "mark_pipeline_failed",
        ):
            getattr(whoop_webhook, name).reset_mock()
        whoop_webhook.run_daily_pipeline.reset_mock()
        whoop_webhook.run_daily_pipeline.return_value = {"status": "completed"}

    @contextmanager
    def _lock(self, acquired):
        @contextmanager
        def fake_lock():
            yield acquired

        with patch.object(whoop_webhook, "pipeline_lock", fake_lock):
            yield

    def test_lock_acquired_runs_full_pipeline(self):
        with self._lock(True):
            result = whoop_webhook._execute_pipeline_once(5, "t", "recovery.updated")
        self.assertEqual(result["status"], "completed")
        whoop_webhook.mark_pipeline_started.assert_called_once_with(5)
        whoop_webhook.run_daily_pipeline.assert_called_once_with()
        whoop_webhook.mark_pipeline_completed.assert_called_once_with(5)

    def test_lock_busy_marks_skipped_and_does_not_run(self):
        with self._lock(False):
            result = whoop_webhook._execute_pipeline_once(5, "t", "sleep.updated")
        self.assertEqual(result["status"], "skipped_pipeline_busy")
        whoop_webhook.mark_pipeline_skipped.assert_called_once_with(
            5, "skipped_pipeline_busy"
        )
        whoop_webhook.run_daily_pipeline.assert_not_called()
        whoop_webhook.mark_pipeline_completed.assert_not_called()


class ImmediatePipelineCoalescingTests(unittest.TestCase):

    def setUp(self):
        whoop_webhook.take_superseded_skips.reset_mock()
        whoop_webhook.take_superseded_skips.return_value = 0
        whoop_webhook.mark_pipeline_failed.reset_mock()

    def test_rerun_once_when_a_related_event_was_superseded(self):
        with patch.object(
            whoop_webhook,
            "_execute_pipeline_once",
            return_value={"status": "completed"},
        ) as once:
            whoop_webhook.take_superseded_skips.return_value = 1
            whoop_webhook._run_immediate_pipeline(7, "trace-sep6", "recovery.updated")

        self.assertEqual(once.call_count, 2)
        whoop_webhook.take_superseded_skips.assert_called_once()

    def test_no_rerun_when_nothing_was_superseded(self):
        with patch.object(
            whoop_webhook,
            "_execute_pipeline_once",
            return_value={"status": "completed"},
        ) as once:
            whoop_webhook.take_superseded_skips.return_value = 0
            whoop_webhook._run_immediate_pipeline(7, "trace-sep6", "recovery.updated")

        self.assertEqual(once.call_count, 1)

    def test_no_rerun_and_no_claim_when_this_run_itself_was_skipped(self):
        with patch.object(
            whoop_webhook,
            "_execute_pipeline_once",
            return_value={"status": "skipped_pipeline_busy"},
        ) as once:
            whoop_webhook._run_immediate_pipeline(7, "trace-sep6", "recovery.updated")

        self.assertEqual(once.call_count, 1)
        whoop_webhook.take_superseded_skips.assert_not_called()

    def test_pipeline_exception_is_marked_failed(self):
        with patch.object(
            whoop_webhook,
            "_execute_pipeline_once",
            side_effect=RuntimeError("boom"),
        ):
            whoop_webhook._run_immediate_pipeline(7, "trace-sep6", "recovery.updated")
        whoop_webhook.mark_pipeline_failed.assert_called_once()
        self.assertEqual(whoop_webhook.mark_pipeline_failed.call_args[0][0], 7)


if __name__ == "__main__":
    unittest.main()
