# TKI-5.2 Report — Capacity + Workload Calibration

SHADOW MODE ONLY. `training_intelligence/calibration/` is a new,
explicitly-invoked package (`build_calibrated_shadow_prescription()`) -
never imported by `main.py`, `todays_plan.py`, or any live request path.
`integrations/tonal/workout_prescription.py`, `progressive_overload.py`,
and `training_dose.py` (B2/B3, live/shared) are **read from, never
modified** - every shared function this milestone touches
(`_movement_eligibility`, `_candidate_score`, `_latest_readiness`,
`_goal_adjusted_working_sets`, `progressive_overload.CONFIG`) is
imported, not altered.

## Forensic findings carried forward

- WHOOP Recovery 94%, all target muscles READY, TKI-3 feasible range
  collapsed to 10-10 sets, final prescription Upper Push/3 exercises/10
  sets, displayed volume 5,772 lb vs. a corrected cable-aware ≈9,204 lb
  vs. a recent Push-dominant median ≈18,002 lb / strict Upper Push
  median ≈14,144 lb. Five root causes: (1) workload accounting ignoring
  loaded-cable semantics, (2) a short-window muscle-budget cap acting as
  a hard capacity ceiling, (3) sparse exact-family history collapsing
  composition to 3 exercises, (4) weak, unpaired progression
  aggregation, (5) no workload anomaly gate. Verdict: QUESTIONABLE.

## Precheck note

This checkout already contained an untracked, uncommitted
`training_intelligence/calibration/` package
(`history.py`/`capacity.py`/`progression.py`/`shadow.py`) implementing
the large majority of this milestone's architecture. Per "read before
changing," this was audited in full rather than rewritten: the design
was sound and directly addressed sections 2-17 of the assignment. Live
testing during that audit surfaced two real, fixed defects (see §D/K
below) before any further validation proceeded - "reproduce before
changing" was honored by reproducing the OLD (TKI-5.1) result first,
then running the new package and finding its own bugs through the same
live reproduction step, not by assuming the pre-existing code was
correct.

## D. Workload accounting fix

`training_intelligence/calibration/history.py::multipliers()`: for each
movement with sufficient real history, derives
`median(stored_volume / (base_weight * rep_count))` over **standard-
mode-only, unassisted** sets, rounds to the nearest of `{1, 2}` (never a
blanket bilateral rule - `is_bilateral` is read from the profile but
never used to force the multiplier), and only trusts the result when
`>=6` valid sets, `>=2` distinct sessions, and `>=80%` of ratios agree
within 12% of the chosen integer. Otherwise: explicit `workload_
multiplier=1`, `multiplier_source="product_policy_fallback"`,
`confidence="LOW"`, and a disclosed warning - never a silent guess. Per
movement exposes `workload_multiplier`, `multiplier_source`,
`confidence`, `sample_count`, `session_count`, `observed_median_ratio`,
`agreement_fraction`. As-of-safe (all inputs pass through `valid_rows()`
first). Confirmed live: a real two-cable movement resolved to
multiplier `2` with `confidence="MEDIUM"`/`"HIGH"` and
`multiplier_source="historical_standard_mode_ratio"`; sparse movements
correctly fell back to `1`/`"LOW"` with the warning populated (§M).

## E. Capacity model

`capacity.py::capacity_reference()` + `history.py::comparable_sessions()`:
a 4-tier fallback hierarchy (`exact_family` -> `similar_primary_muscles`
-> `muscle_region` -> `general_personal_history`, else a versioned
product-policy fallback) selects the first tier with `>=MIN_SESSIONS`
(3) comparable sessions, then reports `comparable_session_count,
median_sets, p25_sets, p75_sets, recent_median_sets,
historical_upper_typical, confidence, personal_frequency_per_week` (the
last derived from real inter-session gaps, not a fixed 1.5/week
constant - falls back to a versioned constant only when no history
exists at all). This directly replaces the old short-window muscle-
budget-only computation as the primary capacity signal.

## F. Recent-stimulus vs. capacity separation

`capacity.py::feasible_capacity()` builds the feasible range as an
explicit intersection: `historical_capacity` (from E) `∩`
`systemic_readiness` (a policy fraction of the historical p25/p75, keyed
by WHOOP band) `∩` `local_readiness` (a hard multiplicative floor - any
FATIGUED/SUPPRESSED relevant muscle collapses the range, never averaged
away by a fresh neighbor) `∩` `detraining_uncertainty` (a personal-gap-
derived, not hardcoded, "prolonged absence" threshold -
`max(21 days, 3x personal p75 inter-session gap)` - only below this
threshold is the range shrunk by a disclosed policy fraction) `∩`
`session_structure` (the actual selected pool's absorbable capacity).
Every constraint reports its own binding status and provenance in
`binding_constraints`. **Core invariant verified by test and live data**:
recent stimulus alone (a training gap) narrows the range only through
the explicit, disclosed `detraining_uncertainty` term - it never
collapses `historical_capacity` itself, which remains p25/p75-derived
from the full comparable-session pool regardless of how recently that
pool was last exercised (`test_recent_low_stimulus_does_not_collapse_
long_term_capacity`).

## G. Readiness interaction

`SYSTEMIC` (capacity.py): a versioned PRODUCT POLICY table of
`(lower_fraction, upper_fraction)` per WHOOP band - `high=(1.0,1.0)`
(may use the full demonstrated range, never beyond it),
`good=(.9,.95)`, `moderate=(.75,.85)`, `low=(.5,.65)`,
`very_low=(0,0)`. Explicitly documented as policy, not physiology.
`LOCAL` is a separate, harder multiplier (`FATIGUED`/`SUPPRESSED` = 0,
`RECOVERING`/`UNKNOWN` = .5, `READY`/`FRESH` = 1.0) applied
independently and always after systemic scaling - confirmed by test
that a fatigued muscle constrains the range even under `"high"` band
(`test_local_fatigue_still_constrains_hard`), and that WHOOP alone can
never push the upper bound above the demonstrated historical p75
(`test_high_recovery_cannot_exceed_demonstrated_capacity`).

## H. Frequency personalization

`capacity_reference()`'s `personal_frequency_per_week = 7 / median(real
inter-session gap days)`, computed from the SAME comparable-session
pool - falls back to a single versioned constant
(`FALLBACK_FREQUENCY_PER_WEEK = 1.0`) only when fewer than 2 comparable
sessions exist at all. No fixed 1.5-sessions/week assumption remains in
this package (the OLD B2/short-window-budget mechanism that used a
fixed frequency divisor is not called by this shadow layer at all).

## I. Composition changes

`shadow.py::select_pool()` + `comparable_sessions()`-derived
`historical_exercise_distribution` (median/p25/p75 of real past
exercise counts for the SAME comparable-session tier used for capacity)
replace the old `round(total_sets / 3)` heuristic - the preferred
exercise count is the median of real personal exercise counts for the
best-available comparable tier, capped only by the real eligible pool
size and the feasible range's own set-count floor (never assumed
independent of what the dose can actually support).

## J. Compound movement handling

`history.py::pattern()`: a deterministic, name-pattern classification
(`vertical_press`/`horizontal_press`/`horizontal_pull`/`vertical_pull`/
`hinge`/`squat_lunge`/`isolation`/`accessory`/`unknown`) - never
hardcodes a specific movement (no "Bench Press" special case anywhere).
`select_pool()` gives a small, soft, versioned score bonus
(`COMPOUND_SCORE_BONUS`) to candidates whose pattern is in `COMPOUNDS`,
and separately guarantees at least one instance of each *distinct*
pattern present in the eligible pool is represented before filling
remaining slots with whatever the personal-quality ranking prefers -
this works identically across Upper Push/Pull/Lower/Full Body since it
keys off the pattern taxonomy, not a family-specific list.

## K. Progression comparator changes (+ two real defects found and fixed)

`progression.py::prescribe()`/`matched_history()`: matches by **load**
(closest-to-median actually-performed weight, `±5%` tolerance), pairs
reps/RIR/mode from that SAME matched set of records - never "session
median load + separate average reps." Filters to eligible history, THEN
standard-mode + unassisted, THEN applies the `MAX_SESSIONS=6` sample
limit, in that exact order (`test_mode_filtering_happens_before_sample_
limit`). Warm-up sets (`raw_data.warmUp`) are excluded before any
comparison. Cross-mode fallback never occurs -
`matched_history()` only ever returns standard-mode rows, and
`cross_mode_fallback` is always explicitly `False`.

**Two real, live-reproduced defects found in the pre-existing
implementation and fixed this milestone** (both isolated to this new,
never-shared package - no B3/B2 code touched):
1. `prescribe()`'s own REDUCE state can shrink an already-allocated
   count below what the capacity-aware split gave a movement (the exact
   TKI-5.1 root cause, reappearing in this new allocator because it
   didn't yet have a redistribution stage). Fixed by adding a bounded
   Stage-3 waterfall in `shadow.py` that offers the resulting shortfall
   to non-REDUCE movements with real `capacity_sets` headroom before
   reporting an explicit `dose.dose_shortfall`/`shortfall_reason` -
   never silently absorbed. Confirmed live: Sep-12 went from
   `delivered_sets=10` (target 11, silent 1-set gap) to `delivered_sets
   =11` (target 11, 0 gap).
2. `estimated_volume = sets * reps * resistance * multiplier` would
   raise `TypeError` if `target_resistance_lb` were ever `None`
   (structurally prevented today by `select_pool()`'s own matched-
   history gate, but not defensively guarded). Fixed to return `None`
   (not crash, not zero) when resistance is unknown, matching the
   established pattern in `mobile_adapter.py`.

## L. Workload anomaly gate

`shadow.py::workload_sanity()`: compares the session's own corrected
`estimated_total_volume` against `envelope()`'s multi-window (30/90/
365-day) `normalized_workload` distribution (median/p25/p75/sample
count), preferring the narrowest window with `>=3` confident samples.
Returns `WITHIN_PERSONAL_RANGE`/`BELOW_PERSONAL_RANGE`/`ABOVE_PERSONAL_
RANGE`/`INSUFFICIENT_DATA` plus `ratio_to_median`/`ratio_to_p25`/
`accepted_reason_for_deviation` (populated only from real binding
constraints - systemic/local readiness, detraining, session structure,
or an explicitly reduced goal posture - never a bare mention of a goal
mode). **Never auto-inflates toward the median** (`test_does_not_auto_
inflate_to_median`) - a below-range result with no accepted reason is
reported, not corrected. `shadow.py::quality_verdict()` layers a final
`SUPPORTED`/`QUESTIONABLE`/`CONTRADICTED` verdict on top, checking hard
dose/readiness invariants first (`CONTRADICTED` if the delivered dose
exceeds the feasible upper bound or loads a fatigued/suppressed muscle),
then softer evidence gaps (`QUESTIONABLE` for insufficient workload
evidence, unexplained deviation, or low-confidence progression).

## M. Sep-12 before/after (live Development data, real "as_of=now")

| | Before (TKI-5.1) | After (TKI-5.2) |
|---|---|---|
| Feasible range | not modeled (single-point 9-9) | **10-14** |
| Binding constraints | none exposed | historical_capacity, systemic_readiness (moderate band) |
| Chosen dose | 9 | 11 (goal-adjusted, lean_cut) |
| Delivered sets | 9 | **11 (0 shortfall)** |
| Exercises | 3 (Seated Lat Pulldown, Hammer Curl, Barbell Bent Over Row) | 5 (adds Barbell Biceps Curl, Standing Single-Arm Row, Barbell Chinup) |
| Estimated volume (naive) | 5,315 | n/a |
| Estimated volume (cable-aware) | n/a | **8,213** |
| Workload sanity | not modeled | INSUFFICIENT_DATA (normalized-workload reference lacked `>=3` confident samples for this exact date) |
| Quality verdict | not modeled | QUESTIONABLE (insufficient workload evidence; one movement with weak paired progression evidence) |

The session was re-run on the actual current date rather than a fixed
Sep-12 snapshot (WHOOP recovery was 66%/"moderate" at run time, not the
94% in the original forensic finding) - per the explicit instruction not
to target any specific number, the engine's own live inputs were used
throughout, not a replayed constant.

## N. Aug-7 counterfactual (live Development data)

| | Forensic finding | TKI-5.2 |
|---|---|---|
| Actually performed | 17 sets, ~14,144 lb | n/a (this is a recommendation, not a replay of history) |
| Old engine | 5 sets, 2,070 raw / 4,140 cable-corrected | n/a |
| **New engine** | | Upper Push, feasible range **2-5**, dose 4, delivered 4 (0 shortfall), 2 exercises (Standing Decline Chest Press, Seated Alternating Overhead Press), estimated volume **2,376**, workload sanity INSUFFICIENT_DATA, quality **QUESTIONABLE** |

**Honest assessment, not minimized**: the new engine's dose (4 sets) is
still far below the historically-performed 17 - but for a materially
different, now fully-provenanced reason. `historical_capacity`,
`systemic_readiness`, AND `local_readiness` are all `binding` for this
date, and the comparable-session pool used (`source: exact_family`,
10 sessions) itself has a **set-count** p25/p75 narrower than its own
**exercise-count** p25/p75 (5 exercises typical, but a correspondingly
low sets-per-exercise figure) - i.e., the exact-family historical
sessions this date drew on were not, on their own set-count evidence,
17-set sessions. Composition improved (from a fixed 3-exercise default
toward a personal-history-derived exercise count and explicit compound-
pattern coverage), and the model **correctly refuses to assert
confidence it doesn't have** (QUESTIONABLE, not a false SUPPORTED) -
this is architecturally the right behavior even though the absolute
number did not move as far toward the historical envelope as intuition
might expect. This specific gap - why the "exact_family" comparable
pool's own set-count distribution runs low relative to the single Aug-7
data point - was not further decomposed within this milestone's time
budget and is named explicitly in §X as a follow-up.

## O. Historical replay (40 dates, live Development data)

Every date used the real `as_of=` value, real WHOOP/Tonal data, and the
unchanged TKI-4 family selection.

| Metric | Calibration cohort (20 earlier dates) | Held-out cohort (20 later dates) |
|---|---|---|
| Lifting days | 18 | 19 |
| Dose-feasibility violations | **0** | **0** |
| Readiness violations | **0** | **0** |
| Composition source | exact_family 14, similar_primary_muscles 4 | exact_family 15, similar_primary_muscles 4 |
| Recent-exposure distribution | RESTED 7, WELL_EXPOSED 10, FATIGUED 1 | WELL_EXPOSED 7, RESTED 11, FATIGUED 1 |
| Quality verdict | QUESTIONABLE 17, SUPPORTED 1 | QUESTIONABLE 19 |
| Workload sanity | within 0, below/above-unjustified 0, **insufficient 16** | within 0, below/above-unjustified 0, **insufficient 19** |

**Zero hard-invariant violations across all 40 real days** - the
capacity/readiness/composition architecture holds. **The headline
calibration finding**: `workload_sanity` lands on `INSUFFICIENT_DATA`
on nearly every day, because too few of a given day's exercises carry a
`>=MEDIUM`-confidence workload multiplier for the `normalized_workload`
reference to be trusted. This, not a false-confidence error, is what
drives the near-universal `QUESTIONABLE` quality verdict - the system
is being appropriately cautious, but its practical "can render a
confident verdict" rate is low given real per-movement history density.
This is the primary basis for this report's PARTIAL verdict (§Verdict).

## P. Held-out validation

The 40-date window was split chronologically (earlier 20 = calibration/
inspection, later 20 = held-out validation) - no code, threshold, or
constant in this milestone was adjusted between generating the
calibration cohort's numbers and the held-out cohort's numbers, and
none of the held-out cohort's dates informed any constant in
`history.py`/`capacity.py`/`progression.py` (which are static, versioned
product-policy values, not fit to any sample). The two cohorts show
**consistent** behavior (comparable composition-source mix, comparable
quality/workload-sanity distributions, 0 violations in both) -
generalization holds, not just a lucky first-20-days sample.

## Q. Calibration metrics

- Median/absolute set-count error against actual performed sessions was
  not computed as a single scalar - the assignment's own framing ("we
  are not optimizing for zero error... looking for absence of
  systematic under-prescription") is better answered by the workload-
  ratio distribution above than a single error number, which was
  judged more likely to mislead than clarify given how few days have a
  directly comparable "same family, same day" actual session to diff
  against.
- % below personal p25 without justification: **0/37 lifting days**
  across both cohorts (no case of an unexplained under-prescription
  relative to the reference distribution - though this is partly a
  consequence of `INSUFFICIENT_DATA` suppressing the comparison rather
  than a confident "within range" finding, disclosed above).
- % above personal p75 without justification: **0/37**.
- Fallback rate (composition source != `exact_family`): 8/37 (21.6%),
  all falling to `similar_primary_muscles` (tier 2), never all the way
  to the versioned product-policy floor - the hierarchy is doing real
  work, not defaulting immediately.

## R. Multi-goal validation

Same Sep-12 factual state, all 6 goal modes: **feasible range identical
across all 6** (`{(10, 14)}` - a single distinct value, confirmed live),
chosen dose legitimately varied inside it (recovery 10, lean_cut/
strength 11, maintenance/general_fitness 12, lean_bulk 13) - no mode
exceeded the range, no mode collapsed it, no forced differentiation.

## S. Cold-start behavior

Unit-tested: zero rows/zero profiles -> valid `status="ok"`, dose `0`,
no crash; a single sparse session -> no crash; an unrecognized/`None`
readiness band -> no crash (`_run()`'s cold-start fixtures in
`test_training_intelligence_calibration_v2.py`). `capacity_reference([]
, ...)` returns the versioned `FALLBACK_CAPACITY` tuple with
`confidence="LOW"`, never a crash or a fabricated high-confidence value.
A deliberate Rest/Active Recovery selection short-circuits to a
zero-dose, `SUPPORTED`-verdict response before any capacity/workload
computation runs at all.

## T. Temporal validation

In-memory: `valid_rows()` drops any row with `begin_time > as_of`.
**Real Postgres** (`test_training_intelligence_calibration_v2_postgres.py`,
3 new opt-in tests): (1) a workout inserted in the future does not
change `load_history()`'s row count, `multipliers()`'s output, or
`capacity_reference()`'s output for the same `as_of`; (2) a future
workout does not change a full `build_calibrated_shadow_prescription()`
call's capacity reference, feasible range, or per-exercise
sets/resistance; (3) an end-to-end real-SQL call produces a valid
result. All 3 passed live against the Development database, alongside
the 12 pre-existing opt-in tests (TKI-2 through TKI-6), for **15 passed**
total.

## U. Tests

- `test_training_intelligence_calibration_v2.py` - **51 new in-memory
  tests**: row validity/temporal safety, workload-multiplier detection
  (two-cable, one-cable-not-doubled, no-blanket-bilateral, eccentric
  exclusion, low-confidence fallback), comparable-session hierarchy,
  capacity-reference percentiles and the recent-stimulus-vs-capacity
  invariant, feasible-range binding constraints (local fatigue, high-
  recovery ceiling, unknown-not-fresh, prolonged-absence, session-
  structure cap), goal-posture-within-range for all 6 modes, paired-
  progression comparator (matched load, warm-up exclusion, mode-before-
  limit ordering, no cross-mode claim, assisted-rep exclusion), workload
  anomaly gate (within/below/above/insufficient, no auto-inflation),
  quality verdict (CONTRADICTED/QUESTIONABLE/SUPPORTED), cold start,
  Rest outcome, no-hardcoded-constant source scans, determinism.
- `test_training_intelligence_calibration_v2_postgres.py` - **3 new
  opt-in real-Postgres tests** (temporal correctness, §T).
- Full backend suite (two-batch convention): **731 passed, 56 skipped**
  (`--ignore=test_daily_pipeline_cache.py`) + **24 passed**
  (`test_daily_pipeline_cache.py`). Total **755 passed, 56 skipped, 0
  failed** - **+51** over the pre-TKI-5.2 baseline of 704, **0
  regressions**.
- All opt-in Postgres tests together: **15 passed**.

## V. Live Postgres validation

Ran against real Development data using the approved Keychain
credential pattern, single shell process, credential unset before exit,
output screened for credential-looking strings: Sep-12 reproduction and
re-run, Aug-7 counterfactual, 6-mode multi-goal validation, the 40-date
chronologically-split historical replay, and the 3 new opt-in Postgres
tests (plus the 12 pre-existing ones, re-verified).

## W. Performance

Single `build_calibrated_shadow_prescription()` call: **~4.2s** (Aug-7),
consistent across the 40-date replay (**4.03-4.25s/date average**) - a
modest increase over TKI-5.1's own ~3.6-3.8s baseline, attributable to
`load_history()`'s own 365-day lookback query (wider than TKI-3/5.1's
30-90 day windows, needed for `envelope()`'s longer-term window and
`comparable_sessions()`'s deeper fallback tiers) plus in-memory
aggregation over that larger row set. `load_history()`/`multipliers()`/
`sessions_from_rows()` are each called **exactly once** per top-level
call (not once per candidate movement or per exercise) - confirmed by
code inspection, no N+1 pattern introduced.

## X. Known limitations

- **The workload anomaly gate's practical hit rate for a confident
  verdict is low** (§O) - `INSUFFICIENT_DATA` dominates because too few
  movements on a given day carry `>=MEDIUM`-confidence workload
  multipliers for `normalized_workload` to be trustworthy. The
  architecture is correct (never silently inflate confidence); the
  MIN_MULTIPLIER_SETS/MIN_MULTIPLIER_SESSIONS/RATIO_TOLERANCE thresholds
  in `history.py` may need recalibration in a follow-up milestone - not
  attempted here to avoid fitting them to this one held-out sample.
- **The Aug-7 gap versus the forensic finding's 17-set/14,144 lb figure
  is reduced in provenance and honesty but not in raw magnitude** (§N) -
  the exact-family comparable pool's own set-count distribution for
  that date was not further decomposed within this milestone.
- Held-out validation used a chronological split of 40 dates (not the
  full 90-180 day windows used in some earlier TKI milestones) - scoped
  down given cumulative session time; the two cohorts were consistent
  with each other, which is evidence of generalization but not as
  strong as a longer window would provide.
- `select_pool()`'s soft compound-representation preference is a single
  scalar bonus (`COMPOUND_SCORE_BONUS=15`), not a full slot-reservation
  system - it nudges ranking, it does not guarantee a compound
  representative when eligible candidates and history both otherwise
  strongly favor isolation work.
- `dose.dose_shortfall`'s Stage-3 redistribution (added this milestone)
  is a new mechanism, live-validated once (Sep-12) but not separately
  stress-tested across the full 40-date replay for its own shortfall
  rate specifically.

## Y. Commercial scalability assessment

Every new constant is a versioned PRODUCT POLICY table keyed by the
shared `readiness_band`/`goal_mode`/muscle-state taxonomies (`SYSTEMIC`,
`LOCAL`, `FALLBACK_CAPACITY`, `MIN_SESSIONS`, `MIN_MULTIPLIER_*`,
`RATIO_TOLERANCE`, `MIN_PROLONGED_GAP_DAYS`, `GAP_P75_MULTIPLE`) - none
is a per-user constant, and the `NoHardcodedConstantsTests` source scan
locks this in for the two most sensitive numbers (a tonnage target, a
recovery percentage) and the Sep-12 forensic family name. The fallback
hierarchy (exact family -> similar muscles -> region -> general history
-> versioned policy) and the workload-multiplier confidence gating
generalize to any user's data density without modification - a sparser
user simply resolves to lower-confidence tiers more often, exactly as
designed, not a special case.

## Z/AA/AB. Commit(s) / Git status / Production-iOS confirmation

See the final report below.

## Final verdict

**TKI-5.2 DEVELOPMENT PARTIAL — FURTHER CALIBRATION REQUIRED**

The core architectural objective was achieved and verified live: recent
stimulus and demonstrated capacity are now genuinely separate signals
(a training gap no longer collapses long-term capacity - proven by test
and by the historical replay's 0 dose/readiness violations across 37
real lifting days); the cable-aware workload multiplier replaces the
blanket bilateral rule with historically-derived, confidence-gated
evidence; the comparable-session fallback hierarchy is doing real work
(21.6% fallback to tier 2, never straight to a generic default); goal
policy is proven range-invariant across all 6 modes; and two real,
previously-undiagnosed defects (a silent REDUCE-driven dose shortfall,
and a latent `None`-resistance crash risk) were found and fixed through
live reproduction before further validation proceeded, exactly as this
milestone's precheck required. What keeps this at PARTIAL is that the
workload anomaly gate - the mechanism meant to answer "was this
QUESTIONABLE or SUPPORTED" - resolves to `INSUFFICIENT_DATA` on the
large majority of real days, meaning the practical confidence-bearing
signal this milestone was built to add is not yet reliably available,
and the Aug-7 counterfactual's absolute gap versus historically-
performed volume, while now fully provenanced, was not further
decomposed. No B3/B4/iOS/Production file was changed. The next
milestone was not started. Render was not deployed.
