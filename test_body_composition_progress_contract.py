"""Mobile body-composition progress contract.

Goal Progress -> detail cards read GET /api/v1/body-composition/progress, which
returns body_composition_progress(). The iOS detail screen renders the generic
"Detailed body-composition progress is unavailable." whenever `metrics` is
missing from that response, so these tests pin the shape the app depends on:

  - an active goal + Hume history  -> status "ok" with a full `metrics` block
  - BUILDING_BASELINE (phase age < minimum) still returns detail metrics
  - every metric carries `available` (bool) and per-horizon `sufficient_data`
  - the whole payload is JSON-serializable (dates/numbers)
  - no active goal -> typed "not_ready" state, not an exception
  - Hume remains the sole current-scoring source
  - request-scoped DB connections do not change the response
"""

import json
import os
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault(
    "TOKEN_ENCRYPTION_KEY",
    "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
)

import body_composition_progress as bcp
import db


HUME = bcp.HUME_BUNDLE_ID
FITDAYS = bcp.FITDAYS_BUNDLE_ID


def _rows(*, days: int, end: date, source=HUME, start_weight_kg=84.0, start_bf=21.0):
    """One Hume weight + body-fat row per day for `days` days ending at `end`."""
    out = []
    for i in range(days):
        d = end - timedelta(days=days - 1 - i)
        # gentle downward weight drift, flat-ish body fat
        w = start_weight_kg - i * 0.03
        bf = start_bf - i * 0.01
        out.append(
            {
                "measurement_date": d,
                "source_bundle_id": source,
                "metric_name": "body_weight",
                "daily_value": w,
                "observations": 1,
            }
        )
        out.append(
            {
                "measurement_date": d,
                "source_bundle_id": source,
                "metric_name": "body_fat_percentage",
                "daily_value": bf,
                "observations": 1,
            }
        )
    return out


def _goal(phase_start: date, *, phase_end: date | None = None):
    return {
        "id": 7,
        "phase": "lean_cut",
        "phase_start_date": phase_start.isoformat(),
        "phase_end_date": (phase_end.isoformat() if phase_end else None),
        "phase_start_weight_lb": 185.0,
        "phase_start_body_fat_percentage": 21.0,
        "target_weight_lb": 175.0,
        "target_body_fat_percentage": 15.0,
    }


def _today_eastern():
    return datetime.now(timezone.utc).astimezone(bcp.EASTERN).date()


class BodyCompositionProgressContractTests(unittest.TestCase):

    def _run(self, rows, goal):
        with patch.object(bcp, "_load_daily_body_history", return_value=rows), patch.object(
            bcp, "get_active_goal", return_value=goal
        ):
            return bcp.body_composition_progress()

    # ---- 1 / 8 : active goal + history -> 200-shaped "ok" with metrics ----
    def test_ok_response_carries_full_metrics_block(self):
        today = _today_eastern()
        result = self._run(
            _rows(days=40, end=today - timedelta(days=1)),
            _goal(today - timedelta(days=20)),
        )
        self.assertEqual(result["status"], "ok")
        self.assertIn("metrics", result)
        self.assertEqual(
            set(result["metrics"].keys()),
            {"weight", "body_fat_percentage", "fat_mass", "lean_mass"},
        )
        for name, metric in result["metrics"].items():
            self.assertIn("available", metric, name)
            self.assertIsInstance(metric["available"], bool, name)
            self.assertIn("horizons", metric, name)
            for hname, horizon in metric["horizons"].items():
                self.assertIn("sufficient_data", horizon, f"{name}/{hname}")
                self.assertIsInstance(
                    horizon["sufficient_data"], bool, f"{name}/{hname}"
                )

    # ---- 2 : BUILDING_BASELINE still returns detail metrics ----
    def test_building_baseline_phase_still_returns_metrics(self):
        today = _today_eastern()
        # phase started 3 days ago -> phase_age_days 3 < minimum 7
        result = self._run(
            _rows(days=30, end=today - timedelta(days=1)),
            _goal(today - timedelta(days=3)),
        )
        self.assertEqual(result["status"], "ok")
        self.assertLess(result["phase"]["phase_age_days"], 7)
        self.assertEqual(result["phase"]["minimum_status_age_days"], 7)
        self.assertIn("metrics", result)
        self.assertTrue(result["metrics"]["weight"]["available"])

    # ---- 3-6 : horizons all present and typed, never crash ----
    def test_all_ios_horizons_are_present_and_typed(self):
        today = _today_eastern()
        result = self._run(
            _rows(days=60, end=today - timedelta(days=1)),
            _goal(today - timedelta(days=30)),
        )
        weight_horizons = result["metrics"]["weight"]["horizons"]
        for key in ("1W", "4W", "6M", "1Y"):  # the periods the iOS picker uses
            self.assertIn(key, weight_horizons)
            self.assertIn("sufficient_data", weight_horizons[key])

    # ---- 7 : insufficient history -> typed horizon state, not a failure ----
    def test_sparse_history_returns_typed_insufficient_state(self):
        today = _today_eastern()
        # only 4 days of data, phase just started
        result = self._run(
            _rows(days=4, end=today - timedelta(days=1)),
            _goal(today - timedelta(days=2)),
        )
        self.assertEqual(result["status"], "ok")  # still a valid response
        long_horizon = result["metrics"]["weight"]["horizons"]["1Y"]
        self.assertFalse(long_horizon["sufficient_data"])
        self.assertEqual(long_horizon["status"], "insufficient_data")

    # ---- 9 : no active goal -> typed not_ready, no exception, no metrics ----
    def test_no_active_goal_returns_not_ready_state(self):
        result = self._run(_rows(days=20, end=_today_eastern()), None)
        self.assertEqual(result["status"], "not_ready")
        self.assertNotIn("metrics", result)
        self.assertIn("reason", result)

    # ---- 11 : full payload is JSON-serializable (dates / numbers) ----
    def test_response_is_json_serializable(self):
        today = _today_eastern()
        result = self._run(
            _rows(days=45, end=today - timedelta(days=1)),
            _goal(today - timedelta(days=25)),
        )
        encoded = json.dumps(result)  # must not raise
        self.assertIn('"status": "ok"', encoded)
        self.assertIn('"metrics"', encoded)

    # ---- 12 : Hume authoritative current-scoring rule intact ----
    def test_current_scoring_source_is_hume_only(self):
        today = _today_eastern()
        rows = _rows(days=30, end=today - timedelta(days=1))
        # add Fitdays rows: they must not become the current scoring source
        rows += _rows(days=30, end=today - timedelta(days=1), source=FITDAYS)
        result = self._run(rows, _goal(today - timedelta(days=15)))
        self.assertEqual(
            result["source_policy"]["current_scoring_source"], "Hume"
        )
        self.assertFalse(result["source_policy"]["sources_normalized"])
        self.assertIn("fitdays", result["historical_context"])
        self.assertIn("hume", result["historical_context"])

    # ---- 10 : request-scoped DB connection does not change the response ----
    def test_request_scoped_connection_does_not_change_response(self):
        today = _today_eastern()
        rows = _rows(days=40, end=today - timedelta(days=1))
        goal = _goal(today - timedelta(days=20))

        plain = self._run(rows, goal)

        with patch.object(db.psycopg, "connect") as connect:
            connect.return_value.commit.return_value = None
            connect.return_value.rollback.return_value = None
            connect.return_value.close.return_value = None
            with patch.object(
                bcp, "_load_daily_body_history", return_value=rows
            ), patch.object(bcp, "get_active_goal", return_value=goal):
                with db.request_scoped_connection():
                    scoped = bcp.body_composition_progress()

        # generated_at differs; everything else must match
        plain.pop("generated_at", None)
        scoped.pop("generated_at", None)
        self.assertEqual(plain, scoped)


if __name__ == "__main__":
    unittest.main()
