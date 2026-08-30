from datetime import date
import unittest

from body_composition_goal import phase_aware_daily_mean


def _series(*values):
    return [
        {"date": date(2026, 8, day), "value": value}
        for day, value in values
    ]


class PhaseAwareDailyMeanTests(unittest.TestCase):
    def test_early_phase_uses_all_phase_days_and_excludes_prior_days(self):
        value, days = phase_aware_daily_mean(
            _series((19, 100), (20, 110), (22, 130)),
            date(2026, 8, 20),
            6,
        )

        self.assertEqual(value, 120)
        self.assertEqual(days, 2)

    def test_mature_phase_uses_latest_seven_calendar_days(self):
        value, days = phase_aware_daily_mean(
            _series(
                (1, 10),
                (19, 20),
                (23, 30),
                (26, 40),
            ),
            date(2026, 8, 1),
            7,
        )

        self.assertEqual(value, 35)
        self.assertEqual(days, 2)

    def test_phase_with_no_measurements_does_not_use_pre_phase_data(self):
        value, days = phase_aware_daily_mean(
            _series((19, 100)),
            date(2026, 8, 20),
            2,
        )

        self.assertIsNone(value)
        self.assertEqual(days, 0)


if __name__ == "__main__":
    unittest.main()
