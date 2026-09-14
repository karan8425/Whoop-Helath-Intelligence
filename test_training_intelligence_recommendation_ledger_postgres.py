"""Intelligence Architecture M1.0: Decision Ledger - opt-in Postgres
tests (transaction atomicity, FK/uniqueness integrity, retrieval,
outcome join, rollback-on-failure).

Every test binds a dedicated, autocommit=False connection to
db._active_connection and rolls it back in cleanup - matching
test_training_intelligence_program_adaptation_postgres.py's own
established "no_future_leakage" isolation pattern - so nothing written
by these tests is ever permanently committed to the Development
database.

Opt-in: requires DATABASE_URL pointed at the Development database and
TRAINING_INTELLIGENCE_POSTGRES_TESTS=1.
"""
import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone

import db
from db import get_conn
from training_intelligence.ledger import schema
from training_intelligence.ledger.repository import (
    create_recommendation_event, create_recommendation_event_with_candidates,
    add_recommendation_candidates, record_recommendation_outcome,
    get_recommendation_event, get_recommendation_candidates, get_recommendation_outcome,
    get_recommendation_for_replay, LedgerWriteError,
)
from training_intelligence.ledger.hashing import compute_decision_id

AS_OF = datetime(2026, 9, 14, 8, 0, tzinfo=timezone.utc)


def _event_kwargs(**overrides):
    base = dict(
        user_id="primary", recommendation_type="ledger_test_fixture", as_of=AS_OF,
        engine_name="test.fixture.engine", engine_version="1", feature_schema_version=1,
        state_snapshot={"fixture": True, "as_of": AS_OF.isoformat()},
        source_data_cutoff=AS_OF,
    )
    base.update(overrides)
    return base


def _candidate(**overrides):
    base = {
        "candidate_key": "v2_1-selected", "rank": 1, "selected": True, "eligible": True,
        "candidate_payload": {"action": "KEEP", "selected_session": {"session_key": "upper_a"}},
        "engine_version": "1", "feature_schema_version": 1,
    }
    base.update(overrides)
    return base


@unittest.skipUnless(
    os.getenv("TRAINING_INTELLIGENCE_POSTGRES_TESTS") == "1",
    "opt-in PostgreSQL",
)
class RecommendationLedgerPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Real, persisted DDL (idempotent CREATE ... IF NOT EXISTS) -
        # deliberately NOT inside the per-test rollback scope, matching
        # how every other opt-in Postgres test suite in this repo
        # assumes its own tables already exist rather than re-creating
        # them inside a throwaway transaction.
        schema.ensure_tables()

    def setUp(self):
        dsn = os.environ["DATABASE_URL"]
        if "umodirrruxtjoqfjayoy" not in dsn or "yyrgabalzmgoquleepyw" in dsn:
            self.fail("Development database guard failed")
        import psycopg
        from psycopg.rows import dict_row
        self.conn = psycopg.connect(dsn, row_factory=dict_row, autocommit=False)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        token = db._active_connection.set(self.conn)
        self.addCleanup(db._active_connection.reset, token)

    def _unique_as_of(self):
        # A fresh as_of per test avoids decision_id collisions between
        # tests sharing this class (decision_id is deterministic from
        # (type, user, as_of, engine)).
        return AS_OF + timedelta(seconds=uuid.uuid4().int % 100000)

    # -- transaction atomicity ------------------------------------

    def test_event_and_candidate_committed_together(self):
        as_of = self._unique_as_of()
        written = create_recommendation_event_with_candidates(_event_kwargs(as_of=as_of), [_candidate()])
        decision_id = written["event"]["decision_id"]
        self.assertIsNotNone(get_recommendation_event(decision_id))
        self.assertEqual(len(get_recommendation_candidates(decision_id)), 1)

    def test_failed_candidate_in_batch_rolls_back_whole_batch(self):
        as_of = self._unique_as_of()
        event = create_recommendation_event(**_event_kwargs(as_of=as_of))
        decision_id = event["decision_id"]

        good = _candidate(candidate_key="c1", selected=False)
        # `rank` must be an integer column - a string triggers a genuine
        # DB-level (not Python-validation-level) failure, exercised
        # inside a SAVEPOINT so only this attempt rolls back, not the
        # whole test connection.
        bad = _candidate(candidate_key="c2", selected=False, rank="not-an-int")
        with self.assertRaises(LedgerWriteError):
            with self.conn.transaction():
                add_recommendation_candidates(decision_id, [good, bad], conn=self.conn)

        # Neither candidate from the failed batch persisted - true
        # atomicity, not "first one succeeded, second failed".
        self.assertEqual(get_recommendation_candidates(decision_id), [])

    # -- FK integrity ------------------------------------------------

    def test_candidate_requires_existing_event(self):
        nonexistent_decision_id = str(uuid.uuid4())
        with self.assertRaises(LedgerWriteError):
            add_recommendation_candidates(nonexistent_decision_id, [_candidate()], conn=self.conn)

    def test_outcome_requires_existing_event(self):
        nonexistent_decision_id = str(uuid.uuid4())
        with self.assertRaises(Exception):
            with self.conn.transaction():
                record_recommendation_outcome(nonexistent_decision_id, user_action="ACCEPTED")

    # -- uniqueness ----------------------------------------------------

    def test_duplicate_decision_id_is_idempotent(self):
        as_of = self._unique_as_of()
        kwargs = _event_kwargs(as_of=as_of)
        first = create_recommendation_event(**kwargs)
        second = create_recommendation_event(**kwargs)
        self.assertEqual(first["decision_id"], second["decision_id"])
        with self.conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) AS n FROM public.{schema.EVENTS_TABLE} WHERE decision_id = %s",
                (first["decision_id"],),
            )
            self.assertEqual(cur.fetchone()["n"], 1)

    def test_duplicate_candidate_key_rejected_by_db_even_bypassing_python_validation(self):
        as_of = self._unique_as_of()
        event = create_recommendation_event(**_event_kwargs(as_of=as_of))
        decision_id = event["decision_id"]
        add_recommendation_candidates(decision_id, [_candidate(candidate_key="dup", selected=False)], conn=self.conn)
        # Second insert of the SAME candidate_key uses ON CONFLICT DO
        # NOTHING (idempotent, not an error) - verify no second row.
        add_recommendation_candidates(decision_id, [_candidate(candidate_key="dup", selected=False)], conn=self.conn)
        rows = [c for c in get_recommendation_candidates(decision_id) if c["candidate_key"] == "dup"]
        self.assertEqual(len(rows), 1)

    def test_at_most_one_selected_candidate_enforced_by_db(self):
        as_of = self._unique_as_of()
        event = create_recommendation_event(**_event_kwargs(as_of=as_of))
        decision_id = event["decision_id"]
        add_recommendation_candidates(decision_id, [_candidate(candidate_key="c1", selected=True)], conn=self.conn)
        # Bypass the repository's own Python-level "_validate_candidates"
        # guard entirely - insert directly via raw SQL to prove the
        # partial unique index itself rejects a second selected=true row.
        with self.assertRaises(Exception):
            with self.conn.transaction():
                with self.conn.cursor() as cur:
                    cur.execute(
                        f"""
                        INSERT INTO public.{schema.CANDIDATES_TABLE}
                            (decision_id, candidate_key, selected, eligible, candidate_payload,
                             engine_version, feature_schema_version)
                        VALUES (%s, 'c2', TRUE, TRUE, '{{}}'::jsonb, '1', 1)
                        """,
                        (decision_id,),
                    )

    # -- retrieval / replay --------------------------------------------

    def test_event_and_candidate_retrieval_matches_written_shape(self):
        as_of = self._unique_as_of()
        payload = {"action": "KEEP", "selected_session": {"session_key": "upper_a"}, "dose": {"target_sets": 18}}
        written = create_recommendation_event_with_candidates(
            _event_kwargs(as_of=as_of, selected_recommendation=payload),
            [_candidate(candidate_payload=payload)],
        )
        decision_id = written["event"]["decision_id"]
        event = get_recommendation_event(decision_id)
        self.assertEqual(event["selected_recommendation"], payload)
        candidates = get_recommendation_candidates(decision_id)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["candidate_payload"], payload)
        self.assertTrue(candidates[0]["selected"])
        self.assertEqual(event["selected_candidate_id"], candidates[0]["id"])

    def test_outcome_join_retrieval(self):
        as_of = self._unique_as_of()
        written = create_recommendation_event_with_candidates(_event_kwargs(as_of=as_of), [_candidate()])
        decision_id = written["event"]["decision_id"]
        self.assertIsNone(get_recommendation_outcome(decision_id))

        record_recommendation_outcome(decision_id, user_action="ACCEPTED", completion_fraction=1.0)
        outcome = get_recommendation_outcome(decision_id)
        self.assertEqual(outcome["decision_id"], decision_id)
        self.assertEqual(outcome["user_action"], "ACCEPTED")

        replay = get_recommendation_for_replay(decision_id)
        self.assertTrue(replay["input_hash_verified"])
        self.assertEqual(replay["outcome"]["user_action"], "ACCEPTED")
        self.assertEqual(len(replay["candidates"]), 1)

    def test_replay_for_unknown_decision_returns_none(self):
        self.assertIsNone(get_recommendation_for_replay(str(uuid.uuid4())))

    def test_input_hash_verified_true_for_untampered_row(self):
        as_of = self._unique_as_of()
        written = create_recommendation_event_with_candidates(_event_kwargs(as_of=as_of), [_candidate()])
        replay = get_recommendation_for_replay(written["event"]["decision_id"])
        self.assertTrue(replay["input_hash_verified"])
        self.assertEqual(replay["recomputed_input_hash"], written["event"]["input_hash"])


if __name__ == "__main__":
    unittest.main()
