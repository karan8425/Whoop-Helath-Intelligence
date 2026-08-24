WHOOP Phase 2 Validation Patch

Replace db.py, sync.py, and main.py in GitHub.

Validation sequence after Render redeploys:
1. Sign in.
2. Click "Validate body measurement". Expected: status ok and one body record.
3. Click "Validate historical date ranges". Expected: oldest/newest dates for cycles, recoveries, sleeps, workouts.
4. Click "Run incremental sync" once and save the JSON.
5. Run it a second time immediately.
6. The second run should normally show new_rows = 0 for the historical tables unless WHOOP created a genuinely new record between runs.
7. Check database record counts. Body measurements should now equal 1.

This patch intentionally keeps incremental sync simple: it refreshes the latest WHOOP page for each dataset and upserts by WHOOP IDs, making repeat runs duplicate-safe.
