"""Real-PostgreSQL tests for TKI-3's shadow dose layer, exercising B2's
actual SQL (integrations.tonal.training_dose.load_session_history /
_load_muscle_set_rows) end-to-end through build_shadow_dose(sessions=None,
muscle_rows=None) - not injected fixtures. Analogous to
test_training_intelligence_postgres.py (TKI-2).

Every test runs inside one transaction that is ALWAYS rolled back (never
committed), using deterministic uuid.uuid5()-derived fixture ids (movement_
id/activity_id/workout_id are uuid-typed columns, confirmed in TKI-2.1).

Opt-in: requires DATABASE_URL pointed at the Development database and
TRAINING_INTELLIGENCE_POSTGRES_TESTS=1.
"""

import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import psycopg
from psycopg.rows import dict_row

from training_intelligence.dose.shadow_dose import build_shadow_dose

_FIXTURE_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "training-intelligence-tki3-dose-fixture")


def _uuid(label: str) -> str:
    return str(uuid.uuid5(_FIXTURE_NAMESPACE, label))


@unittest.skipUnless(
    os.getenv("TRAINING_INTELLIGENCE_POSTGRES_TESTS") == "1",
    "opt-in PostgreSQL",
)
class ShadowDosePostgresTests(unittest.TestCase):
    def setUp(self):
        dsn = os.environ["DATABASE_URL"]
        if "umodirrruxtjoqfjayoy" not in dsn or "yyrgabalzmgoquleepyw" in dsn:
            self.fail("Development database guard failed")
        self.conn = psycopg.connect(dsn, row_factory=dict_row, autocommit=False)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        # training_dose.py's queries default to a fresh get_conn() each
        # call, not an injectable conn=, so route them through this one
        # rolled-back connection via the shared request-scoped mechanism.
        import db
        token = db._active_connection.set(self.conn)
        self.addCleanup(db._active_connection.reset, token)

    def _insert_workout(self, activity_label, begin_time, set_count=9, movement_count=4,
                        total_reps=80, total_volume=5080.0, duration_seconds=2400,
                        movement_label="tki3_test_movement", muscle_groups=None):
        muscle_groups = muscle_groups if muscle_groups is not None else ["Back", "Biceps"]
        activity_id = _uuid(activity_label)
        movement_id = _uuid(movement_label)
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tonal_movements (movement_id, name, muscle_groups, synced_at) "
                "VALUES (%s, %s, %s::jsonb, NOW()) ON CONFLICT (movement_id) DO UPDATE SET "
                "muscle_groups = EXCLUDED.muscle_groups",
                (movement_id, "TKI3_TEST_FIXTURE", psycopg.types.json.Jsonb(muscle_groups)),
            )
            cur.execute(
                """
                INSERT INTO tonal_workouts
                    (activity_id, begin_time, workout_type, set_count, movement_count,
                     total_reps, total_volume, duration_seconds, synced_at)
                VALUES (%s, %s, 'Upper Pull', %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (activity_id) DO UPDATE SET begin_time = EXCLUDED.begin_time
                """,
                (activity_id, begin_time, set_count, movement_count, total_reps,
                 total_volume, duration_seconds),
            )
            for set_index in range(set_count):
                cur.execute(
                    """
                    INSERT INTO tonal_sets (activity_id, set_index, movement_id, rep_count, volume, synced_at)
                    VALUES (%s, %s, %s, 10, %s, NOW())
                    ON CONFLICT (activity_id, set_index) DO UPDATE SET
                        movement_id = EXCLUDED.movement_id
                    """,
                    (activity_id, set_index, movement_id, total_volume / set_count),
                )
        return activity_id

    def test_future_workout_does_not_influence_real_shadow_dose(self):
        as_of = datetime(2035, 1, 15, 8, 0, tzinfo=timezone.utc)
        for i in range(4):
            self._insert_workout(f"tki3_past_{i}", as_of - timedelta(days=3 + i * 6))
        with_baseline = build_shadow_dose(as_of, "Upper Pull")

        self._insert_workout("tki3_future", as_of + timedelta(days=5), total_volume=999999.0, set_count=99)
        with_future = build_shadow_dose(as_of, "Upper Pull")

        self.assertEqual(
            with_baseline["historical_dose_reference"]["comparable_session_count"],
            with_future["historical_dose_reference"]["comparable_session_count"],
        )
        self.assertEqual(
            with_baseline["recommended_dose"]["working_sets"],
            with_future["recommended_dose"]["working_sets"],
        )

    def test_real_sql_produces_a_populated_shadow_dose(self):
        as_of = datetime(2035, 2, 20, 8, 0, tzinfo=timezone.utc)
        for i in range(4):
            self._insert_workout(f"tki3_real_{i}", as_of - timedelta(days=3 + i * 6))

        result = build_shadow_dose(as_of, "Upper Pull")

        self.assertEqual(result["status"], "ok")
        self.assertGreaterEqual(result["historical_dose_reference"]["comparable_session_count"], 3)
        self.assertGreater(result["recommended_dose"]["working_sets"], 0)


if __name__ == "__main__":
    unittest.main()
