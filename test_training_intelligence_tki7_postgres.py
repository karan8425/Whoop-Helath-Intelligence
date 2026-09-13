"""Real-PostgreSQL tests for TKI-7's orchestration layer: that
build_training_intelligence_prescription() actually persists a decision
snapshot end-to-end against real SQL, and that the resulting snapshot
replays exactly - matching the established v2/v3/v4 opt-in Postgres
test convention exactly.

Opt-in: requires DATABASE_URL pointed at the Development database and
TRAINING_INTELLIGENCE_POSTGRES_TESTS=1.
"""

import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row

from training_intelligence.calibration.orchestrator import build_training_intelligence_prescription
from training_intelligence.calibration.mobile_adapter import adapt_calibrated_to_workout_schema
from training_intelligence.calibration.snapshot import load_snapshot, compare_to_snapshot

_FIXTURE_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "training-intelligence-tki7-orchestration-fixture")


def _uuid(label: str) -> str:
    return str(uuid.uuid5(_FIXTURE_NAMESPACE, label))


@unittest.skipUnless(
    os.getenv("TRAINING_INTELLIGENCE_POSTGRES_TESTS") == "1",
    "opt-in PostgreSQL",
)
class TKI7OrchestrationPostgresTests(unittest.TestCase):
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

    def _insert_workout(self, activity_label, begin_time, set_count=6, movement_label="tki7_test_movement",
                        muscle_groups=None, base_weight=60.0, volume_ratio=2.0):
        muscle_groups = muscle_groups if muscle_groups is not None else ["Chest"]
        activity_id = _uuid(activity_label)
        movement_id = _uuid(movement_label)
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tonal_movements "
                "(movement_id, name, muscle_groups, is_bilateral, is_two_sided, is_generic, custom_movement, synced_at) "
                "VALUES (%s, %s, %s::jsonb, TRUE, FALSE, FALSE, FALSE, NOW()) "
                "ON CONFLICT (movement_id) DO UPDATE SET muscle_groups = EXCLUDED.muscle_groups",
                (movement_id, movement_label, psycopg.types.json.Jsonb(muscle_groups)),
            )
            cur.execute(
                """
                INSERT INTO tonal_workouts
                    (activity_id, begin_time, end_time, workout_type, set_count, movement_count,
                     total_reps, total_volume, duration_seconds, synced_at)
                VALUES (%s, %s, %s, 'Upper Push', %s, 1, 60, 5000.0, 2400, NOW())
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

    def test_orchestrator_persists_snapshot_end_to_end(self):
        as_of = datetime(2038, 1, 1, 8, 0, tzinfo=timezone.utc)
        for i in range(4):
            self._insert_workout(f"tki7_p1_{i}", as_of - timedelta(days=3 + i * 6))

        result, snapshot_info = build_training_intelligence_prescription(as_of)

        self.assertEqual(result["status"], "ok")
        self.assertTrue(snapshot_info["stored"])
        decision_id = snapshot_info["snapshot"]["decision_id"]
        loaded = load_snapshot(decision_id)
        self.assertIsNotNone(loaded)

        # Replay: rebuild without persisting again, compare.
        replay_result, _ = build_training_intelligence_prescription(as_of, persist_snapshot=False)
        comparison = compare_to_snapshot(loaded, replay_result)
        self.assertTrue(comparison["equivalent"], comparison.get("mismatched_fields"))

    def test_mobile_adapter_output_matches_persisted_snapshot_volume(self):
        as_of = datetime(2038, 2, 1, 8, 0, tzinfo=timezone.utc)
        for i in range(4):
            self._insert_workout(f"tki7_p2_{i}", as_of - timedelta(days=3 + i * 6))

        result, snapshot_info = build_training_intelligence_prescription(as_of)
        workout = adapt_calibrated_to_workout_schema(as_of, result, snapshot_info)

        self.assertEqual(workout["status"], "ok")
        self.assertEqual(
            workout["session"]["estimated_total_volume"],
            round(result["estimated_total_volume"], 1) if result["estimated_total_volume"] is not None else None,
        )
        self.assertEqual(workout["session"]["training_intelligence"]["decision_id"], snapshot_info["snapshot"]["decision_id"])

    def test_persist_snapshot_false_does_not_write(self):
        as_of = datetime(2038, 3, 1, 8, 0, tzinfo=timezone.utc)
        for i in range(4):
            self._insert_workout(f"tki7_p3_{i}", as_of - timedelta(days=3 + i * 6))

        result, snapshot_info = build_training_intelligence_prescription(as_of, persist_snapshot=False)
        self.assertIsNone(snapshot_info)


if __name__ == "__main__":
    unittest.main()
