"""Real-PostgreSQL temporal-correctness tests for TKI-4.1's calibration
layer (monotony / rolling representation / starvation), exercising the
real SQL path (the same ledger_rows query TKI-2/TKI-4 already use) end-
to-end via build_shadow_selection(recent_selections=...), not injected
fixtures. Analogous to test_training_intelligence_selection_postgres.py
(TKI-4).

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

_FIXTURE_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "training-intelligence-tki41-calibration-fixture")


def _uuid(label: str) -> str:
    return str(uuid.uuid5(_FIXTURE_NAMESPACE, label))


@unittest.skipUnless(
    os.getenv("TRAINING_INTELLIGENCE_POSTGRES_TESTS") == "1",
    "opt-in PostgreSQL",
)
class CalibrationPostgresTests(unittest.TestCase):
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

    def _insert_workout(self, activity_label, begin_time, set_count=8, movement_label="tki41_test_movement",
                        muscle_groups=None, workout_type="Upper Pull"):
        muscle_groups = muscle_groups if muscle_groups is not None else ["Back", "Biceps"]
        activity_id = _uuid(activity_label)
        movement_id = _uuid(movement_label)
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tonal_movements (movement_id, name, muscle_groups, synced_at) "
                "VALUES (%s, %s, %s::jsonb, NOW()) ON CONFLICT (movement_id) DO UPDATE SET "
                "muscle_groups = EXCLUDED.muscle_groups",
                (movement_id, "TKI41_TEST_FIXTURE", psycopg.types.json.Jsonb(muscle_groups)),
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
                    INSERT INTO tonal_sets (activity_id, set_index, movement_id, rep_count, volume, synced_at)
                    VALUES (%s, %s, %s, 10, 500.0, NOW())
                    ON CONFLICT (activity_id, set_index) DO UPDATE SET
                        movement_id = EXCLUDED.movement_id
                    """,
                    (activity_id, set_index, movement_id),
                )

    def test_future_real_workout_does_not_change_calibrated_scores_or_streak(self):
        as_of = datetime(2035, 5, 20, 8, 0, tzinfo=timezone.utc)
        for i in range(4):
            self._insert_workout(f"tki41_past_{i}", as_of - timedelta(days=3 + i * 6))
        recent_selections = [
            (as_of - timedelta(days=1), "Lower Body"),
            (as_of - timedelta(days=2), "Lower Body"),
        ]
        baseline = build_shadow_selection(as_of, recent_selections=recent_selections)

        self._insert_workout(
            "tki41_future", as_of + timedelta(days=5),
            set_count=99, movement_label="tki41_future_movement",
        )
        with_future = build_shadow_selection(as_of, recent_selections=recent_selections)

        self.assertEqual(baseline["selected_session_family"], with_future["selected_session_family"])
        self.assertEqual(
            [(c["session_family"], c["score_total_calibrated"], (c.get("monotony") or {}).get("consecutive_repeat_streak"))
             for c in baseline["ranked_eligible"]],
            [(c["session_family"], c["score_total_calibrated"], (c.get("monotony") or {}).get("consecutive_repeat_streak"))
             for c in with_future["ranked_eligible"]],
        )

    def test_future_recent_selection_entry_does_not_change_calibrated_scores(self):
        as_of = datetime(2035, 6, 15, 8, 0, tzinfo=timezone.utc)
        for i in range(4):
            self._insert_workout(f"tki41_b_{i}", as_of - timedelta(days=2 + i * 5))
        baseline = build_shadow_selection(as_of, recent_selections=[(as_of - timedelta(days=1), "Lower Body")])
        with_future_entry = build_shadow_selection(
            as_of,
            recent_selections=[
                (as_of - timedelta(days=1), "Lower Body"),
                (as_of + timedelta(days=3), "Lower Body"),  # future decision - must be ignored
            ],
        )
        self.assertEqual(baseline["selected_session_family"], with_future_entry["selected_session_family"])
        self.assertEqual(
            [c["score_total_calibrated"] for c in baseline["ranked_eligible"]],
            [c["score_total_calibrated"] for c in with_future_entry["ranked_eligible"]],
        )


if __name__ == "__main__":
    unittest.main()
