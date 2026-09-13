"""Program Intelligence V1 - read operations against the schema in
supabase/migrations/20260913120000_add_program_intelligence_v1.sql.

Explicit, testable SQL. Loading a full program is 4 queries total
(program, all its sessions, all their slots in one IN(...) query) -
never one query per session or per slot (no N+1).

Read-mostly by design for this milestone: the only write function is
enroll_user_in_program(), needed to make section-7 "user state" tests
meaningful; no program/session/slot/mapping write path exists yet
(seed.py inserts those directly, not through this module, and is not
wired into any request path).
"""
from __future__ import annotations

import json
from datetime import date

from db import get_conn
from training_intelligence.programs.models import (
    TrainingProgram, ProgramWeek, ProgramSession, SessionSlot,
    MovementMapping, UserProgramEnrollment, ProgramStructure,
)
from training_intelligence.programs.taxonomy import MAPPING_QUALITY_RANK


def _row_to_program(row) -> TrainingProgram:
    return TrainingProgram(
        id=str(row["id"]), slug=row["slug"], name=row["name"], description=row["description"],
        goal_mode=row["goal_mode"], experience_level=row["experience_level"], split_type=row["split_type"],
        days_per_week=row["days_per_week"], duration_weeks=row["duration_weeks"],
        default_session_duration_min=row["default_session_duration_min"],
        source_type=row["source_type"], source_name=row["source_name"], source_url=row["source_url"],
        reference_notes=row["reference_notes"], version=row["version"], status=row["status"],
        metadata=row["metadata_json"] or {}, created_at=row["created_at"], updated_at=row["updated_at"],
    )


def _row_to_week(row) -> ProgramWeek:
    return ProgramWeek(
        id=str(row["id"]), program_id=str(row["program_id"]), week_number=row["week_number"],
        label=row["label"], phase_name=row["phase_name"], metadata=row["metadata_json"] or {},
    )


def _row_to_session(row) -> ProgramSession:
    return ProgramSession(
        id=str(row["id"]), program_id=str(row["program_id"]),
        program_week_id=str(row["program_week_id"]) if row["program_week_id"] else None,
        session_key=row["session_key"], session_name=row["session_name"], session_family=row["session_family"],
        sequence_index=row["sequence_index"], day_of_week=row["day_of_week"],
        expected_duration_min=row["expected_duration_min"], primary_focus=row["primary_focus"],
        metadata=row["metadata_json"] or {},
    )


def _row_to_slot(row) -> SessionSlot:
    return SessionSlot(
        id=str(row["id"]), session_id=str(row["session_id"]), slot_index=row["slot_index"],
        movement_pattern=row["movement_pattern"], movement_subpattern=row["movement_subpattern"],
        primary_muscle=row["primary_muscle"], secondary_muscles=tuple(row["secondary_muscles"] or []),
        exercise_role=row["exercise_role"], required=row["required"],
        set_min=row["set_min"], set_max=row["set_max"], rep_min=row["rep_min"], rep_max=row["rep_max"],
        rir_min=float(row["rir_min"]) if row["rir_min"] is not None else None,
        rir_max=float(row["rir_max"]) if row["rir_max"] is not None else None,
        rest_seconds_min=row["rest_seconds_min"], rest_seconds_max=row["rest_seconds_max"],
        priority=row["priority"], substitution_group=row["substitution_group"], metadata=row["metadata_json"] or {},
    )


def _row_to_mapping(row) -> MovementMapping:
    return MovementMapping(
        id=str(row["id"]), tonal_movement_id=str(row["tonal_movement_id"]), movement_name=row.get("movement_name"),
        movement_pattern=row["movement_pattern"], primary_muscle=row["primary_muscle"], exercise_role=row["exercise_role"],
        mapping_quality=row["mapping_quality"], confidence=float(row["confidence"]),
        rationale_code=row["rationale_code"], rationale_notes=row["rationale_notes"],
        required_accessory=row["required_accessory"], contraindication_notes=row["contraindication_notes"],
        active=row["active"], version=row["version"],
    )


def _row_to_enrollment(row) -> UserProgramEnrollment:
    return UserProgramEnrollment(
        id=str(row["id"]), user_id=row["user_id"], program_id=str(row["program_id"]),
        started_on=row["started_on"], ended_on=row["ended_on"], status=row["status"],
        current_week=row["current_week"], current_session_sequence=row["current_session_sequence"],
        preferred_days_per_week=row["preferred_days_per_week"],
        preferred_session_duration_min=row["preferred_session_duration_min"], metadata=row["metadata_json"] or {},
    )


def get_program(*, program_id=None, slug=None, conn=None):
    if not program_id and not slug:
        raise ValueError("get_program requires program_id or slug")

    def read(connection):
        with connection.cursor() as cur:
            if program_id:
                cur.execute("SELECT * FROM public.training_programs WHERE id = %s", (program_id,))
            else:
                cur.execute("SELECT * FROM public.training_programs WHERE slug = %s", (slug,))
            row = cur.fetchone()
            return _row_to_program(row) if row else None

    if conn is not None:
        return read(conn)
    with get_conn() as connection:
        return read(connection)


def list_programs(*, goal_mode=None, experience_level=None, days_per_week=None, split_type=None,
                   status="active", conn=None):
    clauses, params = ["status = %s"], [status]
    if goal_mode:
        clauses.append("goal_mode = %s")
        params.append(goal_mode)
    if experience_level:
        clauses.append("experience_level = %s")
        params.append(experience_level)
    if days_per_week:
        clauses.append("days_per_week = %s")
        params.append(days_per_week)
    if split_type:
        clauses.append("split_type = %s")
        params.append(split_type)
    query = f"SELECT * FROM public.training_programs WHERE {' AND '.join(clauses)} ORDER BY slug"

    def read(connection):
        with connection.cursor() as cur:
            cur.execute(query, params)
            return [_row_to_program(r) for r in cur.fetchall()]

    if conn is not None:
        return read(conn)
    with get_conn() as connection:
        return read(connection)


def get_program_weeks(program_id, *, conn=None):
    def read(connection):
        with connection.cursor() as cur:
            cur.execute(
                "SELECT * FROM public.training_program_weeks WHERE program_id = %s ORDER BY week_number",
                (program_id,),
            )
            return [_row_to_week(r) for r in cur.fetchall()]

    if conn is not None:
        return read(conn)
    with get_conn() as connection:
        return read(connection)


def get_program_sessions(program_id, *, conn=None):
    def read(connection):
        with connection.cursor() as cur:
            cur.execute(
                "SELECT * FROM public.training_program_sessions WHERE program_id = %s ORDER BY sequence_index",
                (program_id,),
            )
            return [_row_to_session(r) for r in cur.fetchall()]

    if conn is not None:
        return read(conn)
    with get_conn() as connection:
        return read(connection)


def get_program_session_by_key(program_id, session_key, *, conn=None):
    def read(connection):
        with connection.cursor() as cur:
            cur.execute(
                "SELECT * FROM public.training_program_sessions WHERE program_id = %s AND session_key = %s",
                (program_id, session_key),
            )
            row = cur.fetchone()
            return _row_to_session(row) if row else None

    if conn is not None:
        return read(conn)
    with get_conn() as connection:
        return read(connection)


def get_session_slots(session_id, *, conn=None):
    def read(connection):
        with connection.cursor() as cur:
            cur.execute(
                "SELECT * FROM public.training_session_slots WHERE session_id = %s ORDER BY slot_index",
                (session_id,),
            )
            return [_row_to_slot(r) for r in cur.fetchall()]

    if conn is not None:
        return read(conn)
    with get_conn() as connection:
        return read(connection)


def get_slots_for_sessions(session_ids, *, conn=None):
    """Batch-loads slots for MANY sessions in one query (the anti-N+1
    primitive get_full_program_structure() relies on) - returns
    {session_id: [SessionSlot, ...]}."""
    if not session_ids:
        return {}

    def read(connection):
        with connection.cursor() as cur:
            cur.execute(
                "SELECT * FROM public.training_session_slots WHERE session_id = ANY(%s) ORDER BY session_id, slot_index",
                (list(session_ids),),
            )
            grouped = {}
            for row in cur.fetchall():
                grouped.setdefault(str(row["session_id"]), []).append(_row_to_slot(row))
            return grouped

    if conn is not None:
        return read(conn)
    with get_conn() as connection:
        return read(connection)


def get_full_program_structure(*, program_id=None, slug=None) -> ProgramStructure | None:
    """Exactly 4 queries total regardless of how many weeks/sessions/
    slots the program has: the program row, all its sessions, and all
    their slots in one batched IN(...)/ANY(...) query - never N+1."""
    with get_conn() as conn:
        program = get_program(program_id=program_id, slug=slug, conn=conn)
        if program is None:
            return None
        weeks = get_program_weeks(program.id, conn=conn)
        sessions = get_program_sessions(program.id, conn=conn)
        slots_by_session_id = get_slots_for_sessions([s.id for s in sessions], conn=conn)
    return ProgramStructure(program=program, weeks=tuple(weeks), sessions=tuple(sessions),
                             slots_by_session_id=slots_by_session_id)


def get_candidate_tonal_movements(movement_pattern, primary_muscle, exercise_role, *,
                                   include_unsuitable=False, conn=None):
    """Deterministic candidate list for one abstract slot requirement -
    joined against public.tonal_movements (reused, never duplicated) so
    a movement's name/accessory/bilateral metadata is available
    alongside the mapping row for tonal_mapping.py to score."""
    quality_clause = "" if include_unsuitable else "AND m.mapping_quality != 'UNSUITABLE'"

    def read(connection):
        with connection.cursor() as cur:
            cur.execute(
                f"""
                SELECT m.*, tm.name AS movement_name, tm.accessory AS tonal_accessory,
                       tm.is_bilateral, tm.is_two_sided, tm.is_alternating,
                       tm.is_generic, tm.custom_movement
                FROM public.program_movement_mappings m
                JOIN public.tonal_movements tm ON tm.movement_id = m.tonal_movement_id
                WHERE m.movement_pattern = %s AND m.primary_muscle = %s AND m.exercise_role = %s
                  AND m.active {quality_clause}
                ORDER BY m.mapping_quality, m.confidence DESC
                """,
                (movement_pattern, primary_muscle, exercise_role),
            )
            return cur.fetchall()

    if conn is not None:
        return read(conn)
    with get_conn() as connection:
        return read(connection)


def enroll_user_in_program(user_id, program_id, *, started_on=None, preferred_days_per_week=None,
                            preferred_session_duration_min=None):
    """The one write this module performs - a fresh enrollment. Relies
    on the DB's own partial-unique-index (at most one 'active' row per
    user_id) rather than an application-level check-then-insert race."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.user_training_programs
                    (user_id, program_id, started_on, preferred_days_per_week, preferred_session_duration_min)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING *
                """,
                (user_id, program_id, started_on or date.today(), preferred_days_per_week,
                 preferred_session_duration_min),
            )
            row = cur.fetchone()
    return _row_to_enrollment(row)


def get_active_user_program(user_id, *, conn=None):
    def read(connection):
        with connection.cursor() as cur:
            cur.execute(
                "SELECT * FROM public.user_training_programs WHERE user_id = %s AND status = 'active'",
                (user_id,),
            )
            row = cur.fetchone()
            return _row_to_enrollment(row) if row else None

    if conn is not None:
        return read(conn)
    with get_conn() as connection:
        return read(connection)


def list_user_programs(user_id, *, conn=None):
    """All historical enrollments (any status), most recent first."""
    def read(connection):
        with connection.cursor() as cur:
            cur.execute(
                "SELECT * FROM public.user_training_programs WHERE user_id = %s ORDER BY started_on DESC",
                (user_id,),
            )
            return [_row_to_enrollment(r) for r in cur.fetchall()]

    if conn is not None:
        return read(conn)
    with get_conn() as connection:
        return read(connection)


def get_user_program_state(user_training_program_id, *, conn=None):
    def read(connection):
        with connection.cursor() as cur:
            cur.execute(
                "SELECT * FROM public.user_program_session_state WHERE user_training_program_id = %s "
                "ORDER BY scheduled_date NULLS LAST, created_at",
                (user_training_program_id,),
            )
            return cur.fetchall()

    if conn is not None:
        return read(conn)
    with get_conn() as connection:
        return read(connection)
