# Phase 3B.1 Baseline Hardening

Replace `baselines.py` and `main.py`.

Changes:
- Baselines are explicitly defined by preceding calendar days rather than physical row position.
- Current day remains excluded.
- Missing physiological observations remain excluded from averages and observation counts.
- Workout count remains zero on valid no-workout calendar days.
- Every 7/14/30/90 baseline now exposes coverage percentage.

Deploy:
1. Replace baselines.py and main.py in GitHub.
2. Commit `Harden Phase 3B calendar baselines`.
3. Confirm Render /health = phase 3B.1, version 0.3.3.
4. Run Rebuild hardened baselines.
5. Run Validate hardened baselines.
6. Send both JSON outputs before Phase 3C.
