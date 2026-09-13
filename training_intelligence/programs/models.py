"""Typed internal representations for Program Intelligence V1.

Plain frozen dataclasses, matching the one established convention in
this codebase (training_intelligence.stimulus.mapping.MuscleMapping) -
no new framework (Pydantic exists in this environment for FastAPI
request/response models elsewhere, but is not used here; these are
internal domain objects, not wire schemas).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True)
class TrainingProgram:
    id: str
    slug: str
    name: str
    description: str | None
    goal_mode: str
    experience_level: str
    split_type: str
    days_per_week: int
    duration_weeks: int | None
    default_session_duration_min: int | None
    source_type: str
    source_name: str | None
    source_url: str | None
    reference_notes: str | None
    version: int
    status: str
    metadata: dict
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ProgramWeek:
    id: str
    program_id: str
    week_number: int
    label: str | None
    phase_name: str | None
    metadata: dict


@dataclass(frozen=True)
class ProgramSession:
    id: str
    program_id: str
    program_week_id: str | None
    session_key: str
    session_name: str
    session_family: str
    sequence_index: int
    day_of_week: int | None
    expected_duration_min: int | None
    primary_focus: str | None
    metadata: dict


@dataclass(frozen=True)
class SessionSlot:
    id: str
    session_id: str
    slot_index: int
    movement_pattern: str
    movement_subpattern: str | None
    primary_muscle: str
    secondary_muscles: tuple
    exercise_role: str
    required: bool
    set_min: int
    set_max: int
    rep_min: int
    rep_max: int
    rir_min: float | None
    rir_max: float | None
    rest_seconds_min: int | None
    rest_seconds_max: int | None
    priority: int
    substitution_group: str | None
    metadata: dict


@dataclass(frozen=True)
class MovementMapping:
    id: str
    tonal_movement_id: str
    movement_name: str | None
    movement_pattern: str
    primary_muscle: str
    exercise_role: str
    mapping_quality: str
    confidence: float
    rationale_code: str
    rationale_notes: str | None
    required_accessory: str | None
    contraindication_notes: str | None
    active: bool
    version: int


@dataclass(frozen=True)
class UserProgramEnrollment:
    id: str
    user_id: str
    program_id: str
    started_on: date
    ended_on: date | None
    status: str
    current_week: int | None
    current_session_sequence: int | None
    preferred_days_per_week: int | None
    preferred_session_duration_min: int | None
    metadata: dict


@dataclass(frozen=True)
class ProgramStructure:
    """A full, hierarchically-loaded program: program -> weeks (if any)
    -> sessions -> slots. What service.get_program_structure() returns -
    the read-only "program knowledge" contract Daily Intelligence (a
    future milestone) will consume."""
    program: TrainingProgram
    weeks: tuple
    sessions: tuple
    slots_by_session_id: dict = field(default_factory=dict)
