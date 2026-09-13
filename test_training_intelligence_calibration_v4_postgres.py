"""Real-PostgreSQL temporal-correctness and snapshot-versioning tests
for TKI-5.4's progression_v2 (mode-tiered evidence), justification_v2
(counterfactual binding), and workload_sanity_v3 - exercising the real
SQL path end-to-end, matching test_training_intelligence_calibration_
v2_postgres.py / _v3_postgres.py's convention exactly.

Every test runs inside one transaction that is ALWAYS rolled back.

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

_FIXTURE_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "training-intelligence-tki54-calibration-fixture")


def _uuid(label: str) -> str:
    return str(uuid.uuid5(_FIXTURE_NAMESPACE, label))


@unittest.skipUnless(
    os.getenv("TRAINING_INTELLIGENCE_POSTGRES_TESTS") == "1",
    "opt-in PostgreSQL",
)
class CalibrationV4PostgresTests(unittest.TestCase):
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

    def _insert_workout(self, activity_label, begin_time, set_count=6, movement_label="tki54_test_movement",
                        muscle_groups=None, base_weight=60.0, volume_ratio=2.0, eccentric=False, rir=2.0):
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
                        (activity_id, set_index, movement_id, rep_count, base_weight, avg_weight, volume,
                         eccentric, raw_data, synced_at)
                    VALUES (%s, %s, %s, 10, %s, %s, %s, %s, %s::jsonb, NOW())
                    ON CONFLICT (activity_id, set_index) DO UPDATE SET movement_id = EXCLUDED.movement_id
                    """,
                    (activity_id, set_index, movement_id, base_weight, base_weight, volume, eccentric,
                     json.dumps({"warmUp": False, "repsInReserve": rir})),
                )

    def test_future_workout_does_not_change_progression_evidence(self):
        as_of = datetime(2037, 1, 1, 8, 0, tzinfo=timezone.utc)
        for i in range(4):
            self._insert_workout(f"tki54_p1_{i}", as_of - timedelta(days=3 + i * 6))
        baseline = build_calibrated_shadow_prescription(as_of)

        self._insert_workout("tki54_p1_future", as_of + timedelta(days=20), base_weight=999.0, volume_ratio=1.0)
        with_future = build_calibrated_shadow_prescription(as_of)

        baseline_evidence = [e.get("progression_evidence") for e in baseline["exercises"]]
        future_evidence = [e.get("progression_evidence") for e in with_future["exercises"]]
        self.assertEqual(baseline_evidence, future_evidence)
        self.assertEqual(baseline["workload_sanity_v3"], with_future["workload_sanity_v3"])

    def test_future_eccentric_mode_history_does_not_alter_prior_comparison(self):
        as_of = datetime(2037, 2, 1, 8, 0, tzinfo=timezone.utc)
        for i in range(4):
            self._insert_workout(f"tki54_p2_{i}", as_of - timedelta(days=3 + i * 6))
        baseline = build_calibrated_shadow_prescription(as_of)

        self._insert_workout("tki54_p2_future_eccentric", as_of + timedelta(days=10), eccentric=True)
        with_future = build_calibrated_shadow_prescription(as_of)

        self.assertEqual(
            [(e["movement_id"], e["confidence"]) for e in baseline["exercises"]],
            [(e["movement_id"], e["confidence"]) for e in with_future["exercises"]],
        )

    def test_future_rir_does_not_alter_prior_confidence(self):
        as_of = datetime(2037, 3, 1, 8, 0, tzinfo=timezone.utc)
        for i in range(4):
            self._insert_workout(f"tki54_p3_{i}", as_of - timedelta(days=3 + i * 6), rir=2.0)
        baseline = build_calibrated_shadow_prescription(as_of)

        self._insert_workout("tki54_p3_future_bad_rir", as_of + timedelta(days=5), rir=99.0)
        with_future = build_calibrated_shadow_prescription(as_of)

        self.assertEqual(
            [(e["movement_id"], e["progression_evidence"]["effort_quality"]) for e in baseline["exercises"]],
            [(e["movement_id"], e["progression_evidence"]["effort_quality"]) for e in with_future["exercises"]],
        )

    def test_later_same_day_workout_does_not_leak_backward_into_justification(self):
        as_of = datetime(2037, 4, 1, 18, 0, tzinfo=timezone.utc)
        for i in range(4):
            self._insert_workout(f"tki54_p4_{i}", as_of - timedelta(days=3 + i * 6))
        baseline = build_calibrated_shadow_prescription(as_of)

        self._insert_workout("tki54_p4_later_same_day", as_of + timedelta(hours=3), base_weight=999.0)
        replay = build_calibrated_shadow_prescription(as_of)

        self.assertEqual(baseline["workload_sanity_v3"], replay["workload_sanity_v3"])

    def test_snapshot_v3_persists_and_replays_exactly_via_real_sql(self):
        as_of = datetime(2037, 5, 1, 8, 0, tzinfo=timezone.utc)
        for i in range(4):
            self._insert_workout(f"tki54_p5_{i}", as_of - timedelta(days=3 + i * 6))
        result = build_calibrated_shadow_prescription(as_of)

        saved = save_snapshot(as_of, result)
        self.assertTrue(saved["stored"])
        self.assertIn("progression_evidence_version", saved["snapshot"]["engine_version"])
        self.assertIn("justification_policy_version", saved["snapshot"]["engine_version"])
        self.assertIn("workload_sanity_version", saved["snapshot"]["engine_version"])
        self.assertIn("quality_verdict_version", saved["snapshot"]["engine_version"])

        loaded = load_snapshot(saved["snapshot"]["decision_id"])
        replay = build_calibrated_shadow_prescription(as_of)
        comparison = compare_to_snapshot(loaded, replay)
        self.assertTrue(comparison["equivalent"], comparison.get("mismatched_fields"))


if __name__ == "__main__":
    unittest.main()
