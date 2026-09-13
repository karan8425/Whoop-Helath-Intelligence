"""Program Intelligence V1 - orchestration layer. Returns structured
program KNOWLEDGE only - never a personalized workout, never anything
readiness/WHOOP-aware. That is Daily Intelligence's job (a future
milestone); this module only exposes what a program says it intends,
plus (deterministically, statically) which Tonal movements COULD
satisfy each requirement.
"""
from __future__ import annotations

from dataclasses import asdict

from db import get_conn
from training_intelligence.programs import repository
from training_intelligence.programs.tonal_mapping import rank_candidates, TONAL_MAPPING_ENGINE_VERSION


def _program_dict(program):
    d = asdict(program)
    d["created_at"] = program.created_at.isoformat() if program.created_at else None
    d["updated_at"] = program.updated_at.isoformat() if program.updated_at else None
    return d


def _week_dict(week):
    return asdict(week)


def _session_dict(session):
    return asdict(session)


def _slot_dict(slot):
    d = asdict(slot)
    d["secondary_muscles"] = list(slot.secondary_muscles)
    return d


def list_programs(*, goal_mode=None, experience_level=None, days_per_week=None, split_type=None,
                   status="active"):
    programs = repository.list_programs(
        goal_mode=goal_mode, experience_level=experience_level, days_per_week=days_per_week,
        split_type=split_type, status=status,
    )
    return [_program_dict(p) for p in programs]


def get_program_structure(program_slug):
    """Returns the full program hierarchy (program -> weeks -> sessions
    -> slots) in 4 DB queries regardless of program size (see
    repository.get_full_program_structure's own docstring). Returns
    None if the slug does not exist - callers decide the 404 shape."""
    structure = repository.get_full_program_structure(slug=program_slug)
    if structure is None:
        return None
    sessions_out = []
    for session in structure.sessions:
        slots = structure.slots_by_session_id.get(session.id, [])
        session_dict = _session_dict(session)
        session_dict["slots"] = [_slot_dict(s) for s in slots]
        sessions_out.append(session_dict)
    return {
        "program": _program_dict(structure.program),
        "weeks": [_week_dict(w) for w in structure.weeks],
        "sessions": sessions_out,
    }


def get_session_structure(program_slug, session_key):
    """One session's slots, without loading the rest of the program."""
    with get_conn() as conn:
        program = repository.get_program(slug=program_slug, conn=conn)
        if program is None:
            return None
        session = repository.get_program_session_by_key(program.id, session_key, conn=conn)
        if session is None:
            return None
        slots = repository.get_session_slots(session.id, conn=conn)
    session_dict = _session_dict(session)
    session_dict["slots"] = [_slot_dict(s) for s in slots]
    return {"program": _program_dict(program), "session": session_dict}


def get_tonal_candidates_for_slot(slot_id):
    """Ranked, explainable Tonal-movement candidates for one slot. Does
    NOT consider WHOOP recovery, today's readiness, today's stimulus
    debt, or any tonnage target - deterministic mapping-quality/
    confidence/accessory ranking only (tonal_mapping.py)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM public.training_session_slots WHERE id = %s", (slot_id,))
            slot_row = cur.fetchone()
        if slot_row is None:
            return None
        mapping_rows = repository.get_candidate_tonal_movements(
            slot_row["movement_pattern"], slot_row["primary_muscle"], slot_row["exercise_role"], conn=conn,
        )
    ranked = rank_candidates(mapping_rows)
    return {
        "slot_id": str(slot_id),
        "movement_pattern": slot_row["movement_pattern"],
        "primary_muscle": slot_row["primary_muscle"],
        "exercise_role": slot_row["exercise_role"],
        "engine_version": TONAL_MAPPING_ENGINE_VERSION,
        "candidates": ranked,
    }


def get_session_structure_with_candidates(program_slug, session_key):
    """Section 5's third admin endpoint contract: session + slots +
    candidate Tonal movements per slot, with mapping quality/rationale."""
    base = get_session_structure(program_slug, session_key)
    if base is None:
        return None
    with get_conn() as conn:
        for slot in base["session"]["slots"]:
            mapping_rows = repository.get_candidate_tonal_movements(
                slot["movement_pattern"], slot["primary_muscle"], slot["exercise_role"], conn=conn,
            )
            slot["candidates"] = rank_candidates(mapping_rows)
    base["engine_version"] = TONAL_MAPPING_ENGINE_VERSION
    return base


def get_active_program_context(user_id):
    """Read-only: the user's active enrollment plus its program
    structure. Returns None if no active enrollment exists - never
    fabricates a default program."""
    with get_conn() as conn:
        enrollment = repository.get_active_user_program(user_id, conn=conn)
        if enrollment is None:
            return None
        program = repository.get_program(program_id=enrollment.program_id, conn=conn)
    structure = get_program_structure(program.slug) if program else None
    return {
        "enrollment": {
            "id": enrollment.id, "user_id": enrollment.user_id, "program_id": enrollment.program_id,
            "started_on": enrollment.started_on.isoformat() if enrollment.started_on else None,
            "status": enrollment.status, "current_week": enrollment.current_week,
            "current_session_sequence": enrollment.current_session_sequence,
            "preferred_days_per_week": enrollment.preferred_days_per_week,
            "preferred_session_duration_min": enrollment.preferred_session_duration_min,
        },
        "program_structure": structure,
    }
