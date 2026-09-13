"""Real-PostgreSQL temporal-correctness and persistence tests for
TKI-5.3's workload_sanity_v2 and the decision-snapshot store, exercising
the real SQL path end-to-end - not injected fixtures.

Every test runs inside one transaction that is ALWAYS rolled back (never
committed), using deterministic uuid.uuid5()-derived fixture ids -
matching test_training_intelligence_calibration_v2_postgres.py's
convention exactly. The ONE exception: snapshot.ensure_table()/
save_snapshot()/load_snapshot() write through db.get_conn(), which
(inside the same request_scoped-style override used here) still lands
on this test's own rolled-back connection/transaction - no row from
this file is ever committed to training_decision_snapshots.

Opt-in: requires DATABASE_URL pointed at the Development database and
TRAINING_INTELLIGENCE_POSTGRES_TESTS=1.
"""

import json
import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row

from training_intelligence.calibration.shadow import build_calibrated_shadow_prescription
from training_intelligence.calibration.snapshot import save_snapshot, load_snapshot, compare_to_snapshot

_FIXTURE_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "training-intelligence-tki53-calibration-fixture")


def _uuid(label: str) -> str:
    return str(uuid.uuid5(_FIXTURE_NAMESPACE, label))


@unittest.skipUnless(
    os.getenv("TRAINING_INTELLIGENCE_POSTGRES_TESTS") == "1",
    "opt-in PostgreSQL",
)
class CalibrationV3PostgresTests(unittest.TestCase):
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

    def _insert_workout(self, activity_label, begin_time, set_count=6, movement_label="tki53_test_movement",
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

    def test_future_workout_does_not_change_workload_sanity_v2(self):
        as_of = datetime(2036, 6, 1, 8, 0, tzinfo=timezone.utc)
        for i in range(4):
            self._insert_workout(f"tki53_p1_{i}", as_of - timedelta(days=3 + i * 6))
        baseline = build_calibrated_shadow_prescription(as_of)

        self._insert_workout("tki53_p1_future", as_of + timedelta(days=20), base_weight=999.0, volume_ratio=1.0)
        with_future = build_calibrated_shadow_prescription(as_of)

        self.assertEqual(baseline["workload_sanity_v2"], with_future["workload_sanity_v2"])
        self.assertEqual(baseline["quality_v2"], with_future["quality_v2"])

    def test_later_same_day_workout_does_not_leak_backward(self):
        as_of = datetime(2036, 7, 1, 18, 0, tzinfo=timezone.utc)
        for i in range(4):
            self._insert_workout(f"tki53_p2_{i}", as_of - timedelta(days=3 + i * 6))
        baseline = build_calibrated_shadow_prescription(as_of)

        # a workout logged LATER the same calendar day, after as_of's
        # own clock time, must not be visible to an as_of decision made
        # earlier that day.
        self._insert_workout("tki53_p2_later_same_day", as_of + timedelta(hours=3), base_weight=999.0)
        replay = build_calibrated_shadow_prescription(as_of)

        self.assertEqual(baseline["workload_sanity_v2"], replay["workload_sanity_v2"])

    def test_snapshot_persists_and_replays_exactly_via_real_sql(self):
        as_of = datetime(2036, 8, 1, 8, 0, tzinfo=timezone.utc)
        for i in range(4):
            self._insert_workout(f"tki53_p3_{i}", as_of - timedelta(days=3 + i * 6))
        result = build_calibrated_shadow_prescription(as_of)

        saved = save_snapshot(as_of, result)
        self.assertTrue(saved["stored"])
        loaded = load_snapshot(saved["snapshot"]["decision_id"])
        self.assertIsNotNone(loaded)

        replay = build_calibrated_shadow_prescription(as_of)
        comparison = compare_to_snapshot(loaded, replay)
        self.assertTrue(comparison["equivalent"], comparison.get("mismatched_fields"))

    def test_snapshot_is_insert_only_never_overwritten(self):
        as_of = datetime(2036, 9, 1, 8, 0, tzinfo=timezone.utc)
        for i in range(4):
            self._insert_workout(f"tki53_p4_{i}", as_of - timedelta(days=3 + i * 6))
        result = build_calibrated_shadow_prescription(as_of)
        first = save_snapshot(as_of, result)
        self.assertTrue(first["stored"])

        # A "mutated" result for the exact same (as_of, family) decision
        # re-saved under the same deterministic decision_id must be a
        # no-op, not an UPDATE - immutability is enforced by never
        # issuing an UPDATE, not merely documented.
        mutated = dict(result, estimated_total_volume=999999.0)
        second = save_snapshot(as_of, mutated)
        self.assertFalse(second["stored"])

        loaded = load_snapshot(first["snapshot"]["decision_id"])
        self.assertEqual(
            loaded["final_prescription"]["estimated_total_volume"],
            first["snapshot"]["final_prescription"]["estimated_total_volume"],
        )
        self.assertNotEqual(loaded["final_prescription"]["estimated_total_volume"], 999999.0)

    def test_later_cache_style_rebuild_cannot_rewrite_a_persisted_snapshot(self):
        as_of = datetime(2036, 10, 1, 8, 0, tzinfo=timezone.utc)
        for i in range(4):
            self._insert_workout(f"tki53_p5_{i}", as_of - timedelta(days=3 + i * 6))
        result = build_calibrated_shadow_prescription(as_of)
        saved = save_snapshot(as_of, result)

        # Simulate a later rebuild against a materially different history
        # (a new workout inserted after the original snapshot was taken).
        self._insert_workout("tki53_p5_after_snapshot", as_of - timedelta(days=1), base_weight=999.0)
        build_calibrated_shadow_prescription(as_of)  # a fresh, different-input build - never persisted here

        reloaded = load_snapshot(saved["snapshot"]["decision_id"])
        # reloaded came back through a JSON round-trip (tuples -> lists);
        # normalize the in-memory side the same way before comparing, so
        # this asserts real immutability rather than tripping on a
        # harmless JSON type coercion.
        expected = json.loads(json.dumps(saved["snapshot"], default=str))
        self.assertEqual(reloaded, expected)


if __name__ == "__main__":
    unittest.main()
