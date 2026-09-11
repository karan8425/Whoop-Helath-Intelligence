"""Read-only Dev route scenarios; external AI/auth/cache writes are isolated."""
import os
import re
import unittest
from contextlib import contextmanager
from datetime import datetime, time, timedelta, timezone
from unittest.mock import patch

import db
import goals
import weekly_health_intelligence_store as store
from freshness import freshness_status
from weekly_health_intelligence import _mock_weekly_health_intelligence
import test_weekly_activity_target as activity_tests


@unittest.skipUnless(os.getenv('WEEKLY_P0_POSTGRES_TESTS') == '1',
                     'requires explicit Development PostgreSQL opt-in')
class WeeklyRefreshPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Reuse the exact Development credential guard and connection setup.
        activity_tests.WeeklyActivityPostgresTests.setUpClass.__func__(cls)
        import main
        cls.main = main

    def setUp(self):
        self.addCleanup(self.conn.rollback)
        self.conn.execute('SET TRANSACTION READ ONLY')
        self.assertEqual(self.conn.execute('SHOW transaction_read_only').fetchone()['transaction_read_only'], 'on')
        self.latest = self.conn.execute('SELECT max(metric_date) AS day FROM whoop_daily_metrics').fetchone()['day']
        if self.latest is None:
            self.skipTest('Development needs WHOOP history for route scenario tests')
        self.enterContext(patch.object(goals, 'init_goal_profiles'))
        self.enterContext(patch.object(store, 'ensure_table'))
        self.enterContext(patch.object(self.main, 'require_ingest_key'))
        self.enterContext(patch.object(store, 'generate_weekly_health_intelligence', side_effect=_mock_weekly_health_intelligence))
        self.enterContext(patch.object(store, 'save_intelligence', return_value={'id': None}))
        # Exercise the miss path even if the real Dev cache is now populated.
        self.enterContext(patch.object(store, 'load_current_intelligence', return_value=None))
        self.enterContext(patch.object(store, 'load_cached_intelligence', return_value=None))

    def scenario(self, state):
        case = self
        class Connection:
            metric_reads = 0
            @contextmanager
            def cursor(self):
                with case.conn.cursor() as real:
                    connection = self
                    class Cursor:
                        def execute(self, query, params=None):
                            query = query.strip()
                            ctes = []
                            if 'whoop_daily_metrics' in query:
                                connection.metric_reads += 1
                                prior = state == 'pending' or (state == 'transition' and connection.metric_reads == 1)
                                cutoff = case.latest - timedelta(days=int(prior))
                                query = re.sub(r'\b(?:public\.)?whoop_daily_metrics\b', 'probe_metrics', query)
                                ctes.append("probe_metrics AS (SELECT * FROM public.whoop_daily_metrics WHERE metric_date <= DATE '" + cutoff.isoformat() + "')")
                            if state == 'optional_missing' and 'apple_health_daily_activity' in query:
                                query = re.sub(r'\b(?:public\.)?apple_health_daily_activity\b', 'probe_activity', query)
                                ctes.append('probe_activity AS (SELECT * FROM public.apple_health_daily_activity WHERE false)')
                            if ctes:
                                query = 'WITH ' + ', '.join(ctes) + (', ' + query[5:] if query.upper().startswith('WITH ') else ' ' + query)
                            real.execute(query, params)
                        def fetchone(self): return real.fetchone()
                        def fetchall(self): return real.fetchall()
                    yield Cursor()
        token = db._active_connection.set(Connection())
        try:
            from fastapi.testclient import TestClient
            # No lifespan/startup runs: schema setup and pipeline writes stay off.
            response = TestClient(self.main.app).get('/api/v1/health-intelligence/weekly')
            self.assertEqual(response.status_code, 200)
            result = response.json()
            self.assertEqual(result['status'], 'ok')
            expected = self.latest - timedelta(days=int(state == 'pending'))
            self.assertEqual(result['period_end_date'], expected.isoformat())
            self.assertEqual(result['brief']['period_end_date'], expected.isoformat())
            freshness = freshness_status(now_utc=datetime.combine(self.latest, time(12), tzinfo=timezone.utc))
            self.assertEqual(freshness['can_generate_current_recommendation'], state != 'pending')
        finally:
            db._active_connection.reset(token)
        self.assertEqual(self.conn.execute('SHOW transaction_read_only').fetchone()['transaction_read_only'], 'on')

    def test_stable_weekly_endpoint_200(self): self.scenario('stable')
    def test_pending_today_uses_prior_period_without_weakening_freshness(self): self.scenario('pending')
    def test_refresh_boundary_between_reads_does_not_raise(self): self.scenario('transition')
    def test_immediately_after_refresh_is_current(self): self.scenario('after_refresh')
    def test_missing_optional_activity_data_does_not_raise(self): self.scenario('optional_missing')
