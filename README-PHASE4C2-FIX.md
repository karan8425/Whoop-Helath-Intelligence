# Phase 4C.2 — WHOOP Coaching-Date Alignment

Diagnostic finding:
Today's 76% recovery was linked to:
- sleep start: 2026-08-24 22:50 Eastern
- sleep end:   2026-08-25 06:32 Eastern
- cycle start: 2026-08-24 22:50 Eastern

The previous analytics rule used cycle start, which shifted today's physiology
to yesterday.

Production rule:
Recovery + HRV + RHR + main sleep + linked cycle are assigned to the LOCAL
DATE OF MAIN NON-NAP SLEEP END (wake date).

Freshness rule:
Only `latest_physiology_date == local_today` is fresh.
Yesterday = pending_today.
Older = stale.

Deploy:
1. Replace `analytics.py`
2. Replace `freshness.py`
3. Replace `main.py`
4. Keep `debug_whoop_dates.py` from the diagnostic patch
5. Commit: `Fix WHOOP coaching date alignment`
6. Confirm `/health` = phase 4C.2, version 0.4.5

Then, in this exact order:
1. Rebuild corrected daily metrics
2. View latest 7 corrected days
3. Check corrected WHOOP freshness
4. Rebuild personal baselines
5. Validate personal baselines

Expected today:
- latest physiology date: 2026-08-25
- recovery: 76
- HRV: ~66.31
- RHR: 57
- sleep: ~7.53 hours
- freshness age_days: 0
- freshness status: fresh

Do not resume Apple Health/Hume integration until this passes.
