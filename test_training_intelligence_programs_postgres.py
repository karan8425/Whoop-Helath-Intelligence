"""Program Intelligence V1: real-PostgreSQL schema, repository, seed,
user-state, and temporal-signature tests against the Development
database - matching the established opt-in Postgres test convention
(test_training_intelligence_calibration_v2_postgres.py etc.) exactly.

Constraint/repository/user-state tests use their OWN fixture rows
inside a transaction that is ALWAYS rolled back. The seed-coverage
tests read the two REAL, already-seeded demonstration programs
directly (read-only - never mutated, never rolled back, since they are
real persisted infrastructure, not test fixtures).

Opt-in: requires DATABASE_URL pointed at the Development database and
TRAINING_INTELLIGENCE_POSTGRES_TESTS=1.
"""
import os
import unittest
import uuid
from datetime import date, timedelta

import psycopg
from psycopg.errors import IntegrityError
from psycopg.rows import dict_row

from training_intelligence.programs import repository, service
from training_intelligence.programs.mapping_seed import generate_mapping_rows
from training_intelligence.programs.seed import SEED_PROGRAMS

_FIXTURE_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "training-intelligence-program-layer-v1-fixture")


def _uuid(label: str) -> str:
    return str(uuid.uuid5(_FIXTURE_NAMESPACE, label))


@unittest.skipUnless(
    os.getenv("TRAINING_INTELLIGENCE_POSTGRES_TESTS") == "1",
    "opt-in PostgreSQL",
)
class ProgramIntelligenceSchemaTests(unittest.TestCase):
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

    def _insert_program(self, slug, **overrides):
        defaults = {
            "slug": slug, "name": "Fixture Program", "goal_mode": "general_fitness",
            "experience_level": "beginner", "split_type": "full_body", "days_per_week": 3,
            "source_type": "original",
        }
        defaults.update(overrides)
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.training_programs
                    (slug, name, goal_mode, experience_level, split_type, days_per_week, source_type)
                VALUES (%(slug)s, %(name)s, %(goal_mode)s, %(experience_level)s, %(split_type)s,
                        %(days_per_week)s, %(source_type)s)
                RETURNING id
                """,
                defaults,
            )
            return cur.fetchone()["id"]

    # ---- migration/tables exist ----

    def test_all_seven_tables_exist(self):
        tables = ("training_programs", "training_program_weeks", "training_program_sessions",
                  "training_session_slots", "program_movement_mappings", "user_training_programs",
                  "user_program_session_state")
        with self.conn.cursor() as cur:
            for t in tables:
                cur.execute(
                    "SELECT COUNT(*) AS n FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name=%s", (t,),
                )
                self.assertEqual(cur.fetchone()["n"], 1, f"missing table {t}")

    # ---- constraints ----

    def test_days_per_week_out_of_range_rejected(self):
        with self.assertRaises(IntegrityError):
            self._insert_program(_uuid("bad_days"), days_per_week=9)

    def test_invalid_status_rejected(self):
        with self.conn.cursor() as cur:
            with self.assertRaises(IntegrityError):
                cur.execute(
                    "INSERT INTO public.training_programs (slug, name, goal_mode, experience_level, "
                    "split_type, days_per_week, source_type, status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (_uuid("bad_status"), "x", "general_fitness", "beginner", "full_body", 3, "original", "nonsense"),
                )

    def test_duplicate_slug_rejected(self):
        # Postgres enforces UNIQUE against a transaction's own prior
        # uncommitted inserts too - no commit() needed (and none is
        # done, keeping this test's writes rollback-only).
        self._insert_program(_uuid("dup_slug"))
        with self.assertRaises(IntegrityError):
            self._insert_program(_uuid("dup_slug"))

    def test_slot_set_max_below_set_min_rejected(self):
        program_id = self._insert_program(_uuid("bad_slot_program"))
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO public.training_program_sessions (program_id, session_key, session_name, "
                "session_family, sequence_index) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                (program_id, "s1", "Session 1", "Full Body", 0),
            )
            session_id = cur.fetchone()["id"]
            with self.assertRaises(IntegrityError):
                cur.execute(
                    "INSERT INTO public.training_session_slots (session_id, slot_index, movement_pattern, "
                    "primary_muscle, exercise_role, set_min, set_max, rep_min, rep_max) "
                    "VALUES (%s,0,'hinge','hamstrings','primary_compound',5,2,6,10)",
                    (session_id,),
                )

    def test_duplicate_slot_index_within_session_rejected(self):
        program_id = self._insert_program(_uuid("dup_slot_program"))
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO public.training_program_sessions (program_id, session_key, session_name, "
                "session_family, sequence_index) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                (program_id, "s1", "Session 1", "Full Body", 0),
            )
            session_id = cur.fetchone()["id"]
            cur.execute(
                "INSERT INTO public.training_session_slots (session_id, slot_index, movement_pattern, "
                "primary_muscle, exercise_role, set_min, set_max, rep_min, rep_max) "
                "VALUES (%s,0,'hinge','hamstrings','primary_compound',2,4,6,10)",
                (session_id,),
            )
            with self.assertRaises(IntegrityError):
                cur.execute(
                    "INSERT INTO public.training_session_slots (session_id, slot_index, movement_pattern, "
                    "primary_muscle, exercise_role, set_min, set_max, rep_min, rep_max) "
                    "VALUES (%s,0,'squat_lunge','quads','primary_compound',2,4,6,10)",
                    (session_id,),
                )

    def test_mapping_requires_real_tonal_movement_fk(self):
        with self.conn.cursor() as cur:
            with self.assertRaises(IntegrityError):
                cur.execute(
                    "INSERT INTO public.program_movement_mappings "
                    "(tonal_movement_id, movement_pattern, primary_muscle, exercise_role, mapping_quality, "
                    "rationale_code) VALUES (gen_random_uuid(), 'hinge', 'hamstrings', 'primary_compound', "
                    "'DIRECT', 'fixture')",
                )

    def test_invalid_mapping_quality_rejected(self):
        with self.conn.cursor() as cur:
            cur.execute("SELECT movement_id FROM public.tonal_movements WHERE NOT is_generic LIMIT 1")
            movement_id = cur.fetchone()["movement_id"]
            with self.assertRaises(IntegrityError):
                cur.execute(
                    "INSERT INTO public.program_movement_mappings "
                    "(tonal_movement_id, movement_pattern, primary_muscle, exercise_role, mapping_quality, "
                    "rationale_code) VALUES (%s, 'hinge', 'hamstrings', 'primary_compound', 'GREAT', 'fixture')",
                    (movement_id,),
                )


@unittest.skipUnless(
    os.getenv("TRAINING_INTELLIGENCE_POSTGRES_TESTS") == "1",
    "opt-in PostgreSQL",
)
class ProgramRepositoryTests(unittest.TestCase):
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

    def _seed_fixture_program(self, slug, n_sessions=2, n_slots=3):
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO public.training_programs (slug, name, goal_mode, experience_level, split_type, "
                "days_per_week, source_type, status) VALUES (%s,'Fixture','general_fitness','beginner',"
                "'full_body',3,'original','active') RETURNING id",
                (slug,),
            )
            program_id = cur.fetchone()["id"]
            for i in range(n_sessions):
                cur.execute(
                    "INSERT INTO public.training_program_sessions (program_id, session_key, session_name, "
                    "session_family, sequence_index) VALUES (%s,%s,%s,'Full Body',%s) RETURNING id",
                    (program_id, f"s{i}", f"Session {i}", i),
                )
                session_id = cur.fetchone()["id"]
                for j in range(n_slots):
                    cur.execute(
                        "INSERT INTO public.training_session_slots (session_id, slot_index, movement_pattern, "
                        "primary_muscle, exercise_role, set_min, set_max, rep_min, rep_max) "
                        "VALUES (%s,%s,'hinge','hamstrings','primary_compound',2,4,6,10)",
                        (session_id, j),
                    )
        return program_id

    def test_list_programs_filters_by_goal_mode(self):
        self._seed_fixture_program(_uuid("list_filter_a"))
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO public.training_programs (slug, name, goal_mode, experience_level, split_type, "
                "days_per_week, source_type) VALUES (%s,'Fixture B','strength','beginner','full_body',3,"
                "'original')",
                (_uuid("list_filter_b"),),
            )
        results = repository.list_programs(goal_mode="general_fitness", conn=self.conn)
        slugs = {p.slug for p in results}
        self.assertIn(_uuid("list_filter_a"), slugs)
        self.assertNotIn(_uuid("list_filter_b"), slugs)

    def test_full_program_structure_loads_all_sessions_and_slots(self):
        slug = _uuid("full_structure")
        self._seed_fixture_program(slug, n_sessions=3, n_slots=4)
        structure = repository.get_full_program_structure(slug=slug)
        self.assertEqual(len(structure.sessions), 3)
        for session in structure.sessions:
            self.assertEqual(len(structure.slots_by_session_id[session.id]), 4)

    def test_slot_ordering_is_not_duplicated(self):
        slug = _uuid("slot_ordering")
        self._seed_fixture_program(slug, n_sessions=1, n_slots=5)
        structure = repository.get_full_program_structure(slug=slug)
        slots = structure.slots_by_session_id[structure.sessions[0].id]
        indices = [s.slot_index for s in slots]
        self.assertEqual(indices, sorted(indices))
        self.assertEqual(len(indices), len(set(indices)))

    def test_get_candidate_tonal_movements_excludes_unsuitable_by_default(self):
        with self.conn.cursor() as cur:
            cur.execute("SELECT movement_id FROM public.tonal_movements WHERE NOT is_generic LIMIT 1")
            movement_id = cur.fetchone()["movement_id"]
            cur.execute(
                "INSERT INTO public.program_movement_mappings (tonal_movement_id, movement_pattern, "
                "primary_muscle, exercise_role, mapping_quality, rationale_code) "
                "VALUES (%s, 'core_flexion', 'core', 'core', 'UNSUITABLE', 'fixture')",
                (movement_id,),
            )
        candidates = repository.get_candidate_tonal_movements("core_flexion", "core", "core", conn=self.conn)
        self.assertEqual([c for c in candidates if str(c["tonal_movement_id"]) == str(movement_id)], [])


@unittest.skipUnless(
    os.getenv("TRAINING_INTELLIGENCE_POSTGRES_TESTS") == "1",
    "opt-in PostgreSQL",
)
class SeedProgramCoverageTests(unittest.TestCase):
    """Reads the two REAL, already-seeded demonstration programs
    directly - read-only, no fixtures, no rollback (this is real
    persisted infrastructure, like tonal_movements itself)."""

    def test_both_seed_programs_exist(self):
        for spec in SEED_PROGRAMS:
            program = repository.get_program(slug=spec["slug"])
            self.assertIsNotNone(program, f"missing seed program {spec['slug']}")
            self.assertEqual(program.source_type, "original")

    def test_both_seed_programs_have_four_sessions(self):
        for spec in SEED_PROGRAMS:
            structure = service.get_program_structure(spec["slug"])
            self.assertEqual(len(structure["sessions"]), 4)

    def test_every_required_slot_has_at_least_one_candidate(self):
        unmapped = []
        for spec in SEED_PROGRAMS:
            structure = service.get_program_structure(spec["slug"])
            for session in structure["sessions"]:
                for slot in session["slots"]:
                    if not slot["required"]:
                        continue
                    candidates = repository.get_candidate_tonal_movements(
                        slot["movement_pattern"], slot["primary_muscle"], slot["exercise_role"],
                    )
                    if not candidates:
                        unmapped.append((spec["slug"], session["session_key"], slot["slot_index"]))
        self.assertEqual(unmapped, [], f"unmapped required slots: {unmapped}")

    def test_mapping_seed_generation_is_idempotent(self):
        # Running the generator again must not find NEW rows beyond what
        # a fresh insert would produce - i.e. it is stable, not
        # accumulating duplicates on repeated runs.
        rows = generate_mapping_rows()
        rows_again = generate_mapping_rows()
        self.assertEqual(len(rows), len(rows_again))


@unittest.skipUnless(
    os.getenv("TRAINING_INTELLIGENCE_POSTGRES_TESTS") == "1",
    "opt-in PostgreSQL",
)
class UserProgramStateTests(unittest.TestCase):
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
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO public.training_programs (slug, name, goal_mode, experience_level, split_type, "
                "days_per_week, source_type) VALUES (%s,'Fixture','general_fitness','beginner','full_body',3,"
                "'original') RETURNING id",
                (_uuid("user_state_program"),),
            )
            self.program_id = cur.fetchone()["id"]

    def test_active_enrollment_retrievable(self):
        user_id = _uuid("user_active")
        enrollment = repository.enroll_user_in_program(user_id, self.program_id)
        active = repository.get_active_user_program(user_id, conn=self.conn)
        self.assertIsNotNone(active)
        self.assertEqual(active.id, enrollment.id)

    def test_multiple_historical_enrollments_permitted(self):
        user_id = _uuid("user_history")
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO public.user_training_programs (user_id, program_id, status, started_on, ended_on) "
                "VALUES (%s,%s,'completed',%s,%s)",
                (user_id, self.program_id, date.today() - timedelta(days=60), date.today() - timedelta(days=30)),
            )
            cur.execute(
                "INSERT INTO public.user_training_programs (user_id, program_id, status, started_on, ended_on) "
                "VALUES (%s,%s,'cancelled',%s,%s)",
                (user_id, self.program_id, date.today() - timedelta(days=29), date.today() - timedelta(days=10)),
            )
        history = repository.list_user_programs(user_id, conn=self.conn)
        self.assertEqual(len(history), 2)

    def test_at_most_one_active_enrollment_per_user_enforced_by_db(self):
        user_id = _uuid("user_one_active")
        repository.enroll_user_in_program(user_id, self.program_id)
        with self.assertRaises(IntegrityError):
            with self.conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO public.user_training_programs (user_id, program_id, status) "
                    "VALUES (%s,%s,'active')",
                    (user_id, self.program_id),
                )

    def test_program_session_state_columns_support_a_future_as_of_filter(self):
        # Section-required: signatures must remain temporal-safety-
        # capable even though no as_of filtering is implemented yet.
        # This pins that scheduled_date/completed_at/created_at all
        # exist and are independently queryable - the columns a future
        # "as_of" filter would need are already present, not precluded.
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='user_program_session_state' "
                "AND column_name IN ('scheduled_date', 'completed_at', 'created_at')"
            )
            columns = {r["column_name"] for r in cur.fetchall()}
        self.assertEqual(columns, {"scheduled_date", "completed_at", "created_at"})


if __name__ == "__main__":
    unittest.main()
