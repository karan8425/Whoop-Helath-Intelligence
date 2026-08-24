# Phase 2 Supabase Upgrade

This upgrade:
- moves OAuth tokens from Render-local SQLite into Supabase Postgres
- protects health endpoints with an admin login
- creates Postgres tables for WHOOP cycles, recoveries, sleep, workouts, body measurements, profile and sync runs
- preserves raw WHOOP JSON inside each table
- follows WHOOP cursor pagination through all pages
- retries WHOOP rate-limit responses

## Required Render environment variables
Existing:
WHOOP_CLIENT_ID
WHOOP_CLIENT_SECRET
WHOOP_REDIRECT_URI
SESSION_SECRET
TOKEN_ENCRYPTION_KEY

New:
DATABASE_URL
ADMIN_PASSWORD

After deployment:
1. Open the app and sign in with ADMIN_PASSWORD.
2. Test Supabase database.
3. Reconnect WHOOP once so the OAuth token is persisted in Supabase.
4. Run the full historical sync.
5. Check database record counts.
