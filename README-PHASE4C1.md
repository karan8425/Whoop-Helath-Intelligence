# WHOOP Health Intelligence — Phase 4C.1 Freshness Guardrail

Why:
The automation can run successfully before WHOOP has produced a new recovery/sleep score.
Without a freshness check, an old Push recommendation could be mistaken for today's guidance.

Freshness rules use America/New_York local time:

- fresh:
  latest complete recovery+sleep physiology is yesterday or today.
  A new recommendation may be generated.

- pending_today:
  latest physiology is two calendar days behind.
  The job syncs/rebuilds but DOES NOT generate a new recommendation or AI brief.

- stale:
  latest physiology is three or more calendar days behind.
  No new recommendation is generated.

Recommended Render schedule during EDT:
0,20 9 * * *

This runs at approximately:
5:00 AM and 5:20 AM Eastern during daylight saving time.

The second run is a retry. If the first run already succeeded, the second run is safe:
upserts prevent duplication and the same day's intelligence is overwritten.

When Eastern Standard Time begins, adjust the UTC schedule to:
0,20 10 * * *

Later we can remove this manual DST adjustment with a timezone-aware scheduler.

Deployment:
1. Upload freshness.py (new)
2. Replace daily_job.py
3. Replace automation_status.py
4. Replace main.py
5. Commit: Add Phase 4C.1 freshness guardrail
6. Confirm /health = version 0.4.3
7. Trigger the Cron Job manually once
8. Inspect /freshness and /automation/latest-run
