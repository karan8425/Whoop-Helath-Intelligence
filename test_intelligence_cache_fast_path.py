import unittest
from unittest.mock import Mock, patch

import daily_health_intelligence_store as daily_store
import weekly_health_intelligence_store as weekly_store


# ============================================================
# SHARED FIXTURES
# ============================================================

DAILY_SOURCE_FRESHNESS = {
    "metric_date": "2026-09-04",
    "source_updated_at": "2026-09-04T11:00:00+00:00",
    "metrics_generated_at": "2026-09-04T11:05:00+00:00",
}

DAILY_SOURCE_FRESHNESS_NEWER = {
    "metric_date": "2026-09-04",
    "source_updated_at": "2026-09-04T15:30:00+00:00",
    "metrics_generated_at": "2026-09-04T15:35:00+00:00",
}

WEEKLY_SOURCE_FRESHNESS = {
    "metric_date": "2026-09-04",
    "source_updated_at": "2026-09-04T11:00:00+00:00",
    "metrics_generated_at": "2026-09-04T11:05:00+00:00",
}

WEEKLY_SOURCE_FRESHNESS_NEWER = {
    "metric_date": "2026-09-04",
    "source_updated_at": "2026-09-05T09:00:00+00:00",
    "metrics_generated_at": "2026-09-05T09:05:00+00:00",
}


def daily_freshness(
    *,
    can_generate=True,
    status="fresh",
    source_freshness=None,
    local_today="2026-09-04",
):
    return {
        "status": status,
        "local_today": local_today,
        "latest_physiology_date": (source_freshness or {}).get("metric_date"),
        "age_days": 0 if can_generate else 1,
        "can_generate_current_recommendation": can_generate,
        "source_freshness": (
            DAILY_SOURCE_FRESHNESS
            if source_freshness is None
            else source_freshness
        ),
        "message": "test freshness",
    }


class DailyIntelligenceFastPathTests(unittest.TestCase):

    def cached_row(
        self,
        plan_date="2026-09-04",
        version=1,
        source_freshness=None,
    ):
        return {
            "id": 11,
            "plan_date": plan_date,
            "plan_fingerprint": "daily-fingerprint",
            "intelligence_version": version,
            "model_name": "stored-model",
            "deterministic_payload": {
                "daily_coaching_summary": {"headline": "Stored coaching"},
                "source_freshness": (
                    DAILY_SOURCE_FRESHNESS
                    if source_freshness is None
                    else source_freshness
                ),
            },
            "intelligence_payload": {"headline": "Stored brief"},
        }

    # ----- CASE A: current source + current intelligence -----
    @patch.object(daily_store, "build_daily_health_ai_payload")
    @patch.object(daily_store, "load_current_intelligence")
    @patch.object(daily_store, "freshness_status")
    def test_case_a_current_day_hit_skips_expensive_builder(
        self, freshness, load, build
    ):
        freshness.return_value = daily_freshness()
        load.return_value = self.cached_row()

        result = daily_store.get_or_create_intelligence(generator=Mock())

        build.assert_not_called()
        self.assertEqual(result["cache"]["source"], "stored")
        self.assertEqual(
            result["daily_coaching_summary"]["headline"],
            "Stored coaching",
        )

    # ----- CASE B: stored intelligence older than newer WHOOP source -----
    @patch.object(daily_store, "save_intelligence", return_value={"id": 13})
    @patch.object(daily_store, "load_cached_intelligence", return_value=None)
    @patch.object(daily_store, "build_daily_health_ai_payload")
    @patch.object(daily_store, "load_current_intelligence")
    @patch.object(daily_store, "freshness_status")
    def test_case_b_stale_source_freshness_rejects_fast_path(
        self, freshness, load, build, _cached, _save
    ):
        freshness.return_value = daily_freshness(
            source_freshness=DAILY_SOURCE_FRESHNESS_NEWER
        )
        # Stored row was generated against the OLDER source snapshot.
        load.return_value = self.cached_row(
            source_freshness=DAILY_SOURCE_FRESHNESS
        )
        build.return_value = {"plan_date": "2026-09-04"}
        generator = Mock(return_value={
            "status": "ok", "model": "test", "brief": {"headline": "Rebuilt"}
        })

        result = daily_store.get_or_create_intelligence(generator=generator)

        build.assert_called_once_with()
        generator.assert_called_once()
        self.assertEqual(result["cache"]["source"], "generated")

    # ----- CASE C: current local day, only previous-day physiology -----
    @patch.object(daily_store, "build_daily_health_ai_payload")
    @patch.object(daily_store, "load_current_intelligence")
    @patch.object(daily_store, "freshness_status")
    def test_case_c_pending_freshness_does_not_generate(
        self, freshness, load, build
    ):
        freshness.return_value = daily_freshness(
            can_generate=False,
            status="pending_today",
        )
        generator = Mock()

        result = daily_store.get_or_create_intelligence(generator=generator)

        self.assertEqual(result["status"], "pending_freshness")
        self.assertEqual(result["plan_date"], "2026-09-04")
        build.assert_not_called()
        generator.assert_not_called()
        load.assert_not_called()

    # ----- CASE D: current source arrives after a pending state -----
    @patch.object(daily_store, "save_intelligence", return_value={"id": 14})
    @patch.object(daily_store, "load_cached_intelligence", return_value=None)
    @patch.object(daily_store, "build_daily_health_ai_payload")
    @patch.object(daily_store, "load_current_intelligence", return_value=None)
    @patch.object(daily_store, "freshness_status")
    def test_case_d_current_source_after_pending_builds(
        self, freshness, _load, build, _cached, save
    ):
        freshness.return_value = daily_freshness()
        build.return_value = {"plan_date": "2026-09-04"}
        generator = Mock(return_value={
            "status": "ok", "model": "test", "brief": {"headline": "New day"}
        })

        result = daily_store.get_or_create_intelligence(generator=generator)

        build.assert_called_once_with()
        generator.assert_called_once()
        save.assert_called_once()
        self.assertEqual(result["cache"]["source"], "generated")
        # The saved deterministic payload carries the source freshness marker.
        saved_payload = save.call_args[0][0]
        self.assertEqual(
            saved_payload["source_freshness"], DAILY_SOURCE_FRESHNESS
        )

    # ----- CASE I: wrong date / wrong version still rebuild -----
    def _assert_invalid_cache_rebuilds(self, cached):
        with (
            patch.object(daily_store, "freshness_status", return_value=daily_freshness()),
            patch.object(daily_store, "load_current_intelligence", return_value=cached),
            patch.object(
                daily_store,
                "build_daily_health_ai_payload",
                return_value={"plan_date": "2026-09-04"},
            ) as build,
            patch.object(daily_store, "load_cached_intelligence", return_value=None),
            patch.object(daily_store, "save_intelligence", return_value={"id": 15}),
        ):
            generator = Mock(return_value={
                "status": "ok", "model": "test", "brief": {"headline": "New"}
            })
            result = daily_store.get_or_create_intelligence(generator=generator)

        build.assert_called_once_with()
        generator.assert_called_once()
        self.assertEqual(result["cache"]["source"], "generated")

    def test_case_i_wrong_date_rebuilds(self):
        self._assert_invalid_cache_rebuilds(
            self.cached_row(plan_date="2026-09-03")
        )

    def test_case_i_wrong_version_rebuilds(self):
        self._assert_invalid_cache_rebuilds(
            self.cached_row(version=2)
        )


class WeeklyIntelligenceFastPathTests(unittest.TestCase):

    def cached_row(
        self,
        period="2026-09-04",
        version=1,
        source_freshness=None,
    ):
        return {
            "id": 21,
            "period_end_date": period,
            "analytics_fingerprint": "weekly-fingerprint",
            "intelligence_version": version,
            "model_name": "stored-model",
            "deterministic_payload": {
                "metric_date": period,
                "source_freshness": (
                    WEEKLY_SOURCE_FRESHNESS
                    if source_freshness is None
                    else source_freshness
                ),
            },
            "intelligence_payload": {"headline": "Stored week"},
        }

    # ----- CASE G: weekly intelligence current with source -----
    @patch.object(weekly_store, "build_weekly_health_ai_payload")
    @patch.object(weekly_store, "load_current_intelligence")
    @patch.object(
        weekly_store,
        "weekly_source_freshness",
        return_value=WEEKLY_SOURCE_FRESHNESS,
    )
    def test_case_g_current_period_hit_skips_expensive_builder(
        self, _freshness, load, build
    ):
        load.return_value = self.cached_row()

        result = weekly_store.get_or_create_intelligence(generator=Mock())

        build.assert_not_called()
        self.assertEqual(result["cache"]["source"], "stored")

    # ----- CASE H: weekly source updated after stored intelligence -----
    @patch.object(weekly_store, "save_intelligence", return_value={"id": 23})
    @patch.object(weekly_store, "load_cached_intelligence", return_value=None)
    @patch.object(weekly_store, "build_weekly_health_ai_payload")
    @patch.object(weekly_store, "load_current_intelligence")
    @patch.object(
        weekly_store,
        "weekly_source_freshness",
        return_value=WEEKLY_SOURCE_FRESHNESS_NEWER,
    )
    def test_case_h_stale_source_freshness_rebuilds(
        self, _freshness, load, build, _cached, _save
    ):
        load.return_value = self.cached_row(
            source_freshness=WEEKLY_SOURCE_FRESHNESS
        )
        build.return_value = {"metric_date": "2026-09-04"}
        generator = Mock(return_value={
            "status": "ok", "model": "test", "brief": {"headline": "Rebuilt week"}
        })

        result = weekly_store.get_or_create_intelligence(generator=generator)

        build.assert_called_once_with()
        generator.assert_called_once()
        self.assertEqual(result["cache"]["source"], "generated")

    @patch.object(weekly_store, "save_intelligence", return_value={"id": 22})
    @patch.object(weekly_store, "load_cached_intelligence", return_value=None)
    @patch.object(weekly_store, "build_weekly_health_ai_payload")
    @patch.object(weekly_store, "load_current_intelligence", return_value=None)
    @patch.object(
        weekly_store,
        "weekly_source_freshness",
        return_value=WEEKLY_SOURCE_FRESHNESS,
    )
    def test_miss_builds_generates_and_saves(
        self, _freshness, _load, build, _cached, save
    ):
        build.return_value = {"metric_date": "2026-09-04"}
        generator = Mock(return_value={
            "status": "ok", "model": "test", "brief": {"headline": "New"}
        })

        result = weekly_store.get_or_create_intelligence(generator=generator)

        build.assert_called_once_with()
        generator.assert_called_once()
        save.assert_called_once()
        self.assertEqual(result["cache"]["source"], "generated")
        saved_payload = save.call_args[0][0]
        self.assertEqual(
            saved_payload["source_freshness"], WEEKLY_SOURCE_FRESHNESS
        )

    # ----- CASE I: wrong period / wrong version still rebuild -----
    def _assert_invalid_cache_rebuilds(self, cached):
        with (
            patch.object(
                weekly_store,
                "weekly_source_freshness",
                return_value=WEEKLY_SOURCE_FRESHNESS,
            ),
            patch.object(weekly_store, "load_current_intelligence", return_value=cached),
            patch.object(
                weekly_store,
                "build_weekly_health_ai_payload",
                return_value={"metric_date": "2026-09-04"},
            ) as build,
            patch.object(weekly_store, "load_cached_intelligence", return_value=None),
            patch.object(weekly_store, "save_intelligence", return_value={"id": 24}),
        ):
            generator = Mock(return_value={
                "status": "ok", "model": "test", "brief": {"headline": "New"}
            })
            result = weekly_store.get_or_create_intelligence(generator=generator)

        build.assert_called_once_with()
        generator.assert_called_once()
        self.assertEqual(result["cache"]["source"], "generated")

    def test_case_i_wrong_period_rebuilds(self):
        self._assert_invalid_cache_rebuilds(
            self.cached_row(period="2026-09-03")
        )

    def test_case_i_wrong_version_rebuilds(self):
        self._assert_invalid_cache_rebuilds(
            self.cached_row(version=2)
        )


if __name__ == "__main__":
    unittest.main()
