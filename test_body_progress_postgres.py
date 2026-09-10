"""Real PostgreSQL regression coverage for nullable replay cutoffs.

Opt in with BODY_PROGRESS_POSTGRES_TESTS=1 and DATABASE_URL already exported
from Development Keychain. No credentials are stored here. All SQL runs in
read-only transactions; a VALUES CTE shadows the body table with synthetic
samples, so no fixtures, schemas, or application data are written.
"""
import os
import unittest
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

import body_composition_progress as body
import goal_progress as v1
import goal_progress_v2 as v2


@unittest.skipUnless(os.getenv("BODY_PROGRESS_POSTGRES_TESTS") == "1",
                     "requires explicitly enabled Development PostgreSQL validation")
class BodyProgressPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dsn = os.environ.get("DATABASE_URL", "")
        info = conninfo_to_dict(dsn)
        if ("umodirrruxtjoqfjayoy" not in info.get("user", "")
                or "yyrgabalzmgoquleepyw" in dsn):
            raise RuntimeError("Development database guard failed")
        try:
            cls.conn = psycopg.connect(
                dsn, row_factory=dict_row, connect_timeout=10,
                options="-c default_transaction_read_only=on -c statement_timeout=15000",
            )
        except Exception as exc:
            raise RuntimeError("Development test connection failed: " + type(exc).__name__) from None
        cls.addClassCleanup(cls.conn.close)

    def setUp(self):
        self.addCleanup(self.conn.rollback)
        self.conn.execute("SET TRANSACTION READ ONLY")
        self.conn.execute("SET LOCAL TIME ZONE 'UTC'")
        self.assertEqual(self.conn.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"], "on")
        self.cutoff = datetime(2026, 7, 15, 11, tzinfo=timezone.utc)
        self.samples = [
            (self.cutoff - timedelta(days=1), body.HUME_BUNDLE_ID, "body_weight", 82.0),
            (self.cutoff, body.HUME_BUNDLE_ID, "body_weight", 80.0),
            (self.cutoff, body.HUME_BUNDLE_ID, "body_fat_percentage", 20.0),
            (self.cutoff, body.FITDAYS_BUNDLE_ID, "body_weight", 83.0),
            (self.cutoff, "unrelated.source", "body_weight", 999.0),
            (self.cutoff, body.HUME_BUNDLE_ID, "unrelated_metric", 999.0),
        ]
        self.goal = {
            "id": 7, "phase": "lean_cut", "phase_start_date": "2026-07-01",
            "phase_start_weight_lb": 185.0, "phase_start_body_fat_percentage": 21.0,
            "target_weight_lb": 175.0, "target_body_fat_percentage": 15.0,
        }
        self.enterContext(patch.object(body, "get_conn", self.fixture_connection))
        self.enterContext(patch.object(body, "get_active_goal", return_value=self.goal))
        for module in (v1, v2):
            self.enterContext(patch.object(module, "get_active_goal", return_value=self.goal))
            self.enterContext(patch.object(module, "apple_health_trends", return_value={}))
            self.enterContext(patch.object(module, "strength_adherence",
                                           return_value={"status": "not_connected"}))
        self.enterContext(patch.object(v2, "_load_whoop_daily", return_value=[]))
        self.enterContext(patch.object(v2, "_load_apple_steps", return_value=[]))

    @contextmanager
    def fixture_connection(self):
        case = self

        class Connection:
            @contextmanager
            def cursor(self):
                with case.conn.cursor() as real:
                    class Cursor:
                        def execute(self, query, params):
                            case.assertIn("FROM apple_health_body_samples", query)
                            values = sql.SQL(",").join(
                                sql.SQL("({})").format(sql.SQL(",").join(map(sql.Literal, row)))
                                for row in case.samples
                            )
                            cte = sql.SQL(
                                "WITH apple_health_body_samples "
                                "(observed_at, source_bundle_id, metric_name, value) AS "
                                "(VALUES {}) "
                            ).format(values)
                            real.execute(cte + sql.SQL(query), params)

                        def fetchall(self):
                            return real.fetchall()
                    yield Cursor()
        yield Connection()

    def add_future_samples(self):
        for at in (self.cutoff + timedelta(microseconds=1), self.cutoff + timedelta(days=1)):
            for source in (body.HUME_BUNDLE_ID, body.FITDAYS_BUNDLE_ID):
                self.samples.extend([(at, source, "body_weight", 999.0),
                                     (at, source, "body_fat_percentage", 99.0)])

    def test_loader_live_none_executes_on_postgres(self):
        self.assertEqual(len(body._load_daily_body_history(as_of=None)), 4)

    def test_body_progress_live_none_succeeds(self):
        result = body.body_composition_progress(as_of=None)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["source_policy"]["current_scoring_source"], "Hume")

    def test_explicit_historical_timestamp_succeeds(self):
        result = body.body_composition_progress(as_of=self.cutoff)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["generated_at"], self.cutoff.isoformat())

    def test_loader_explicit_date_succeeds(self):
        rows = body._load_daily_body_history(as_of=self.cutoff.date())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["daily_value"], 82.0)

    def test_historical_filter_excludes_future_before_daily_aggregation(self):
        baseline = body._load_daily_body_history(as_of=self.cutoff)
        self.add_future_samples()
        self.assertEqual(body._load_daily_body_history(as_of=self.cutoff), baseline)
        # A real cutoff is active: live results include the added samples.
        self.assertNotEqual(body._load_daily_body_history(as_of=None), baseline)
        exact = [r for r in baseline if r["measurement_date"] == self.cutoff.date()
                 and r["source_bundle_id"] == body.HUME_BUNDLE_ID
                 and r["metric_name"] == "body_weight"]
        self.assertEqual(exact[0]["daily_value"], 80.0)

    def test_future_samples_do_not_change_historical_body_progress(self):
        baseline = body.body_composition_progress(as_of=self.cutoff)
        self.add_future_samples()
        self.assertEqual(body.body_composition_progress(as_of=self.cutoff), baseline)

    def test_goal_progress_v2_live_calls_real_body_loader(self):
        self.assertEqual(v2.goal_progress_v2()["status"], "ok")

    def test_mobile_endpoints_execute_real_body_sql(self):
        # Avoid application startup DDL and unrelated providers; retain the
        # actual route -> V2 -> V1 -> body loader chain and PostgreSQL execute.
        defaults = {
            "SESSION_SECRET": "test-session-secret", "ADMIN_PASSWORD": "test-admin",
            "WHOOP_CLIENT_ID": "test-client", "WHOOP_CLIENT_SECRET": "test-client-secret",
            "TOKEN_ENCRYPTION_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
        }
        with patch.dict(os.environ, {k: os.getenv(k) or v for k, v in defaults.items()}):
            import config
            with patch.object(config, "validate_config"):
                import main
        from fastapi.testclient import TestClient
        with patch.object(main, "require_ingest_key"), \
             patch.object(main, "request_scoped_connection", nullcontext):
            client = TestClient(main.app, base_url="https://testserver")
            self.addCleanup(client.close)
            for path in ("/api/v1/goals/progress", "/api/v1/body-composition/progress"):
                with self.subTest(path=path):
                    response = client.get(path)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["status"], "ok")


if __name__ == "__main__":
    unittest.main()
