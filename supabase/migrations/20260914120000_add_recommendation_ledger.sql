-- Intelligence Architecture M1.0 - Decision Ledger foundation.
--
-- A durable, append-mostly record of every recommendation the training
-- intelligence stack produces, independent of which engine produced it
-- (V2.1 today, future multi-candidate V3 later). Additive and isolated:
-- no existing table is altered. Development only - not applied to
-- Production. The backend also applies this idempotently via
-- training_intelligence.ledger.schema.ensure_tables() (CREATE TABLE IF
-- NOT EXISTS, matching program_intelligence_v1's/snapshot.py's own
-- established convention - see 20260913120000_add_program_intelligence
-- _v1.sql's own header comment); this file is the versioned record.
--
-- Three tables:
--   recommendation_events     - one immutable row per decision (engine
--                                identity, state snapshot, input hash,
--                                the selected recommendation).
--   recommendation_candidates - one row per candidate the engine
--                                considered. V2.1 (a single-recommendation
--                                engine) always writes exactly one row,
--                                selected=true - never a fabricated
--                                alternative. A future multi-candidate
--                                engine writes its full candidate set
--                                (including losers) into this SAME table
--                                without a schema change.
--   recommendation_outcomes   - what actually happened, structurally
--                                separated from the decision itself so
--                                the decision record never needs to be
--                                mutated to represent a later outcome.
--
-- No PII beyond the existing opaque user_id already used throughout this
-- schema (user_training_programs.user_id, etc.) - no name/email/DOB, no
-- secrets/tokens. state_snapshot/candidate_payload hold derived training-
-- intelligence state (readiness bands, program context, prescriptions),
-- the same shape/sensitivity as this user's own data already returned by
-- the admin diagnostic endpoints - never a credential.

BEGIN;

-- ============================================================
-- 1. recommendation_events
-- ============================================================

CREATE TABLE IF NOT EXISTS public.recommendation_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Deterministic natural key (uuid5 of engine+user+as_of+recommendation
    -- type - see ledger.hashing.compute_decision_id) - the same
    -- decision recomputed from the same inputs collides here rather than
    -- duplicating a row, mirroring training_decision_snapshots'
    -- established insert-only idempotency convention.
    decision_id TEXT NOT NULL UNIQUE,

    user_id TEXT NOT NULL DEFAULT 'primary',
    recommendation_type TEXT NOT NULL,

    as_of TIMESTAMPTZ NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    engine_name TEXT NOT NULL,
    engine_version TEXT NOT NULL,
    feature_schema_version INTEGER NOT NULL,
    -- Nullable: this decision's own predecessor decision_id, for a
    -- future superseding-decision chain. Never populated by V2.1's
    -- shadow-only, one-shot integration this milestone.
    prior_version TEXT NULL,

    -- Curated, replay-sufficient state (see ledger.v2_1_integration for
    -- exactly what V2.1 puts here) - never a raw dump of unrelated
    -- personal data.
    state_snapshot JSONB NOT NULL,

    -- FK added below (after recommendation_candidates exists) as a
    -- DEFERRABLE INITIALLY DEFERRED constraint, so one transaction can
    -- insert the event and its candidate(s) in either order and have
    -- the reference checked once, at COMMIT.
    selected_candidate_id UUID NULL,
    selected_recommendation JSONB NULL,

    reason_codes JSONB NULL,
    validator_result JSONB NULL,

    source_data_cutoff TIMESTAMPTZ NOT NULL,
    input_hash TEXT NOT NULL,

    latency_ms INTEGER NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_recommendation_events_user_as_of
    ON public.recommendation_events (user_id, as_of DESC);

CREATE INDEX IF NOT EXISTS idx_recommendation_events_type_as_of
    ON public.recommendation_events (recommendation_type, as_of DESC);

CREATE INDEX IF NOT EXISTS idx_recommendation_events_engine_version
    ON public.recommendation_events (engine_name, engine_version);

-- ============================================================
-- 2. recommendation_candidates
-- ============================================================

CREATE TABLE IF NOT EXISTS public.recommendation_candidates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    decision_id TEXT NOT NULL REFERENCES public.recommendation_events(decision_id) ON DELETE CASCADE,

    candidate_key TEXT NOT NULL,
    rank INTEGER NULL,
    selected BOOLEAN NOT NULL DEFAULT FALSE,
    eligible BOOLEAN NOT NULL DEFAULT TRUE,

    candidate_payload JSONB NOT NULL,
    feature_snapshot JSONB NULL,
    score_components JSONB NULL,
    total_score DOUBLE PRECISION NULL,
    rejection_reasons JSONB NULL,

    engine_version TEXT NOT NULL,
    feature_schema_version INTEGER NOT NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (decision_id, candidate_key)
);

-- At most one selected candidate per decision - DB-enforced, not just
-- application-checked.
CREATE UNIQUE INDEX IF NOT EXISTS uq_recommendation_candidates_selected
    ON public.recommendation_candidates (decision_id)
    WHERE selected = TRUE;

CREATE INDEX IF NOT EXISTS idx_recommendation_candidates_decision
    ON public.recommendation_candidates (decision_id);

-- Deferred FK from events.selected_candidate_id, added now that the
-- candidates table exists. Idempotent (guarded) since ADD CONSTRAINT has
-- no native IF NOT EXISTS in PostgreSQL.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_recommendation_events_selected_candidate'
    ) THEN
        ALTER TABLE public.recommendation_events
            ADD CONSTRAINT fk_recommendation_events_selected_candidate
            FOREIGN KEY (selected_candidate_id)
            REFERENCES public.recommendation_candidates(id)
            DEFERRABLE INITIALLY DEFERRED;
    END IF;
END $$;

-- ============================================================
-- 3. recommendation_outcomes
-- ============================================================

CREATE TABLE IF NOT EXISTS public.recommendation_outcomes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    decision_id TEXT NOT NULL REFERENCES public.recommendation_events(decision_id) ON DELETE CASCADE,
    user_id TEXT NOT NULL DEFAULT 'primary',

    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    user_action TEXT NULL CHECK (
        user_action IS NULL OR user_action IN ('ACCEPTED', 'MODIFIED', 'REJECTED', 'SKIPPED', 'UNKNOWN')
    ),
    chosen_candidate_id UUID NULL REFERENCES public.recommendation_candidates(id),

    actual_session JSONB NULL,
    completion_fraction DOUBLE PRECISION NULL CHECK (
        completion_fraction IS NULL OR (completion_fraction >= 0 AND completion_fraction <= 1)
    ),
    session_rating INTEGER NULL,
    session_rpe DOUBLE PRECISION NULL,
    user_feedback JSONB NULL,

    downstream_state JSONB NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- One outcome record per decision - appended once, updated in place as
-- more is learned (e.g. accepted now, completion_fraction filled in
-- later), never duplicated. The decision row itself is never touched.
CREATE UNIQUE INDEX IF NOT EXISTS uq_recommendation_outcomes_decision
    ON public.recommendation_outcomes (decision_id);

CREATE INDEX IF NOT EXISTS idx_recommendation_outcomes_user_observed
    ON public.recommendation_outcomes (user_id, observed_at DESC);

COMMIT;
