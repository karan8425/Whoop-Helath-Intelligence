"""db.request_scoped_connection() - connection-reuse correctness.

Goal Progress calls get_conn() dozens of times across
apple_health_trends(), body_composition_progress(), and Tonal
strength adherence. Each call normally opens (and TLS/auth
handshakes) its own physical connection; that per-call setup cost,
not query time, is what makes GET /api/v1/goals/progress slow.

request_scoped_connection() opens exactly one physical connection
and makes every nested get_conn() call inside its block reuse it.
It introduces no value cache and no TTL - it only changes how many
physical connections a single request opens - so there is no new
staleness surface: every query still runs against the database at
request time, through the (shared) connection, with the same SQL,
same parameters, same results.

These tests pin:

  - a "cache hit"-shaped case: nested get_conn() calls inside the
    scope reuse the one connection instead of opening new ones
  - a "cache miss"-shaped case: get_conn() calls made outside the
    scope are completely unaffected (open/close exactly as before)
  - commit/rollback/close still happen correctly on success and on
    error, exactly once for the whole scope
  - nesting request_scoped_connection() inside itself is a no-op
    (does not open a second physical connection or double-close)
"""

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://test:test@localhost/test",
)
os.environ.setdefault(
    "TOKEN_ENCRYPTION_KEY",
    "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
)

import db


class RequestScopedConnectionTests(unittest.TestCase):

    def setUp(self):
        # A single fixed fake connection object, returned by every
        # psycopg.connect() call in this test unless a test
        # overrides side_effect to hand out a fresh one per call.
        self.fake_conn = MagicMock(name="fake_connection")

        self.connect_patcher = patch.object(
            db.psycopg,
            "connect",
            side_effect=lambda *a, **kw: self.fake_conn,
        )
        self.mock_connect = self.connect_patcher.start()
        self.addCleanup(self.connect_patcher.stop)

    # ------------------------------------------------------------
    # Nested get_conn() calls reuse the one shared connection
    # ------------------------------------------------------------

    def test_nested_get_conn_calls_share_one_physical_connection(self):
        seen_connections = []

        with db.request_scoped_connection():
            for _ in range(5):
                with db.get_conn() as conn:
                    seen_connections.append(conn)

        # Only one physical psycopg.connect() call for all 5 nested
        # get_conn() uses - this is the entire point of the fix.
        self.assertEqual(self.mock_connect.call_count, 1)

        # And every nested call really did get the same object.
        self.assertEqual(len(seen_connections), 5)
        self.assertTrue(
            all(c is seen_connections[0] for c in seen_connections)
        )

    def test_shared_connection_commits_once_on_success(self):
        with db.request_scoped_connection():
            with db.get_conn() as conn:
                pass
            with db.get_conn() as conn:
                pass

        shared_conn = self.fake_conn
        shared_conn.commit.assert_called_once()
        shared_conn.rollback.assert_not_called()
        shared_conn.close.assert_called_once()

    def test_shared_connection_rolls_back_once_on_error(self):
        with self.assertRaises(RuntimeError):
            with db.request_scoped_connection():
                with db.get_conn():
                    pass

                raise RuntimeError("boom")

        shared_conn = self.fake_conn
        shared_conn.commit.assert_not_called()
        shared_conn.rollback.assert_called_once()
        shared_conn.close.assert_called_once()

    def test_nesting_request_scoped_connection_is_a_no_op(self):
        with db.request_scoped_connection():
            with db.request_scoped_connection():
                with db.get_conn() as conn:
                    pass

        # The inner request_scoped_connection() call must not open
        # a second physical connection or close the outer one early.
        self.assertEqual(self.mock_connect.call_count, 1)
        shared_conn = self.fake_conn
        shared_conn.close.assert_called_once()

    # ------------------------------------------------------------
    # get_conn() outside the scope is completely unaffected
    # ------------------------------------------------------------

    def test_get_conn_outside_scope_commits_and_closes_each_call(self):
        connections = []

        def _connect(*args, **kwargs):
            conn = MagicMock()
            connections.append(conn)
            return conn

        self.mock_connect.side_effect = _connect

        with db.get_conn():
            pass

        with db.get_conn():
            pass

        self.assertEqual(len(connections), 2)
        for conn in connections:
            conn.commit.assert_called_once()
            conn.close.assert_called_once()

    def test_scope_does_not_leak_to_a_later_unrelated_get_conn_call(self):
        with db.request_scoped_connection():
            with db.get_conn():
                pass

        # After the scope exits, a fresh get_conn() call must open a
        # brand new connection, not the one that was just closed.
        with db.get_conn():
            pass

        self.assertEqual(self.mock_connect.call_count, 2)


if __name__ == "__main__":
    unittest.main()
