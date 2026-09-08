# Training recommendation replay

Training-B3.1 answers: **what would today's engine recommend using only data
available by a historical local-morning cutoff?** It does not reconstruct the
historical source-code version.

## As-of policy

`ReplayContext` owns the replay date, UTC cutoff, timezone, and mode. The default
cutoff is 07:00 America/New_York. Tonal sessions and sets are bounded by workout
time, WHOOP metrics by their source update time, Apple activity by local date,
body samples by observation time, strength scores by observation time, and
goals by the best creation timestamp retained in the current schema.

Recommendation inputs and actual outcomes are queried separately. Same-day
outcomes after the cutoff are allowed only in `actual_outcome` and `comparison`;
they are never supplied to B3/B4.

Historical goal edits cannot be reconstructed exactly because the goal table is
not an immutable event log. A row created after the cutoff is excluded, but an
in-place edit to an older row may be indistinguishable from its prior state.
Likewise, source update timestamps approximate historical availability when an
explicit event-receipt timestamp was not retained. These limitations are not
silently treated as exact evidence.

## Run

```bash
python -m training_backtest --date 2026-07-15
python -m training_backtest --start-date 2026-06-01 --end-date 2026-08-31 \
  --output backtest-output/replay.json --format json --include-details
python -m training_backtest --start-date 2026-06-01 --end-date 2026-08-31 \
  --output backtest-output/replay.csv --format csv
```

JSON contains structured per-day inputs, recommendations, actual outcomes,
comparisons, and an aggregate report. CSV is a compact day-level dataset.
`backtest-output/` is gitignored because artifacts can contain personal health
history. The runner performs reads only and never touches Today caches.

Versions recorded in every day: `engine_version`, `plan_version`, and
`backtest_version`.

## Leakage audit

| Source | Previous live query | Replay protection |
|---|---|---|
| WHOOP recovery/sleep | Latest daily metric | Local date and `source_updated_at <= as_of` |
| Tonal readiness | Already accepted `now` | Workout range ends at `as_of` |
| Tonal analytics/strength scores | Wall-clock rolling windows | Injected clock plus upper timestamp bound |
| Movement/progression history | Wall-clock 180-day lookup | Injected clock plus upper timestamp bound |
| Comparable dose history | Already accepted `now` | Existing lower/upper bounded queries preserved |
| Recommendation rotation | Previous cached plan dates | Dates strictly before replay day |
| Apple activity/B4 | Local-day bounded, but resting HR was latest | All activity bounded by replay day; resting HR also bounded |
| Hume/body composition | Entire sample history | `observed_at <= as_of` |
| Goals | Current active row | Creation/phase dates bounded; in-place-edit limitation disclosed |
| Engine configuration | Current source constants | Explicitly interpreted as today's engine on historical data |

No replay path calls cache save/invalidation functions or historical ingest.
