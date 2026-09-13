"""Real-PostgreSQL temporal-correctness tests for TKI-5.2's capacity/
workload calibration layer, exercising the real SQL path
(training_intelligence.calibration.history.load_history()) end-to-end -
not injected fixtures.

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

from training_intelligence.calibration.history import load_history, multipliers, sessions_from_rows
from training_intelligence.calibration.capacity import capacity_reference
from training_intelligence.calibration.shadow import build_calibrated_shadow_prescription

_FIXTURE_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "training-intelligence-tki52-calibration-fixture")


def _uuid(label: str) -> str:
    return str(uuid.uuid5(_FIXTURE_NAMESPACE, label))


@unittest.skipUnless(
    os.getenv("TRAINING_INTELLIGENCE_POSTGRES_TESTS") == "1",
    "opt-in PostgreSQL",
)
class CalibrationV2PostgresTests(unittest.TestCase):
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

    def _insert_workout(self, activity_label, begin_time, set_count=6, movement_label="tki52_test_movement",
                        muscle_groups=None, base_weight=60.0, volume_ratio=2.0, is_bilateral=True):
        muscle_groups = muscle_groups if muscle_groups is not None else ["Back"]
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
                    (activity_id, begin_time, end_time, workout_type, set_count, movement_count,
                     total_reps, total_volume, duration_seconds, synced_at)
                VALUES (%s, %s, %s, 'Upper Pull', %s, 1, 60, 5000.0, 2400, NOW())
                ON CONFLICT (activity_id) DO UPDATE SET begin_time = EXCLUDED.begin_time
                """,
                (activity_id, begin_time, begin_time + timedelta(minutes=40), set_count),
            )
            for set_index in range(set_count):
                volume = base_weight * 10 * volume_ratio
                cur.execute(
                    """
                    INSERT INTO tonal_sets
                        (activity_id, set_index, movement_id, rep_count, base_weight, avg_weight, volume, synced_at)
                    VALUES (%s, %s, %s, 10, %s, %s, %s, NOW())
                    ON CONFLICT (activity_id, set_index) DO UPDATE SET movement_id = EXCLUDED.movement_id
                    """,
                    (activity_id, set_index, movement_id, base_weight, base_weight, volume),
                )

    def test_future_workout_does_not_change_historical_multiplier_or_capacity(self):
        as_of = datetime(2035, 6, 1, 8, 0, tzinfo=timezone.utc)
        for i in range(3):
            self._insert_workout(f"tki52_past_{i}", as_of - timedelta(days=3 + i * 5))
        baseline_rows = load_history(as_of)
        baseline_multipliers = multipliers(baseline_rows, as_of)
        baseline_sessions = sessions_from_rows(baseline_rows, as_of, baseline_multipliers)
        baseline_capacity = capacity_reference(baseline_sessions, "Upper Pull", as_of)

        self._insert_workout(
            "tki52_future", as_of + timedelta(days=10),
            base_weight=999.0, volume_ratio=1.0,
        )
        rows_with_future = load_history(as_of)
        multipliers_with_future = multipliers(rows_with_future, as_of)
        sessions_with_future = sessions_from_rows(rows_with_future, as_of, multipliers_with_future)
        capacity_with_future = capacity_reference(sessions_with_future, "Upper Pull", as_of)

        self.assertEqual(len(baseline_rows), len(rows_with_future))
        self.assertEqual(baseline_multipliers, multipliers_with_future)
        self.assertEqual(baseline_capacity, capacity_with_future)

    def test_future_workout_does_not_change_calibrated_prescription(self):
        as_of = datetime(2035, 7, 1, 8, 0, tzinfo=timezone.utc)
        for i in range(3):
            self._insert_workout(f"tki52_p2_{i}", as_of - timedelta(days=3 + i * 5))
        baseline = build_calibrated_shadow_prescription(as_of)

        self._insert_workout("tki52_p2_future", as_of + timedelta(days=15), base_weight=999.0)
        with_future = build_calibrated_shadow_prescription(as_of)

        self.assertEqual(baseline["personal_capacity_reference"], with_future["personal_capacity_reference"])
        self.assertEqual(baseline["feasible_range"], with_future["feasible_range"])
        self.assertEqual(
            [(e["movement_id"], e["working_sets"], e["prescribed_resistance_lb"]) for e in baseline["exercises"]],
            [(e["movement_id"], e["working_sets"], e["prescribed_resistance_lb"]) for e in with_future["exercises"]],
        )

    def test_real_sql_produces_a_valid_calibrated_prescription(self):
        as_of = datetime(2035, 8, 1, 8, 0, tzinfo=timezone.utc)
        for i in range(4):
            self._insert_workout(f"tki52_real_{i}", as_of - timedelta(days=3 + i * 6))
        result = build_calibrated_shadow_prescription(as_of)
        self.assertEqual(result["status"], "ok")
        self.assertIn("feasible_range", result)
        self.assertIn("quality", result)


if __name__ == "__main__":
    unittest.main()
