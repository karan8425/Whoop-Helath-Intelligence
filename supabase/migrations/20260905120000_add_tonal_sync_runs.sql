CREATE TABLE IF NOT EXISTS public.tonal_sync_runs (
    id BIGSERIAL PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    sync_mode TEXT NOT NULL,
    source_latest_workout_at TIMESTAMPTZ,
    stored_latest_workout_before TIMESTAMPTZ,
    stored_latest_workout_after TIMESTAMPTZ,
    workouts_received INTEGER,
    workouts_inserted_or_updated INTEGER,
    sets_received INTEGER,
    sets_inserted_or_updated INTEGER,
    movements_received INTEGER,
    strength_scores_received INTEGER,
    strength_scores_inserted_or_updated INTEGER,
    error_class TEXT,
    error_message_sanitized TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tonal_sync_runs_started_at
    ON public.tonal_sync_runs (started_at DESC);

CREATE INDEX IF NOT EXISTS idx_tonal_sync_runs_status
    ON public.tonal_sync_runs (status);
