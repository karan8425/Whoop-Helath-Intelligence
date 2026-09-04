import unittest
from unittest.mock import Mock, patch

import daily_health_intelligence_store as daily_store
import weekly_health_intelligence_store as weekly_store


class DailyIntelligenceFastPathTests(unittest.TestCase):

    def cached_row(self, plan_date="2026-09-04", version=1):
        return {
            "id": 11,
            "plan_date": plan_date,
            "plan_fingerprint": "daily-fingerprint",
            "intelligence_version": version,
            "model_name": "stored-model",
            "deterministic_payload": {
                "daily_coaching_summary": {"headline": "Stored coaching"},
            },
            "intelligence_payload": {"headline": "Stored brief"},
        }

    @patch.object(daily_store, "build_daily_health_ai_payload")
    @patch.object(daily_store, "load_current_intelligence")
    @patch.object(daily_store, "_current_plan_date", return_value="2026-09-04")
    def test_current_day_hit_skips_expensive_builder(
        self, _date, load, build
    ):
        load.return_value = self.cached_row()

        result = daily_store.get_or_create_intelligence(generator=Mock())

        build.assert_not_called()
        self.assertEqual(result["cache"]["source"], "stored")
        self.assertEqual(
            result["daily_coaching_summary"]["headline"],
            "Stored coaching",
        )

    @patch.object(daily_store, "save_intelligence")
    @patch.object(daily_store, "load_cached_intelligence", return_value=None)
    @patch.object(daily_store, "build_daily_health_ai_payload")
    @patch.object(daily_store, "load_current_intelligence", return_value=None)
    @patch.object(daily_store, "_current_plan_date", return_value="2026-09-04")
    def test_miss_builds_generates_and_saves(
        self, _date, _load, build, _fingerprint_load, save
    ):
        payload = {"plan_date": "2026-09-04"}
        build.return_value = payload
        generator = Mock(return_value={
            "status": "ok", "model": "test", "brief": {"headline": "New"}
        })
        save.return_value = {"id": 12}

        result = daily_store.get_or_create_intelligence(generator=generator)

        build.assert_called_once_with()
        generator.assert_called_once_with(payload)
        save.assert_called_once()
        self.assertEqual(result["cache"]["source"], "generated")

    def _assert_invalid_cache_rebuilds(self, cached):
        payload = {"plan_date": "2026-09-04"}
        generator = Mock(return_value={
            "status": "ok", "model": "test", "brief": {"headline": "New"}
        })

        with (
            patch.object(daily_store, "_current_plan_date", return_value="2026-09-04"),
            patch.object(daily_store, "load_current_intelligence", return_value=cached),
            patch.object(daily_store, "build_daily_health_ai_payload", return_value=payload) as build,
            patch.object(daily_store, "load_cached_intelligence", return_value=None),
            patch.object(daily_store, "save_intelligence", return_value={"id": 13}),
        ):
            result = daily_store.get_or_create_intelligence(generator=generator)

        build.assert_called_once_with()
        generator.assert_called_once_with(payload)
        self.assertEqual(result["cache"]["source"], "generated")

    def test_wrong_date_rebuilds(self):
        self._assert_invalid_cache_rebuilds(
            self.cached_row(plan_date="2026-09-03")
        )

    def test_wrong_version_rebuilds(self):
        self._assert_invalid_cache_rebuilds(
            self.cached_row(version=2)
        )


class WeeklyIntelligenceFastPathTests(unittest.TestCase):

    def cached_row(self, period="2026-09-04", version=1):
        return {
            "id": 21,
            "period_end_date": period,
            "analytics_fingerprint": "weekly-fingerprint",
            "intelligence_version": version,
            "model_name": "stored-model",
            "deterministic_payload": {"metric_date": period},
            "intelligence_payload": {"headline": "Stored week"},
        }

    @patch.object(weekly_store, "build_weekly_health_ai_payload")
    @patch.object(weekly_store, "load_current_intelligence")
    @patch.object(weekly_store, "_latest_metric_date", return_value="2026-09-04")
    def test_current_period_hit_skips_expensive_builder(
        self, _date, load, build
    ):
        load.return_value = self.cached_row()

        result = weekly_store.get_or_create_intelligence(generator=Mock())

        build.assert_not_called()
        self.assertEqual(result["cache"]["source"], "stored")

    @patch.object(weekly_store, "save_intelligence")
    @patch.object(weekly_store, "load_cached_intelligence", return_value=None)
    @patch.object(weekly_store, "build_weekly_health_ai_payload")
    @patch.object(weekly_store, "load_current_intelligence", return_value=None)
    @patch.object(weekly_store, "_latest_metric_date", return_value="2026-09-04")
    def test_miss_builds_generates_and_saves(
        self, _date, _load, build, _fingerprint_load, save
    ):
        payload = {"metric_date": "2026-09-04"}
        build.return_value = payload
        generator = Mock(return_value={
            "status": "ok", "model": "test", "brief": {"headline": "New"}
        })
        save.return_value = {"id": 22}

        result = weekly_store.get_or_create_intelligence(generator=generator)

        build.assert_called_once_with()
        generator.assert_called_once_with()
        save.assert_called_once()
        self.assertEqual(result["cache"]["source"], "generated")

    def _assert_invalid_cache_rebuilds(self, cached):
        payload = {"metric_date": "2026-09-04"}
        generator = Mock(return_value={
            "status": "ok", "model": "test", "brief": {"headline": "New"}
        })

        with (
            patch.object(weekly_store, "_latest_metric_date", return_value="2026-09-04"),
            patch.object(weekly_store, "load_current_intelligence", return_value=cached),
            patch.object(weekly_store, "build_weekly_health_ai_payload", return_value=payload) as build,
            patch.object(weekly_store, "load_cached_intelligence", return_value=None),
            patch.object(weekly_store, "save_intelligence", return_value={"id": 23}),
        ):
            result = weekly_store.get_or_create_intelligence(generator=generator)

        build.assert_called_once_with()
        generator.assert_called_once_with()
        self.assertEqual(result["cache"]["source"], "generated")

    def test_wrong_period_rebuilds(self):
        self._assert_invalid_cache_rebuilds(
            self.cached_row(period="2026-09-03")
        )

    def test_wrong_version_rebuilds(self):
        self._assert_invalid_cache_rebuilds(
            self.cached_row(version=2)
        )


if __name__ == "__main__":
    unittest.main()
