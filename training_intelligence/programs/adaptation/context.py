"""Program Intelligence V2 Phase 1 - user program scheduling context.

Reuses training_intelligence.programs.repository (V1) UNCHANGED for all
reads - never duplicates its SQL. Never writes to user_training_
programs/user_program_session_state (no program-state mutation during
a shadow recommendation, per the milestone's explicit instruction).

Supports an OPTIONAL, NEVER-PERSISTED `enrollment_override` so a
shadow/demo run (Phase 17, and every deterministic test in Phase 15)
can simulate "if the user were enrolled starting at sequence N" without
ever inserting a real user_training_programs row - the durable,
DB-backed path and the non-persistent shadow path share every other
line of code.
"""
from __future__ import annotations

from datetime import datetime, timezone

from training_intelligence.programs import repository
from training_intelligence.programs.models import ProgramSession
from training_intelligence.programs.adaptation.models import ProgramScheduleContext

# How many upcoming/completed sessions to surface - bounded, not "every
# session ever" (matches Phase 3's "candidates should be bounded").
MAX_OUTSTANDING_SESSIONS = 6
MAX_RECENTLY_COMPLETED = 5


def _session_dict(session: ProgramSession) -> dict:
    from dataclasses import asdict
    return asdict(session)


def _rotation_order(sessions, start_index):
    n = len(sessions)
    if n == 0:
        return []
    return [sessions[(start_index + i) % n] for i in range(n)]


def build_schedule_context(user_id, as_of, *, enrollment_override=None) -> ProgramScheduleContext | None:
    """Returns None if there is no active enrollment and no override -
    never fabricates a program. `enrollment_override` (shadow-only):
    {"program_id": str, "sequence_position": int|None,
     "last_completed_at": datetime|None,
     "recently_completed_session_keys": [str, ...]} - a plain dict, not
    a DB row; nothing here is ever written back."""
    is_shadow = enrollment_override is not None

    if enrollment_override is not None:
        program_id = enrollment_override["program_id"]
        enrollment_id = None
        sequence_position = enrollment_override.get("sequence_position")
        last_completed_at = enrollment_override.get("last_completed_at")
        recently_completed_keys = enrollment_override.get("recently_completed_session_keys", [])
    else:
        enrollment = repository.get_active_user_program(user_id)
        if enrollment is None:
            return None
        program_id = enrollment.program_id
        enrollment_id = enrollment.id
        state_rows = repository.get_user_program_state(enrollment_id)
        completed = [r for r in state_rows if r["status"] == "completed" and r["completed_at"] is not None
                     and r["completed_at"] <= as_of]
        completed.sort(key=lambda r: r["completed_at"], reverse=True)
        if completed:
            last_completed_at = completed[0]["completed_at"]
            sequence_position = None  # resolved below via program_session_id -> sequence_index
        else:
            last_completed_at = None
            sequence_position = None
        recently_completed_keys = None  # resolved below from completed rows' program_session_id

    sessions = repository.get_program_sessions(program_id)
    if not sessions:
        return None
    by_id = {s.id: s for s in sessions}

    if enrollment_override is None:
        if completed:
            last_session = by_id.get(str(completed[0]["program_session_id"]))
            sequence_position = last_session.sequence_index if last_session else None
            recently_completed_keys = [
                by_id[str(r["program_session_id"])].session_key
                for r in completed[:MAX_RECENTLY_COMPLETED]
                if str(r["program_session_id"]) in by_id
            ]
        else:
            recently_completed_keys = []

    if sequence_position is None:
        next_index = 0
    else:
        next_index = (sequence_position + 1) % len(sessions)

    rotation = _rotation_order(sessions, next_index)
    nominal = rotation[0] if rotation else None
    outstanding = rotation[1:MAX_OUTSTANDING_SESSIONS + 1]

    by_key = {s.session_key: s for s in sessions}
    recently_completed = tuple(
        _session_dict(by_key[k]) for k in (recently_completed_keys or []) if k in by_key
    )

    days_since = None
    if last_completed_at is not None:
        days_since = round((as_of - last_completed_at).total_seconds() / 86400.0, 2)

    return ProgramScheduleContext(
        program_id=str(program_id),
        enrollment_id=str(enrollment_id) if enrollment_id else None,
        program_week=None,  # neither seed program uses phased weeks; see Known Limitations
        nominal_next_session=_session_dict(nominal) if nominal else None,
        outstanding_sessions=tuple(_session_dict(s) for s in outstanding),
        recently_completed_sessions=recently_completed,
        sequence_position=sequence_position,
        last_completed_at=last_completed_at,
        days_since_last_program_session=days_since,
        is_shadow_context=is_shadow,
    )
