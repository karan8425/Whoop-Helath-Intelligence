from datetime import date, datetime, timezone
import unittest
from unittest.mock import patch

from integrations.tonal.strength_adherence import (
    _count_qualifying_sessions,
    strength_adherence,
)


NOW = datetime(2026, 8, 30, 16, tzinfo=timezone.utc)


def workout(
    activity_id,
    workout_date,
    included=True,
    has_sets=True,
):
    return {
        "activity_id": activity_id,
        "workout_date": date.fromisoformat(workout_date),
        "included": included,
        "has_sets": has_sets,
    }


class StrengthAdherenceTests(unittest.TestCase):
    def result(self, target, rows):
        with patch(
            "integrations.tonal.strength_adherence._load_workout_eligibility",
            return_value=rows,
        ):
            return strength_adherence(target, now=NOW)

    def test_target_three_sessions_three_is_target_met(self):
        result = self.result(
            3,
            [
                workout("a", "2026-08-24"),
                workout("b", "2026-08-26"),
                workout("c", "2026-08-30"),
            ],
        )

        self.assertEqual(result["status"], "target_met")
        self.assertEqual(result["sessions_7d"], 3)
        self.assertEqual(result["percentage_of_target"], 100.0)
        self.assertEqual(result["remaining_sessions"], 0)

    def test_target_four_sessions_two_is_below_target(self):
        result = self.result(
            4,
            [
                workout("a", "2026-08-25"),
                workout("b", "2026-08-29"),
            ],
        )

        self.assertEqual(result["status"], "below_target")
        self.assertEqual(result["sessions_7d"], 2)
        self.assertEqual(result["percentage_of_target"], 50.0)
        self.assertEqual(result["remaining_sessions"], 2)

    def test_zero_recent_with_history_is_valid_zero(self):
        result = self.result(
            3,
            [workout("old", "2026-08-01")],
        )

        self.assertEqual(result["status"], "below_target")
        self.assertEqual(result["sessions_7d"], 0)

    def test_no_strength_goal_is_not_configured(self):
        result = strength_adherence(None, now=NOW)

        self.assertEqual(result["status"], "not_configured")
        self.assertIsNone(result["sessions_7d"])

    def test_query_failure_is_not_connected(self):
        with patch(
            "integrations.tonal.strength_adherence._load_workout_eligibility",
            side_effect=RuntimeError("unavailable"),
        ):
            result = strength_adherence(3, now=NOW)

        self.assertEqual(result["status"], "not_connected")
        self.assertIsNone(result["sessions_7d"])

    def test_duplicate_workout_rows_count_once(self):
        rows = [
            workout("same", "2026-08-28"),
            workout("same", "2026-08-28"),
        ]

        count = _count_qualifying_sessions(
            rows,
            date(2026, 8, 24),
            date(2026, 8, 30),
        )

        self.assertEqual(count, 1)

    def test_excluded_and_setless_workouts_are_not_counted(self):
        result = self.result(
            3,
            [
                workout("included", "2026-08-28"),
                workout("excluded", "2026-08-29", included=False),
                workout("setless", "2026-08-30", has_sets=False),
            ],
        )

        self.assertEqual(result["sessions_7d"], 1)
        self.assertEqual(result["status"], "below_target")

    def test_window_is_seven_eastern_calendar_days(self):
        result = self.result(1, [workout("a", "2026-08-24")])

        self.assertEqual(result["window_start_date"], "2026-08-24")
        self.assertEqual(result["window_end_date"], "2026-08-30")


if __name__ == "__main__":
    unittest.main()
