"""M1.0: idempotent DDL for the Decision Ledger tables, mirroring the
convention `training_intelligence.calibration.snapshot.ensure_table()`
and `training_intelligence.programs.repository` already established -
CREATE TABLE/INDEX IF NOT EXISTS, kept in sync with the versioned
migration (supabase/migrations/20260914120000_add_recommendation_
ledger.sql). Calling `ensure_tables()` when the migration has already
applied is a safe no-op.
"""
from __future__ import annotations

from db import get_conn

EVENTS_TABLE = "recommendation_events"
CANDIDATES_TABLE = "recommendation_candidates"
OUTCOMES_TABLE = "recommendation_outcomes"

# Versions the state_snapshot/candidate_payload SHAPE this ledger writes
# - independent of any engine's own internal version numbers (which are
# preserved verbatim inside engine_version/state_snapshot instead).
LEDGER_SCHEMA_VERSION = 1


def ensure_tables():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS public.{EVENTS_TABLE} (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    decision_id TEXT NOT NULL UNIQUE,
                    user_id TEXT NOT NULL DEFAULT 'primary',
                    recommendation_type TEXT NOT NULL,
                    as_of TIMESTAMPTZ NOT NULL,
                    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    engine_name TEXT NOT NULL,
                    engine_version TEXT NOT NULL,
                    feature_schema_version INTEGER NOT NULL,
                    prior_version TEXT NULL,
                    state_snapshot JSONB NOT NULL,
                    selected_candidate_id UUID NULL,
                    selected_recommendation JSONB NULL,
                    reason_codes JSONB NULL,
                    validator_result JSONB NULL,
                    source_data_cutoff TIMESTAMPTZ NOT NULL,
                    input_hash TEXT NOT NULL,
                    latency_ms INTEGER NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{EVENTS_TABLE}_user_as_of "
                f"ON public.{EVENTS_TABLE} (user_id, as_of DESC)"
            )
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{EVENTS_TABLE}_type_as_of "
                f"ON public.{EVENTS_TABLE} (recommendation_type, as_of DESC)"
            )
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{EVENTS_TABLE}_engine_version "
                f"ON public.{EVENTS_TABLE} (engine_name, engine_version)"
            )

            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS public.{CANDIDATES_TABLE} (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    decision_id TEXT NOT NULL REFERENCES public.{EVENTS_TABLE}(decision_id) ON DELETE CASCADE,
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
                )
                """
            )
            cur.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS uq_{CANDIDATES_TABLE}_selected "
                f"ON public.{CANDIDATES_TABLE} (decision_id) WHERE selected = TRUE"
            )
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{CANDIDATES_TABLE}_decision "
                f"ON public.{CANDIDATES_TABLE} (decision_id)"
            )

            cur.execute(
                """
                SELECT 1 FROM pg_constraint
                WHERE conname = 'fk_recommendation_events_selected_candidate'
                """
            )
            if cur.fetchone() is None:
                cur.execute(
                    f"""
                    ALTER TABLE public.{EVENTS_TABLE}
                        ADD CONSTRAINT fk_recommendation_events_selected_candidate
                        FOREIGN KEY (selected_candidate_id)
                        REFERENCES public.{CANDIDATES_TABLE}(id)
                        DEFERRABLE INITIALLY DEFERRED
                    """
                )

            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS public.{OUTCOMES_TABLE} (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    decision_id TEXT NOT NULL REFERENCES public.{EVENTS_TABLE}(decision_id) ON DELETE CASCADE,
                    user_id TEXT NOT NULL DEFAULT 'primary',
                    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    user_action TEXT NULL CHECK (
                        user_action IS NULL OR user_action IN
                        ('ACCEPTED', 'MODIFIED', 'REJECTED', 'SKIPPED', 'UNKNOWN')
                    ),
                    chosen_candidate_id UUID NULL REFERENCES public.{CANDIDATES_TABLE}(id),
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
                )
                """
            )
            cur.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS uq_{OUTCOMES_TABLE}_decision "
                f"ON public.{OUTCOMES_TABLE} (decision_id)"
            )
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{OUTCOMES_TABLE}_user_observed "
                f"ON public.{OUTCOMES_TABLE} (user_id, observed_at DESC)"
            )
