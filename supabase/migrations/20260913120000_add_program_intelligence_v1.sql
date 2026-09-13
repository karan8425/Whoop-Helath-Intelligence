-- Program Intelligence Layer V1 - normalized program/session/slot schema
-- plus a Tonal movement-mapping table and durable user-enrollment state.
--
-- Additive and isolated: no existing table is altered. Development only -
-- not applied to Production. The backend also applies this idempotently
-- via training_intelligence.programs.repository.ensure_tables() (CREATE
-- TABLE IF NOT EXISTS, matching todays_plan_store.py's/snapshot.py's own
-- established pattern); this file is the versioned record, matching
-- 20260907020000_add_goal_timeline_contract.sql's own stated convention.
--
-- Design note: slots represent training INTENT (movement pattern, primary
-- muscle, exercise role, set/rep/RIR/rest ranges) - never a specific
-- exercise name. program_movement_mappings is the only place an abstract
-- slot requirement is connected to a concrete, existing
-- public.tonal_movements row (reused by FK, never duplicated).
--
-- This schema is single-tenant-compatible with the rest of this codebase
-- today (tonal_movements/health_goal_profiles/etc. carry no user_id) but
-- user_training_programs/user_program_session_state carry an explicit
-- user_id column (default 'primary') so multi-user support is a data
-- migration, not a schema migration, later - no user-specific value is
-- hard-coded beyond this single placeholder identity.

BEGIN;

-- ============================================================
-- 1. training_programs
-- ============================================================

CREATE TABLE IF NOT EXISTS public.training_programs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    description TEXT NULL,

    -- Reuses training_intelligence.dose.goal_policy.GOAL_MODES verbatim -
    -- no new/competing goal vocabulary. "lean_bulk" IS the hypertrophy
    -- goal in that taxonomy (training_objective =
    -- "maximize_hypertrophy_stimulus") - there is no separate
    -- "hypertrophy_gain" value to avoid a conflicting alias.
    goal_mode TEXT NOT NULL CHECK (
        goal_mode IN ('lean_cut', 'lean_bulk', 'strength', 'maintenance', 'general_fitness', 'recovery')
    ),

    -- New vocabulary (training_intelligence/programs/taxonomy.py) - no
    -- existing TKI/B3 equivalent to reuse or conflict with.
    experience_level TEXT NOT NULL CHECK (
        experience_level IN ('beginner', 'intermediate', 'advanced')
    ),
    split_type TEXT NOT NULL CHECK (
        split_type IN ('upper_lower', 'full_body', 'push_pull_legs', 'body_part', 'hybrid')
    ),

    days_per_week INTEGER NOT NULL CHECK (days_per_week BETWEEN 1 AND 7),
    duration_weeks INTEGER NULL CHECK (duration_weeks IS NULL OR duration_weeks > 0),

    -- Runtime/user preference default, never a system-wide constant - see
    -- user_training_programs.preferred_session_duration_min, which is
    -- where an actual session duration is ever chosen.
    default_session_duration_min INTEGER NULL CHECK (
        default_session_duration_min IS NULL OR default_session_duration_min > 0
    ),

    source_type TEXT NOT NULL CHECK (
        source_type IN ('original', 'reference_derived', 'user_defined')
    ),
    source_name TEXT NULL,
    source_url TEXT NULL,
    reference_notes TEXT NULL,

    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'active', 'archived')),

    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_training_programs_goal_mode ON public.training_programs (goal_mode);
CREATE INDEX IF NOT EXISTS idx_training_programs_status ON public.training_programs (status);


-- ============================================================
-- 2. training_program_weeks (optional phased structure)
-- ============================================================

CREATE TABLE IF NOT EXISTS public.training_program_weeks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    program_id UUID NOT NULL REFERENCES public.training_programs (id) ON DELETE CASCADE,
    week_number INTEGER NOT NULL CHECK (week_number > 0),
    label TEXT NULL,
    phase_name TEXT NULL CHECK (
        phase_name IS NULL OR phase_name IN ('accumulation', 'intensification', 'deload', 'peak', 'maintenance')
    ),
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (program_id, week_number)
);

CREATE INDEX IF NOT EXISTS idx_training_program_weeks_program ON public.training_program_weeks (program_id);


-- ============================================================
-- 3. training_program_sessions
-- ============================================================

CREATE TABLE IF NOT EXISTS public.training_program_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    program_id UUID NOT NULL REFERENCES public.training_programs (id) ON DELETE CASCADE,
    program_week_id UUID NULL REFERENCES public.training_program_weeks (id) ON DELETE CASCADE,

    session_key TEXT NOT NULL,
    session_name TEXT NOT NULL,

    -- Reuses integrations.tonal.training_priority.SESSION_TEMPLATES'
    -- existing Title-Case family names ("Upper Push", "Lower Body",
    -- "Full Body", ...) verbatim wherever a session's structure actually
    -- corresponds to one - no parallel snake_case family vocabulary.
    session_family TEXT NOT NULL,

    sequence_index INTEGER NOT NULL CHECK (sequence_index >= 0),
    day_of_week INTEGER NULL CHECK (day_of_week IS NULL OR day_of_week BETWEEN 0 AND 6),
    expected_duration_min INTEGER NULL CHECK (expected_duration_min IS NULL OR expected_duration_min > 0),
    primary_focus TEXT NULL,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- session_key is stable/human-readable ("upper_a") and unique within
    -- a program regardless of week.
    UNIQUE (program_id, session_key)
);

CREATE INDEX IF NOT EXISTS idx_training_program_sessions_program ON public.training_program_sessions (program_id);
CREATE INDEX IF NOT EXISTS idx_training_program_sessions_week ON public.training_program_sessions (program_week_id);
CREATE INDEX IF NOT EXISTS idx_training_program_sessions_family ON public.training_program_sessions (session_family);

-- sequence_index must be unique within whatever scope actually orders
-- it (a week when phased, the program itself when not) - an expression
-- index rather than a plain table UNIQUE(...), since COALESCE over two
-- columns is not valid in a table-level UNIQUE constraint. Keeps weeks
-- optional: a non-phased program's sessions are ordered directly by
-- (program_id, sequence_index).
CREATE UNIQUE INDEX IF NOT EXISTS uq_training_program_sessions_sequence
    ON public.training_program_sessions (program_id, COALESCE(program_week_id, program_id), sequence_index);


-- ============================================================
-- 4. training_session_slots - THE most important table. A slot is
--    training INTENT, never a specific exercise.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.training_session_slots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES public.training_program_sessions (id) ON DELETE CASCADE,
    slot_index INTEGER NOT NULL CHECK (slot_index >= 0),

    -- training_intelligence/programs/taxonomy.py MOVEMENT_PATTERNS -
    -- reuses history.py's existing compound-pattern names
    -- (horizontal_press, vertical_press, horizontal_pull, vertical_pull,
    -- hinge, squat_lunge) verbatim, extended (not forked) with new
    -- isolation sub-patterns history.py's own taxonomy never
    -- distinguished (elbow_flexion, elbow_extension, lateral_raise,
    -- calf_raise, core_anti_extension, core_anti_rotation, core_flexion).
    movement_pattern TEXT NOT NULL,
    movement_subpattern TEXT NULL,

    -- training_intelligence.stimulus.taxonomy.CANONICAL_MUSCLES verbatim
    -- - the same 10-group vocabulary readiness/stimulus-ledger already use.
    primary_muscle TEXT NOT NULL,
    secondary_muscles JSONB NOT NULL DEFAULT '[]'::jsonb,

    exercise_role TEXT NOT NULL CHECK (
        exercise_role IN ('primary_compound', 'secondary_compound', 'isolation', 'unilateral', 'accessory', 'core', 'conditioning')
    ),
    required BOOLEAN NOT NULL DEFAULT TRUE,

    set_min INTEGER NOT NULL CHECK (set_min > 0),
    set_max INTEGER NOT NULL CHECK (set_max >= set_min),

    rep_min INTEGER NOT NULL CHECK (rep_min > 0),
    rep_max INTEGER NOT NULL CHECK (rep_max >= rep_min),

    rir_min NUMERIC NULL CHECK (rir_min IS NULL OR rir_min >= 0),
    rir_max NUMERIC NULL CHECK (rir_max IS NULL OR (rir_min IS NOT NULL AND rir_max >= rir_min)),

    rest_seconds_min INTEGER NULL CHECK (rest_seconds_min IS NULL OR rest_seconds_min >= 0),
    rest_seconds_max INTEGER NULL CHECK (
        rest_seconds_max IS NULL OR (rest_seconds_min IS NOT NULL AND rest_seconds_max >= rest_seconds_min)
    ),

    priority INTEGER NOT NULL DEFAULT 100,
    substitution_group TEXT NULL,

    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (session_id, slot_index)
);

CREATE INDEX IF NOT EXISTS idx_training_session_slots_session ON public.training_session_slots (session_id);
CREATE INDEX IF NOT EXISTS idx_training_session_slots_pattern ON public.training_session_slots (movement_pattern);
CREATE INDEX IF NOT EXISTS idx_training_session_slots_muscle ON public.training_session_slots (primary_muscle);


-- ============================================================
-- 5. program_movement_mappings - abstract requirement -> concrete Tonal
--    movement. Generic (not slot-bound): one mapping row can serve any
--    slot with matching (movement_pattern, primary_muscle, exercise_role)
--    - the scalable design, since a "chest primary_compound horizontal
--    press" requirement recurs across many programs/sessions/slots and
--    should not need re-mapping every time.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.program_movement_mappings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    tonal_movement_id UUID NOT NULL REFERENCES public.tonal_movements (movement_id),

    movement_pattern TEXT NOT NULL,
    primary_muscle TEXT NOT NULL,
    exercise_role TEXT NOT NULL CHECK (
        exercise_role IN ('primary_compound', 'secondary_compound', 'isolation', 'unilateral', 'accessory', 'core', 'conditioning')
    ),

    mapping_quality TEXT NOT NULL CHECK (mapping_quality IN ('DIRECT', 'CLOSE', 'FUNCTIONAL', 'UNSUITABLE')),
    confidence NUMERIC NOT NULL DEFAULT 0.5 CHECK (confidence >= 0 AND confidence <= 1),
    rationale_code TEXT NOT NULL,
    rationale_notes TEXT NULL,

    required_accessory TEXT NULL,
    contraindication_notes TEXT NULL,

    active BOOLEAN NOT NULL DEFAULT TRUE,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),

    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (tonal_movement_id, movement_pattern, primary_muscle, exercise_role)
);

CREATE INDEX IF NOT EXISTS idx_program_movement_mappings_pattern_muscle_role
    ON public.program_movement_mappings (movement_pattern, primary_muscle, exercise_role)
    WHERE active AND mapping_quality != 'UNSUITABLE';
CREATE INDEX IF NOT EXISTS idx_program_movement_mappings_movement ON public.program_movement_mappings (tonal_movement_id);


-- ============================================================
-- 6. user_training_programs (enrollment)
-- ============================================================

CREATE TABLE IF NOT EXISTS public.user_training_programs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- This schema is single-tenant today (no other table here carries a
    -- user_id either) - 'primary' is a placeholder identity, not a
    -- hard-coded person, kept so multi-user support later is a data
    -- migration, not a schema migration.
    user_id TEXT NOT NULL DEFAULT 'primary',

    program_id UUID NOT NULL REFERENCES public.training_programs (id),

    started_on DATE NOT NULL DEFAULT CURRENT_DATE,
    ended_on DATE NULL CHECK (ended_on IS NULL OR ended_on >= started_on),

    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'completed', 'cancelled')),

    current_week INTEGER NULL CHECK (current_week IS NULL OR current_week > 0),
    current_session_sequence INTEGER NULL CHECK (current_session_sequence IS NULL OR current_session_sequence >= 0),

    -- Explicit runtime/user preference - never a system default. No
    -- fixed session-duration value is written anywhere in this schema
    -- or in the seed data; this column is the only place one may ever
    -- be recorded, and only when a user actually sets it.
    preferred_days_per_week INTEGER NULL CHECK (preferred_days_per_week IS NULL OR preferred_days_per_week BETWEEN 1 AND 7),
    preferred_session_duration_min INTEGER NULL CHECK (
        preferred_session_duration_min IS NULL OR preferred_session_duration_min > 0
    ),

    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_training_programs_user ON public.user_training_programs (user_id);
CREATE INDEX IF NOT EXISTS idx_user_training_programs_program ON public.user_training_programs (program_id);

-- At most one ACTIVE enrollment per user, enforced in the database (a
-- user may have many historical paused/completed/cancelled enrollments).
CREATE UNIQUE INDEX IF NOT EXISTS uq_user_training_programs_one_active_per_user
    ON public.user_training_programs (user_id)
    WHERE status = 'active';


-- ============================================================
-- 7. user_program_session_state (durable progression)
-- ============================================================

CREATE TABLE IF NOT EXISTS public.user_program_session_state (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    user_training_program_id UUID NOT NULL REFERENCES public.user_training_programs (id) ON DELETE CASCADE,
    program_session_id UUID NOT NULL REFERENCES public.training_program_sessions (id),

    program_week_number INTEGER NULL CHECK (program_week_number IS NULL OR program_week_number > 0),
    scheduled_date DATE NULL,
    completed_at TIMESTAMPTZ NULL,

    status TEXT NOT NULL DEFAULT 'scheduled' CHECK (
        status IN ('scheduled', 'completed', 'skipped', 'shifted', 'substituted', 'cancelled')
    ),

    -- Points at an existing tonal_workouts row (TEXT to match that
    -- table's own activity_id typing) when a real Tonal session fulfilled
    -- this state row - never enforced by FK here since a workout may be
    -- synced well after the state row is created (or never, if skipped).
    source_workout_id TEXT NULL,
    adaptation_reason TEXT NULL,

    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_program_session_state_enrollment ON public.user_program_session_state (user_training_program_id);
CREATE INDEX IF NOT EXISTS idx_user_program_session_state_session ON public.user_program_session_state (program_session_id);
CREATE INDEX IF NOT EXISTS idx_user_program_session_state_scheduled_date ON public.user_program_session_state (scheduled_date);

COMMIT;
