# Historical replay temporal boundaries

Classification: **REPLAY-TEMPORAL-LEAKAGE-P0 — RELEASE BLOCKER**, corrected in Development.

## Cause and call graph

The historical clock previously reached B4 only as `now`. Its SQL applied a
lower lookback bound to WHOOP workouts without an upper bound; its Apple query
included the replay day's full daily aggregate. Consequently later workouts
could enable running, and end-of-day steps could eliminate a morning session.
The previous orchestration leakage test replaced both the activity loader and
builder, so it did not exercise either defective query.

```text
training_backtest.run (B3.1 and all B3.2 profiles)
  -> ReplayContext.morning(date, cutoff_hour)
     -> replay_day (READ ONLY transaction)
        -> B3 build_daily_workout_prescription(now=context.as_of)
           -> readiness / Tonal priority, history, dose, progression / goal / Hume
        -> B4 load_activity_context(as_of=context.as_of_utc)
           -> WHOOP workouts / recovery / profile: timestamp predicates
           -> Apple daily activity: activity_date < cutoff's New York date
           -> Tonal training-day classification: begin_time <= as_of
        -> build_activity_plan(as_of=context.as_of_utc, context=bounded inputs)
        -> actual_outcome (separate, intentionally after cutoff)
        -> comparison (never passed back to B3/B4)
```

## Contract and Apple limitation

`ReplayContext` rejects naive timestamps and a replay date inconsistent with its
local cutoff. `as_of_utc` is the canonical B4 boundary. `now` alone continues to
mean the existing live B4 path; replay must explicitly pass `as_of`.

Development schema inspection confirmed only `apple_health_daily_activity` and
`apple_health_body_samples` for these Apple inputs. Activity has one row per
`activity_date`, containing steps, energy, distance, raw aggregate JSON, and
`received_at`. `_upsert_activity` overwrites all those fields on conflict,
including `received_at`. There are no retained step samples or snapshot versions.
The body's sample table contains weight/body-fat measurements, not step samples.
The latest receipt timestamp cannot reconstruct an overwritten intraday total.

Replay therefore excludes the entire same-day activity aggregate, even if a
current retained row might have arrived before cutoff. Prior complete local days
remain available by their measurement date. Receipt dates are not used to erase
legitimate backfilled prior-day history; historical ingestion availability cannot
be reconstructed from this schema. This is an explicit event-date approximation
for completed days, not a claim of exact historical ingestion state.

In replay output `steps_so_far`, `steps_remaining`, and `percent_complete` are
null, with `current_day_steps=unavailable_intraday` in `replay_input_boundary`.
For planning only, the unchanged B4 algorithm starts with zero *known* intraday
steps and projects from prior-day baseline patterns. It does not claim zero
observed steps or prorate a final daily total. Targets, recovery modifiers,
modality thresholds, and calibration constants are unchanged. The planning gap
and sessions are estimates under that stated missing-input assumption.

WHOOP workout history requires start, completion (when retained), and latest
source revision (when retained) at or before cutoff. Resting HR requires exact
creation/source-revision boundaries. Singleton maximum HR is available only if
its retained `observed_at` is at or before cutoff; otherwise B4 can use bounded
workout HR or its existing talk-test fallback. No historical profile is invented.

## Full replay-input audit

| Input | Classification | Boundary / limitation |
|---|---|---|
| WHOOP recovery and sleep used by B3 | SAFE | `_latest_readiness`: `metric_date <= now.date()` plus exact `source_updated_at <= now`; source timestamp is the greatest contributing update. Rebuilt rows are not an immutable history; later revisions can make an old row unavailable. |
| WHOOP runs, aerobic/conditioning history, HR from workouts | FIXED | `load_activity_context`: exact start, end fallback, and source-revision fallback <= `as_of`. Lookbacks preserved. |
| WHOOP resting HR used by B4 | FIXED | Replaced date-only cutoff with exact creation and revision boundaries in replay. |
| WHOOP profile maximum HR | FIXED | Retained singleton observation must predate cutoff; unavailable historical versions are not reconstructed. |
| Tonal training-day classification in B4 | FIXED | Parent workout `begin_time <= as_of`; same-day activity rows are excluded in replay. |
| Tonal workouts/recent load/muscle readiness | SAFE | `muscle_readiness._load_rows`: exact begin-time upper/lower bounds, including latest-workout query. |
| Tonal comparable sessions and muscle-dose baselines | SAFE | `training_dose.load_session_history` and `_load_muscle_set_rows`: exact workout begin-time bounds. |
| Tonal sets/movement and progression history | SAFE | `movement_performance._load_recent_sets`: parent workout begin-time <= cutoff; sets have no individual event timestamp. This is workout-event resolution, not reconstruction of sets within an ongoing workout. |
| Tonal strength scores and training frequency | SAFE | `strength_analytics` bounds workout begin times and score `observed_at` exactly; injected clock drives all windows. |
| Apple same-day steps | UNAVAILABLE-INTRADAY | FIXED exclusion: daily aggregate is never an intraday snapshot. Output is unknown, not final-day steps. |
| Apple prior-day baseline steps | SAFE | Only completed local dates enter historical baselines, under the explicit event-date/backfill approximation above. |
| Apple active/resting energy and distance | NOT USED IN REPLAY recommendations | Read separately for actual outcomes; not fed into B3/B4 decisions. |
| Hume weight/body fat | SAFE | `body_composition_progress._load_daily_body_history` applies exact observed-time cutoff before daily averaging. |
| Fat mass / lean mass | SAFE | Derived from those bounded weight/body-fat observations. No independent unbounded loader. |
| Goals and timeline | SAFE at retained creation-time granularity | `get_active_goal(as_of)` filters exact `created_at` and phase date; `get_goal_contract` forwards cutoff. Prior in-place edits cannot be reconstructed; no immutable goal event log exists. No new goal snapshots or schema changes. |
| Prior simulated recommendations / rotation / coverage | SAFE | Runner passes only previous simulated days; `bounded_history` requires strictly earlier local dates within 30 days. Future/same-day entries are covered by existing B3.2 tests. |
| Stored Today plan cache history | NOT USED IN REPLAY | Runner always supplies a list (including empty on first day), preventing fallback to live cache history. |
| Raw WHOOP sleep/cycle totals and workout strain outcomes | NOT USED IN REPLAY recommendations directly | B3 uses bounded daily physiology; actual strain is loaded only for outcome comparison. |
| Final daily outcome aggregates | DATE-ONLY RISK if reused as inputs | Intentionally remain in `actual_outcome`/`comparison` only. They cannot drive recommendations. |
| Movement metadata, overrides, engine constants | NOT time-series observations | Current engine/metadata are used to interpret historical event rows. Historical edits to these records cannot be reconstructed. No claim of reproducing past source-code versions. |

No remaining unbounded future-workout query was identified in the B3/B4 input
path. The retained-data limitations above remain explicit; this change does not
create an immutable event store or claim exact historical ingestion availability.

## Actual-SQL validation

`test_replay_temporal_postgres.py` adds 17 PostgreSQL opt-in tests. They execute
the real loaders and builders. Synthetic CTEs shadow query sources, without
creating tables or inserting rows. Full-engine tests append post-cutoff source
copies using the actual Development schema and compare complete recommendation
and input objects, with nonempty B3 exercises to avoid vacuous success.

Coverage: future microsecond, same-day 18:00, tomorrow, cutoff equality,
unfinished workout, later score revision, same-day final steps, prior-day steps,
live steps, live profile behavior, later recovery/profile observations, sparse
activity, low recovery plus strength, equivalent timezone cutoffs, and two full
B3/B4 invariance scenarios including daily energy/step outcomes. Three additional
unit tests cover invalid/mismatched cutoffs and local/UTC date differences.

Opt in with `REPLAY_TEMPORAL_POSTGRES_TESTS=1` and `DATABASE_URL` loaded securely
from the existing Development Keychain entry. The suite enforces the Development
project identity and rejects Production. Connections set
`default_transaction_read_only=on`, verify `transaction_read_only`, and roll back.
Never put credentials into command arguments or tracked files.

### Probe evidence at June 10, 2026, 07:00 ET

Original commit `8001efd8e1e8ee82b30ad05cc7278ce60057da48` and fixed code were
executed against identical synthetic CTEs using actual Development PostgreSQL.

| Probe | Original | Fixed |
|---|---|---|
| Add two runs timestamped June 11, 11:00 UTC | Runs 0 -> 2; running eligibility false -> true | Runs stay 0; eligibility stays false; recommendation identical |
| Add June 10 final 15,000 steps | Visible steps 0 -> 15,000; sessions 1 -> 0 | Visible steps remain null; sessions stay 1; recommendation identical |

Both defects were also reproduced before editing at July 15, 07:00 ET.

## Ninety-day revalidation

Window: **2026-06-10 through 2026-09-07**, 07:00 America/New_York, 90 days.
Fresh real Development queries and deterministic engines were used for pre-fix
Candidate D, corrected Candidate D, and corrected baseline. No thresholds tuned.

| Metric | Pre-fix D | Corrected D | Corrected baseline |
|---|---:|---:|---:|
| Lower Body days | 23 | 23 | 47 |
| Full Body days | 19 | 19 | 9 |
| Longest Back gap | 20 | 20 | 60 |
| Longest Chest gap | 13 | 13 | 34 |
| Longest identical-template streak | 5 | 5 | 13 |
| Average sets, all days | 8.789 | 8.789 | 8.611 |
| RECOVERING muscle selections | 69 | 69 | 83 |
| FATIGUED/SUPPRESSED selections | 0 | 0 | 0 |
| Mean step target | 5,667.78 | 5,667.78 | 5667.78 |
| Running-eligible days | 0 | 0 | 0 |
| Activity sessions / days with sessions | 7 / 7 | 74 / 74 | 74 / 74 |
| Replay runtime | 110.86 s | 111.54 s | 111.21 s |

**All 90 B3 training output objects are identical before/after.** All step
targets and muscle selections are identical. Progression counts remain REDUCE
18, PROGRESS_REPS 97, PROGRESS_LOAD 33, REBUILD 87, HOLD 67. B3.2 conclusions hold:
no lower-body domination reappears, and Back/Chest gaps remain improved.
Candidate D still has 15 RECOVERING selections with viable safer alternatives,
unchanged from the accepted tradeoff; no recalibration was performed. The
baseline's lower raw alternative count is not comparable evidence of better
selection because its legacy movement-family defect suppresses alternatives.

B4 materially changes as intended: 69 days have changed session payloads; 67
additional days now receive a session. Modality counts change from EASY_WALK 3,
BRISK_WALK 2, ZONE2_WALK 2 to EASY_WALK 9, RECOVERY_WALK 4, BRISK_WALK 38,
ZONE2_WALK 23. No jogging appears. Synthetic actual-SQL cases exercise the
future-running defect because this real 90-day window has no running-eligible
days. Low recovery/strength, rest-day, high prior-day activity, and sparse-history
cases remain bounded by existing B4 policies.

Runtime increased **0.68 seconds / 0.62%** on this single paired 90-day run.
There is no material end-to-end slowdown evidence. Individual SQL timings were
not separately instrumented, so this is not a per-query performance guarantee.
No schema changes or indexes were needed.

## Regression and repository safety

**460 unique tests passed: 424 ordinary + 36 PostgreSQL opt-in.** The opt-in total
includes 8 body-progress, 6 weekly nullable-target, 5 weekly route, and 17 new
temporal tests. The two ordinary weekly observability tests also pass when that
module is rerun with PostgreSQL enabled; they are counted only once.

Modules run separately because legacy module-level stubs collide in combined
discovery. Freshness, OAuth/webhooks, Goal Progress/activation, Weekly
Intelligence, B1/B2/B3/B3.2/B4, replay and existing leakage tests all pass.
Four interactive Tonal-account scripts are not unit tests and were not invoked.

Detailed replay JSON/logs, schema metadata, same-cutoff probe results and local
reproduction scripts remain under gitignored `backtest-output/temporal-p0/`.
They contain no credentials and are not staged. No Production/main, iOS, schema,
Render configuration, live API contracts, or Nutrition code was changed.
No PR, promotion, or Production deployment belongs to this task.

REPLAY-TEMPORAL-LEAKAGE-P0 DEVELOPMENT PASS — RELEASE UNBLOCKED
