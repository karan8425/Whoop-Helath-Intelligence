# Training Intelligence TKI-5.1 Report — Exercise Count + Set Allocation Calibration

SHADOW MODE ONLY. This is a calibration pass over TKI-5's composition/
allocation wrapper - `integrations.tonal.workout_prescription.
_select_movements()`, `_movement_eligibility()`, `_candidate_score()`,
and `integrations.tonal.progressive_overload.prescribe()` are **byte-
for-byte unchanged** (audited per section 28 - no reproducible defect
was found in any of them; see §D). B3/B4/iOS/Production are untouched.

## A. Original TKI-5 calibration failures

- Sep-12: TKI-3 justified 9 working sets; TKI-5 v1 delivered only 7.
- 90-day backtest: 71.2% fallback rate.

## B. Precheck / reproduction

Starting HEAD `f85ea68`, branch `develop`, worktree clean. Before
writing any new code, the two failures were reproduced and their exact
mechanism traced by reading `_select_movements()`, `_set_allocation()`,
and `progressive_overload.prescribe()` line-by-line (not merely
re-running the numbers) - this located a **second, previously-
undiagnosed root cause** beyond what TKI-5's own report had identified
(see §D).

## D. Fallback root-cause decomposition (section 2)

Two independent mechanisms were producing the fallback population, only
the first of which TKI-5's own report had identified:

**Mechanism 1 - exercise count computed before the pool was known
(category A/B, "requested exercise count > eligible/quality movement
count")**: TKI-5 v1's `_exercise_count()` derived a preferred count from
`dose / DEFAULT_SETS_PER_EXERCISE` alone, asked `_select_movements()`
to fill that many slots, and recorded a fallback whenever fewer eligible
movements existed - even when the resulting smaller session was
perfectly valid. This was the assignment's stated hypothesis and is
**fully resolved** in TKI-5.1 (see §E) - it no longer produces any
"only N of M desired slots" fallback at all; every such case is now
either fully satisfied (the preference never exceeds the real pool) or
recorded as an `adaptations_used` entry, never `fallbacks_used`.

**Mechanism 2 - the Sep-12 7-of-9 shortfall's ACTUAL cause (category D,
"per-movement set cap prevented full dose allocation" - via a mechanism
not previously documented)**: reproducing the Sep-12 case line-by-line
showed `_set_allocation()` correctly allocated `[3, 3, 3]` (summing to
the full target of 9) across the 3 selected movements. The 7-of-9
shortfall was created **afterward**, by `progressive_overload.
prescribe()` itself - two of the three movements had a `DECLINING`
trend under `readiness_band="low"/"moderate"`, which is `prescribe()`'s
own, deliberate `REDUCE` state (`set_count = max(2, set_count - 1)`).
TKI-5 v1 never redistributed that reduction to a movement that COULD
safely absorb it, and never reported the resulting gap - it was a
**silent, undisclosed shortfall**, not a composition-count problem at
all. This is genuinely category (I) - an implementation gap in TKI-5's
OWN wrapper (it never checked whether `_set_allocation()`'s output
survived `prescribe()` unchanged) - **not a defect in `_set_allocation()`
or `prescribe()` themselves**, both of which behaved exactly as
designed (see §28's audit conclusion below).

**Live 90-day fallback-category breakdown** (post-calibration numbers,
run to decompose the *original* population honestly - see §M for the
before/after comparison):

| Category | 90-day | 180-day |
|---|---|---|
| D. True dose shortfall (a movement's own REDUCE/cap left no safe way to fully absorb the target) | 4/80 (5.0%) | 11/162 (6.8%) |
| F. Insufficient comparable movement history (low/insufficient confidence) | 58/80 (72.5%) | 87/162 (53.7%) |
| A/B. Exercise-count-vs-pool mismatch reported as a bare fallback | **0** | **0** |
| H. Generic-placeholder leakage | 0 | 0 |

**TRUE FALLBACK vs. STRUCTURAL DEGRADATION (section 2)**: category F
(insufficient movement history) is a genuine, disclosed TRUE FALLBACK -
the system really does lack enough trustworthy per-movement history for
a majority of candidate movements on a given day (unchanged from TKI-5's
own finding; this milestone was never scoped to fix it - see §U).
Category D is also a genuine TRUE FALLBACK (a real safety constraint
legitimately prevented full absorption). What is **no longer** reported
as a fallback at all is the former A/B population - a smaller-but-valid
composition from a smaller-but-adequate pool is now correctly classified
as a STRUCTURAL DEGRADATION (`adaptations_used`), because a valid
personalized workout was still possible and was, in fact, formed.

## E. Exercise-count architecture (sections 3, 7)

`training_intelligence/prescription/composition.py::feasible_exercise_
count()` replaces `_exercise_count()`. The full ranked eligible pool is
discovered **first** - `_select_movements()` (unmodified) is called
ONCE with `max_exercises=MAX_EXERCISES` (5, the absolute ceiling); since
its own internal loops break early at any smaller `max_exercises`, a
smaller composition is always exactly a prefix of this same ranked
list, so it is never called twice. `eligible_pool_size = len(full_pool)`
then bounds everything downstream:

```
pool_max = min(MAX_EXERCISES, eligible_pool_size)
pool_max = min(pool_max, dose_upper_bound // 2)   # _set_allocation's 2-set floor
preferred = <personal history, else goal/dose default>, clamped to [MIN_EXERCISES, MAX_EXERCISES]
chosen = min(preferred, pool_max)                  # NEVER exceeds the real pool
```

Returns `{min, max, preferred, confidence, limiting_factors,
preference_source}` - `limiting_factors` only names a constraint that
actually reduced `chosen` below the raw preference, never merely because
the pool is smaller than the absolute ceiling (a bug caught by
`test_two_exercise_composition_is_valid_not_penalized` before it reached
live validation).

## F. Feasible count-range + personal historical session structure
(sections 4-5)

`historical_session_structure()` groups the SAME already-loaded
`ledger_rows` (one query, see §32/24) by real workout, classifies each
into a session family via TKI-2's own `classify_workout_family()`
(reused, unmodified), and for workouts matching the selected family
computes the median distinct-movement count per real session. With
`>= MIN_COMPARABLE_SESSIONS_FOR_HISTORICAL_STRUCTURE` (3) comparable
real sessions, this becomes `preferred`'s source
(`session_family_personal_history`); otherwise `preferred` falls back to
the goal/dose default (`goal_policy_dose_default`) - two real,
distinct tiers of section 5's fallback hierarchy (a third/fourth/fifth
tier - movement-pattern history, general lifting history - is not
separately implemented, since no additional real distinction was
available from existing data without a second query; disclosed, not
fabricated - see §U).

## G. Composition selection (section 7)

No combinatorial search was introduced (per the assignment's own
"do not introduce a complex combinatorial optimizer if a simple
deterministic strategy works"): because the ranked pool is already
ordered by `_candidate_score()` (unmodified - target-muscle match,
history, earned progression, struggling/inconsistency penalties), the
best feasible composition of size `chosen` is simply `full_pool[:chosen]`
- a deterministic slice of an already-quality-ranked list, not a search
over combinations.

## H. Set absorption model (sections 10-11)

`_personal_absorption_cap(profile)`:

- **Tier A** (evidence-driven): the movement's own `recent_sets_per_
  session` + `PERSONAL_TOLERATED_SETS_MARGIN` (1), used only when backed
  by `>= MIN_COMPARABLE_SESSIONS_FOR_HISTORICAL_STRUCTURE` (3) real
  comparable sessions for THAT movement (gated on `profile["history"][
  "sessions_in_lookback"]` - a single lucky session must not set an
  inflated cap; caught by `test_sparse_pool_does_not_produce_giant_
  per_exercise_allocation` before it reached live validation).
- **Tier D** (labeled PRODUCT POLICY fallback, used when tier A doesn't
  apply): `PER_MOVEMENT_ABSORPTION_CAP_FALLBACK = 4` - identical to
  B3's OWN existing `_set_allocation()` remainder-loop ceiling, not a
  new number invented for this milestone.

Applied **before** `_set_allocation()`'s output is ever passed to
`prescribe()` - a sparse pool (e.g. one eligible movement) is clipped to
its own safe cap rather than handed the entire dose, converting what
would otherwise be an unsafe concentration into an explicit, disclosed
shortfall (section 22).

## I. Set allocation logic (sections 8-9)

`dose_absorption_waterfall()`:

1. **Stage 1** - `_set_allocation()` (unmodified) produces the initial,
   compound-biased, dose-summing split.
2. **Per-movement cap clip** - each movement's initial share is clipped
   to its own absorption cap (§H) before it is ever prescribed.
3. **Stage 2** - `progressive_overload.prescribe()` (unmodified) is
   consulted per (capped) movement - this is where a REDUCE state can
   still give back sets.
4. **Stage 3 (new)** - any shortfall from either #2 or #3 is offered to
   movements whose state is NOT `REDUCE`/`REBUILD`, **primary-role
   movements first** (section 12/13), bounded by each movement's own cap
   AND by `MAX_REALLOCATION_SHARE_PER_MOVEMENT` (0.4 of the total target
   - section 27's "no excessive set dumping into one accessory
   movement"), by re-calling `prescribe()` with an incremented
   `set_count` (a normal use of that unmodified function, not a
   modification of it). Only if nothing can safely absorb the remainder
   is an explicit `dose_shortfall`/`shortfall_reason` returned - **no
   silent shortfalls** (section 8's primary invariant).
   `target_sets` is defensively clamped to `<= max_sets` before any of
   this runs, so the waterfall itself can never chase a target beyond
   what TKI-3 permits, even if called with an inconsistent input.

## K. Adaptation vs. fallback semantics (section 16)

`adaptations_used` now carries "used `len(selected)` exercise(s)
(feasible range `min`-`max`, source: ...) - limited by: ..." whenever the
chosen composition is smaller than the raw preference for a real,
disclosed reason (pool size or dose bound) - this is normal, adaptive
behavior, never `fallbacks_used`. `fallbacks_used` is reserved for: a
true dose shortfall, low/insufficient-confidence prescriptions, and
generic-placeholder exclusion (defense-in-depth, unchanged from TKI-5).

## L. Sep-12 before/after (section 17)

| | Before (TKI-5 v1) | After (TKI-5.1) |
|---|---|---|
| Feasible exercise count | n/a (not computed) | min 2, max 4, preferred 3 (goal_policy_dose_default, MEDIUM confidence, no limiting factors) |
| Selected exercises | Seated Lat Pulldown, Hammer Curl, Barbell Bent Over Row | same 3 movements |
| Per-exercise sets | 2, 2, 3 | 2, 2, **5** |
| Dose target | 9 | 9 |
| **Dose delivered** | **7** | **9** |
| Dose shortfall | 2 (undisclosed) | **0** |
| Adaptations/fallbacks | none reported (silent) | none needed - full dose absorbed |

**Could 9 sets be safely allocated using the selected eligible
movements? Yes.** Seated Lat Pulldown and Hammer Curl both legitimately
entered `REDUCE` (declining trend + moderate readiness that day) and
correctly gave back one set each versus their initial 3/3/3 split; the
waterfall's Stage 3 redirected both returned sets to Barbell Bent Over
Row (role=secondary but the only remaining HOLD-state, non-capped
movement, with room under both its personal cap (5) and the 40%-of-
target reallocation ceiling), raising it from 3 to 5 sets - the full
9-set target was delivered with zero shortfall, zero invariant
violation, and no exercise-count change (still 3 exercises, matching
TKI-5 v1's 3-of-4-exercise overlap finding, unchanged from §L of the
TKI-5 report).

## M. 90-day backtest before/after (section 18)

| Metric | Before (TKI-5 v1) | After (TKI-5.1) |
|---|---|---|
| Lifting days | 80 | 80 |
| Exact-dose-delivery rate | not measured (shortfalls were silent) | **95.0% (76/80)** |
| Dose-shortfall rate (genuine) | not measured | 5.0% (4/80), mean shortfall 2.50 sets |
| Fallback rate (raw) | 71.2% | 75.0% - **see decomposition, §D** |
| - of which: true dose shortfall | (silently included in the old number, uncounted) | 5.0% |
| - of which: insufficient movement history | (dominant driver, undecomposed) | 72.5% |
| - of which: exercise-count-vs-pool mismatch | dominant driver (see TKI-5's own report) | **0%** |
| Adaptation rate | not distinguished from fallback | 5.0% |
| Dose-feasibility violations | 0 | 0 |
| Readiness violations | 0 (eligibility unchanged) | 0 |
| Generic-placeholder leakage | 0 | 0 |
| Distinct movements | 44 | 46 |
| Exercises/session | not reported | min 2, max 5, mean 3.44 |
| Sets/exercise | not reported | min 2, max 5, mean 2.97 |
| Progression-state distribution | PROGRESS_LOAD 26, PROGRESS_REPS 87, REBUILD 82, HOLD 67, REDUCE 11 | PROGRESS_LOAD 26, PROGRESS_REPS 87, REBUILD 83, HOLD 68, REDUCE 11 - **essentially identical**, confirming `progressive_overload.prescribe()` itself is genuinely unmodified |

The raw fallback-rate NUMBER did not drop, and is not claimed to - per
the assignment's explicit instruction not to manipulate classification
to improve a headline number. What changed is what the number MEANS:
the exercise-count/composition defect this milestone targeted is now at
0% (was the dominant driver before), replaced by a 5% adaptation rate
for the same underlying situations; the remaining ~72.5% is entirely a
different, already-disclosed, out-of-scope data-quality signal
(insufficient per-movement comparable history) that TKI-5.1 was never
tasked to resolve.

## N. 180-day backtest (section 20)

Run this time (TKI-5 skipped it). 162 lifting days: exact-dose rate
93.2% (151/162), true-shortfall rate 6.8% (11/162), insufficient-
movement-history rate 53.7% (87/162), adaptation rate 5.6% (9/162), 0
dose violations, 0 placeholder leakage - **the calibration generalizes**;
the 90-day sample was not a lucky window.

## O. Multi-goal validation (section 21)

Same Sep-12 factual state, all 6 goal modes:

| goal_mode | feasible range | chosen | total sets | exercises |
|---|---|---|---|---|
| lean_cut | 2-4 | 3 | 9 | Seated Lat Pulldown, Hammer Curl, Barbell Bent Over Row |
| lean_bulk | 2-4 | 4 | 9 | + X-Pulldown with Triceps Extension |
| strength | 2-4 | 2 | 6 | Seated Lat Pulldown, Hammer Curl |
| maintenance | 2-4 | 3 | 9 | same 3 as lean_cut |
| general_fitness | 2-4 | 4 | 9 | same 4 as lean_bulk |
| recovery | 2-4 | 2 | 6 | same 2 as strength |

The feasible range (2-4) is **identical across all 6 modes** - it is a
fact of the candidate pool/dose, not policy. Only the chosen point
inside that range differs, exactly per `EXERCISE_COUNT_DELTA_BY_GOAL_
MODE` (unchanged from TKI-5). No mode produced an infeasible 4-exercise
session when only 4 was truly available; strength/recovery legitimately
chose fewer without being forced to.

## P. Cold-start behavior (section 22)

- Zero eligible movements: unchanged Rest-style output with an explicit
  reason (no exercises invented).
- One-movement pool: the personal-absorption-cap clip (§H) prevents that
  single movement from absorbing an implausible full-dose allocation -
  confirmed by test; any resulting gap is an explicit, disclosed
  shortfall rather than an unsafe concentration.
- `<3` real comparable sessions for a movement or for the session family:
  both fall through to their respective PRODUCT POLICY defaults (tier D
  absorption cap; goal/dose exercise-count preference) - never treated
  as zero/absent.
- Unknown WHOOP readiness band: unchanged `_safe_readiness_band()`
  normalization (carried over from TKI-5).

## Q. Temporal validation (section 23)

- In-memory: a future ledger row does not change `historical_session_
  structure()`'s median or sample count (new test).
- **Real Postgres** (new opt-in test): a workout inserted 10 days in the
  future does not change `feasible_exercise_count` or the historical-
  structure sample count for the SAME `as_of`. Combined with the 2
  existing TKI-5 opt-in tests (future workout does not change the
  selected family or any exercise's sets/reps/resistance), all re-run
  live and passing against the real, refactored code path.

## R. Tests (section 31)

- `test_training_intelligence_prescription.py` - **51 in-memory tests**
  (was 34; net +17 after replacing the old `_exercise_count`-based suite
  with feasible-count-range, historical-structure, and dose-absorption-
  waterfall unit tests plus updated end-to-end tests): feasible-count
  bounds/pool-capping/dose-bound-capping, personal-history usage vs.
  fallback, 2-exercise and 4+-exercise validity, historical-structure
  computation + its own temporal safety, exact-dose delivery, true
  shortfall disclosure, never-exceeds-max_sets, no-excessive-single-
  movement-share, plus the full existing TKI-5 suite (placeholder
  filtering, role assignment, goal rep posture, eligibility, duplicate-
  pattern suppression, muscle coverage, dose bounds, rep ranges, all
  progression states, readiness combinations, goal-mode variation +
  no-goal-forces-infeasible-count, movement continuity, unilateral
  handling, cold start, sparse pool, Rest output, determinism, temporal
  leakage, simultaneous-progression guardrail, and the new adaptation-
  vs-fallback semantics test).
- `test_training_intelligence_prescription_postgres.py` - **3 opt-in
  real-Postgres tests** (was 2; added the historical-structure temporal
  test).
- Full backend suite (two-batch convention): **642 passed, 53 skipped**
  + **24 passed** = **666 passed, 53 skipped, 0 failed** - **+17** over
  the pre-TKI-5.1 baseline of 649 (net of the rewritten prescription test
  file), **0 regressions**.
- All opt-in Postgres tests together (TKI-1 through TKI-5.1): **12
  passed**.

## S. Live Postgres validation (section 20/23/29)

Ran against real Development data using the approved Keychain credential
pattern, single shell process, credential unset before exit, output
screened for credential-looking strings: the Sep-12 recheck, 6-mode
multi-goal validation, 90-day backtest (with fallback-category
decomposition), a second 90-day pass to decompose fallback reasons
precisely, the 180-day generalization check, and the 3 opt-in Postgres
tests (including the 2 pre-existing ones, re-verified against the
refactored code).

## T. Performance (section 25)

Single call: **3.66s** (Sep-12) vs. TKI-5's own baseline of ~3.0-3.1s -
a modest, justified increase from `historical_session_structure()`'s
one-time grouping of the (already-loaded-for-this-call) ledger rows and
from `progressive_overload.prescribe()` being called a small, bounded
number of additional times per movement during Stage 3's reallocation
(never per-candidate, never unbounded - `guard < 200` is a safety cap,
not a typical iteration count; in practice at most a handful of calls
per session). 90-day average: **3.55-3.76s/day** (up from TKI-5's
3.04s/day) - one additional real query (`load_rows()` for historical
structure, justified and singular, section 24) plus the bounded extra
`prescribe()` calls account for the difference. No N+1 pattern:
`build_movement_performance_profiles()` and the new `load_rows()` call
are each called exactly once per `build_shadow_prescription()` call,
never once per candidate movement.

## U. Known limitations

- The 72.5%/53.7% "insufficient movement history" fallback rate is
  unchanged from TKI-5 and was never in scope for this milestone - it
  reflects how much real, comparable Tonal history exists per movement,
  not a composition/allocation policy choice. A future milestone would
  need to address it directly (e.g. a broader historical-fallback tier
  for progression confidence itself, which was explicitly out of scope
  here per "do not redesign progressive_overload.py").
- Section 5's fallback hierarchy is realized as 2 real tiers (session-
  family personal history, else goal/dose default), not the full 5-tier
  hierarchy the assignment sketches - a 3rd/4th tier (movement-pattern
  history, general lifting history) was not separately implementable
  without an additional query or a materially different data shape;
  disclosed rather than fabricated.
- `MAX_REALLOCATION_SHARE_PER_MOVEMENT`/`PERSONAL_TOLERATED_SETS_MARGIN`/
  `PER_MOVEMENT_ABSORPTION_CAP_FALLBACK` are, like all TKI-5/5.1
  constants, PRODUCT POLICY / CALIBRATION PARAMETERs - not scientific
  findings - and are less battle-tested than `_set_allocation()`'s own
  much older constants.

## V. Commercial scalability assessment

Every new parameter remains goal-mode-keyed or a single, disclosed,
versioned constant - nothing is user-specific. The root-cause finding
in §D (a redistribution/disclosure GAP in the wrapper, not a defect in
B3's own reused engine) is itself a generalizable lesson: any dose-
allocation layer built on top of a per-movement progression engine that
can legitimately reduce its own output must explicitly reconcile the
two, or it will silently under-deliver for any user whose movements
enter a declining/reduced state - this is now closed architecturally,
not just for this account's Sep-12 data.

## W/X/Y. Commit(s) / Git status / Production-iOS confirmation

See the final report below.

## Final verdict

**TKI-5 DEVELOPMENT PASS — PRESCRIPTION VALIDATED**

Sep-12 now delivers the full TKI-3-justified dose (9/9, zero shortfall,
zero invariant violation); the 90-day exact-dose-delivery rate is 95.0%
(93.2% at 180 days, confirming generalization); the exercise-count-vs-
pool mismatch that was TKI-5's dominant, mislabeled "fallback" driver is
now 0%, replaced by an honestly-reported 5% adaptation rate; the
remaining fallback rate is composed almost entirely of a different,
already-disclosed, out-of-scope data-quality signal (insufficient
movement history) plus a small (5-7%), genuinely necessary true-
shortfall rate, both reported transparently rather than manipulated to
produce a better headline number; every hard invariant held (0 dose
violations, 0 readiness violations, 0 placeholder leakage, no pathological
per-movement concentration, deterministic and temporally correct output,
confirmed both in-memory and live); progression-state distribution is
essentially unchanged, confirming `progressive_overload.prescribe()`
and `_select_movements()`/`_movement_eligibility()`/`_candidate_score()`
were genuinely left unmodified, as required, with no reproducible defect
found in any of them. No B3/B4/iOS/Production file was changed. The next
milestone was not started.
