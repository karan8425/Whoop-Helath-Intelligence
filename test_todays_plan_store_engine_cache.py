"""TKI-6: proves B3 and TKI never accidentally share (or overwrite)
each other's cached today's-plan row - each engine gets a numerically
distinct plan_version partition of the SAME existing todays_plan_cache
table (no new table, no migration).
"""

import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import todays_plan_store
from training_engine_flag import ENGINE_B3, ENGINE_TKI


class EffectivePlanVersionTests(unittest.TestCase):
    def test_b3_and_tki_get_distinct_versions(self):
        b3_version = todays_plan_store._effective_plan_version(ENGINE_B3)
        tki_version = todays_plan_store._effective_plan_version(ENGINE_TKI)
        self.assertNotEqual(b3_version, tki_version)

    def test_b3_version_equals_base_plan_version(self):
        self.assertEqual(todays_plan_store._effective_plan_version(ENGINE_B3), todays_plan_store.PLAN_VERSION)

    def test_missing_engine_defaults_to_b3_version(self):
        self.assertEqual(todays_plan_store._effective_plan_version(None), todays_plan_store.PLAN_VERSION)

    def test_unknown_engine_string_does_not_crash_and_defaults_to_base(self):
        self.assertEqual(todays_plan_store._effective_plan_version("nonsense"), todays_plan_store.PLAN_VERSION)


class _FakeCursor:
    def __init__(self, recorder, fetch_result=None):
        self._recorder = recorder
        self._fetch_result = fetch_result

    def execute(self, query, params=None):
        self._recorder.append((query, params))

    def fetchone(self):
        return self._fetch_result

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeConn:
    def __init__(self, recorder, fetch_result=None):
        self._recorder = recorder
        self._fetch_result = fetch_result

    def cursor(self):
        return _FakeCursor(self._recorder, self._fetch_result)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class CacheReadWritePartitionTests(unittest.TestCase):
    def test_load_cached_plan_queries_the_requested_partition(self):
        recorder = []
        with patch.object(todays_plan_store, "ensure_table"), \
             patch.object(todays_plan_store, "get_conn", return_value=_FakeConn(recorder)):
            todays_plan_store.load_cached_plan("2026-09-12", todays_plan_store._effective_plan_version(ENGINE_TKI))
        select_query, params = next(q for q in recorder if "SELECT" in q[0])
        self.assertEqual(params[1], todays_plan_store._effective_plan_version(ENGINE_TKI))

    def test_load_cached_plan_defaults_to_b3_partition(self):
        recorder = []
        with patch.object(todays_plan_store, "ensure_table"), \
             patch.object(todays_plan_store, "get_conn", return_value=_FakeConn(recorder)):
            todays_plan_store.load_cached_plan("2026-09-12")
        select_query, params = next(q for q in recorder if "SELECT" in q[0])
        self.assertEqual(params[1], todays_plan_store.PLAN_VERSION)

    def test_save_plan_writes_the_requested_partition(self):
        recorder = []
        fake_row = {"id": 1, "plan_date": "2026-09-12", "plan_version": 512, "plan_payload": "{}"}
        with patch.object(todays_plan_store, "ensure_table"), \
             patch.object(todays_plan_store, "get_conn", return_value=_FakeConn(recorder, fake_row)):
            todays_plan_store.save_plan(
                {"plan_date": "2026-09-12", "status": "ok"},
                todays_plan_store._effective_plan_version(ENGINE_TKI),
            )
        insert_query, params = next(q for q in recorder if "INSERT" in q[0])
        self.assertEqual(params[1], todays_plan_store._effective_plan_version(ENGINE_TKI))

    def test_tki_write_is_not_visible_under_a_b3_read_partition_key(self):
        """The two partitions are numerically distinct - a caller
        reading the b3 partition key can never coincidentally match a
        row written under the tki partition key, and vice versa."""
        b3_version = todays_plan_store._effective_plan_version(ENGINE_B3)
        tki_version = todays_plan_store._effective_plan_version(ENGINE_TKI)
        self.assertNotEqual(b3_version, tki_version)
        # Direct proxy for "a SELECT ... WHERE plan_version = <b3_version>
        # cannot match a row inserted with plan_version = <tki_version>".
        self.assertFalse(b3_version == tki_version)


if __name__ == "__main__":
    unittest.main()
