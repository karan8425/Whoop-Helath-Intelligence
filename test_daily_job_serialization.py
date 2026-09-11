"""Regression coverage for the 2026-09-06 morning-freshness incident.

On 2026-09-06 both Development reconciliation cron runs reached the success
branch and then crashed in ``daily_job.finish_run()`` with:

    TypeError: Object of type date is not JSON serializable

because the ``intelligence_result`` payload passed to psycopg's JSONB adapter
still contained raw ``datetime.date`` values. The crash happened before the
Today cache was invalidated, so the reconciliation pipeline never completed.

These tests reproduce that payload shape and assert every value persisted by
``finish_run`` / ``store_intelligence`` now serializes cleanly.
"""

import json
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID

from psycopg.types.json import Jsonb

import daily_job
from json_safe import json_safe


# --- Sep-6-style pipeline payloads (raw DB-derived types) --------------------

SEP6 = date(2026, 9, 6)
SEP6_SOURCE_TS = datetime(2026, 9, 6, 8, 41, 52, 933000, tzinfo=timezone.utc)
SLEEP_UUID = UUID("1170a900-5a8c-47e1-b07e-404f28fd4a22")


def _sep6_intelligence_result():
    return {
        "status": "ok",
        "cache": {"source": "forced_refresh", "llm_called": False},
        "model": "deterministic-health-intelligence-fallback",
        # get_or_create_intelligence returns plan_date unwrapped on the
        # forced_refresh branch - this is the value that crashed Sep 6.
        "plan_date": SEP6,
        "brief": {"headline": "Prioritise recovery"},
        "daily_coaching_summary": {
            "as_of": SEP6,
            "generated_at": datetime(2026, 9, 6, 9, 31, 30, tzinfo=timezone.utc),
            "sleep_id": SLEEP_UUID,
            "hrv_rmssd": Decimal("41.2"),
        },
    }


def _sep6_deterministic():
    return {
        "metric_date": SEP6,
        "training_recommendation": "Active Recovery",
        "overall_status": "recover",
        "source_freshness": {
            "metric_date": SEP6,
            "source_updated_at": SEP6_SOURCE_TS,
        },
    }


class _FakeCursor:
    def __init__(self, sink):
        self._sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._sink.append((sql, params))

    def fetchone(self):
        return {"id": 99}


class _FakeConn:
    def __init__(self, sink):
        self._sink = sink

    def cursor(self):
        return _FakeCursor(self._sink)


@contextmanager
def _fake_conn(sink):
    yield _FakeConn(sink)


def _jsonb_params(params):
    return [p for p in params if isinstance(p, Jsonb)]


class Sep6ReproductionTests(unittest.TestCase):

    def test_raw_intelligence_result_would_crash_stdlib_encoder(self):
        # Documents the original defect: psycopg's JSONB adapter serializes
        # with json.dumps, which rejects the raw date payload.
        with self.assertRaises(TypeError):
            json.dumps(_sep6_intelligence_result())

    def test_json_safe_makes_the_same_payload_encodable(self):
        json.dumps(json_safe(_sep6_intelligence_result()))


class FinishRunSerializationTests(unittest.TestCase):

    def _run_finish(self, **overrides):
        sink = []
        kwargs = dict(
            run_id=99,
            status="completed",
            metric_date=SEP6,
            sync_result={"status": "completed", "new_rows": {"sleeps": 1}},
            analytics_result={"newest": "2026-09-06", "calendar_days": 400},
            baselines_result={"as_of": SEP6, "rmssd_baseline": Decimal("55.5")},
            deterministic=_sep6_deterministic(),
            intelligence_result=_sep6_intelligence_result(),
            freshness={
                "status": "fresh",
                "latest_physiology_date": "2026-09-06",
                "source_freshness": {"metric_date": "2026-09-06"},
            },
        )
        kwargs.update(overrides)
        # Other test modules stub psycopg with an identity Jsonb and leave
        # daily_job.Jsonb bound to it. Pin the real adapter so this test
        # exercises the real JSONB serialization path regardless of order.
        with patch.object(daily_job, "Jsonb", Jsonb), patch.object(
            daily_job, "get_conn", lambda: _fake_conn(sink)
        ):
            daily_job.finish_run(**kwargs)
        self.assertEqual(len(sink), 1, "expected exactly one UPDATE execute")
        return sink[0][1]

    def test_sep6_success_branch_no_longer_raises(self):
        params = self._run_finish()
        # Every JSONB-bound payload round-trips through the stdlib encoder.
        for wrapped in _jsonb_params(params):
            json.dumps(wrapped.obj)

    def test_dates_are_coerced_to_iso_strings_in_persisted_payloads(self):
        params = self._run_finish()
        encoded = json.dumps([w.obj for w in _jsonb_params(params)])
        self.assertIn("2026-09-06", encoded)
        # No raw date/datetime repr leaked through.
        self.assertNotIn("datetime.date", encoded)
        self.assertNotIn("datetime.datetime", encoded)

    def test_none_intelligence_and_deterministic_still_bind_null(self):
        params = self._run_finish(deterministic=None, intelligence_result=None)
        # deterministic_recommendation + ai_result placeholders are plain None,
        # not Jsonb wrappers.
        self.assertIn(None, params)

    def test_pending_branch_payload_also_serializes(self):
        # freshness early-return path: deterministic + intelligence are None,
        # metric_date is the latest physiology date string.
        params = self._run_finish(
            status="pending_freshness",
            metric_date="2026-09-05",
            deterministic=None,
            intelligence_result=None,
        )
        for wrapped in _jsonb_params(params):
            json.dumps(wrapped.obj)


class StoreIntelligenceSerializationTests(unittest.TestCase):

    def test_store_intelligence_serializes_date_valued_deterministic(self):
        sink = []
        deterministic = _sep6_deterministic()
        intelligence_result = _sep6_intelligence_result()
        with patch.object(daily_job, "Jsonb", Jsonb), patch.object(
            daily_job, "get_conn", lambda: _fake_conn(sink)
        ):
            returned = daily_job.store_intelligence(
                deterministic, intelligence_result
            )
        self.assertEqual(returned, SEP6)
        self.assertEqual(len(sink), 1)
        for wrapped in _jsonb_params(sink[0][1]):
            json.dumps(wrapped.obj)


if __name__ == "__main__":
    unittest.main()
