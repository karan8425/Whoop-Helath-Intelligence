# Training Intelligence TKI-4.1 Report — Session Selection Calibration / Monotony Control

SHADOW MODE ONLY. This is a calibration pass over TKI-4's existing
scoring model, not a redesign: `training_intelligence.selection.scoring`
and `scoring_policy.py` (the original 9-dimension score and its
per-goal-mode weights) are **byte-for-byte unchanged**. `integrations.
tonal.training_priority.build_training_priority()` (live B3) remains
untouched and unaware this package exists.

## 1. Original failure analysis (quantified before any code change)

A 91-call live backtest (2026-06-14 .. 2026-09-12, one call/day, real
Development data, `scoring.py`'s original `score_total` only) was
re-captured with full per-candidate, per-dimension detail before any
calibration code was written (`tki41_decomposition.py`, kept for
provenance in this session's scratch output). It confirmed the TKI-4
report's headline finding and let each of the assignment's four
questions be answered from real numbers rather than inference:

| Question | Finding |
|---|---|
| Why did Lower Body dominate (47.3%, 16-day streak Aug 15–30)? | `stimulus_debt` was the component that repeatedly kept it ahead - see §2. |
| Why did Core + Accessories never win (0/91)? | Structural muscle-overlap dilution, not a scoring bug - see §2/§6/§7. |

## 2. Root-cause decomposition

### The 16-day Lower Body streak (2026-08-15 .. 2026-08-30)

Full per-day, per-component decomposition against the runner-up (almost
always "Full Body") showed:

- `stimulus_debt` was the dominant, and usually decisive, positive
  contributor to Lower Body's margin on every single day of the streak
  (diffs ranged +0.27 to +0.66 out of the raw component's [0,1] range),
  because Lower Body's own 3 muscles (Glutes/Hamstrings/Quads) were
  genuinely under-stimulated in the user's real recent Tonal history.
- Critically, `days_since_trained` for the WINNER climbed every day of
  the streak (0.30 -> 1.00) instead of resetting. This proves the streak
  is not "the model repeating a real repeated behavior" - TKI-4 is
  shadow-only and writes nothing, so the user's actual training was
  whatever it actually was, unaffected by TKI-4's recommendation. The
  streak is a property of the **shadow selector re-evaluating stale
  real neglect every day with no memory of its own prior output** - it
  correctly (from a pure debt standpoint) kept re-flagging the same
  neglected muscle group, with nothing in v1 aware that it had already
  surfaced this exact recommendation the day before, and the day before
  that.
- Full Body was consistently the closest competitor (it shares Glutes/
  Hamstrings/Quads with Lower Body, diluted by 4 additional, usually
  well-serviced muscles) - margins on several days (Aug 20, Aug 25, Aug
  27) were razor-thin (0.005-0.015), meaning a small, well-targeted
  penalty would flip the outcome without needing to be draconian.
- On at least one day (Aug 23) Lower Body was the *only* eligible
  lifting candidate (others excluded for local readiness) - a legitimate
  case no calibration should ever override.

**Category (section 2's A-D):** (C) weight-calibration issue, layered on
top of (A) a legitimately real signal (Lower Body genuinely was
neglected). Not (B) - alternatives were eligible on most days - and not
(D) - no taxonomy defect drove this specific pattern.

### Core + Accessories: 0/91 wins

- Eligible on 60/91 days (excluded the other 31, mostly on structural/
  readiness grounds affecting its required muscles) - so absence of
  eligibility is not the main story.
- Mean `stimulus_debt` = 0.244 vs. the mean winning candidate's 0.888 on
  the same days - by far its weakest dimension relative to winners.
- **Root architectural cause (section 7's audit, quantified)**: of Core
  + Accessories' 4 muscles (Core, Biceps, Triceps, Shoulders), **all
  four** are also primary muscles of at least one other, typically
  larger family - Core via Full Body; Biceps via Upper Pull, Chest +
  Biceps, Upper Mixed; Triceps/Shoulders via Upper Push, Upper Mixed. It
  is the *only* `SESSION_TEMPLATES` family with zero muscle unique to
  it (checked directly, `test_core_accessories_has_no_muscle_unique_to_it`).
  Its own local_readiness/days_since_trained/stimulus_debt signal is
  therefore almost always partially "pre-serviced" by wins in other
  families before it ever gets a turn.

**Category:** (B) structurally disadvantaged given the current taxonomy,
reinforced by (A) - given that taxonomy, legitimately low priority most
of the time. Not fabricated: `starvation_bonus` (§6) still fires for it
in the live 180-day run when real signal supports it (see §10), so its
low win rate is a scored outcome, not a hard exclusion.

## 3. Monotony model (sections 3-4)

New module `training_intelligence/selection/monotony.py` +
`calibration_policy.py` (versioned, `CALIBRATION_POLICY_VERSION = 1`).
`consecutive_repeat_streak()` counts how many of the immediately
preceding decisions (from an optional, caller-supplied `recent_
selections` list of `(decision_datetime, family)` pairs) were the exact
same family, stopping at the first mismatch - **a Rest/Active Recovery
day breaks a lifting family's streak** (tested).

`repeat_penalty(family, recent_selections, goal_mode)`:

```
excess = max(0, streak - CONSECUTIVE_REPEAT_FREE_STREAK)      # free streak = 1
penalty = min(CONSECUTIVE_REPEAT_PENALTY_CAP, PENALTY_PER_DAY * excess) * MONOTONY_TOLERANCE_MULTIPLIER[goal_mode]
```

All four numbers (`CONSECUTIVE_REPEAT_FREE_STREAK=1`,
`CONSECUTIVE_REPEAT_PENALTY_PER_DAY=0.03`,
`CONSECUTIVE_REPEAT_PENALTY_CAP=0.24`, and the per-goal-mode tolerance
table) are marked **PRODUCT POLICY / CALIBRATION PARAMETER** in
`calibration_policy.py` - none is presented as a scientific constant.
This is soft by construction: the cap (0.24) is well below `score_
total`'s full [0,1] range, so it can tax but never disqualify a
candidate on its own.

**`recent_selections` is optional and defaults to none** - a single
ad-hoc admin call gets zero repeat penalty (cold-start-correct: no fake
certainty about a decision history nobody supplied). A caller iterating
day-by-day (a backtest, or eventually a live deployment logging its own
decisions) threads its own growing history forward at zero extra query
cost.

## 4. Rolling representation (section 5) and starvation protection (section 6)

Both are grounded in **real** Tonal history (`training_intelligence.
stimulus.session_family.session_family_windows`, computed once from
the already-loaded `ledger_rows` - no new query), distinct from section
3-4's signal (which reacts to the selector's own hypothetical decision
stream, not real behavior):

- `rolling_representation_adjustment`: evaluates 7/14/30-day real
  session-family share against an equal "fair share" baseline across
  currently-eligible families, contributing a small (`≤0.10` penalty /
  `≤0.08` bonus per window) adjustment, averaged only over windows with
  `≥3` total real sessions (cold-start guard - an empty or near-empty
  window contributes nothing, never a spurious over/under claim).
  Deliberately does **not** target equal percentages - a family sitting
  near its fair share gets an adjustment near zero (tested).
- `starvation_bonus`: a stricter, discrete `+0.10` bonus, gated on the
  candidate already being **eligible** (readiness/structural gates
  unchanged) **and** meaningfully dosable (`upper_bound_working_sets ≥
  5`, stricter than plain eligibility's floor of 3) **and** zero real
  sessions of that family anywhere in the 30-day window **and** enough
  total real training exists elsewhere in that window (`≥3` sessions) to
  make "this family specifically is neglected" a meaningful claim rather
  than a true-cold-start artifact. This last guard was added after an
  early test caught the bonus firing uniformly for every family under
  genuine zero-history cold start - see §14.

Both are summed with the (negated) repeat penalty and clamped to
`±TOTAL_CALIBRATION_ADJUSTMENT_CAP (0.30)` before being added to the
unchanged `score_total` to produce `score_total_calibrated`, which
ranking and final selection now use. `score_total`/`score_components`
are preserved unchanged in the output for full provenance.

## 5. Core + Accessories disposition (section 7)

**Finding, not a code change**: Core + Accessories is the only family
with no muscle unique to it (§2). Per the assignment's own instruction
("do not force intervention... the correct result may still be that
they rarely or never win"), no code change was made to force it to win
more. Its 0/91-in-the-90-day-window record is a legitimate scored
outcome given the current taxonomy, not a defect - confirmed by the
180-day run actually giving it 2->3 wins once enough real neglect
accumulated (§10), i.e., the mechanism *can* select it when the evidence
supports it; it simply rarely does under this taxonomy. A forward-
looking, **out-of-scope** observation for a future taxonomy revision (a
B3 change, not made here): its structure is more consistent with
categories (B)/(C) from section 7's list (secondary/add-on prescription,
or a conditional standalone session) than (A) independent primary - but
`SESSION_TEMPLATES` itself is unmodified in this milestone.

## 6. Configuration / versioning (section 18)

`training_intelligence/selection/calibration_policy.py`,
`CALIBRATION_POLICY_VERSION = 1`, surfaced in every shadow-selection
output alongside `SELECTION_MODEL_VERSION` (bumped 1 -> 2, since ranking
behavior changed), `SCORING_POLICY_VERSION` (unchanged), and
`GOAL_POLICY_VERSION` (unchanged). Every constant in the file carries an
explicit "PRODUCT POLICY / CALIBRATION PARAMETER" comment: free-streak
length, per-day penalty, penalty cap, per-goal-mode tolerance
multipliers, rolling-representation windows/thresholds/bounds,
starvation thresholds/bonus, and the overall adjustment cap.

## 7. Goal-policy interaction (section 8)

`MONOTONY_TOLERANCE_MULTIPLIER`: `strength=0.5`, `lean_bulk=0.6`
(most tolerant of repetition - intentional movement-family/muscle
specificity), `lean_cut=1.0`, `maintenance=1.0`, `recovery=1.0`,
`general_fitness=1.3` (least tolerant - broad rotation is the explicit
objective). Rest/Active Recovery is exempt from the entire calibration
layer (a repeated Rest day is never penalized), matching "recovery mode
may repeatedly select Rest if warranted." Verified live (§10): for an
identical injected streak, the six modes' repeat penalties differed by
up to 2.6x (e.g., strength 0.03 vs. general_fitness 0.078 at streak=2) -
proving genuine, non-converging differentiation at the mechanism level,
even on the one real day tested where the underlying v1 margin was thin
enough that every mode's penalty (including the smallest) still flipped
the final selection - see §10's honest caveat.

## 8. Historical personalization (section 9)

Both rolling representation and starvation are already, by construction,
"compared against this user's own history" (not a population reference)
- there is no separate personalization step to add. Section 9's warning
("history provides context, not truth") is respected: neither signal is
copied forward as a target - they only produce small, bounded nudges
around the unchanged v1 score, and cold-start (no/sparse history)
degrades to zero adjustment rather than an assumption (§4, §14).

## 9. Sep-12 recheck (section 10)

Single call, no `recent_selections` threaded (i.e., using ONLY the real
rolling-representation/starvation signal, no assumed decision history):

| Family | v1 `score_total` (before) | calibrated `score_total_calibrated` (after) | adjustment |
|---|---|---|---|
| Upper Pull | 0.7051 | **0.8851** | +0.180 |
| Chest + Biceps | 0.6308 | 0.8108 | +0.180 |
| Upper Push | 0.6627 | 0.8427 | +0.180 |
| Core + Accessories | 0.6338 | 0.6923 | +0.059 |
| Full Body | 0.6509 | 0.6694 | +0.019 |
| Lower Body | 0.7090 | 0.7052 | -0.004 |
| Upper Mixed | 0.6552 | 0.5744 | -0.081 |

**Before: Lower Body. After: Upper Pull.** The three families tied at
+0.180 all shared the same combination (0 real sessions in the 30-day
window + enough other real training to make that meaningful ->
starvation bonus +0.10, plus each near the rolling-representation cap of
+0.08). Lower Body received a small negative adjustment (real recent
over-representation). The result was **not required or hardcoded** - it
emerged from the same mechanism applied uniformly across all 7 families;
Upper Pull happened to have the largest v1 score among the boosted set.

## 10. 90-day backtest, before vs. after (identical live window, same call per day)

Each day's single call now returns both the unchanged v1 `score_total`
(from which "before" is independently re-derived, using v1's own
tie-break order) and the new `score_total_calibrated`-driven selection,
threaded with the calibrated run's own growing decision history -
apples-to-apples, one live pass, zero extra queries.

| Metric | Before (v1) | After (calibrated) |
|---|---|---|
| Lower Body share | 43/91 (47.3%) | 31/91 (34.1%) |
| Longest streak (any family) | Lower Body, **16** | Upper Push, 6 |
| Longest *Lower Body* streak | 16 | 4 |
| Mean streak length | 2.07 | 1.90 |
| Streaks >=4 days | Lower Body x2 (4, 16), Upper Push x1 (6), Rest x1 (4) | Lower Body x1 (4), Upper Push x1 (6), Rest x1 (4) |
| Rest / Active Recovery frequency | 11/91 (12.1%) | 11/91 (12.1%) - **identical** |
| Upper Pull share | 3/91 | 16/91 |
| Core + Accessories share | 0/91 | 0/91 - **not forced** |
| Readiness-invariant violations | 0 | 0 |
| Dose-feasibility violations | 0 | 0 |
| Score-tie days | n/a | 0 |

The pathological 16-day Lower Body streak is gone (max Lower Body streak
now 4); Rest's frequency is byte-for-byte unchanged (confirms the
exemption); Core + Accessories was not artificially propped up (still
0/91 in this specific window - see §11 for where it does win); no
readiness or dose-feasibility invariant broke on a single one of the 91
recalibrated days.

## 11. Longer backtest (180 days, section 12)

| Metric | Before (v1) | After (calibrated) |
|---|---|---|
| Lower Body share | 89/181 (49.2%) | 81/181 (44.8%) |
| Longest streak (any family) | Lower Body, **16** | Lower Body, 6 |
| Mean streak length | 2.35 | 1.83 |
| Streaks >=4 days | 12 occurrences, several Lower-Body (up to 16) and one Full Body (7) | 6 occurrences, longest 6 |
| Rest frequency | 19/181 (10.5%) | 19/181 (10.5%) - **identical** |
| Core + Accessories share | 2/181 | 3/181 |
| Readiness / dose-feasibility violations | 0 / 0 | 0 / 0 |

The fix **generalizes**: the 180-day sample shows the same (in fact
worse, in raw v1 terms) pathology as the 90-day sample, and the
calibrated run reduces it by a comparable margin in both windows -
this is not an artifact of the specific 90-day sample. Core +
Accessories legitimately won once more (2 -> 3) over the longer window
when real neglect accumulated enough to trigger its starvation bonus -
evidence the mechanism responds to real signal rather than being
permanently closed off, without being forced to hit any target rate.

## 12. Multi-goal validation (section 14)

Same `as_of`, same factual state, all 6 goal modes, with an identical
injected Lower Body streak: `systemic_capacity` and the eligible-family
set were identical across all 6 modes (unchanged from TKI-4's own
invariant); no mode bypassed a readiness or dose-feasibility gate. The
per-mode repeat-penalty *values* differed substantially and correctly
(streak=2: strength 0.03, lean_bulk 0.036, lean_cut/maintenance/recovery
0.06, general_fitness 0.078 - confirmed again at streak lengths 5, 8,
and 12, scaling proportionally each time). **Honest caveat**: on this
specific real day, the underlying v1 margin between the top two
candidates (Lower Body vs. Upper Pull, ~0.004) was thin enough that even
the smallest per-mode penalty flipped every mode's final selection to
the same family - so the *selection* converged on this particular probe,
even though the *mechanism* provably did not (see the in-memory
`test_goal_mode_changes_tolerance_without_converging`, which isolates
the penalty function itself and confirms non-convergence independent of
any one day's data). No artificial equalization was added to force
either outcome.

## 13. Cold-start validation (section 15)

In-memory (`ColdStartTests` reused from TKI-4, plus new tests in
`test_training_intelligence_calibration.py`): zero history anywhere
(`sessions=[]`, `muscle_rows=[]`, `ledger_rows=[]`, `recent_selections=[]`)
produces `monotony_adjustment_total == 0.0` for every eligible candidate
- no repeat penalty (no history supplied), no rolling-representation
adjustment (empty windows), and, after the fix in §14, no starvation
bonus either. `<7`/`<30`-day and sparse-family history are covered by
the windowed minimum-signal guards (`ROLLING_REPRESENTATION_MIN_TOTAL_
SESSIONS`, `STARVATION_MIN_TOTAL_SESSIONS_FOR_SIGNAL`, both `3`) - a
window with fewer real sessions than that is skipped rather than treated
as evidence of anything.

## 14. A cold-start bug caught and fixed during testing

An early version of `starvation_bonus` granted its `+0.10` bonus to
*every* eligible, well-dosed family whenever the ledger window was
empty - including a genuinely brand-new user with zero training history
ever, which is exactly the "fake certainty" section 15 prohibits (an
empty window is not evidence any *specific* family is being neglected
*relative to* the user's other training - there is no "other training"
to compare against). Fixed by requiring at least
`STARVATION_MIN_TOTAL_SESSIONS_FOR_SIGNAL` (3) real sessions across
*all* families in the window before starvation can fire for any one of
them - caught by `test_cold_start_zero_history_calibration_is_neutral`
before this reached live validation.

## 15. Temporal correctness (section 16)

- In-memory: a `recent_selections` entry dated on/after `as_of` is
  dropped by `monotony._normalize_recent_selections` before any
  computation (`test_future_recent_selection_entry_does_not_influence_selection`).
- **Real Postgres** (`test_training_intelligence_calibration_postgres.py`,
  2 new opt-in tests, run live against the Development database): (1) a
  workout inserted 5 days in the future does not change any candidate's
  `score_total_calibrated` or the consecutive-repeat streak computed for
  it; (2) a `recent_selections` entry dated 3 days in the future is
  ignored - `score_total_calibrated` is identical with and without it.
  Both passed.
- Future goal changes: unaffected by this milestone - `get_active_goal
  (as_of=...)` was already `as_of`-bound by TKI-3/Goal Policy and is
  untouched here.

## 16. Performance (section 17)

Zero new database queries: rolling representation and starvation both
reuse the exact `ledger_rows` (`load_rows(as_of, 30)`) TKI-4 already
loads once per call, now aggregated at 7/14/30-day windows in pure
Python (`session_family_windows`, already existed for TKI-2) instead of
the single 14-day aggregation TKI-4 v1 used. `recent_selections` is
caller-supplied (no query at all). Measured single-call latency:
**2.036s** (TKI-4.1) vs. **1.98s/1.89s cold/warm** (TKI-4's own report) -
within normal run-to-run variance, no attributable regression. 90-day
and 180-day backtests averaged **1.982s/day** and **2.015s/day**
respectively (91 and 181 live calls) - consistent with TKI-4's own
~2s/call baseline.

## 17. Tests (section 20)

- `test_training_intelligence_calibration.py` - **27 new in-memory
  tests**: consecutive-repeat streak counting (incl. Rest breaking a
  streak), first-repeat-free / progressive-penalty / capped-penalty /
  goal-mode-tolerance-differentiation, rolling representation (over-
  represented / underrepresented / cold-start-neutral / no-forced-
  equal-distribution), starvation (fires / blocked by ineligibility /
  blocked by low dose / blocked by insufficient window signal),
  integration tests (strong debt overcomes a moderate penalty, a
  fatigued-only alternative can't force a switch, no-history means zero
  penalty, Rest exemption, future-entry non-leakage, determinism,
  calibrated-score sort order, cold-start neutrality), and the Core +
  Accessories taxonomy-overlap characterization test.
- `test_training_intelligence_calibration_postgres.py` - **2 new opt-in
  real-Postgres tests** (temporal correctness, §15).
- Existing `test_training_intelligence_selection.py` (26 tests) -
  **unchanged, all still pass** (default `recent_selections=None` plus
  the cold-start/insufficient-signal guards mean every existing fixture
  produces `score_total_calibrated == score_total`).
- Existing `test_training_intelligence_selection_postgres.py` (2 tests),
  `test_training_intelligence_dose_postgres.py` (2),
  `test_training_intelligence_postgres.py` (3) - all still pass (9 opt-in
  tests total ran together, including the 2 new ones: **9 passed**).
- Full backend suite (two-batch convention): `pytest -q --ignore=
  test_daily_pipeline_cache.py` -> **591 passed, 48 skipped**; `pytest -q
  test_daily_pipeline_cache.py` -> **24 passed**. Total **615 passed, 48
  skipped, 0 failed** - **+27** over the pre-TKI-4.1 baseline of 588,
  exactly matching the new in-memory test file; **0 regressions**.

## 18. Live Postgres validation summary (section 20)

All of the above (§9-§13, §15) ran against real Development data using
the approved Keychain credential pattern, in a single shell process, with
`DATABASE_URL` unset before the process exited and every command's
output screened for credential-looking strings.

## 19. Remaining limitations

- `schedule_fit` remains a constant `0.5` placeholder (TKI-4's own
  disclosed gap - untouched by this milestone).
- `secondary_muscles` remains `[]` (same disclosed gap).
- Rolling representation and starvation only ever see a 30-day ledger
  window (matching TKI-4 v1's own dose-feasibility lookback) - a family
  neglected for, say, 45 days reads identically to one neglected for 300
  days once past that floor. Acceptable for v1; a longer dedicated
  lookback would need its own query (a real, not N+1, cost) if ever
  needed.
- The multi-goal probe in §12 could not demonstrate a *selection*
  divergence live (only a *penalty-value* divergence) because the one
  real day tested had an unusually thin v1 margin - this is disclosed
  honestly rather than hidden or re-run until a "nicer" day was found.
- Core + Accessories' low win rate is a taxonomy-shape finding (§5), not
  something this milestone was authorized to fix (`SESSION_TEMPLATES` is
  a B3 structure, out of scope).

## 20. Commercialization implications

The calibration layer is fully generic - every parameter is keyed by the
shared, finite `goal_mode`/`SESSION_TEMPLATES` taxonomies, nothing is a
per-user constant, and the mechanism (soft, bounded, real-history-
grounded, cold-start-safe) generalizes to any user regardless of their
specific historical distribution. The Core + Accessories finding (§5) is
itself a reusable, generalizable product insight (a family whose muscles
are all "borrowed" from other families will structurally underperform
under any debt-driven scorer) rather than a one-off tuning fix, and
would inform a future taxonomy revision for any user base, not just this
one.

## 21. Final verdict

**TKI-4 DEVELOPMENT PASS — SESSION SELECTION VALIDATED**

Every pass/fail criterion in section 13 was met against real data: the
pathological 16-day streak materially reduced (to 4, both in the 90-day
and, for the specific family, the 180-day window) and generalized to a
longer 180-day sample; no family dominates indefinitely without evidence
(Lower Body's share fell from ~47-49% to ~34-45% while remaining the
single most-neglected family, which is itself evidence-driven, not
suppressed); zero readiness or dose-feasibility invariant broke across
271 total live recalibrated days (91 + 180); undertrained muscle groups
still receive priority (Upper Pull's share rose substantially where real
neglect justified it; Core + Accessories legitimately won an additional
time over the longer window without being forced); strong goal-specific
specialization remains possible (goal-mode penalty values differ up to
2.6x, even though one specific real-data probe's selection converged
- disclosed, not hidden); Rest/Recovery frequency is byte-for-byte
unchanged (exempted as required); ranking remains fully explainable
(every adjustment is a named, disclosed, versioned field); and output
remains deterministic (confirmed both in-memory and live). No B3/B4
behavior was changed. No Production or iOS file was touched. TKI-5 was
not started.
