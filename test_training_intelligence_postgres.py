"""Real-PostgreSQL tests for the muscle stimulus ledger's actual SQL
(training_intelligence.stimulus.ledger.load_rows), analogous to
test_replay_temporal_postgres.py / test_morning_refresh_postgres.py.

tonal_workouts / tonal_sets / tonal_movements / tonal_workout_overrides
are pre-existing production tables with no in-repo CREATE TABLE (they
predate this milestone), so this suite cannot recreate them in a scratch
schema the way test_morning_refresh_postgres.py does for tables owned by
Python `ensure_table()` calls. Instead every test runs inside one
transaction that is ALWAYS rolled back (never committed), using
obviously-namespaced fixture ids ("tki_test_..."), so no real Tonal
history is ever mutated even if a test fails.

Opt-in: requires DATABASE_URL pointed at the Development database and
TRAINING_INTELLIGENCE_POSTGRES_TESTS=1. Not executed in this session -
see TRAINING_INTELLIGENCE_TKI12_REPORT.md for why, and what running it
would additionally validate.
"""

import os
import unittest
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row

from training_intelligence.stimulus.ledger import build_muscle_stimulus_ledger


@unittest.skipUnless(
    os.getenv("TRAINING_INTELLIGENCE_POSTGRES_TESTS") == "1",
    "opt-in PostgreSQL",
)
class MuscleStimulusLedgerPostgresTests(unittest.TestCase):
    def setUp(self):
        dsn = os.environ["DATABASE_URL"]
        if "umodirrruxtjoqfjayoy" not in dsn or "yyrgabalzmgoquleepyw" in dsn:
            self.fail("Development database guard failed")
        self.conn = psycopg.connect(dsn, row_factory=dict_row, autocommit=False)
        self.addCleanup(self.conn.close)
        # Never committed - see module docstring.
        self.addCleanup(self.conn.rollback)

    def _insert_workout(self, activity_id, begin_time, movement_id="tki_test_lat_pulldown",
                         movement_name="Lat Pulldown", muscle_groups=None,
                         rep_count=10, volume=200.0):
        muscle_groups = muscle_groups if muscle_groups is not None else ["Back", "Biceps"]
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tonal_movements (movement_id, name, muscle_groups, synced_at)
                VALUES (%s, %s, %s::jsonb, NOW())
                ON CONFLICT (movement_id) DO UPDATE SET
                    name = EXCLUDED.name, muscle_groups = EXCLUDED.muscle_groups
                """,
                (movement_id, movement_name, psycopg.types.json.Jsonb(muscle_groups)),
            )
            cur.execute(
                """
                INSERT INTO tonal_workouts
                    (activity_id, begin_time, set_count, movement_count, synced_at)
                VALUES (%s, %s, 1, 1, NOW())
                ON CONFLICT (activity_id) DO UPDATE SET begin_time = EXCLUDED.begin_time
                """,
                (activity_id, begin_time),
            )
            cur.execute(
                """
                INSERT INTO tonal_sets (activity_id, set_index, movement_id, rep_count, volume, synced_at)
                VALUES (%s, 0, %s, %s, %s, NOW())
                ON CONFLICT (activity_id, set_index) DO UPDATE SET
                    movement_id = EXCLUDED.movement_id, rep_count = EXCLUDED.rep_count,
                    volume = EXCLUDED.volume
                """,
                (activity_id, movement_id, rep_count, volume),
            )

    def test_future_workout_excluded_from_real_sql_query(self):
        as_of = datetime(2026, 1, 15, 8, 0, tzinfo=timezone.utc)
        self._insert_workout("tki_test_past", as_of - timedelta(days=1))
        self._insert_workout("tki_test_future", as_of + timedelta(days=1))

        ledger = build_muscle_stimulus_ledger(as_of, window_days=7, conn=self.conn)

        self.assertEqual(ledger["muscles"]["back"]["working_sets"], 1)
        self.assertEqual(ledger["data_quality"]["total_working_sets_considered"], 1)

    def test_direct_and_secondary_credit_from_real_join(self):
        as_of = datetime(2026, 1, 15, 8, 0, tzinfo=timezone.utc)
        self._insert_workout(
            "tki_test_pull_day",
            as_of - timedelta(days=1),
            movement_id="tki_test_lat_pulldown_2",
            muscle_groups=["Back", "Biceps"],
        )

        ledger = build_muscle_stimulus_ledger(as_of, window_days=7, conn=self.conn)

        self.assertEqual(ledger["muscles"]["back"]["direct_sets"], 1.0)
        self.assertGreater(ledger["muscles"]["biceps"]["secondary_set_equivalents"], 0.0)

    def test_workout_override_exclusion_respected_via_real_join(self):
        as_of = datetime(2026, 1, 15, 8, 0, tzinfo=timezone.utc)
        activity_id = "tki_test_excluded"
        self._insert_workout(activity_id, as_of - timedelta(days=1))
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tonal_workout_overrides
                    (activity_id, include_in_training_analysis, exclusion_reason)
                VALUES (%s, FALSE, %s)
                ON CONFLICT (activity_id) DO UPDATE SET
                    include_in_training_analysis = EXCLUDED.include_in_training_analysis,
                    exclusion_reason = EXCLUDED.exclusion_reason
                """,
                (activity_id, "test fixture exclusion"),
            )

        ledger = build_muscle_stimulus_ledger(as_of, window_days=7, conn=self.conn)

        self.assertEqual(ledger["muscles"]["back"]["total_stimulus_sets"], 0.0)


if __name__ == "__main__":
    unittest.main()
