"""Real PostgreSQL publication tests. All fixtures/DDL roll back; no live cache writes."""
import os
import time
import unittest
import uuid
from contextlib import ExitStack
from datetime import date
from unittest.mock import patch
import psycopg
from psycopg.rows import dict_row
from psycopg import sql
import db
import whoop_refresh as refresh
import todays_plan_store as plans
import daily_health_intelligence_store as intelligence

DAY = "2026-09-12"
A = {"metric_date": DAY, "source_updated_at":"2026-09-12T05:34:00+00:00"}
B = {**A, "source_updated_at":"2026-09-12T08:15:00+00:00"}

@unittest.skipUnless(os.getenv("MORNING_REFRESH_POSTGRES_TESTS") == "1", "opt-in PostgreSQL")
class AtomicRefreshPostgresTests(unittest.TestCase):
    def setUp(self):
        dsn = os.environ["DATABASE_URL"]
        if "umodirrruxtjoqfjayoy" not in dsn or "yyrgabalzmgoquleepyw" in dsn:
            self.fail("Development database guard failed")
        self.conn = psycopg.connect(dsn, row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        token = db._active_connection.set(self.conn)
        self.addCleanup(db._active_connection.reset, token)
        suffix = uuid.uuid4().hex[:12]
        schema = "morning_v3_" + suffix
        self.conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        self.conn.execute(sql.SQL("SET LOCAL search_path TO {}, public").format(sql.Identifier(schema)))
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(plans, "TABLE_NAME", "morning_v3_plan_" + suffix))
        self.stack.enter_context(patch.object(intelligence, "TABLE_NAME", "morning_v3_intel_" + suffix))
        self.stack.enter_context(patch.object(refresh, "coaching_date", return_value=DAY))
        self.stack.enter_context(patch.object(plans, "_today_local", return_value=date.fromisoformat(DAY)))
        self.stack.enter_context(patch.object(plans, "freshness_status", return_value={"status":"fresh", "can_generate_current_recommendation":True, "source_freshness":B}))
        refresh.mark_started("initial")
        self.publish(A, 1)
    def publish(self, source, generation):
        with db.request_scoped_connection():
            intelligence.save_intelligence({"plan_date":DAY, "source_freshness":source}, {"status":"ok", "model":"fixture", "brief":{"headline":"Fixture"}})
            plans.save_plan({"status":"ok", "plan_date":DAY, "training":{}, "source_freshness":source})
            with self.conn.cursor() as cur:
                refresh.complete(cur, DAY, generation, refresh.data_version(source), source["source_updated_at"], "signature")
    def test_cached_A_during_refresh_and_B_only_after_publish(self):
        generation = refresh.mark_started("recovery.updated")
        start = time.perf_counter()
        a = plans.get_or_build_todays_plan()
        elapsed = time.perf_counter() - start
        self.assertEqual(a["data_version"], refresh.data_version(A))
        self.assertTrue(a["refresh_in_progress"])
        self.publish(B, generation)
        state = refresh.read_state(DAY)
        cached = plans.load_cached_plan(DAY)
        b = refresh.metadata(cached["plan_payload"], state)
        self.assertEqual(b["data_version"], refresh.data_version(B))
        self.assertFalse(b["refresh_in_progress"])
        print(f"MORNING_V3 postgres_cached_route_seconds={elapsed:.4f}", flush=True)
    def test_failure_rolls_back_both_caches_and_completion(self):
        generation = refresh.mark_started("recovery.updated")
        with self.assertRaisesRegex(RuntimeError, "publish failure"):
            with self.conn.transaction():
                self.publish(B, generation)
                raise RuntimeError("publish failure")
        self.assertEqual(refresh.data_version(plans.load_cached_plan(DAY)["plan_payload"]["source_freshness"]), refresh.data_version(A))
        self.assertEqual(refresh.read_state(DAY)["last_completed_data_version"], refresh.data_version(A))
        self.assertTrue(refresh.read_state(DAY)["refresh_in_progress"])
        self.assertEqual(intelligence.load_current_intelligence(DAY)["deterministic_payload"]["source_freshness"], A)
    def test_later_event_remains_pending_after_earlier_publication(self):
        generation = refresh.mark_started("recovery.updated")
        refresh.mark_started("sleep.updated")
        self.publish(B, generation)
        state = refresh.read_state(DAY)
        self.assertTrue(state["refresh_in_progress"])
        self.assertGreater(state["generation"], state["completed_generation"])
    def test_distributed_lock_is_reentrant_only_in_owning_context(self):
        import whoop_webhook_store as webhooks
        key = int(uuid.uuid4().hex[:7], 16)
        with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as other:
            with patch.object(webhooks, "PIPELINE_LOCK_ID", key):
                with webhooks.pipeline_lock() as acquired:
                    self.assertTrue(acquired)
                    with webhooks.pipeline_lock() as nested:
                        self.assertTrue(nested)
                    row = other.execute("SELECT pg_try_advisory_lock(%s) AS acquired", (key,)).fetchone()
                    self.assertFalse(row["acquired"])
                self.assertTrue(other.execute("SELECT pg_try_advisory_lock(%s) AS acquired", (key,)).fetchone()["acquired"])
                other.execute("SELECT pg_advisory_unlock(%s)", (key,))
    def test_same_record_revision_changes_raw_signature_without_new_rows(self):
        # Shadow only source tables with temporary fixtures; live WHOOP rows are untouched.
        for table, key in (("whoop_cycles","id"),("whoop_recoveries","sleep_id"),("whoop_sleeps","id"),("whoop_workouts","id")):
            self.conn.execute(sql.SQL("CREATE TEMP TABLE {} ({} TEXT PRIMARY KEY, updated_at TIMESTAMPTZ)").format(sql.Identifier(table),sql.Identifier(key)))
            self.conn.execute(sql.SQL("INSERT INTO {} VALUES ('one', %s)").format(sql.Identifier(table)), (A["source_updated_at"],))
        before = refresh.raw_source_signature()
        self.conn.execute("UPDATE whoop_recoveries SET updated_at=%s", (B["source_updated_at"],))
        self.assertNotEqual(before, refresh.raw_source_signature())
        self.assertEqual(self.conn.execute("SELECT COUNT(*) AS n FROM whoop_recoveries").fetchone()["n"], 1)

if __name__ == "__main__": unittest.main()
