# Program Intelligence V2 Report — Daily Program Adaptation Engine

Shadow-only. Not connected to `/api/v1/todays-plan`; no mobile output
changes; no `TRAINING_INTELLIGENCE_MOBILE_ENABLED`-gated behavior
touched; Program Intelligence V1's schema/repository/service/tonal_
mapping/seed/admin endpoints are reused entirely unchanged.

## 1. Starting commit

`1875e84` (Program Intelligence V1) - verified clean, in sync with
`origin/develop` before any edit.

## 2. Ending commit

See "Commit" section below.

## 3. Files changed

New package `training_intelligence/programs/adaptation/`: `__init__.py`,
`taxonomy.py`, `models.py`, `context.py`, `progress.py`, `feasibility.py`,
`decision.py`, `tonal_resolution.py`, `time_budget.py`, `explanation.py`,
`snapshot.py`, `shadow.py`. New tests: `test_training_intelligence_
program_adaptation.py` (pure), `test_training_intelligence_program_
adaptation_postgres.py` (opt-in). Modified: `main.py` (one new admin-
only GET route). This report.

## 4. Schema changes

**None.** V2 reuses Program Intelligence V1's schema (`training_
programs`, `training_program_sessions`, `training_session_slots`,
`program_movement_mappings`, `user_training_programs`, `user_program_
session_state`) and the existing shadow-only `training_decision_
snapshots` table (TKI-5.3) unchanged - no migration was written or
applied this milestone.

## 5. Reused TKI components (no logic duplicated)

- `integrations.tonal.workout_prescription._latest_readiness` (systemic
  band), `integrations.tonal.muscle_readiness.calculate_muscle_
  readiness` (local states) - unchanged.
- `training_intelligence.stimulus.ledger.build_muscle_stimulus_ledger`
  (ACTUAL training - `direct_sets`/`secondary_set_equivalents`/
  `total_stimulus_sets`, its own terminology reused verbatim, never
  renamed to "effective sets").
- `training_intelligence.stimulus.taxonomy.to_canonical`/
  `CANONICAL_MUSCLES`.
- `training_intelligence.calibration.history.multipliers`/
  `sessions_from_rows`, `capacity.capacity_reference`/`feasible_
  capacity`/`choose_dose`, `progression.movement_capacity` - TKI-5.2.
- `training_intelligence.calibration.progression_v2.prescribe_v2` -
  TKI-5.4's tiered, mode-compatibility-aware progression engine.
- `training_intelligence.calibration.workload_v2.workload_sanity_v3`
  and `calibration.shadow.quality_verdict_v3` - TKI-5.4.
- `training_intelligence.calibration.snapshot` (`TABLE_NAME`,
  `ensure_table`, `_canonical_json`, `_normalize`) - TKI-5.3's
  reproducibility architecture, reused for a differently-shaped,
  differently-namespaced payload (section 14).
- `training_intelligence.programs.repository`/`service`/`tonal_mapping`
  - Program Intelligence V1, entirely unchanged.
- `training_intelligence.selection.shadow_selection.REST_FAMILY_NAME`.

## 6. New adaptation components

`context.py` (Phase 1 - `ProgramScheduleContext`, sequence-based
rotation, an optional non-persisted `enrollment_override` for shadow/
demo use); `progress.py` (Phase 2 - PROGRAM INTENT vs. ACTUAL TRAINING,
explicitly separated); `feasibility.py` (Phase 3 - local-readiness hard
gate, systemic-band classification, sequence cost, recency flag,
program-need score); `decision.py` (Phase 5-6 - the 8-step ordered
decision hierarchy, never a weighted score); `tonal_resolution.py`
(Phase 8-10 - slot resolution via V1's ranking engine, a waterfall dose
allocation, per-movement progression); `time_budget.py` (Phase 11 -
transparent duration estimator, never a 45-minute default);
`explanation.py` (Phase 7); `snapshot.py` (Phase 14).

## 7. Decision taxonomy

`KEEP`, `SHIFT`, `SUBSTITUTE`, `REDUCE`, `RECOVERY`, `REST`, `DEFER`
(`taxonomy.py`) - mutually exclusive, pinned by
`test_actions_are_distinct`.

## 8. Decision hierarchy

Implemented EXACTLY as an ordered sequence of gates, never a weighted
score (`decision.py`'s own module docstring restates all 8 steps): (1)
safety/hard eligibility (documented pass-through - no additional hard-
safety data source exists in this codebase beyond local readiness);
(2) local muscle readiness (HARD gate - a FATIGUED/SUPPRESSED required
muscle excludes a candidate regardless of systemic recovery); (3)
program sequence validity (enforced by construction - candidates are
only ever the nominal session plus its immediate rotation neighbors);
(4) program need (required, nonzero, before a fully-eligible nominal is
ever passed over); (5) systemic capacity (downgrades KEEP/SHIFT to
REDUCE, or forces RECOVERY/REST - never upgrades a hard-ineligible
candidate); (6) recency/monotony (informational only); (7) time
feasibility (applied after session/dose selection, Phase 11's
deterministic reduction order); (8) tie break (smallest sequence cost).

## 9. Explanation schema

`{"reason_codes": [...], "summary": "..."}` - structured codes (e.g.
`NOMINAL_SESSION_UPPER_B`, `CHEST_RECOVERING`, `LOWER_B_OUTSTANDING`,
`HIGH_SYSTEMIC_CAPACITY`, `SHIFT_MINIMAL_SEQUENCE_COST`) plus one
human-readable paragraph built directly from those codes - no medical/
physiological certainty language.

## 10. Time-budget architecture

`resolve_available_duration()`: request override > user enrollment
preference > program default > `None` (UNKNOWN) - **no global 45-
minute constant exists anywhere in this codebase's new code**, pinned
by `test_never_defaults_to_45`. `estimate_session_duration_minutes()`
returns a (low, high) range from named, versioned constants
(`SECONDS_PER_REP=3.0`, `TRANSITION_SECONDS_PER_EXERCISE=90.0`,
`UNILATERAL_SIDE_MULTIPLIER=2.0`) - disclosed approximations, not
exercise-science claims. `classify_fit()` -> FITS/TIGHT/EXCEEDS/
UNKNOWN. On EXCEEDS, `shadow._apply_time_reduction()` applies the
milestone's exact deterministic order: shrink non-required slots
toward 1 set, then remove non-required slots entirely, then (only
then) shrink required slots toward their own program `set_min` - a
required movement's PRESENCE is never removed, pinned by
`test_required_movement_is_never_fully_removed`.

## 11. Current live shadow example (2026-09-13, real Development data, non-persistent shadow enrollment context)

Recovery 94% (HIGH band), all upper-body target muscles READY, nominal
= Upper A (fresh rotation start). Decision: **KEEP** Upper A (fully
eligible nominal - Case C's rule: a fresh Lower A alternative with real
program need, 7.0, is correctly NOT taken, since the nominal itself has
no issue). Dose 20/20 delivered (feasible range 14-21). 6 resolved
exercises (Bench Press, Seated Row, Seated Overhead Press, Barbell
Straight Arm Pulldown, Hammer Curl, Reverse Grip Barbell Triceps
Extension) with real per-movement progression evidence (tiers 1, 1, 3,
4, 5 all represented honestly - two exercises correctly flagged LOW
confidence/REBUILD). Estimated volume 17,092 lb (cable-aware).
Workload: `WITHIN_PERSONAL_RANGE` (ratio 1.13, confidence HIGH).
Quality: `QUESTIONABLE` (one precise reason - weak progression evidence
for 2 of 6 movements). Time: 51.6-75.9 min estimated, `UNKNOWN` fit
(no duration was supplied - correctly not defaulted to 45).

## 12. KEEP example

Retrospective simulation, 2026-07-16: nominal = Lower A, HIGH systemic
band, all target muscles ready -> **KEEP** Lower A (verdict
QUESTIONABLE). Also the live example in section 11.

## 13. SHIFT example

Deterministic scenario test (Case B, both pure and opt-in Postgres):
nominal = Upper B, chest/shoulders RECOVERING (not blocking), Lower B's
muscles fully ready with genuine program need -> **SHIFT** to Lower B.
Retrospective simulation, 2026-06-18: nominal = Upper A, HIGH band ->
**SHIFT** to Lower A.

## 14. REDUCE example

Retrospective simulation, 2026-07-12: nominal = Upper A, all target
muscles locally eligible, systemic band MODERATE -> **REDUCE** (same
session, same movements, lower dose - never an arbitrary session swap
for a systemic-capacity reason). Deterministic scenario test (Case D)
confirms this exact rule in isolation.

## 15. High-recovery/local-fatigue example

Deterministic scenario test (Case I, both pure and opt-in Postgres):
readiness_band=`high`, nominal's required muscles chest/shoulders
FATIGUED -> the engine does **not** select the fatigued nominal despite
high systemic recovery; `HIGH_SYSTEMIC_CAPACITY` is still recorded in
the reason codes (it is real and disclosed) but never overrides the
hard local-readiness gate - pinned structurally (the gate never even
inspects `readiness_band`) and behaviorally (12 opt-in + 29 pure tests).

## 16. Historical/simulation replay statistics

**RETROSPECTIVE PROGRAM SIMULATION** - the user was never actually
enrolled in `hypertrophy_upper_lower_4d_v1` historically; every
`sequence_position` below is a synthetic, non-persistent simulation
(the rotation advances one step per replay date, driven only by what
the engine itself selected the prior date) run against REAL
Development WHOOP/readiness/Tonal history. Never written to
`user_training_programs`/`user_program_session_state`. 45 dates,
2-day cadence (2026-06-16 through 2026-09-12), zero errors:

| Action | % |
|---|---|
| REST | 37.8% |
| SUBSTITUTE | 22.2% |
| REDUCE | 26.7% |
| SHIFT | 8.9% |
| KEEP | 4.4% |

## 17. Sequence violations

**Zero** - candidates are bounded to the nominal session plus its
immediate rotation neighbors by construction (`context.py`'s own
`_rotation_order`); no date in the 45-day replay selected a session
outside that window (pinned additionally by the pure `test_case_h_*`
and opt-in `test_case_h_*` tests).

## 18. Readiness violations

**Zero** - no replay date's final `quality_verdict` was `CONTRADICTED`
for a fatigued/suppressed target muscle. The hard local-readiness gate
held on every one of the 45 dates.

## 19. Time-fit failures

Not directly comparable across the replay since `available_duration_
min` was never supplied for the retrospective simulation (matching
real-world "no duration known" behavior - `time_context` is absent
entirely for the 17/45 REST decisions, and `UNKNOWN` for the remaining
28, since no duration was ever supplied). Time-fit `EXCEEDS`/reduction
behavior is validated deterministically instead (section 10, `Time
ReductionMechanicsTests`) with synthetic data that has genuine
headroom, and live end-to-end (Case F) against real Development data.

## 20. Required-slot mapping failures

**Zero** across all 45 replay dates and all opt-in scenario tests -
`unresolved_required_slots` was empty every time, reflecting Program
Intelligence V1's own 100% required-slot mapping coverage (its own
report, section 8) carried forward unchanged.

## 21. Workload sanity results

Reused unchanged (TKI-5.4's `workload_sanity_v3`/`quality_verdict_v3`).
The live KEEP example (section 11): `WITHIN_PERSONAL_RANGE`, ratio
1.13, confidence HIGH, zero dominant gap causes. No replay date
produced a workload `CONTRADICTED` verdict on its own (a `CONTRADICTED`
verdict, when it occurs, is driven by the same fatigued/suppressed-
muscle or dose-envelope checks already covered in sections 18/20, not
by workload magnitude alone) - workload was used as a sanity CHECK
throughout, never as the dose target (the dose itself comes from
`capacity.choose_dose`, unchanged from TKI-5.2, before workload sanity
ever runs).

## 22. Performance/query count

One full `build_daily_program_adaptation()` call (live Development
data, KEEP outcome): **17 SQL queries total**, ~4.5s wall time
(dominated by this environment's per-connection round-trip latency,
consistent with every other live-DB measurement in this project's
history, not per-query cost). Breakdown: 1 program lookup + 4 for
`service.get_program_structure` (V1, unchanged) + 1 muscle-stimulus-
ledger load + 1 `load_history` + up to 6 (one per resolved-session
slot) `get_candidate_tonal_movements` calls + 1 batched movement-
profile lookup for all candidates' chosen movements together (never
one query per candidate movement). This is bounded by the CHOSEN
session's own slot count (typically 5-6), never by the full candidate
set's combined slot count and never per-exercise-alternate - no "dozens
of per-slot SQL calls" anywhere in the call graph.

## 23. Test totals

New: `test_training_intelligence_program_adaptation.py` (29 pure
tests - taxonomy, local-readiness gate, sequence cost, 7 decision-
hierarchy scenarios mirroring Cases A/B/C/D/E/H/I, time-budget
precedence/never-45/fit classification/duration scaling, and 3 direct
reduction-mechanics tests) - all pass. `test_training_intelligence_
program_adaptation_postgres.py` (12 opt-in Postgres tests - Cases A-J,
snapshot persist/replay, no-future-leakage) - all pass. **Full backend
suite: 889 passed (865 in-memory + 24 in the isolated cache batch), 101
skipped (opt-in Postgres), 0 failed** - zero regressions from the
pre-milestone (V1) baseline of 836/860/89.

## 24. Known limitations

1. **Neither seed program defines a Recovery/Rest session family** (V1
   seed data) - so whenever every candidate is locally blocked, the
   engine's only honest fallback is REST rather than a gentler
   RECOVERY, which the architecture supports (`decision.py` already
   checks for a matching `REST_FAMILY_NAME` session and would select
   it) but has nothing to select from in the current 2 demonstration
   programs. This is the main driver of REST's 37.8% share in the
   45-day replay - a real, disclosed program-content gap, not a V2
   decision-engine defect.
2. `SUBSTITUTE`'s concrete effect in this milestone is limited to
   labeling/reason-coding a recovering-muscle-driven session retention
   (keeping the nominal session rather than the specific per-slot
   alternate-movement substitution a fuller implementation might make)
   - the underlying `tonal_mapping` ranked alternates are already
   surfaced per resolved slot (`resolve_slots()`'s own `alternates`
   field) for a future milestone to act on.
3. `program_week`/phased-program support is schema-complete
   (`ProgramScheduleContext.program_week`) but untested against a real
   phased program, since neither seed program uses `training_program_
   weeks` rows (matches V1's own disclosed limitation).
4. `recency_flag`/monotony is informational only in this milestone
   (feeds `reason_codes`, never the action itself) - 4 duplicate-family
   streaks (>=3 consecutive same family) were observed in the 45-day
   replay, driven by the same REST-dominance pattern in item 1 (REST
   decisions don't advance the simulated rotation, so the same nominal
   session recurs) rather than a genuine repeated-selection defect.
5. `user_preference_min` (a real user's own `preferred_session_
   duration_min` enrollment column) is read by `resolve_available_
   duration()`'s signature but the shadow/demo context path (no real
   enrollment row) never supplies it - only exercised by the request-
   override and program-default paths this milestone; a real
   persistent enrollment would supply it identically to how V1's
   `enroll_user_in_program()` already stores it.

## 25. Explicit confirmations

- **No Production changes**: every new/modified file lives in the
  Development repo only.
- **No Render deployment**: all live verification used the Keychain-
  scoped Development `DATABASE_URL` procedure; nothing was deployed.
- **No iOS changes**: zero files in `HealthInteligence-dev` were read
  or modified.
- **No Today Plan integration**: nothing in `training_intelligence/
  programs/adaptation/` is imported by `todays_plan.py`, `todays_plan_
  store.py`, or any TKI-5.x/6/7 module; the full 889-test backend suite
  passing with the exact pre-milestone pass count plus only new tests
  confirms this.
- **No Tia integration**: Tia was never called, referenced as ground
  truth, or used to tune any output.
- **No external workout copying**: no new program content was added
  this milestone (V1's two original seed programs, unchanged); no
  Muscle & Strength or other external program text was read or stored.
- **No hard-coded 45-minute behavior**: confirmed by `test_never_
  defaults_to_45` and by every code path's `resolve_available_
  duration()` returning `None`/`"unknown"` in the complete absence of a
  request/user/program value (never a fallback constant).

## Commit

Explicit files staged (never `git add .`/`git add -A`): the 12 new
`training_intelligence/programs/adaptation/` files, `main.py`, the 2 new
test files, this report. Pushed to `origin/develop`, never merged to
`main`.

## Final verdict

**PROGRAM INTELLIGENCE V2 DEVELOPMENT PASS — DAILY ADAPTATION ENGINE VALIDATED**

All 19 acceptance criteria are met with live evidence: the program
schedule is the starting point for every recommendation (never an
invented session family); local readiness is a hard eligibility
constraint that WHOOP systemic recovery structurally cannot override
(proven both by code construction and by 12+29 passing tests); KEEP,
SHIFT, and REDUCE are each demonstrated with real and/or deterministic
examples; program sequence cannot be jumped illegally (enforced by
candidate construction, zero violations across 45 replay dates);
available duration is optional runtime data with no global 45-minute
assumption anywhere; required program slots resolved successfully on
every one of 45 replay dates (zero unresolved); personal history (not
sparse recent stimulus) drives capacity via TKI-5.2's own unchanged
`capacity_reference`; workload is a sanity check, never the dose
target; every decision is explainable via structured reason codes plus
a human-readable summary; frozen inputs replay byte/semantically
equivalent (opt-in snapshot test); Today Plan behavior is provably
unchanged (889 passed, 0 regressions); and no Production/Render/iOS
change occurred. Not proceeding to V3 automatically - awaiting review.
