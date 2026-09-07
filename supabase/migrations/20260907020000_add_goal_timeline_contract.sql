-- Goal Setting V2 - timeline / trajectory contract.
--
-- Additive and backward compatible. Every column is nullable (or has a
-- default), so existing V1 goal rows continue to load and Goal Progress
-- V2.2 keeps working. The backend also applies these via
-- goals.init_goal_profiles() (ADD COLUMN IF NOT EXISTS) on boot; this file
-- is the versioned record. Development only - not applied to Production.

BEGIN;

ALTER TABLE public.health_goal_profiles
    ADD COLUMN IF NOT EXISTS goal_type TEXT,
    ADD COLUMN IF NOT EXISTS lean_mass_priority TEXT,
    ADD COLUMN IF NOT EXISTS phase_start_fat_mass_lb DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS phase_start_lean_mass_lb DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS target_fat_mass_lb DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS target_lean_mass_lb DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS target_date DATE,
    ADD COLUMN IF NOT EXISTS aspirational_target_date DATE,
    ADD COLUMN IF NOT EXISTS selected_pace TEXT,
    ADD COLUMN IF NOT EXISTS expected_weekly_weight_change_lb DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS timeline_status TEXT,
    ADD COLUMN IF NOT EXISTS goal_version INTEGER NOT NULL DEFAULT 1;

COMMIT;
