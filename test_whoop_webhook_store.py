import re
import unittest
from contextlib import contextmanager
from unittest.mock import patch

import whoop_webhook_store as store


class _FakeCursor:
    def __init__(self, sink, fetchone_result=None, fetchall_result=None):
        self._sink = sink
        self._fetchone_result = fetchone_result
        self._fetchall_result = fetchall_result or []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._sink.append((sql, params))

    def fetchone(self):
        return self._fetchone_result

    def fetchall(self):
        return self._fetchall_result


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def _fake_get_conn(cursor):
    @contextmanager
    def factory():
        yield _FakeConn(cursor)

    return factory


def _norm(sql):
    return re.sub(r"\s+", " ", sql).strip().lower()


class TakeSupersededSkipsTests(unittest.TestCase):

    def test_returns_number_of_claimed_rows(self):
        sink = []
        cursor = _FakeCursor(sink, fetchall_result=[{"id": 41}, {"id": 42}])
        with patch.object(store, "get_conn", _fake_get_conn(cursor)):
            claimed = store.take_superseded_skips()
        self.assertEqual(claimed, 2)

    def test_returns_zero_when_nothing_pending(self):
        sink = []
        cursor = _FakeCursor(sink, fetchall_result=[])
        with patch.object(store, "get_conn", _fake_get_conn(cursor)):
            self.assertEqual(store.take_superseded_skips(), 0)

    def test_claims_only_recent_busy_skips_and_flips_status(self):
        sink = []
        cursor = _FakeCursor(sink, fetchall_result=[{"id": 41}])
        with patch.object(store, "get_conn", _fake_get_conn(cursor)):
            store.take_superseded_skips(within_minutes=10)

        sql, params = sink[0]
        norm = _norm(sql)
        self.assertIn("update whoop_webhook_events", norm)
        self.assertIn("set pipeline_status = 'skipped_superseded'", norm)
        self.assertIn("where pipeline_status = 'skipped_pipeline_busy'", norm)
        self.assertIn("received_at >=", norm)
        self.assertIn("returning id", norm)
        self.assertEqual(params, ("10",))

    def test_within_minutes_is_coerced_to_int_text(self):
        sink = []
        cursor = _FakeCursor(sink, fetchall_result=[])
        with patch.object(store, "get_conn", _fake_get_conn(cursor)):
            store.take_superseded_skips(within_minutes=5)
        self.assertEqual(sink[0][1], ("5",))


class StoreWebhookEventDedupTests(unittest.TestCase):

    def test_returns_new_id_on_insert(self):
        sink = []
        cursor = _FakeCursor(sink, fetchone_result={"id": 7})
        with patch.object(store, "get_conn", _fake_get_conn(cursor)):
            event_id = store.store_webhook_event(
                trace_id="trace-1",
                event_type="recovery.updated",
                resource_id="1170a900-5a8c-47e1-b07e-404f28fd4a22",
                user_id=25298070,
                payload={"type": "recovery.updated"},
            )
        self.assertEqual(event_id, 7)
        sql, params = sink[0]
        norm = _norm(sql)
        self.assertIn("on conflict ( trace_id, event_type ) do nothing", norm)
        # user_id is stringified for the TEXT column.
        self.assertEqual(params[3], "25298070")

    def test_returns_none_on_exact_duplicate(self):
        sink = []
        cursor = _FakeCursor(sink, fetchone_result=None)
        with patch.object(store, "get_conn", _fake_get_conn(cursor)):
            event_id = store.store_webhook_event(
                trace_id="trace-1",
                event_type="recovery.updated",
                resource_id="r1",
                user_id="u1",
                payload={},
            )
        self.assertIsNone(event_id)

    def test_missing_trace_id_or_type_raises(self):
        with self.assertRaises(ValueError):
            store.store_webhook_event("", "recovery.updated", "r", "u", {})
        with self.assertRaises(ValueError):
            store.store_webhook_event("t", "", "r", "u", {})


class MarkPipelineSkippedTests(unittest.TestCase):

    def test_skip_records_busy_reason(self):
        sink = []
        cursor = _FakeCursor(sink)
        with patch.object(store, "get_conn", _fake_get_conn(cursor)):
            store.mark_pipeline_skipped(41, "skipped_pipeline_busy")
        sql, params = sink[0]
        self.assertIn("pipeline_status = %s", _norm(sql))
        self.assertEqual(params, ("skipped_pipeline_busy", 41))


if __name__ == "__main__":
    unittest.main()
