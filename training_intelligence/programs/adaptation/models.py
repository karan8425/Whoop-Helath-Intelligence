"""Program Intelligence V2 - typed context objects. Matches the
established convention (frozen dataclasses for the few, clearly-named
top-level objects the spec calls for; plain dicts for the rest, same
as the rest of training_intelligence/calibration)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class ProgramScheduleContext:
    """Section: Phase 1. Durable scheduling state derived from Program
    Intelligence V1's enrollment/session-state tables (or a supplied,
    NEVER-persisted `enrollment_override` for shadow/demo use - see
    context.py). Never mutated during a shadow recommendation."""
    program_id: str
    enrollment_id: str | None
    program_week: int | None
    nominal_next_session: dict | None
    outstanding_sessions: tuple
    recently_completed_sessions: tuple
    sequence_position: int | None
    last_completed_at: datetime | None
    days_since_last_program_session: float | None
    is_shadow_context: bool = False


@dataclass(frozen=True)
class TimeContext:
    """Phase 11. `available_duration_min` is None (UNKNOWN) unless
    explicitly supplied by the request, the user's own enrollment
    preference, or the program's own default - NEVER a global 45."""
    available_duration_min: int | None
    source: str  # "request_override" | "user_preference" | "program_default" | "unknown"
