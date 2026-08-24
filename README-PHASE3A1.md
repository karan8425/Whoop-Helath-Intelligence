# Phase 3A.1 Data Integrity Patch

This patch replaces `analytics.py` and `main.py`.

What changes:
- Builds a continuous calendar-day spine instead of only dates that have a selected WHOOP cycle.
- Keeps exactly one selected cycle per local date where WHOOP supplies multiple cycles.
- Maps every workout independently to its own local calendar date.
- Adds explicit `has_cycle`, `has_recovery`, `has_sleep`, and `has_workout` flags.
- Adds a data-integrity endpoint showing missing-cycle days and the workout mapping difference.

Deployment:
1. Upload `analytics.py` and `main.py` to GitHub.
2. Commit `Add Phase 3A.1 data integrity layer`.
3. Confirm Render `/health` returns version 0.3.1.
4. Sign in and run `Rebuild calendar-based daily metrics`.
5. Then run `Validate data integrity`.
6. Send both JSON responses before Phase 3B.
