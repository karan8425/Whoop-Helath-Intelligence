"""Actual SQL temporal regressions; opt in only against the Development DB.

CTEs supply synthetic rows to the real loaders. No tables/fixtures are written;
all connections enforce READ ONLY. Full-engine checks also exercise the actual
Tonal, WHOOP, Hume and goal SQL against Development history plus future rows.
"""
import contextlib
import io
import os
import re
import unittest
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

import activity_plan
import db
from training_replay import ReplayContext, replay_day


@unittest.skipUnless(os.getenv("REPLAY_TEMPORAL_POSTGRES_TESTS") == "1",
                     "requires explicit Development PostgreSQL opt-in")
class ReplayTemporalPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dsn = os.getenv("DATABASE_URL", "")
        info = conninfo_to_dict(dsn)
        if "umodirrruxtjoqfjayoy" not in info.get("user", "") or "yyrgabalzmgoquleepyw" in dsn:
            raise RuntimeError("Development database guard failed")
        try:
            cls.conn = psycopg.connect(
                dsn, row_factory=dict_row, connect_timeout=10,
                options="-c default_transaction_read_only=on -c statement_timeout=15000",
            )
        except Exception as exc:
            raise RuntimeError("Development connection failed: " + type(exc).__name__) from None
        cls.addClassCleanup(cls.conn.close)

    def setUp(self):
        self.addCleanup(self.conn.rollback)
        self.conn.execute("SET TRANSACTION READ ONLY")
        self.assertEqual(self.conn.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"], "on")
        self.context = ReplayContext.morning(date(2026, 6, 10))
        self.cutoff = self.context.as_of_utc

    @contextlib.contextmanager
    def fixture(self, ctes):
        """Replace only SQL data sources; never mock a loader or calculation."""
        case = self
        class Connection:
            @contextlib.contextmanager
            def cursor(self):
                with case.conn.cursor() as real:
                    class Cursor:
                        def execute(self, query, params=None):
                            query = query.strip()
                            if query == "SET TRANSACTION READ ONLY":
                                real.execute(query)
                                return
                            case.assertTrue(query.upper().startswith(("SELECT", "WITH")))
                            used = {}
                            for table, cte in ctes.items():
                                pattern = r"\b(?:public\.)?" + table + r"\b"
                                if re.search(pattern, query):
                                    query = re.sub(pattern, "fixture_" + table, query)
                                    used[table] = cte
                            if used:
                                prefix = ", ".join("fixture_" + name + " AS (" + definition + ")"
                                                   for name, definition in used.items())
                                query = "WITH " + prefix + (", " + query[5:] if query.upper().startswith("WITH ") else " " + query)
                            real.execute(query, params)
                        def fetchone(self): return real.fetchone()
                        def fetchall(self): return real.fetchall()
                    yield Cursor()
        token = db._active_connection.set(Connection())
        try:
            yield
        finally:
            db._active_connection.reset(token)

    def literal(self, value):
        return sql.Literal(value).as_string(self.conn)

    def activity_sources(self, *, workout_at=None, steps=0, history_days=30,
                         recovery_at=None, profile_at=None, prior_steps=5000,
                         workout_end=None, workout_updated=None):
        t = self.cutoff
        at = workout_at or t - timedelta(days=2)
        end = workout_end or at
        updated = workout_updated or end
        recovery = recovery_at or t - timedelta(days=1)
        profile = profile_at or t - timedelta(days=1)
        lit = self.literal
        return {
            "apple_health_daily_activity":
                f"SELECT {lit(self.context.local_date)} - n AS activity_date, {prior_steps}::double precision AS steps "
                f"FROM generate_series(1,{history_days}) n UNION ALL SELECT {lit(self.context.local_date)}, {steps}::double precision",
            "tonal_workouts": f"SELECT {lit(at)}::timestamptz AS begin_time",
            "whoop_workouts":
                f"SELECT {lit(at)}::timestamptz AS start_time, {lit(end)}::timestamptz AS end_time, "
                f"{lit(at)}::timestamptz AS created_at, {lit(updated)}::timestamptz AS updated_at, "
                "'running'::text AS sport_name, 140::integer AS average_heart_rate, 180::integer AS max_heart_rate "
                "FROM generate_series(1,2)",
            "whoop_recoveries":
                f"SELECT {lit(recovery)}::timestamptz AS created_at, {lit(recovery)}::timestamptz AS updated_at, 60::double precision AS resting_heart_rate",
            "whoop_body_measurements":
                f"SELECT 1 AS id, 190::integer AS max_heart_rate, {lit(profile)}::timestamptz AS observed_at",
        }

    def plan(self, *, live=False, recovery=90, strength_day=False, **kwargs):
        sources = self.activity_sources(**kwargs)
        with self.fixture(sources):
            clock = {"now": self.cutoff.astimezone(ZoneInfo("America/New_York"))} if live else {"as_of": self.cutoff}
            loaded = activity_plan.load_activity_context(**clock)
            result = activity_plan.build_activity_plan(
                goal={"phase": "lean_cut"}, context=loaded,
                strength={"recovery_score": recovery, "available": strength_day,
                          "session_type": "Lower Body" if strength_day else None}, **clock)
        return loaded, result

    def assert_future_workout_excluded(self, at):
        sources = self.activity_sources(workout_at=at)
        empty = dict(sources)
        empty["whoop_workouts"] += " WHERE false"
        empty["tonal_workouts"] += " WHERE false"
        results = []
        for fixture in (empty, sources):
            with self.fixture(fixture):
                results.append(activity_plan.build_activity_plan(as_of=self.cutoff))
        self.assertEqual(results[0], results[1])
        self.assertFalse(results[1]["conditioning_history"]["running_supported"])

    def test_future_workout_invariance(self):
        self.assert_future_workout_excluded(self.cutoff + timedelta(microseconds=1))

    def test_same_day_1800_workout_invariance(self):
        self.assert_future_workout_excluded(self.cutoff + timedelta(hours=11))

    def test_future_day_workout_invariance(self):
        self.assert_future_workout_excluded(self.cutoff + timedelta(days=1))

    def test_cutoff_workouts_included(self):
        loaded, plan = self.plan(workout_at=self.cutoff)
        self.assertEqual(loaded["runs_30"], 2)
        self.assertTrue(plan["conditioning_history"]["running_supported"])

    def test_unfinished_workout_excluded(self):
        loaded, _ = self.plan(workout_at=self.cutoff - timedelta(minutes=5), workout_end=self.cutoff + timedelta(minutes=5))
        self.assertEqual(loaded["runs_30"], 0)

    def test_post_cutoff_score_revision_excluded(self):
        loaded, _ = self.plan(workout_updated=self.cutoff + timedelta(hours=1))
        self.assertEqual(loaded["runs_30"], 0)

    def test_morning_final_steps_invariance(self):
        before, plan_before = self.plan(steps=0)
        after, plan_after = self.plan(steps=15000)
        self.assertEqual(before, after)
        self.assertEqual(plan_before, plan_after)
        self.assertIsNone(after["steps_today"])
        self.assertIsNone(plan_after["steps_so_far"])
        self.assertTrue(plan_after["sessions"])

    def test_prior_complete_day_remains_available(self):
        loaded, _ = self.plan(history_days=1, prior_steps=12345)
        self.assertEqual(loaded["avg_7"], 12345)
        self.assertEqual(loaded["n_7"], 1)

    def test_live_uses_current_steps(self):
        loaded, plan = self.plan(live=True, steps=15000)
        self.assertEqual(loaded["steps_today"], 15000)
        self.assertEqual(plan["steps_so_far"], 15000)
        self.assertEqual(plan["sessions"], [])
        self.assertNotIn("replay_input_boundary", plan)

    def test_live_retains_profile_hr(self):
        loaded, _ = self.plan(live=True, profile_at=self.cutoff + timedelta(days=1))
        self.assertEqual(loaded["profile_max_hr"], 190)

    def test_later_recovery_excluded(self):
        loaded, _ = self.plan(recovery_at=self.cutoff + timedelta(hours=11))
        self.assertIsNone(loaded["resting_hr"])

    def test_future_profile_observation_excluded(self):
        loaded, _ = self.plan(profile_at=self.cutoff + timedelta(days=1))
        self.assertIsNone(loaded["profile_max_hr"])
        self.assertEqual(loaded["observed_max_hr"], 180)

    def test_sparse_steps_remain_unknown(self):
        loaded, plan = self.plan(history_days=0, steps=15000)
        self.assertEqual(loaded["n_7"], 0)
        self.assertIsNone(plan["steps_so_far"])
        self.assertEqual(plan["status"], "partial_data")

    def test_low_recovery_strength_day_remains_easy(self):
        _, plan = self.plan(recovery=20, strength_day=True)
        self.assertTrue(plan["sessions"])
        self.assertTrue(all(s["modality"] == "RECOVERY_WALK" for s in plan["sessions"]))

    def test_equivalent_timezone_cutoffs_match(self):
        _, first = self.plan()
        self.cutoff = self.cutoff.astimezone(ZoneInfo("America/Los_Angeles"))
        _, second = self.plan()
        self.assertEqual(first, second)

    def test_full_b3_b4_future_rows_invariance(self):
        self._full_engine_invariance(timedelta(days=1))

    def test_full_b3_b4_same_day_later_rows_invariance(self):
        self._full_engine_invariance(timedelta(hours=11))

    def _full_engine_invariance(self, delta):
        # Real schema/rows seed full engines; a CTE adds copies timestamped
        # after the fixed cutoff, including sources with no single live mock.
        at = self.cutoff + delta
        changes = {
            "tonal_workouts": {"begin_time": at, "end_time": at},
            "tonal_strength_scores": {"observed_at": at},
            "whoop_daily_metrics": {"metric_date": self.context.local_date, "source_updated_at": at},
            "whoop_workouts": {"start_time": at, "end_time": at, "created_at": at, "updated_at": at},
            "whoop_recoveries": {"created_at": at, "updated_at": at},
            "apple_health_body_samples": {"observed_at": at},
            "health_goal_profiles": {"created_at": at},
        }
        ctes = {}
        for table, fields in changes.items():
            # One existing row is enough to introduce a post-cutoff source.
            pairs = ", ".join(self.literal(k) + ", " + self.literal(v) for k, v in fields.items())
            ctes[table] = (f"SELECT * FROM public.{table} UNION ALL "
                           f"SELECT (jsonb_populate_record(NULL::public.{table}, to_jsonb(seed) || jsonb_build_object({pairs}))).* "
                           f"FROM (SELECT * FROM public.{table} LIMIT 1) seed")
        # Same-day accumulated outcomes must also be invisible to both engines.
        day = self.literal(self.context.local_date)
        ctes["apple_health_daily_activity"] = (
            "SELECT (jsonb_populate_record(NULL::public.apple_health_daily_activity, "
            f"to_jsonb(a) || CASE WHEN activity_date = {day} "
            "THEN jsonb_build_object('steps', 99999, 'active_energy_kcal', 99999) "
            "ELSE '{}'::jsonb END)).* FROM public.apple_health_daily_activity a"
        )
        output = []
        with contextlib.redirect_stdout(io.StringIO()):
            for fixture in ({}, ctes):
                with self.fixture(fixture):
                    output.append(replay_day(self.context, include_details=True, recommendation_history=[]))
        self.assertEqual(output[0]["recommendation"], output[1]["recommendation"])
        self.assertEqual(output[0]["inputs"], output[1]["inputs"])
        self.assertEqual(output[0]["diagnostics"]["training"]["status"], "ok")
        self.assertTrue(output[0]["recommendation"]["exercises"])


if __name__ == "__main__":
    unittest.main()
