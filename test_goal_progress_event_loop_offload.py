"""GET /api/v1/goals/progress must not block the asyncio event loop.

goal_progress() is a synchronous, DB-heavy call (apple_health_trends,
body_composition_progress, Tonal strength adherence). The route offloads
it to the anyio worker thread pool so it cannot stall unrelated concurrent
requests on the same Uvicorn worker. These tests pin:

  CASE A  the response shape/result is unchanged
  CASE B  goal_progress() actually executes off the event-loop thread
  CASE C  an exception from goal_progress() propagates unchanged
"""

import os
import threading
import unittest
from contextlib import nullcontext
from unittest.mock import patch

os.environ.setdefault("SESSION_SECRET", "test-session-secret")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")
os.environ.setdefault(
    "TOKEN_ENCRYPTION_KEY",
    "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
)
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://test:test@localhost/test",
)
os.environ.setdefault("WHOOP_CLIENT_ID", "test-client-id")
os.environ.setdefault("WHOOP_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault(
    "WHOOP_REDIRECT_URI",
    "https://development.example/whoop/callback",
)
os.environ.setdefault(
    "APPLE_HEALTH_INGEST_KEY",
    "test-ingest-key",
)

from fastapi.testclient import TestClient

import main


class GoalProgressEventLoopOffloadTests(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(
            main.app,
            base_url="https://testserver",
        )
        self.auth_headers = {
            "Authorization": "Bearer test-ingest-key",
        }

        # These tests are about thread-offload / response-shape /
        # error-propagation behavior, not the DB connection-sharing
        # plumbing (that has its own dedicated tests in
        # test_goal_progress_connection_reuse.py). goal_progress is
        # mocked in every test here, so it never actually needs a
        # database - replace request_scoped_connection with a no-op
        # so opening one doesn't require a real DATABASE_URL.
        self.enterContext(
            patch.object(
                main,
                "request_scoped_connection",
                nullcontext,
            )
        )

    # ------------------------------------------------------------
    # CASE A - response shape/result unchanged
    # ------------------------------------------------------------

    def test_response_matches_goal_progress_result(self):
        fake_result = {
            "status": "ok",
            "phase": "lean_cut",
            "direction": "toward_goal",
        }

        with patch.object(
            main,
            "goal_progress",
            return_value=fake_result,
        ):
            response = self.client.get(
                "/api/v1/goals/progress",
                headers=self.auth_headers,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), fake_result)

    def test_auth_behavior_unchanged(self):
        with patch.object(
            main,
            "goal_progress",
            return_value={"status": "ok"},
        ):
            unauthenticated = self.client.get(
                "/api/v1/goals/progress"
            )

            wrong_key = self.client.get(
                "/api/v1/goals/progress",
                headers={"Authorization": "Bearer not-the-key"},
            )

        self.assertEqual(unauthenticated.status_code, 401)
        self.assertEqual(wrong_key.status_code, 401)

    # ------------------------------------------------------------
    # CASE B - goal_progress() runs off the event-loop thread
    # ------------------------------------------------------------

    def test_goal_progress_executes_off_the_event_loop_thread(self):
        # NOTE: TestClient drives the ASGI app through its own
        # blocking portal thread, so comparing against the test's
        # calling thread would not actually distinguish "ran inline
        # on the event loop" from "ran offloaded" - both look
        # different from the pytest thread either way. Drive the
        # coroutine directly with asyncio.run() instead: that runs
        # the event loop *on the calling thread*, so the calling
        # thread's identity IS the event-loop thread's identity for
        # the duration of the call.
        import asyncio

        event_loop_thread_ident = threading.get_ident()
        observed = {}

        def _record_thread_and_return():
            observed["ident"] = threading.get_ident()
            return {"status": "ok"}

        fake_request = type(
            "FakeRequest",
            (),
            {
                "headers": {
                    "authorization": "Bearer test-ingest-key",
                },
                "session": {},
            },
        )()

        with patch.object(
            main,
            "goal_progress",
            side_effect=_record_thread_and_return,
        ):
            result = asyncio.run(
                main.mobile_goal_progress(fake_request)
            )

        self.assertEqual(result, {"status": "ok"})
        self.assertIn("ident", observed)
        # The synchronous call must not execute on the event-loop
        # thread - it must have been handed to a worker thread.
        self.assertNotEqual(
            observed["ident"],
            event_loop_thread_ident,
        )

    def test_route_delegates_through_anyio_to_thread(self):
        # Direct wiring check: the coroutine itself must hand the
        # goal-progress computation to anyio.to_thread.run_sync
        # rather than calling it inline. It no longer passes
        # goal_progress directly (it passes a small closure that
        # also opens the shared request-scoped connection), so
        # assert on behavior: run_sync must be awaited exactly once
        # with a zero-argument callable that, when invoked, calls
        # goal_progress() and returns its result.
        import asyncio
        from unittest.mock import AsyncMock

        fake_request = type(
            "FakeRequest",
            (),
            {
                "headers": {
                    "authorization": "Bearer test-ingest-key",
                },
                "session": {},
            },
        )()

        with (
            patch.object(
                main.anyio.to_thread,
                "run_sync",
                new_callable=AsyncMock,
                return_value={"status": "ok"},
            ) as mocked_run_sync,
        ):
            result = asyncio.run(
                main.mobile_goal_progress(fake_request)
            )

        mocked_run_sync.assert_awaited_once()
        (delegated_callable,), _ = mocked_run_sync.await_args
        self.assertTrue(callable(delegated_callable))

        with patch.object(
            main,
            "goal_progress",
            return_value={"status": "ok", "marker": "delegated"},
        ):
            self.assertEqual(
                delegated_callable(),
                {"status": "ok", "marker": "delegated"},
            )

        self.assertEqual(result, {"status": "ok"})

    # ------------------------------------------------------------
    # CASE C - exceptions propagate unchanged
    # ------------------------------------------------------------

    def test_goal_progress_exception_propagates_unchanged(self):
        with patch.object(
            main,
            "goal_progress",
            side_effect=RuntimeError("boom"),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                self.client.get(
                    "/api/v1/goals/progress",
                    headers=self.auth_headers,
                )

        self.assertEqual(str(ctx.exception), "boom")


if __name__ == "__main__":
    unittest.main()
