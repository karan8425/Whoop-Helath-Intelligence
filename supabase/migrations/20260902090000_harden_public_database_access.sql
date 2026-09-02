-- The application accesses Supabase only through the Render backend's direct
-- PostgreSQL connection. Public Data API roles require no table access.

ALTER TABLE public.apple_health_body_samples ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.apple_health_daily_activity ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.daily_coaching_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.daily_health_intelligence ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.daily_intelligence_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.daily_workout_prescriptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.health_goal_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.oauth_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.todays_plan_cache ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tonal_movement_overrides ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tonal_movements ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tonal_sets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tonal_strength_scores ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tonal_workout_overrides ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tonal_workouts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.weekly_health_intelligence ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.whoop_body_measurements ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.whoop_cycles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.whoop_daily_automation_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.whoop_daily_baselines ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.whoop_daily_intelligence ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.whoop_daily_metrics ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.whoop_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.whoop_recoveries ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.whoop_sleeps ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.whoop_sync_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.whoop_sync_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.whoop_webhook_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.whoop_workouts ENABLE ROW LEVEL SECURITY;

REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public
FROM PUBLIC, anon, authenticated;

REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public
FROM PUBLIC, anon, authenticated;

REVOKE EXECUTE ON FUNCTION public.set_tonal_updated_at()
FROM PUBLIC, anon, authenticated;

-- Prevent application objects created by the backend's postgres owner from
-- regaining public Data API grants.
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
REVOKE ALL PRIVILEGES ON TABLES FROM PUBLIC, anon, authenticated;

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
REVOKE ALL PRIVILEGES ON SEQUENCES FROM PUBLIC, anon, authenticated;

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC, anon, authenticated;

-- Residual platform-managed boundary:
--
-- * supabase_admin is a Supabase-managed role.
-- * Hosted postgres is not authorized to modify supabase_admin's default
--   privileges.
-- * All current application objects are owned by postgres.
-- * Data API exposure of the public schema has already been disabled.
-- * This application creates application objects through postgres migrations.
-- * supabase_admin defaults must be monitored as a platform-managed residual
--   risk and must not be used to create application objects.
