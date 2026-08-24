# WHOOP Health Intelligence — Phase 3C

Phase 3C adds an objective Trend & Deviation Engine.

It does NOT yet make training, nutrition, recovery, or medical recommendations.

For each configured metric it returns:

- current value
- 7-day, 30-day and 90-day baselines
- percentage deviation from baseline
- raw deviation label
- direction-aware signal
- recent trend (7-day baseline vs 30-day baseline)
- 7/14/30/90 data coverage
- confidence level

Direction-aware metrics:
- Higher is generally favorable: recovery, HRV, sleep duration, sleep performance, sleep consistency.
- Lower is generally favorable: resting heart rate.
- Context-only: cycle strain, workout count.

Domain summaries:
- recovery physiology
- sleep
- training context

Important:
- This engine describes statistical patterns relative to personal baselines.
- It does not establish causation.
- It does not diagnose illness.
- Training recommendations come later, after signal validation.

Deployment:
1. Upload `trends.py`.
2. Replace `main.py`.
3. Upload this README if desired.
4. Commit: `Add Phase 3C trend deviation engine`.
5. Confirm `/health` reports phase 3C and version 0.3.4.
6. Open `Validate signal engine`.
7. Send the JSON result for review.
