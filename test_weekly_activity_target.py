"""Nullable weekly targets: real PostgreSQL checks are explicitly Dev-only."""
import os
import unittest
from contextlib import contextmanager
from datetime import date, timedelta
from unittest.mock import patch

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

import weekly_analytics as weekly
import weekly_health_intelligence_store as store


class WeeklyObservabilityTests(unittest.TestCase):
    def test_failure_logs_locations_but_not_exception_payload(self):
        secret = 'private health payload and credential sentinel'
        with patch.object(store, 'get_or_create_intelligence', side_effect=RuntimeError(secret)), \
             self.assertLogs(store.logger, level='ERROR') as logs:
            with self.assertRaisesRegex(RuntimeError, secret):
                store.get_weekly_health_intelligence()
        output = '\n'.join(logs.output)
        self.assertIn('exception_type=RuntimeError', output)
        self.assertIn('stack_locations=', output)
        self.assertIn('duration_seconds=', output)
        self.assertNotIn(secret, output)

    def test_success_logging_preserves_result_without_logging_brief(self):
        result = {'status': 'ok', 'period_end_date': '2026-09-11', 'brief': {'private': 'sentinel'}}
        with patch.object(store, 'get_or_create_intelligence', return_value=result), \
             self.assertLogs(store.logger, level='INFO') as logs:
            self.assertIs(store.get_weekly_health_intelligence(), result)
        self.assertIn('metric_date=2026-09-11', '\n'.join(logs.output))
        self.assertNotIn('sentinel', '\n'.join(logs.output))


@unittest.skipUnless(os.getenv('WEEKLY_P0_POSTGRES_TESTS') == '1',
                     'requires explicit Development PostgreSQL opt-in')
class WeeklyActivityPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dsn = os.environ.get('DATABASE_URL', '')
        info = conninfo_to_dict(dsn)
        if 'umodirrruxtjoqfjayoy' not in info.get('user', '') or 'yyrgabalzmgoquleepyw' in dsn:
            raise RuntimeError('Development database guard failed')
        try:
            cls.conn = psycopg.connect(dsn, row_factory=dict_row, connect_timeout=10)
        except Exception as exc:
            raise RuntimeError('Development connection failed: ' + type(exc).__name__) from None
        cls.addClassCleanup(cls.conn.close)

    def setUp(self):
        self.addCleanup(self.conn.rollback)
        self.conn.execute('SET TRANSACTION READ ONLY')
        self.assertEqual(self.conn.execute('SHOW transaction_read_only').fetchone()['transaction_read_only'], 'on')
        self.end = date(2026, 9, 11)
        self.start = self.end - timedelta(days=6)
        self.rows = [(self.start + timedelta(days=i), float(1000 + i * 1000)) for i in range(7)]
        self.enterContext(patch.object(weekly, 'get_conn', self.fixture_connection))

    @contextmanager
    def fixture_connection(self):
        case = self
        class Connection:
            @contextmanager
            def cursor(self):
                with case.conn.cursor() as real:
                    class Cursor:
                        def execute(self, query, params):
                            case.assertIn('FROM apple_health_daily_activity', query)
                            values = sql.SQL(',').join(
                                sql.SQL('({}, {})').format(sql.Literal(day), sql.Literal(steps))
                                for day, steps in case.rows)
                            # Shadow only the source table; no fixtures or data are written.
                            cte = sql.SQL('WITH apple_health_daily_activity(activity_date, steps) AS (VALUES {}) ').format(values)
                            real.execute(cte + sql.SQL(query), params)
                        def fetchone(self): return real.fetchone()
                    yield Cursor()
        yield Connection()

    def test_null_target_preserves_observations_without_counting_met_days(self):
        result = weekly._activity_period(self.start, self.end, None)
        self.assertEqual(result['step_days'], 7)
        self.assertEqual(result['average_steps'], 4000)
        self.assertEqual(result['days_target_met'], 0)

    def test_numeric_target_retains_inclusive_threshold(self):
        result = weekly._activity_period(self.start, self.end, 5000)
        self.assertEqual(result['days_target_met'], 3)

    def test_empty_period_is_safe_with_null_target(self):
        result = weekly._activity_period(self.start - timedelta(days=7), self.start - timedelta(days=1), None)
        self.assertEqual(result['step_days'], 0)
        self.assertIsNone(result['average_steps'])
        self.assertEqual(result['days_target_met'], 0)

    def test_null_optional_steps_and_future_rows_are_excluded(self):
        self.rows += [(self.end, None), (self.end + timedelta(days=1), 999999.0)]
        result = weekly._activity_period(self.start, self.end, 5000)
        self.assertEqual(result['step_days'], 7)
        self.assertEqual(result['average_steps'], 4000)
        self.assertEqual(result['days_target_met'], 3)

    def test_missing_goal_is_no_goal_not_zero_division(self):
        result = weekly._activity_adherence(self.start, self.end,
                    self.start - timedelta(days=7), self.start - timedelta(days=1), None)
        self.assertEqual(result['status'], 'no_goal')
        self.assertIsNone(result['adherence_percentage'])

    def test_zero_target_does_not_divide_by_zero(self):
        result = weekly._activity_adherence(self.start, self.end,
                    self.start - timedelta(days=7), self.start - timedelta(days=1), {'daily_step_target': 0})
        self.assertEqual(result['status'], 'no_goal')
        self.assertIsNone(result['adherence_percentage'])


if __name__ == '__main__':
    unittest.main()
