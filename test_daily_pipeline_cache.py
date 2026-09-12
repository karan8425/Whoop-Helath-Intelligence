"""Morning V3 replaces invalidation assertions with publication invariants."""
import copy
import time
import unittest
from contextlib import ExitStack, nullcontext
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import daily_job as job
import todays_plan_store as store
from whoop_refresh import data_version, metadata

DAY = "2026-09-12"
A = {"metric_date": DAY, "source_updated_at": "2026-09-12T05:34:00+00:00", "metrics_generated_at": "2026-09-12T05:35:00+00:00"}
B = {**A, "source_updated_at": "2026-09-12T08:15:00+00:00"}
def plan(source=A):
    return {"status": "ok", "plan_date": DAY, "source_freshness": dict(source), "training": {}}
def fresh(source=A):
    return {"status": "fresh", "local_today": DAY, "can_generate_current_recommendation": True, "source_freshness": source}
def cache(source=A):
    return {"plan_payload": plan(source), "updated_at": datetime.now(timezone.utc)}

class RevisionTests(unittest.TestCase):
    def test_revision_changes(self):
        self.assertNotEqual(data_version(A), data_version(B))
    def test_generation_timestamp_does_not_change_revision(self):
        self.assertEqual(data_version(A), data_version({**A, "metrics_generated_at": "later"}))
    def test_timezone_normalization(self):
        self.assertEqual(data_version(A), data_version({**A, "source_updated_at": "2026-09-12T01:34:00-04:00"}))
    def test_non_max_resource_revision_also_changes_version(self):
        self.assertNotEqual(data_version({**A, "source_revision":"raw-A"}), data_version({**A, "source_revision":"raw-B"}))
    def test_identical_source_fingerprint_is_stable(self):
        self.assertEqual(data_version({**A, "source_revision":"raw-A"}), data_version({**A, "source_revision":"raw-A", "metrics_generated_at":"later"}))
    def test_missing_timestamp_has_no_fabricated_version(self):
        self.assertIsNone(data_version({"metric_date": DAY}))
    def test_metadata_identifies_cached_not_latest_source(self):
        result = metadata(plan(A), {"refresh_in_progress": True, "latest_seen_source_updated_at": B["source_updated_at"]})
        self.assertEqual(result["data_version"], data_version(A))
        self.assertTrue(result["freshness"]["refresh_in_progress"])

class CacheTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(store, "_today_local", return_value=date.fromisoformat(DAY)))
        self.fresh = self.stack.enter_context(patch.object(store, "freshness_status", return_value=fresh(B)))
        self.state = self.stack.enter_context(patch.object(store, "read_state", return_value={"refresh_in_progress": True}))
        self.cached = self.stack.enter_context(patch.object(store, "load_cached_plan", return_value=cache(A)))
        self.build = self.stack.enter_context(patch.object(store, "build_todays_plan", return_value=plan(B)))
        self.stack.enter_context(patch.object(store, "raw_source_signature", return_value=None))
        self.save = self.stack.enter_context(patch.object(store, "save_plan"))
        self.stack.enter_context(patch("whoop_webhook_store.pipeline_lock", side_effect=lambda: nullcontext(True)))
        self.stack.enter_context(patch.object(store, "build_activity_plan", return_value={}))
    def test_fresh_but_updating_serves_A_without_build_or_activity(self):
        with patch.object(store, "build_activity_plan") as activity:
            start = time.perf_counter()
            result = store.get_or_build_todays_plan()
            elapsed = time.perf_counter() - start
        self.assertEqual(result["data_version"], data_version(A))
        self.assertTrue(result["refresh_in_progress"])
        self.build.assert_not_called()
        activity.assert_not_called()
        print(f"MORNING_V3 simulated_swr_seconds={elapsed:.6f}")
    def test_no_cache_while_updating_is_pending_not_duplicate_build(self):
        self.cached.return_value = None
        self.assertEqual(store.get_or_build_todays_plan()["status"], "pending_freshness")
        self.build.assert_not_called()
    def test_matching_settled_source_is_cache_hit(self):
        self.state.return_value = {}
        self.fresh.return_value = fresh(A)
        self.assertEqual(store.get_or_build_todays_plan()["data_version"], data_version(A))
        self.build.assert_not_called()
    def test_changed_settled_source_builds_and_saves(self):
        self.state.return_value = {}
        self.assertEqual(store.get_or_build_todays_plan()["data_version"], data_version(B))
        self.build.assert_called_once()
        self.save.assert_called_once()
    def test_request_racing_pipeline_lock_serves_cached_plan(self):
        self.state.return_value = {}
        with patch("whoop_webhook_store.pipeline_lock", side_effect=lambda: nullcontext(False)):
            result = store.get_or_build_todays_plan()
        self.assertTrue(result["refresh_in_progress"])
        self.assertEqual(result["data_version"], data_version(A))
        self.build.assert_not_called()
    def test_cold_settled_cache_builds_once(self):
        self.state.return_value = {}
        self.cached.return_value = None
        self.assertEqual(store.get_or_build_todays_plan()["status"], "ok")
        self.build.assert_called_once()
    def test_stale_guard_precedes_cached_plan(self):
        self.fresh.return_value = {"status": "stale", "can_generate_current_recommendation": False}
        self.assertEqual(store.get_or_build_todays_plan()["status"], "stale_data")
        self.build.assert_not_called()
    def test_pending_today_guard_is_preserved(self):
        self.fresh.return_value = {"status": "pending_today", "can_generate_current_recommendation": False}
        self.assertEqual(store.get_or_build_todays_plan()["status"], "pending_freshness")
        self.build.assert_not_called()
    def test_sep12_A_updating_A_then_B(self):
        self.fresh.return_value = fresh(A)
        self.state.return_value = {}
        t1 = store.get_or_build_todays_plan()
        self.state.return_value = {"refresh_in_progress": True}
        self.fresh.return_value = fresh(B)
        t3 = store.get_or_build_todays_plan()
        self.cached.return_value = cache(B)
        self.state.return_value = {}
        t5 = store.get_or_build_todays_plan()
        self.assertEqual([x["data_version"] for x in (t1,t3,t5)], [data_version(A),data_version(A),data_version(B)])
        self.assertEqual([x["refresh_in_progress"] for x in (t1,t3,t5)], [False,True,False])
        self.build.assert_not_called()

class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.events = []
        values = {"coaching_date": DAY, "mark_started": 1, "read_state": {"generation": 1},
                  "raw_source_signature": "B", "load_cached_plan": cache(A), "start_run": 1,
                  "freshness_status": fresh(B), "build_todays_plan": plan(B),
                  "daily_recommendation": {"metric_date": DAY}, "build_daily_health_ai_payload": {"plan_date": DAY},
                  "generate_daily_health_intelligence": {"status": "ok", "brief": {"headline":"new"}},
                  "save_intelligence": {}, "_stored_response": {"status":"ok", "cache": {}, "brief": {}},
                  "store_intelligence": DAY}
        names = set(values) | {"init_db", "init_analytics", "init_baselines", "init_automation_tables",
            "rebuild_daily_metrics", "rebuild_baselines", "finish_run", "fail_run", "save_plan", "complete", "mark_failed", "observe_source"}
        self.mocks = {}
        for name in names:
            def action(*args, _name=name, **kwargs):
                self.events.append(_name)
                return copy.deepcopy(values.get(_name, {}))
            self.mocks[name] = self.stack.enter_context(patch.object(job, name, side_effect=action))
        self.stack.enter_context(patch.object(job, "incremental_sync", new=AsyncMock(return_value={"new_rows": {"recoveries": 0}})))
        self.stack.enter_context(patch.object(job, "request_scoped_connection", side_effect=lambda: nullcontext()))
        self.stack.enter_context(patch.object(job, "get_conn", return_value=MagicMock()))
    def run_pipeline(self):
        return job.run_daily_pipeline(_lock_held=True)
    def test_success_builds_once_and_publishes_before_completion_and_audit(self):
        self.assertEqual(self.run_pipeline()["status"], "completed")
        for name in ("build_todays_plan", "generate_daily_health_intelligence", "save_plan", "complete"):
            self.mocks[name].assert_called_once()
        self.assertLess(self.events.index("save_plan"), self.events.index("complete"))
        self.assertLess(self.events.index("complete"), self.events.index("finish_run"))
    def test_failed_build_preserves_last_good_cache(self):
        self.mocks["build_todays_plan"].side_effect = RuntimeError("build failed")
        with self.assertRaises(RuntimeError): self.run_pipeline()
        self.mocks["save_plan"].assert_not_called()
        self.mocks["complete"].assert_not_called()
        self.mocks["mark_failed"].assert_called_once()
    def test_ai_programming_failure_preserves_cache(self):
        self.mocks["generate_daily_health_intelligence"].side_effect = ValueError("bug")
        with self.assertRaises(ValueError): self.run_pipeline()
        self.mocks["save_plan"].assert_not_called()
    def test_pending_does_not_build(self):
        self.mocks["freshness_status"].side_effect = lambda: {"status":"pending_today", "can_generate_current_recommendation":False}
        self.assertEqual(self.run_pipeline()["status"], "pending_freshness")
        self.mocks["build_todays_plan"].assert_not_called()
    def test_zero_rows_unchanged_revision_skips_all_expensive_work(self):
        self.mocks["read_state"].side_effect = lambda *a: {"generation":2, "source_signature":"B", "last_completed_data_version":data_version(A)}
        self.assertTrue(self.run_pipeline()["rebuild_skipped"])
        for name in ("rebuild_daily_metrics", "rebuild_baselines", "build_todays_plan", "generate_daily_health_intelligence"):
            self.mocks[name].assert_not_called()
    def test_zero_rows_newer_timestamp_still_rebuilds(self):
        self.mocks["read_state"].side_effect = lambda *a: {"generation":2, "source_signature":"A", "last_completed_data_version":data_version(A)}
        self.assertEqual(self.run_pipeline()["status"], "completed")
        self.mocks["build_todays_plan"].assert_called_once()
    def test_failed_prior_revision_is_retried_even_when_raw_source_unchanged(self):
        self.mocks["read_state"].side_effect = lambda *a: {"generation":2, "source_signature":"A"}
        self.run_pipeline()
        self.mocks["build_todays_plan"].assert_called_once()
    def test_fallback_intelligence_can_publish(self):
        self.mocks["generate_daily_health_intelligence"].side_effect = lambda *a: {"status":"ok", "ai_synthesis_status":"degraded"}
        result = self.run_pipeline()
        self.assertFalse(result["intelligence_cache"]["llm_called"])
        self.mocks["save_plan"].assert_called_once()

if __name__ == "__main__": unittest.main()
