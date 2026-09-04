"""Daily/weekly source-freshness contract for stale-data guardrails.

These tests pin the cheap DB-metadata signal that the intelligence and
Today Plan fast paths rely on:

  CURRENT            latest complete physiology is today's Eastern date
  PENDING_FRESHNESS  latest complete physiology is only the previous day
  STALE             latest complete physiology is >1 day behind

They also pin the shape of the ``source_freshness`` marker embedded in
stored intelligence / cached plans so a newer WHOOP refresh can be
detected without rebuilding the expensive payload.
"""

import unittest
from contextlib import contextmanager
from datetime import date, datetime, timezone
from unittest.mock import patch

import freshness


class _FakeCursor:
    def __init__(self, row):
        self._row = row

    def execute(self, *args, **kwargs):
        return None

    def fetchone(self):
        return self._row


@contextmanager
def _fake_conn(row):
    cur = _FakeCursor(row)

    class _Conn:
        @contextmanager
        def cursor(self):
            yield cur

    yield _Conn()


def _patch_conn(row):
    return patch.object(freshness, "get_conn", lambda: _fake_conn(row))


NOON_UTC = datetime(2026, 9, 4, 15, 0, tzinfo=timezone.utc)  # 11:00 EDT


class DailySourceFreshnessTests(unittest.TestCase):

    def test_returns_iso_marker_from_row(self):
        row = {
            "metric_date": date(2026, 9, 4),
            "source_updated_at": datetime(2026, 9, 4, 11, 0, tzinfo=timezone.utc),
            "generated_at": datetime(2026, 9, 4, 11, 5, tzinfo=timezone.utc),
        }
        with _patch_conn(row):
            marker = freshness.daily_source_freshness()

        self.assertEqual(marker["metric_date"], "2026-09-04")
        self.assertEqual(
            marker["source_updated_at"], "2026-09-04T11:00:00+00:00"
        )
        self.assertEqual(
            marker["metrics_generated_at"], "2026-09-04T11:05:00+00:00"
        )

    def test_returns_null_marker_when_no_rows(self):
        with _patch_conn(None):
            marker = freshness.daily_source_freshness()

        self.assertEqual(
            marker,
            {
                "metric_date": None,
                "source_updated_at": None,
                "metrics_generated_at": None,
            },
        )


class FreshnessStatusContractTests(unittest.TestCase):

    def _status(self, metric_date):
        row = {
            "metric_date": metric_date,
            "source_updated_at": datetime(2026, 9, 4, 11, 0, tzinfo=timezone.utc),
            "generated_at": datetime(2026, 9, 4, 11, 5, tzinfo=timezone.utc),
        }
        with _patch_conn(row):
            return freshness.freshness_status(now_utc=NOON_UTC)

    def test_current_day_physiology_is_generatable(self):
        status = self._status(date(2026, 9, 4))
        self.assertEqual(status["status"], "fresh")
        self.assertTrue(status["can_generate_current_recommendation"])
        self.assertEqual(status["source_freshness"]["metric_date"], "2026-09-04")

    def test_previous_day_only_is_pending_freshness(self):
        status = self._status(date(2026, 9, 3))
        self.assertEqual(status["status"], "pending_today")
        self.assertFalse(status["can_generate_current_recommendation"])
        # Marker still exposed so callers can log what was available.
        self.assertEqual(status["source_freshness"]["metric_date"], "2026-09-03")

    def test_multi_day_gap_is_stale(self):
        status = self._status(date(2026, 9, 1))
        self.assertEqual(status["status"], "stale")
        self.assertFalse(status["can_generate_current_recommendation"])

    def test_no_data_still_exposes_source_freshness_key(self):
        with _patch_conn(None):
            status = freshness.freshness_status(now_utc=NOON_UTC)

        self.assertEqual(status["status"], "no_data")
        self.assertFalse(status["can_generate_current_recommendation"])
        self.assertIn("source_freshness", status)
        self.assertIsNone(status["source_freshness"]["metric_date"])


class WeeklySourceFreshnessTests(unittest.TestCase):

    def test_returns_iso_marker_for_trailing_window(self):
        row = {
            "metric_date": date(2026, 9, 4),
            "source_updated_at": datetime(2026, 9, 4, 11, 0, tzinfo=timezone.utc),
            "metrics_generated_at": datetime(2026, 9, 4, 11, 5, tzinfo=timezone.utc),
        }
        with _patch_conn(row):
            marker = freshness.weekly_source_freshness()

        self.assertEqual(marker["metric_date"], "2026-09-04")
        self.assertEqual(
            marker["source_updated_at"], "2026-09-04T11:00:00+00:00"
        )
        self.assertEqual(
            marker["metrics_generated_at"], "2026-09-04T11:05:00+00:00"
        )

    def test_returns_null_marker_when_no_rows(self):
        with _patch_conn(None):
            marker = freshness.weekly_source_freshness()

        self.assertIsNone(marker["metric_date"])
        self.assertIsNone(marker["source_updated_at"])


if __name__ == "__main__":
    unittest.main()
