# Training recommendation replay

Training-B3.1 answers: **what would today's engine recommend using only data
available by a historical local-morning cutoff?** It does not reconstruct the
historical source-code version.

## As-of policy

`ReplayContext` owns the replay date, UTC cutoff, timezone, and mode. The default
cutoff is 07:00 America/New_York. Tonal sessions and sets are bounded by workout
time, WHOOP metrics by their source update time, Apple activity by completed prior local dates,
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
| Apple activity/B4 | Same-day final totals and unbounded cardio history | Explicit replay as-of: prior complete days only; exact workout/recovery/profile cutoffs |
| Hume/body composition | Entire sample history | `observed_at <= as_of` |
| Goals | Current active row | Creation/phase dates bounded; in-place-edit limitation disclosed |
| Engine configuration | Current source constants | Explicitly interpreted as today's engine on historical data |

No replay path calls cache save/invalidation functions or historical ingest.


## Temporal leakage correction

Historical B4 now uses an explicit `as_of`, distinct from its unchanged live
`now` path. Same-day steps are unavailable because the daily table overwrites
one aggregate per date without retaining intraday snapshots. Replay reports
unknown steps and plans from prior-day baseline patterns; it never prorates a
final daily total. WHOOP workout, recovery, and profile observations use exact
cutoffs. `BACKTEST_VERSION` is `b3.1-v2-temporal`.

See [REPLAY_TEMPORAL_P0.md](REPLAY_TEMPORAL_P0.md) for the complete input audit,
actual-SQL test instructions, schema limitations, probes and corrected 90-day
Candidate D/baseline results. The previous claim of Apple intraday availability
was invalid; the corrected replay changes B4 sessions but preserves B3.2 choices.
