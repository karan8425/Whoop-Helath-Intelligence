# WHOOP Health Intelligence — Phase 3B

Phase 3B adds personal rolling baselines for:

- Recovery score
- HRV RMSSD
- Resting heart rate
- Sleep duration
- Sleep performance
- Sleep consistency
- Cycle strain
- Workout count

Each daily value is compared against the preceding:

- 7 calendar days
- 14 calendar days
- 30 calendar days
- 90 calendar days

The current day is excluded from its own baseline.

Missing values are ignored by PostgreSQL AVG/COUNT window functions and are never converted to zero.

Each window stores:
- baseline average
- number of valid observations (`n`)
- percentage difference between current value and baseline

Deployment:
1. Upload `baselines.py` and replace `main.py`.
2. Upload this README if desired.
3. Commit `Add Phase 3B personal baseline engine`.
4. Confirm `/health` returns phase `3B`, version `0.3.2`.
5. Sign in.
6. Click `Build / rebuild personal baselines`.
7. Click `Validate baseline engine`.
8. Send both JSON responses before proceeding to trend classification.
