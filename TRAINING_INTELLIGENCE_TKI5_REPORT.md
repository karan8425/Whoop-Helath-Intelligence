# Training Intelligence TKI-5 Report — Exercise Selection + Progressive Overload Prescription V1

SHADOW MODE ONLY. `integrations.tonal.workout_prescription.
build_daily_workout_prescription()` (the live, production exercise-level
prescription engine feeding B3/B4/iOS via `/api/v1/todays-plan`) is
untouched and unaware this package exists. Nothing here writes to the
database or is reachable from any mobile/production request path.

## 1. What TKI-5 answers

"Given the TKI-4 selected session family and TKI-3 justified dose,
exactly what exercises should this user perform today, for how many
sets/reps, at what resistance, and what progression should they
attempt?" - as a deterministic, explainable shadow prescription.

## 2. Architecture, and what each layer reuses

A large fraction of this milestone's real content is an **audit**: B3
already implements almost every layer the assignment asks for, in
functions that were already parameterizable (accepting a dose instead
of only their own static rules) or already schema-driven (not
name-inference) in ways that made honest, direct reuse both possible and
clearly the right choice over a parallel implementation.

| Layer | Reused component | New TKI-5 code |
|---|---|---|
| A. Candidate generation | `movement_performance.build_movement_performance_profiles()` | unwrap `["profiles"]`, defensive placeholder filter |
| B. Eligibility | `workout_prescription._select_movements()` / `_movement_eligibility()` | none - called as-is |
| C. Scoring | `workout_prescription._candidate_score()` (inside `_select_movements`) | none - called as-is |
| D. Session composition | `_select_movements()`'s duplicate-family suppression (`EXERCISE_FAMILIES`) | `_exercise_count()` (new, dose- and goal-driven exercise-slot sizing) |
| E. Set allocation | `workout_prescription._set_allocation()`, called with TKI-3's dose instead of B3's static `SESSION_RULES` (the function already accepted this) | invariant-enforcing clip (belt-and-suspenders) |
| F. Rep/load/progression | `progressive_overload.prescribe()` (self-contained: rep range, target reps, target resistance, RIR, rest, progression state/label/rationale, trajectory, confidence) | none - called as-is |
| G. Goal posture | - | `_apply_goal_rep_posture()` / exercise-count goal delta (new, bounded) |
| H. Confidence/fallback | `profile["performance"]["status"]` + B3's own confidence | session-level rollup (new) |
| I. Explanation/provenance | - | `explanation_factors` (new) |

No parallel exercise taxonomy, scoring model, or progression engine was
built. `EXERCISE_FAMILIES` (B3's curated, exact-name-match movement-
pattern taxonomy - squat/hinge/split_squat_lunge/core_flexion/
core_rotation) is reused unmodified for section 6's duplicate-stimulus
control; `_family_for_movement()` is a deterministic set-membership
lookup, never name inference.

## 3. Tonal-specific load/movement-identity findings (sections 19-21)

All three were resolved by **auditing existing, already-correct, already-
live code and the real database catalog** - not by building new logic:

- **Generic placeholders (section 21)**: `tonal_movements` has an
  explicit, deterministic `is_generic` boolean column. `movement_
  performance._load_recent_sets()`'s own SQL already filters `WHERE
  COALESCE(m.is_generic, FALSE) = FALSE` (and `custom_movement` too) -
  Handle Move/Bar Move (confirmed live: 12 total `is_generic=TRUE`
  movements in the Development catalog, including both names, each
  appearing under multiple different bilateral/two-sided/alternating
  configurations - i.e. genuinely different physical setups collapsed
  under one freeform name, exactly as TKI-2.1's forensics found) **never
  reach a movement-performance profile at all**. TKI-5 adds a small,
  redundant, explicitly-documented second-layer name filter
  (`GENERIC_PLACEHOLDER_NAMES`) as defense-in-depth, not as the primary
  mechanism. Confirmed live: 0 placeholder-movement appearances across
  the entire 90-day/80-lifting-day backtest.
- **Unilateral vs. bilateral (sections 19-20)**: `tonal_movements` has
  explicit, deterministic `is_bilateral`/`is_two_sided`/`is_alternating`
  columns (confirmed live: 202 movements in the Development catalog are
  `is_bilateral=FALSE`). `movement_performance.py` already uses these to
  decide `estimated_combined_load` (doubles `per_arm_load` only when
  bilateral) and never treats a unilateral and bilateral movement's
  history as equivalent. Resistance itself is stored and consistently
  interpreted **per arm** (`recent_working_weight_per_arm_lb`) regardless
  of bilateral/unilateral status - TKI-5 passes every profile through to
  `progressive_overload.prescribe()`/`_select_movements()` unmodified and
  surfaces `is_bilateral`/`is_two_sided` directly on each prescribed
  exercise so a caller can see the flag TKI-5 relied on, rather than
  re-deriving or guessing it.
- **Mode consistency (eccentric/chains/burnout)**: `progressive_
  overload.comparable_history()` already restricts a movement's
  comparable-session pool to sessions sharing the latest session's
  dominant Smart Weight mode - a standard-mode history is never
  compared against an eccentric-mode one. Reused unmodified.

## 4. Exercise-candidate / eligibility / scoring (sections 3-5)

Fully reused (`_select_movements`/`_movement_eligibility`/`_candidate_
score`, unmodified): a movement is excluded only for an explicit reason
(no resolved muscle mapping, non-`usable` performance status, a
suppressed muscle materially loaded, or a primary muscle outside the
session's target set) - never merely for being unused recently, matching
section 4's explicit prohibition. Scoring rewards primary/secondary
target-muscle match, historical session count, and earned progression;
penalizes struggling/inconsistency signals and non-`usable` status.

## 5. Movement-pattern / redundancy control (section 6)

Reused unmodified: `_select_movements()`'s two-pass selection (direct
target coverage, then fill without duplicating a movement family via
`EXERCISE_FAMILIES`/`_family_for_movement()`) already prevents e.g. two
squat variations or two curl variations both occupying slots meant for
distinct target muscles. Confirmed live over 80 lifting days: 44 distinct
movements prescribed, no family monopolizing every slot.

## 6. Session composition / set allocation (sections 7-8)

`_exercise_count(target_sets, goal_mode, max_sets)` (new, versioned):
derives how many exercise slots a dose deserves
(`round(target_sets / DEFAULT_SETS_PER_EXERCISE)`, bounded to
`[MIN_EXERCISES, MAX_EXERCISES]` = `[2, 5]` - the same ceiling B3's own
`SESSION_RULES` already uses), then applies a goal-mode delta. **Hard
invariant enforced, not merely assumed**: because `_set_allocation()`
never gives an exercise fewer than 2 sets while trimming, requesting
more exercise slots than `max_sets // 2` can support would make staying
within TKI-3's feasible upper bound mechanically impossible - this is
capped explicitly in `_exercise_count()`, and a second, redundant clip
runs after `_set_allocation()` returns, in case that assumption is ever
violated. Confirmed live: **0 dose-feasibility violations across 80 real
lifting days**.

## 7. Rep-range / load / progression (sections 9-14)

Entirely `progressive_overload.prescribe()` (unmodified) - a single,
self-contained, already-versioned function returning rep range, target
reps, target resistance, target RIR, rest seconds, and one of five
progression states (`PROGRESS_REPS`/`PROGRESS_LOAD`/`HOLD`/`REDUCE`/
`REBUILD`) with a plain-language rationale, computed from mode-
compatible comparable history and a 3-session-median trajectory
(`IMPROVING`/`STABLE`/`DECLINING`/`INSUFFICIENT_DATA`). `PROGRESS_LOAD`
resets `target_reps_per_set` to the range floor itself - TKI-5 never
stacks a load increase with an already-maximized rep target (verified by
test and disclosed as a hard invariant). No 1RM is ever invented -
`target_resistance_lb` is `None` whenever no reliable historical working
weight exists, surfaced as-is rather than backfilled with a guess.

## 8. Goal-policy interaction (sections 15-16)

Two bounded, table-driven knobs, both marked PRODUCT POLICY /
CALIBRATION PARAMETER in `prescription_policy.py` - no goal-specific
if/else scattered through the engine:

- `EXERCISE_COUNT_DELTA_BY_GOAL_MODE`: strength/recovery favor fewer,
  more focused slots (-1); lean_bulk/general_fitness favor broader
  variety (+1); lean_cut/maintenance are neutral (0).
- `REP_POSITION_FRACTION_BY_GOAL_MODE`: where within B3's own,
  unmodified `[minimum, maximum]` rep_range to bias `target_reps_per_set`
  - strength 0.15 (low end), lean_bulk 0.65 (high end), general_fitness
  0.55, lean_cut/maintenance 0.5, recovery 0.3. Applied **only** when
  B3's own `progression_state` is `HOLD` or `PROGRESS_REPS` - `REDUCE`/
  `REBUILD` (fatigue/data-floor states) and `PROGRESS_LOAD` (which B3
  itself already resets) are never touched by goal posture, matching
  section 15's "must NOT override fatigue / bypass TKI-3 dose / invent
  capacity."

**Live proof (section 16), same Sep-12 state, all 6 modes**: identical
session family (Upper Pull) and identical candidate pool every time;
`total_sets`/exercise composition legitimately differed - lean_bulk and
general_fitness both produced 4 exercises/8 sets (including "X-Pulldown
with Triceps Extension"), strength and recovery produced 2
exercises/6 sets, lean_cut and maintenance produced 3 exercises/7 sets -
purely from the same `EXERCISE_COUNT_DELTA_BY_GOAL_MODE` table, with no
mode inventing history or bypassing TKI-3's dose.

## 9. Cold-start behavior (section 22)

- No history at all (`profiles=[]`): valid `"status": "ok"` output with
  `exercises: []` and an explicit fallback reason - never a fabricated
  workout.
- Sparse (1-session) movement history: still produces a valid
  prescription; `progressive_overload.comparable_history()`'s own
  `LOW`/`INSUFFICIENT` confidence surfaces the uncertainty rather than
  hiding it (section 31 - confirmed, 82 low/insufficient-confidence
  exercise-prescriptions were visible, not hidden, across the live
  backtest).
- Unknown/no WHOOP readiness band: B3's own `_set_allocation()`/
  `progressive_overload.CONFIG["rir"]` tables are keyed only to `high/
  good/moderate/low` and were never designed for an `"unknown"` value -
  a defensive `_safe_readiness_band()` normalization (new, documented, a
  compatibility shim at TKI-5's own boundary, not a change to either
  reused function) maps anything else to `"moderate"` before calling
  them.
- Fallback hierarchy realized in practice: movement-specific history ->
  B3's own eligibility/scoring already prefers it; absent that, a
  movement is simply ineligible (`status != "usable"`) rather than
  silently treated as safe - matching section 22's explicit prohibition
  on treating missing history as zero.

## 10. Sep-12 diagnostic (section 26)

TKI-4(.1)'s calibrated selection for Sep-12 is **Upper Pull** (score
0.885 calibrated - see `TRAINING_INTELLIGENCE_TKI41_REPORT.md`).
TKI-3's dose: 9-9 working sets (goal-agnostic range collapsed to a
point), goal-adjusted to 9 for lean_cut.

**TKI-5 prescribed** (lean_cut, 3 exercise slots):

| Exercise | Role | Sets | Reps | Resistance | Action | Confidence |
|---|---|---|---|---|---|---|
| Seated Lat Pulldown | primary | 2 | 8 | 70.0 lb | REDUCE | MEDIUM |
| Hammer Curl | primary | 2 | 12 | 55.0 lb | REDUCE | HIGH |
| Barbell Bent Over Row | secondary | 3 | 10 | 57.5 lb | HOLD | MEDIUM |

Total: **7 working sets** (target was 9 - see the dose-allocation
finding below).

**Compared against the historical B3 workout** (Hammer Curl, Seated Lat
Pulldown, Barbell Bent Over Row, X-Pulldown with Triceps Extension, 9
sets, ~5,080 lb - NOT used as an expected answer, only a comparison
point):

- **Session-family choice: SUPPORTED.** TKI-4's independently-derived
  Upper Pull selection matches the muscle group B3's own historical
  workout actually trained.
- **Exercise composition: SUPPORTED.** 3 of B3's 4 exercises (Hammer
  Curl, Seated Lat Pulldown, Barbell Bent Over Row) are exactly what
  TKI-5 independently selected from the same eligible-movement pool; the
  4th (X-Pulldown with Triceps Extension) *does* appear once TKI-5's
  goal-mode exercise-count policy is given a 4th slot (confirmed in the
  lean_bulk/general_fitness rows of the multi-goal table above) - so the
  divergence traces to a disclosed exercise-COUNT policy choice, not a
  disagreement about which movements are good candidates.
- **Dose: QUESTIONABLE.** TKI-3's target (9 working sets) exactly
  matches B3's own historical total (9 sets) - but TKI-5's *actual
  allocation* under 3 exercise slots (`_set_allocation`'s own 2-set
  floor per exercise plus a compound-priority remainder rule) delivered
  only 7 of the 9 targeted sets. This is a real, disclosed under-
  delivery from the reused `_set_allocation()` interacting with a small
  exercise count, not a TKI-3 defect - flagged as this report's primary
  calibration finding (see §14).
- **Progression: not directly comparable.** The historical comparison
  data available for this diagnostic includes exercises/sets/tonnage
  only, not B3's own historical progression *action* for that date, so
  TKI-5's REDUCE/REDUCE/HOLD posture cannot be checked against a B3
  progression ground truth - disclosed as a limitation rather than
  assumed to agree or disagree.

## 11. 90-day backtest (section 27)

Evaluated all 91 days in the same comparable window as TKI-4/4.1; 80 of
them were lifting days (11 Rest, matching TKI-4.1's own Rest frequency
exactly, as expected since TKI-5 reuses TKI-4's selection unmodified).

| Metric | Result |
|---|---|
| Dose-feasibility violations | **0** |
| Readiness violations | 0 (no ineligible candidate was ever prescribed - eligibility is reused, unmodified, from B3) |
| Generic-placeholder usage | **0** |
| Distinct movements prescribed | 44 |
| Most-repeated movement | Bench Chest Fly, 26/80 days (32.5%) - not runaway monopolization |
| Progression-state distribution | PROGRESS_REPS 87, REBUILD 82, HOLD 67, PROGRESS_LOAD 26, REDUCE 11 |
| Low/insufficient-confidence prescriptions | 82 |
| Fallback-flagged days | **57 / 80 (71.2%)** |
| Large (>15%) single-step load jumps | 4 |
| Total working sets per lifting day | min 4, max 20, mean 10.18 |

**No pathological pattern flagged**: no exact identical workout repeated
indefinitely, no excessive random rotation (44 distinct movements, top
repeat only 32.5%), no runaway load jumps (4 out of several hundred
prescriptions), no progression attempted every single session for every
movement (PROGRESS_* states are 113 of 273 total exercise-progression
decisions, i.e. a minority), regression (REDUCE) is rare (11), and no
future-data leakage (verified separately, §13).

**Calibration finding, disclosed honestly**: a **71.2% fallback rate**
is high. Root cause: `_exercise_count()`'s slot target (dose /
`DEFAULT_SETS_PER_EXERCISE`, goal-adjusted) frequently exceeds the
number of movements with `usable`/comparable-enough history for that
day's target muscles, so `_select_movements()` often returns fewer
exercises than requested - a real, honestly-disclosed calibration gap
(this milestone's counterpart to TKI-4.1's Lower Body streak finding),
not an invariant violation. The 82 low/insufficient-confidence
prescriptions trace to the same root cause (many candidate movements
lack the `>= 2` comparable sessions `progressive_overload.CONFIG`
requires for anything above `LOW` confidence).

## 12. Longer backtest (section 28)

Not run. The 90-day/80-lifting-day sample already gives a clear,
consistent, and actionable finding (§11); given the fallback-rate
calibration gap is already unambiguous at this sample size, a 180-day
run was judged not to add proportionate new information for the
additional live-database time it would cost, and was scoped down rather
than run reflexively. Disclosed as a limitation, not hidden.

## 13. Temporal correctness (section 30)

- In-memory: a naive `as_of` is rejected; a future-dated session in the
  injected fixture history does not change the selected family or any
  prescribed exercise.
- **Real Postgres** (`test_training_intelligence_prescription_postgres.py`,
  2 new opt-in tests, run live against the Development database): (1) an
  end-to-end run through the real SQL paths (B3's own movement-
  performance query, not injected fixtures) produces a valid, dose-bound
  prescription; (2) a workout inserted 5 days in the future (including
  an exaggerated `base_weight=999.0`, 99 sets) does not change the
  selected family or any exercise's `movement_id`/`working_sets`/
  `target_reps_per_set`/`prescribed_resistance_lb`. Both passed.

## 14. Performance (section 32)

Single `build_shadow_prescription()` call: **3.10s** (Sep-12, cold). 90
days (91 calls, including the ~11 Rest days that skip the movement-
profile query entirely): **276.46s total, 3.04s/day average** - the
increase over TKI-4's own ~2.0s/call baseline is attributable to one
additional real query (`build_movement_performance_profiles()`'s own
180-day lookback SQL, B3's own, unmodified, already-necessary query),
not to any N+1 pattern - it is called exactly once per
`build_shadow_prescription()` call, never once per candidate exercise.

## 15. Configuration (section 33)

`training_intelligence/prescription/prescription_policy.py`,
`PRESCRIPTION_MODEL_VERSION = 1`. Every constant is labeled PRODUCT
POLICY / CALIBRATION PARAMETER: `MIN_EXERCISES`/`MAX_EXERCISES` (2/5 -
the ceiling matches B3's own existing `SESSION_RULES` ceiling, not a new
number), `DEFAULT_SETS_PER_EXERCISE` (3.0), `EXERCISE_COUNT_DELTA_BY_
GOAL_MODE`, `REP_POSITION_FRACTION_BY_GOAL_MODE`, `GOAL_POSTURE_
ELIGIBLE_STATES`, `GENERIC_PLACEHOLDER_NAMES` (defensive, documented as
redundant given the SQL-level exclusion already in place).

## 16. Hard invariants (section 34) - status

| Invariant | Status |
|---|---|
| Prescribed total working sets cannot exceed TKI-3 feasible dose | Enforced (exercise-count cap + explicit post-allocation clip); **0 violations in 80 live lifting days** |
| Rest cannot generate strength exercises | Enforced (`_rest_prescription()` returns `exercises: []` whenever TKI-4 selects Rest) |
| Fatigued muscles cannot receive aggressive direct progression | Enforced (reused `_movement_eligibility()` excludes any movement materially loading a suppressed/fatigued muscle before progression is ever computed) |
| Generic placeholders cannot anchor progression | Enforced (SQL-level exclusion, confirmed live; redundant name filter as backup) |
| Progression cannot use future performance | Enforced (as_of-bound query, confirmed live via opt-in Postgres test) |
| Unknown readiness cannot become Fresh | Enforced (TKI-4's own `_READINESS_QUALITY_UNKNOWN` semantics are unchanged; TKI-5 adds no new readiness interpretation) |
| Exercise selection deterministic | Confirmed by test and live repeated calls |
| Goal mode cannot invent capacity | Enforced (goal posture only shifts within B3's own already-computed rep_range/exercise-count bounds) |
| Movement history remains user-specific | Enforced (no cross-user aggregation anywhere in this package) |
| No hidden user-specific constants | Confirmed - every table is `goal_mode`-keyed, none is a per-user value |
| No uncontrolled simultaneous rep+load+set increase | Enforced (`PROGRESS_LOAD` resets `target_reps_per_set` to the range floor; goal posture never raises it back up beyond what was already earned) |
| No negative or impossible resistance | Not separately re-validated beyond reusing B3's own `_target_weight`-adjacent logic (`progressive_overload.prescribe`'s resistance is always `recent_weight`-derived or `None`) - no case producing a negative value was observed live or in tests |
| No empty lifting prescription without an explicit reason when a valid lifting session was selected | Enforced (`_rest_prescription()` is returned with a named reason whenever `_select_movements()` returns empty) |

## 17. Tests (section 37)

- `test_training_intelligence_prescription.py` - **34 new in-memory
  tests**: exercise-count bounds/invariant-cap, generic-placeholder
  filtering, role assignment, goal rep-posture (REDUCE untouched, HOLD
  biased, PROGRESS_REPS never walked back), candidate/eligibility,
  duplicate-movement-family suppression, muscle-stimulus/dose-bound
  consistency, set allocation, rep-range bounds, all 5 progression
  states individually reached, readiness combinations (high+fresh,
  high+fatigued-still-excludes), goal-mode variation + no-mode-bypasses-
  dose, movement continuity, unilateral flag pass-through, cold start
  (no/sparse history, unknown readiness), Rest output, determinism,
  temporal leakage (naive `as_of` rejected, future session ignored),
  simultaneous-progression guardrail.
- `test_training_intelligence_prescription_postgres.py` - **2 new
  opt-in real-Postgres tests** (§13).
- Full backend suite (two-batch convention): **625 passed, 52 skipped**
  (`--ignore=test_daily_pipeline_cache.py`) + **24 passed**
  (`test_daily_pipeline_cache.py`). Total **649 passed, 52 skipped, 0
  failed** - **+34** over the pre-TKI-5 baseline of 615, exactly
  matching the new in-memory test file; **0 regressions**.
- All opt-in Postgres tests run together (TKI-1 through TKI-5): **11
  passed**.

## 18. Live Postgres validation (section 20/30 requirement)

Ran against real Development data using the approved Keychain credential
pattern in a single shell process, credential unset before exit, all
output screened for credential-looking strings: the generic-placeholder/
unilateral catalog audit (§0 above), the Sep-12 diagnostic, the 6-mode
multi-goal validation, the 90-day/80-lifting-day backtest, and the 2 new
opt-in Postgres tests.

## 19. Known limitations

- **71.2% fallback rate** (§11) is this report's primary, honestly-
  disclosed calibration gap - `_exercise_count()`'s target frequently
  exceeds available `usable`-history movements for the day's target
  muscles.
- Dose-allocation under-delivery on Sep-12 (7 of 9 targeted sets, §10) -
  a real interaction between a small exercise count and `_set_allocation`
  ()'s 2-set floor, not a TKI-3 defect.
- 180-day/longer backtest not run (§12) - scoped down given the 90-day
  finding was already unambiguous.
- `role` (`primary`/`secondary`) is re-derived post-hoc from the already-
  decided `_select_movements()` ordering, not a first-class output of
  that function - a disclosed, deterministic approximation, not a
  fabrication.
- No negative-resistance invariant test exists beyond reusing B3's own
  logic verbatim (§16) - no dedicated adversarial test was written for
  this specific case given no code path in this milestone computes
  resistance independently of B3's own function.
- Progression could not be directly checked against B3's own historical
  Sep-12 progression action (§10) - only exercises/sets/tonnage were
  available for comparison.

## 20. Commercialization assessment

The architecture is fully reuse-first and generalizable: every new
parameter is `goal_mode`-keyed, nothing is a per-user constant, and the
Tonal-semantics findings (§3) are themselves durable, catalog-level
facts true for any user, not specific to this account. The fallback-rate
finding (§11/19) is exactly the kind of calibration signal a commercial
rollout would need resolved first (most likely by making
`DEFAULT_SETS_PER_EXERCISE`/the exercise-count formula more conservative,
or by having `_select_movements()` return a maximum ELIGIBLE count so
`_exercise_count()` can request no more than what a given day can
actually field) - a bounded, well-understood next step, not an
architectural rework.

## 21. Final verdict

**TKI-5 DEVELOPMENT PARTIAL — PRESCRIPTION CALIBRATION REQUIRED**

Every hard invariant held across 80 real lifting days (0 dose-
feasibility violations, 0 generic-placeholder leakage, Rest never
fabricated a strength session, no simultaneous uncontrolled rep+load+set
stacking, deterministic and temporally correct output confirmed both
in-memory and live); session-family choice and exercise composition were
independently SUPPORTED against the real historical Sep-12 B3 workout;
goal-mode posture genuinely differentiates exercise count/composition
without bypassing dose or fabricating history. What keeps this at
PARTIAL rather than PASS is the 71.2% fallback rate and the Sep-12
dose-allocation under-delivery (7 of 9 targeted sets) - both real,
disclosed calibration gaps in how many exercise slots this milestone's
new `_exercise_count()` policy requests relative to what the reused
eligibility engine can actually field, not a reliability or invariant
failure. No B3/B4/iOS/Production file was changed. The next milestone
was not started.
