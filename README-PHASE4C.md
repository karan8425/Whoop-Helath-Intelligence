# WHOOP Health Intelligence — Phase 4C

Phase 4C automates the daily intelligence pipeline.

Daily job sequence:

1. Incremental WHOOP sync
2. Rebuild normalized calendar-day metrics
3. Rebuild 7/14/30/90-day personal baselines
4. Recalculate deterministic recommendation
5. Generate OpenAI daily briefing
6. Persist daily recommendation + AI briefing in Supabase
7. Persist a run log for validation/debugging

## Files

Upload:
- `daily_job.py` (new)
- `automation_status.py` (new)
- `main.py` (replace)
- `render.yaml` (replace if desired)
- `README-PHASE4C.md` (optional)

Commit:
`Add Phase 4C daily automation`

## Web-service verification

After Render redeploys, `/health` must return:

phase: 4C
version: 0.4.2

## Render Cron Job

Create a NEW Render service:
New + -> Cron Job

Repository:
same GitHub WHOOP repository

Branch:
main

Build command:
pip install -r requirements.txt

Command:
python daily_job.py

Recommended initial schedule:
30 11 * * *

Render cron schedules use UTC.
In US Eastern time this is approximately:
- 7:30 AM during EDT
- 6:30 AM during EST

This timing gives WHOOP time to score overnight sleep/recovery while still
providing a morning briefing.

## Cron environment variables

The cron job needs these secret variables:

DATABASE_URL
TOKEN_ENCRYPTION_KEY
WHOOP_CLIENT_ID
WHOOP_CLIENT_SECRET
OPENAI_API_KEY

Optional:
OPENAI_MODEL=gpt-5.6-luna

Do not put these values in GitHub.

It does not need ADMIN_PASSWORD or SESSION_SECRET because the cron job does not
serve browser traffic.

## First validation

After creating the cron:
1. Click `Trigger Run` in Render.
2. Open the cron job logs.
3. Successful output ends with JSON containing:
   status=completed
   metric_date=...
   training_recommendation=...
   ai_headline=...
4. Return to the web app.
5. Open `View automation status`.
6. Open `View latest stored daily intelligence`.

Do not rely on the scheduled run until one manual Trigger Run succeeds.
