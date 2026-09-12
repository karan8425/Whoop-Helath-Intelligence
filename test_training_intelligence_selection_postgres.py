"""Real-PostgreSQL tests for TKI-4's shadow selection, exercising the
real SQL paths (B2's session/muscle-row queries, TKI-2's ledger query)
end-to-end via build_shadow_selection(sessions=None, muscle_rows=None,
ledger_rows=None) - not injected fixtures. Analogous to
test_training_intelligence_dose_postgres.py (TKI-3).

Every test runs inside one transaction that is ALWAYS rolled back (never
committed), using deterministic uuid.uuid5()-derived fixture ids.

Opt-in: requires DATABASE_URL pointed at the Development database and
TRAINING_INTELLIGENCE_POSTGRES_TESTS=1.
"""

import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row

from training_intelligence.selection.shadow_selection import build_shadow_selection

_FIXTURE_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "training-intelligence-tki4-selection-fixture")


def _uuid(label: str) -> str:
    return str(uuid.uuid5(_FIXTURE_NAMESPACE, label))


@unittest.skipUnless(
    os.getenv("TRAINING_INTELLIGENCE_POSTGRES_TESTS") == "1",
    "opt-in PostgreSQL",
)
class ShadowSelectionPostgresTests(unittest.TestCase):
    def setUp(self):
        dsn = os.environ["DATABASE_URL"]
        if "umodirrruxtjoqfjayoy" not in dsn or "yyrgabalzmgoquleepyw" in dsn:
            self.fail("Development database guard failed")
        self.conn = psycopg.connect(dsn, row_factory=dict_row, autocommit=False)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        import db
        token = db._active_connection.set(self.conn)
        self.addCleanup(db._active_connection.reset, token)

    def _insert_workout(self, activity_label, begin_time, set_count=10, movement_count=4,
                        total_reps=80, total_volume=5000.0, duration_seconds=2400,
                        movement_label="tki4_test_movement", muscle_groups=None,
                        workout_type="Upper Pull"):
        muscle_groups = muscle_groups if muscle_groups is not None else ["Back", "Biceps"]
        activity_id = _uuid(activity_label)
        movement_id = _uuid(movement_label)
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tonal_movements (movement_id, name, muscle_groups, synced_at) "
                "VALUES (%s, %s, %s::jsonb, NOW()) ON CONFLICT (movement_id) DO UPDATE SET "
                "muscle_groups = EXCLUDED.muscle_groups",
                (movement_id, "TKI4_TEST_FIXTURE", psycopg.types.json.Jsonb(muscle_groups)),
            )
            cur.execute(
                """
                INSERT INTO tonal_workouts
                    (activity_id, begin_time, workout_type, set_count, movement_count,
                     total_reps, total_volume, duration_seconds, synced_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (activity_id) DO UPDATE SET begin_time = EXCLUDED.begin_time
                """,
                (activity_id, begin_time, workout_type, set_count, movement_count,
                 total_reps, total_volume, duration_seconds),
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

    def test_real_sql_produces_a_valid_shadow_selection(self):
        as_of = datetime(2035, 3, 15, 8, 0, tzinfo=timezone.utc)
        for i in range(4):
            self._insert_workout(f"tki4_real_{i}", as_of - timedelta(days=3 + i * 6))

        result = build_shadow_selection(as_of)

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["candidates"])
        self.assertIn(result["selected_session_family"], {c["session_family"] for c in result["candidates"]})

    def test_future_workout_does_not_influence_real_selection(self):
        as_of = datetime(2035, 4, 10, 8, 0, tzinfo=timezone.utc)
        for i in range(4):
            self._insert_workout(f"tki4_past_{i}", as_of - timedelta(days=3 + i * 6))
        baseline = build_shadow_selection(as_of)

        self._insert_workout(
            "tki4_future", as_of + timedelta(days=5),
            total_volume=999999.0, set_count=99, movement_label="tki4_future_movement",
        )
        with_future = build_shadow_selection(as_of)

        self.assertEqual(baseline["selected_session_family"], with_future["selected_session_family"])
        self.assertEqual(
            [c["score_total"] for c in baseline["ranked_eligible"]],
            [c["score_total"] for c in with_future["ranked_eligible"]],
        )


if __name__ == "__main__":
    unittest.main()
