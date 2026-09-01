import json
import sys
import types
import unittest
from contextlib import ExitStack
from datetime import date
from unittest.mock import AsyncMock, Mock, patch


_DEPENDENCY_MODULES = (
    "psycopg",
    "psycopg.types",
    "psycopg.types.json",
    "db",
    "analytics",
    "baselines",
    "sync",
    "recommendations",
    "freshness",
    "daily_health_intelligence_store",
    "todays_plan",
    "fastapi",
    "whoop_webhook_store",
)
_ORIGINAL_MODULES = {
    name: sys.modules.get(name)
    for name in _DEPENDENCY_MODULES
}


def _module(name, **attributes):
    module = types.ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


# Keep this orchestration test independent of production-only database and
# HTTP packages. Every imported collaborator is replaced again with a focused
# mock in the individual tests.
psycopg = _module("psycopg")
psycopg_types = _module("psycopg.types")
psycopg_json = _module("psycopg.types.json", Jsonb=lambda value: value)
psycopg.types = psycopg_types
psycopg_types.json = psycopg_json

_module("db", get_conn=Mock(), init_db=Mock())
_module("analytics", init_analytics=Mock(), rebuild_daily_metrics=Mock())
_module("baselines", init_baselines=Mock(), rebuild_baselines=Mock())
_module("sync", incremental_sync=AsyncMock())
_module("recommendations", daily_recommendation=Mock())
_module("freshness", freshness_status=Mock())
_module(
    "daily_health_intelligence_store",
    get_daily_health_intelligence=Mock(),
)
_module("todays_plan", build_todays_plan=Mock())


class _Router:
    def post(self, *args, **kwargs):
        return lambda function: function


_module(
    "fastapi",
    APIRouter=lambda: _Router(),
    BackgroundTasks=object,
    HTTPException=Exception,
    Request=object,
)
_module(
    "whoop_webhook_store",
    init_whoop_webhook_tables=Mock(),
    store_webhook_event=Mock(),
    mark_pipeline_started=Mock(),
    mark_pipeline_completed=Mock(),
    mark_pipeline_skipped=Mock(),
    mark_pipeline_failed=Mock(),
    pipeline_lock=Mock(),
)

import daily_job
import todays_plan_store
import whoop_webhook

for _name, _original in _ORIGINAL_MODULES.items():
    if _original is None:
        sys.modules.pop(_name, None)
    else:
        sys.modules[_name] = _original


class DailyPipelineCacheInvalidationTests(unittest.TestCase):

    def _successful_pipeline_patches(self, events):
        freshness = {
            "status": "current",
            "can_generate_current_recommendation": True,
            "latest_physiology_date": "2026-08-31",
        }
        intelligence = {
            "status": "ok",
            "brief": {"headline": "Ready"},
            "cache": "refreshed",
        }

        return {
            "init_db": patch.object(daily_job, "init_db"),
            "init_analytics": patch.object(daily_job, "init_analytics"),
            "init_baselines": patch.object(daily_job, "init_baselines"),
            "init_automation_tables": patch.object(
                daily_job,
                "init_automation_tables",
            ),
            "start_run": patch.object(daily_job, "start_run", return_value=17),
            "incremental_sync": patch.object(
                daily_job,
                "incremental_sync",
                new=AsyncMock(
                    side_effect=lambda: events.append("sync") or {"new_rows": {}},
                ),
            ),
            "rebuild_daily_metrics": patch.object(
                daily_job,
                "rebuild_daily_metrics",
                side_effect=lambda: events.append("daily_metrics") or {},
            ),
            "rebuild_baselines": patch.object(
                daily_job,
                "rebuild_baselines",
                side_effect=lambda: events.append("baselines") or {},
            ),
            "freshness_status": patch.object(
                daily_job,
                "freshness_status",
                side_effect=lambda: events.append("freshness") or freshness,
            ),
            "daily_recommendation": patch.object(
                daily_job,
                "daily_recommendation",
                side_effect=lambda: events.append("recommendation") or {
                    "metric_date": date(2026, 8, 31),
                    "training_recommendation": "Active Recovery",
                    "overall_status": "recover",
                },
            ),
            "get_daily_health_intelligence": patch.object(
                daily_job,
                "get_daily_health_intelligence",
                side_effect=lambda **kwargs: events.append("intelligence")
                or intelligence,
            ),
            "store_intelligence": patch.object(
                daily_job,
                "store_intelligence",
                side_effect=lambda *args: events.append("store")
                or date(2026, 8, 31),
            ),
            "finish_run": patch.object(
                daily_job,
                "finish_run",
                side_effect=lambda *args: events.append("finish"),
            ),
            "fail_run": patch.object(daily_job, "fail_run"),
            "invalidate_todays_plan": patch.object(
                daily_job,
                "invalidate_todays_plan",
                side_effect=lambda: events.append("invalidate")
                or date(2026, 8, 31),
            ),
        }

    def test_success_invalidates_after_refresh_and_completed_audit(self):
        events = []
        patches = self._successful_pipeline_patches(events)

        with ExitStack() as stack:
            mocks = {name: stack.enter_context(item) for name, item in patches.items()}
            result = daily_job.run_daily_pipeline()

        self.assertEqual(result["status"], "completed")
        self.assertLess(events.index("sync"), events.index("invalidate"))
        self.assertLess(events.index("daily_metrics"), events.index("invalidate"))
        self.assertLess(events.index("intelligence"), events.index("invalidate"))
        self.assertLess(events.index("finish"), events.index("invalidate"))
        mocks["invalidate_todays_plan"].assert_called_once_with()
        mocks["fail_run"].assert_not_called()

    def test_failed_pipeline_does_not_invalidate(self):
        with (
            patch.object(daily_job, "init_db"),
            patch.object(daily_job, "init_analytics"),
            patch.object(daily_job, "init_baselines"),
            patch.object(daily_job, "init_automation_tables"),
            patch.object(daily_job, "start_run", return_value=18),
            patch.object(
                daily_job,
                "incremental_sync",
                new=AsyncMock(side_effect=RuntimeError("sync failed")),
            ),
            patch.object(daily_job, "fail_run") as fail_run,
            patch.object(daily_job, "invalidate_todays_plan") as invalidate,
        ):
            with self.assertRaisesRegex(RuntimeError, "sync failed"):
                daily_job.run_daily_pipeline()

        fail_run.assert_called_once()
        invalidate.assert_not_called()

    def test_degraded_intelligence_persists_and_invalidates(self):
        events = []
        patches = self._successful_pipeline_patches(events)
        degraded = {
            "status": "ok",
            "brief": {"headline": "Deterministic fallback"},
            "cache": {"source": "forced_refresh", "llm_called": False},
            "ai_synthesis_status": "degraded",
        }
        patches["get_daily_health_intelligence"] = patch.object(
            daily_job,
            "get_daily_health_intelligence",
            side_effect=lambda **kwargs: events.append("intelligence") or degraded,
        )

        with ExitStack() as stack:
            mocks = {name: stack.enter_context(item) for name, item in patches.items()}
            result = daily_job.run_daily_pipeline()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["ai_synthesis_status"], "degraded")
        mocks["store_intelligence"].assert_called_once()
        mocks["finish_run"].assert_called_once()
        mocks["invalidate_todays_plan"].assert_called_once()
        mocks["fail_run"].assert_not_called()

    def test_unexpected_intelligence_error_still_fails_pipeline(self):
        events = []
        patches = self._successful_pipeline_patches(events)
        patches["get_daily_health_intelligence"] = patch.object(
            daily_job,
            "get_daily_health_intelligence",
            side_effect=ValueError("programming defect"),
        )

        with ExitStack() as stack:
            mocks = {name: stack.enter_context(item) for name, item in patches.items()}
            with self.assertRaisesRegex(ValueError, "programming defect"):
                daily_job.run_daily_pipeline()

        mocks["store_intelligence"].assert_not_called()
        mocks["finish_run"].assert_not_called()
        mocks["invalidate_todays_plan"].assert_not_called()
        mocks["fail_run"].assert_called_once()

    def test_sleep_webhook_only_schedules_delayed_pipeline(self):
        payload = {
            "type": "sleep.updated",
            "trace_id": "trace-1",
            "id": "sleep-1",
            "user_id": "user-1",
        }
        request = Mock()
        request.headers = {
            "X-WHOOP-Signature-Timestamp": "timestamp",
            "X-WHOOP-Signature": "signature",
        }
        request.body = AsyncMock(return_value=json.dumps(payload).encode("utf-8"))
        background_tasks = Mock()

        with (
            patch.object(whoop_webhook, "_validate_timestamp"),
            patch.object(whoop_webhook, "_validate_signature"),
            patch.object(whoop_webhook, "init_whoop_webhook_tables"),
            patch.object(whoop_webhook, "store_webhook_event", return_value=21),
            patch.object(daily_job, "invalidate_todays_plan") as invalidate,
        ):
            result = __import__("asyncio").run(
                whoop_webhook.receive_whoop_webhook(request, background_tasks)
            )

        self.assertEqual(result["trigger_mode"], "wait_for_recovery")
        background_tasks.add_task.assert_called_once_with(
            whoop_webhook._run_sleep_pipeline,
            21,
            "trace-1",
            "sleep.updated",
        )
        invalidate.assert_not_called()


class TodaysPlanCacheBehaviorTests(unittest.TestCase):

    def test_cache_hit_still_uses_saved_plan(self):
        cached_plan = {"status": "ok", "headline": "cached"}
        with (
            patch.object(
                todays_plan_store,
                "load_cached_plan",
                return_value={"plan_payload": cached_plan},
            ),
            patch.object(todays_plan_store, "_cache_is_fresh", return_value=True),
            patch.object(todays_plan_store, "build_todays_plan") as build,
        ):
            result = todays_plan_store.get_or_build_todays_plan()

        self.assertEqual(result, cached_plan)
        build.assert_not_called()

    def test_cache_miss_still_builds_and_saves_plan(self):
        built_plan = {"status": "ok", "headline": "fresh"}
        with (
            patch.object(todays_plan_store, "load_cached_plan", return_value=None),
            patch.object(
                todays_plan_store,
                "build_todays_plan",
                return_value=built_plan,
            ) as build,
            patch.object(todays_plan_store, "save_plan") as save,
        ):
            result = todays_plan_store.get_or_build_todays_plan()

        self.assertEqual(result, built_plan)
        build.assert_called_once_with()
        save.assert_called_once_with(built_plan)


if __name__ == "__main__":
    unittest.main()
