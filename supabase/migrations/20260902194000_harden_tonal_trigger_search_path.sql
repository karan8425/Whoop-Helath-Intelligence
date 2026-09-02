BEGIN;

-- The trigger function only assigns NEW.updated_at using PostgreSQL built-ins.
-- Restrict name resolution to the system catalog without changing its body,
-- ownership, SECURITY INVOKER behavior, or existing privileges.
ALTER FUNCTION public.set_tonal_updated_at()
    SET search_path = pg_catalog;

COMMIT;
