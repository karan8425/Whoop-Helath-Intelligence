"""Real-PostgreSQL tests for TKI-5's shadow prescription, exercising the
real SQL paths (B3's own movement_performance query, plus TKI-2/3/4's
already-tested queries) end-to-end via build_shadow_prescription() - not
injected fixtures. Analogous to
test_training_intelligence_selection_postgres.py (TKI-4).

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

from training_intelligence.prescription.shadow_prescription import build_shadow_prescription

_FIXTURE_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "training-intelligence-tki5-prescription-fixture")


def _uuid(label: str) -> str:
    return str(uuid.uuid5(_FIXTURE_NAMESPACE, label))


@unittest.skipUnless(
    os.getenv("TRAINING_INTELLIGENCE_POSTGRES_TESTS") == "1",
    "opt-in PostgreSQL",
)
class ShadowPrescriptionPostgresTests(unittest.TestCase):
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

    def _insert_workout(self, activity_label, begin_time, set_count=8, movement_label="tki5_test_movement",
                        muscle_groups=None, workout_type="Upper Pull", is_bilateral=True, base_weight=40.0):
        muscle_groups = muscle_groups if muscle_groups is not None else ["Back", "Biceps"]
        activity_id = _uuid(activity_label)
        movement_id = _uuid(movement_label)
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tonal_movements "
                "(movement_id, name, muscle_groups, is_bilateral, is_two_sided, is_generic, custom_movement, synced_at) "
                "VALUES (%s, %s, %s::jsonb, %s, FALSE, FALSE, FALSE, NOW()) "
                "ON CONFLICT (movement_id) DO UPDATE SET muscle_groups = EXCLUDED.muscle_groups",
                (movement_id, movement_label, psycopg.types.json.Jsonb(muscle_groups), is_bilateral),
            )
            cur.execute(
                """
                INSERT INTO tonal_workouts
                    (activity_id, begin_time, workout_type, set_count, movement_count,
                     total_reps, total_volume, duration_seconds, synced_at)
                VALUES (%s, %s, %s, %s, 4, 80, 5000.0, 2400, NOW())
                ON CONFLICT (activity_id) DO UPDATE SET begin_time = EXCLUDED.begin_time
                """,
                (activity_id, begin_time, workout_type, set_count),
            )
            for set_index in range(set_count):
                cur.execute(
                    """
                    INSERT INTO tonal_sets
                        (activity_id, set_index, movement_id, rep_count, base_weight, avg_weight, volume, synced_at)
                    VALUES (%s, %s, %s, 10, %s, %s, %s, NOW())
                    ON CONFLICT (activity_id, set_index) DO UPDATE SET movement_id = EXCLUDED.movement_id
                    """,
                    (activity_id, set_index, movement_id, base_weight, base_weight, base_weight * 10),
                )

    def test_real_sql_produces_a_valid_shadow_prescription(self):
        as_of = datetime(2035, 3, 15, 8, 0, tzinfo=timezone.utc)
        for i in range(4):
            self._insert_workout(f"tki5_real_{i}", as_of - timedelta(days=3 + i * 6))

        result = build_shadow_prescription(as_of)

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["selected_session_family"])
        if result["dose"] is not None:
            self.assertLessEqual(
                result["session_summary"]["total_working_sets"],
                result["dose"]["feasible_range"]["upper_bound_working_sets"],
            )

    def test_future_workout_does_not_influence_real_prescription(self):
        as_of = datetime(2035, 4, 10, 8, 0, tzinfo=timezone.utc)
        for i in range(4):
            self._insert_workout(f"tki5_past_{i}", as_of - timedelta(days=3 + i * 6))
        baseline = build_shadow_prescription(as_of)

        self._insert_workout(
            "tki5_future", as_of + timedelta(days=5),
            set_count=99, movement_label="tki5_future_movement", base_weight=999.0,
        )
        with_future = build_shadow_prescription(as_of)

        self.assertEqual(baseline["selected_session_family"], with_future["selected_session_family"])
        self.assertEqual(
            [(e["movement_id"], e["working_sets"], e["target_reps_per_set"], e["prescribed_resistance_lb"])
             for e in baseline["exercises"]],
            [(e["movement_id"], e["working_sets"], e["target_reps_per_set"], e["prescribed_resistance_lb"])
             for e in with_future["exercises"]],
        )

    def test_future_workout_does_not_change_feasible_exercise_count(self):
        """TKI-5.1: feasible_exercise_count()'s personal-history structure
        input (historical_session_structure) is derived from real ledger
        rows loaded fresh each call - a future workout must not change
        the historical median exercises-per-session it computes."""
        as_of = datetime(2035, 5, 1, 8, 0, tzinfo=timezone.utc)
        for i in range(4):
            self._insert_workout(f"tki5_struct_{i}", as_of - timedelta(days=3 + i * 6))
        baseline = build_shadow_prescription(as_of)

        self._insert_workout(
            "tki5_struct_future", as_of + timedelta(days=10),
            set_count=99, movement_label="tki5_struct_future_movement", base_weight=999.0,
        )
        with_future = build_shadow_prescription(as_of)

        self.assertEqual(baseline["feasible_exercise_count"], with_future["feasible_exercise_count"])
        if baseline["dose"] is not None:
            self.assertEqual(baseline["data_quality"]["historical_structure_sample_count"],
                              with_future["data_quality"]["historical_structure_sample_count"])


if __name__ == "__main__":
    unittest.main()
