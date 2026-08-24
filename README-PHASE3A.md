# Phase 3A Daily Metrics Layer

Replace `main.py` and add `analytics.py`.

This phase creates `whoop_daily_metrics`, one row per WHOOP physiological cycle/day. It combines:
- recovery score, RHR, HRV, SpO2, skin temperature
- cycle strain, kilojoules and calculated kcal
- main (non-nap) sleep duration, performance, consistency, efficiency, respiratory rate and sleep stages
- workout count, summed workout strain and workout duration

Deployment:
1. Upload main.py and analytics.py to GitHub.
2. Commit `Add Phase 3A daily metrics layer`.
3. Confirm Render `/health` returns version 0.3.0.
4. Sign in and click `Build / rebuild daily metrics`.
5. Then click `Validate daily metrics` and send the JSON result.

No 7/14/30/90-day baselines are calculated yet. That belongs to Phase 3B after this normalized daily layer is validated.
