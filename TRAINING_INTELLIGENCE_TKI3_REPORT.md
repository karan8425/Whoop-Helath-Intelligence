# Training Intelligence TKI-3 Report - Personalized Training Dose Model V1

SHADOW MODE ONLY. Nothing described here changes today's selected workout, TrainingDetail
prescription, or any B3/B4 production recommendation output.

## 0. A necessary correction to how this milestone was scoped

Before writing any new code, the audit (required by section 1) found that
**`integrations/tonal/training_dose.py` ("Training-B2") is already a fully-built, live,
production personalized-dose engine**, called directly from
`integrations/tonal/workout_prescription.py` (line ~1899) to compute the actual dose behind
today's real B3 recommendation. It already does almost everything this milestone describes:
personal historical baselines (session-level and muscle-level, 7/14/30/90-day rolling
windows), a tiered comparable-session fallback hierarchy, a WHOOP systemic-capacity
multiplier, a recent-load modifier, a hard per-muscle readiness budget that never resurrects
a fatigued/suppressed muscle, robust median/percentile statistics (not mean), and a
confidence score - all deterministic, all already tested and running.

Separately, `integrations/tonal/progressive_overload.py`'s `trajectory()` already classifies
recent comparable-session performance as IMPROVING/STABLE/DECLINING/INSUFFICIENT_DATA from
volume trend - also already live.

Given the assignment's own explicit instruction ("Reuse validated components rather than
creating parallel definitions") and the standing instruction not to touch B3/B4 output,
building a second, parallel dose engine from scratch would have directly violated both.
**TKI-3, as actually implemented, is a thin, additive, shadow-only composition layer that
calls B2 and progressive_overload directly** and adds only what neither already produces:
a descriptive personal tolerance band, a `dose_classification` label, a cross-check against
the TKI-2 canonical (Calves-aware) ledger, goal-context framing, and one unified,
provenance-rich shadow report. Nothing in `integrations/tonal/training_dose.py`,
`workout_prescription.py`, or `progressive_overload.py` was modified.

## 1. Architecture

```
training_intelligence/dose/
    tolerance_bands.py  - classify_band(value, session_count, p25, p50, p75) ->
                          below_personal_range / within_personal_range /
                          upper_personal_range / insufficient_evidence.
                          Reuses B2's own MIN_HIGH_QUALITY_COMPARABLE_SESSIONS (3)
                          as the minimum-evidence threshold - not a second number.
    shadow_dose.py      - build_shadow_dose(as_of, session_family, sessions=None,
                          muscle_rows=None, ledger_rows=None) -> the full shadow
                          object. Calls, unmodified:
                            - integrations.tonal.training_dose.compute_dose_target
                              (the real dose)
                            - integrations.tonal.training_dose.select_comparable_sessions
                              (the same comparable pool B2 used internally, retrieved
                              again through its own public selector for the band/
                              trajectory calculations)
                            - integrations.tonal.progressive_overload.trajectory
                              (performance classification)
                            - integrations.tonal.muscle_readiness.calculate_muscle_readiness
                            - integrations.tonal.workout_prescription._latest_readiness
                            - goals.get_active_goal
                            - training_intelligence.stimulus.ledger (TKI-2, cross-check only)
```

New admin-diagnostic endpoint, matching the TKI-2 convention exactly:
`GET /training-intelligence/dose?session_family=<name>&as_of=<iso>` (`require_admin`,
plain path, not `/api/v1/...`). `session_family` must be supplied by the caller - this
endpoint does not select which session to train (TKI-4, not built).

Separation of concerns (section 2's A-E), each traceable to a distinct real component:

| | source |
|---|---|
| A. systemic capacity | B2's WHOOP capacity multiplier, reused |
| B. local muscle capacity | B2's per-muscle budget (B1 readiness-gated), reused |
| C. historical tolerated dose | B2's comparable-session baseline, reused |
| D. goal-required stimulus | new: `goal_context`, read-only, TKI-2's own convention |
| E. recent performance trajectory | `progressive_overload.trajectory`, reused |

These are exposed as separate keys in the shadow object, never collapsed into one opaque
score - `dose_classification` is a label on top of B2's own already-computed multiplier, not
a replacement for any of the five.

## 2. Personal baseline methodology

Entirely B2's existing methodology, reused: `compute_session_baselines`/
`compute_muscle_baselines` roll up qualifying Tonal workouts over 7/14/30/90-day windows
using median and percentile (25/50/75) statistics - never a mean, so a handful of unusually
large or small sessions cannot dominate the baseline (validated directly in
`test_extreme_outlier_session_does_not_dominate_median_baseline`). `filter_qualifying_sessions`
excludes structurally unusable or explicitly-overridden sessions before any statistic is
computed.

TKI-3 adds one genuinely new statistic B2 didn't expose: percentiles of **working-set count**
specifically (B2's own `compute_comparable_baseline` only returns volume percentiles) -
computed via B2's own `_percentile` helper (imported, not reimplemented) over the same
comparable-session list B2 already selected.

## 3. Fallback hierarchy

Entirely B2's existing four-tier hierarchy, reused and surfaced:
1. `comparable_sessions_30_90d` - >=3 high-similarity (workout_type/region match) sessions.
2. `broader_muscle_region_sessions` - >=2 broader-region-matching sessions.
3. `global_qualifying_session_baseline` - any qualifying session, no similarity filter.
4. `conservative_configured_fallback` - no history at all.

TKI-3's `fallbacks_used` list surfaces which tier applied (session-level) and separately
flags per-target-muscle when a muscle had no attributable history at all (falling to B2's
`CONSERVATIVE_MUSCLE_SET_BASELINE`), plus whether the working-set tolerance band itself had
to declare `insufficient_evidence`.

## 4. Real-data findings

**Live-data finding, not anticipated going in**: across the full 90-day backtest (60 samples,
2 session families x 30 dates), **every single sample used tier 2 (`broader_muscle_region_
sessions`) - tier 1 (`comparable_sessions_30_90d`) never triggered once.** B2's similarity
scorer matches largely on `workout_type` text (e.g. "Upper Pull" needs to substantially
overlap the session's own `workout_type` string); TKI-2's own audit already found most of
this user's real workouts are logged as `workout_type: "Custom"`, not a family-specific
label. This is a real, systemic property of how this user logs workouts, not a bug -
reported here as a calibration input for whoever tunes B2's similarity scoring next, not
fixed by this milestone (out of scope - B2 is not modified).

**Direct, material consequence**: because the fallback pool is always the broader,
larger-session-inclusive region pool, **the working-set personal band read
`below_personal_range` in 100% of the 60 backtest samples**, including the real Sep-12
Upper Pull session. This does not mean every real session under-trained - it means the
*comparison denominator* (a pool that includes bigger multi-region sessions) is structurally
larger than what a narrow single-region session would ever produce. This is disclosed
explicitly rather than presented as "this user is consistently undertraining."

## 5. Session-family dose distributions (from the 90-day backtest, `broader_muscle_region_sessions` pool)

| | Upper Pull | Lower Body |
|---|---|---|
| comparable pool | same broader-region pool both times (see section 4) | same |
| median working sets (pool) | ~19 | (see raw backtest data in scratch output; not separately re-tabulated here beyond the Sep-12 anchor value below, per report size) |
| p25 / p75 working sets (pool, Sep-12 anchor) | 15.5 / 25.0 | - |
| working_sets actually recommended, range across 30 samples | 0-14 | 0-14 (session-dependent) |
| dose_classification distribution | reduced/normal/upper_normal, tracking WHOOP recovery and recent load sensibly (e.g. recovery>=80 -> mostly `upper_normal`/`normal`; recovery<45 -> mostly `reduced`, several exact 0s) | tracks the same way |

## 6. Muscle-specific dose distributions

Exposed per-target-muscle via `local_readiness[muscle].budget_effective_sets` (B2's own
`muscle_budget()` output, reused) alongside the TKI-2 canonical ledger's 7/14/30-day
`total_stimulus_sets` for the same muscle (cross-check, not a replacement). On the real
Sep-12 Upper Pull query: Back budget 4.33 effective sets (READY), Biceps budget 4.85
(READY) - summing to ~9.18, matching the actual capped `working_sets: 9` almost exactly,
confirming the per-muscle budget - not the session-level comparable multiplier - was the
binding constraint that day (`dose_limited_by: muscle_readiness_budget`).

## 7. Readiness modifiers

**Systemic (WHOOP)**: B2's `whoop_capacity_multiplier`, reused unchanged - a continuous
value inside each band's configured range (e.g. high: 1.05-1.15), never a flat per-band
constant, never "high recovery -> add arbitrary sets."

**Local (per-muscle)**: B2's `muscle_budget()`, reused unchanged - SUPPRESSED/FATIGUED force
budget to exactly 0.0 regardless of systemic readiness (validated directly:
`test_high_systemic_fatigued_muscle_forced_to_zero_budget_and_zero_dose`, and confirmed
against real data on 2026-08-22: recovery 47 "moderate", Back `SUPPRESSED` -> budget 0.0 ->
`working_sets: 0` for the whole session, correctly, not an anomaly). Unrecognized/absent
readiness ("Unknown") gets a conservative half-budget, never the full FRESH-equivalent
allowance (validated: `test_unknown_local_readiness_is_not_silently_treated_as_fresh`).

## 8. Performance-response logic

`integrations.tonal.progressive_overload.trajectory()`, reused unchanged, over the same
comparable-session list used for the dose baseline. Classifies IMPROVING (>=8% median-volume
increase, recent 2 vs. older sessions), DECLINING (>=8% decrease), STABLE, or
INSUFFICIENT_DATA (<3 sessions). On the real Sep-12 query: STABLE over 27 comparable
sessions. Across the 90-day backtest, trajectory varied sensibly session-to-session (both
IMPROVING and STABLE observed), never crashed on sparse history.

## 9. Sep 12 dose diagnostic

Anchored to the same real `whoop_daily_metrics` row as TKI-2 (`recovery_score = 94.0`,
`source_updated_at = 2026-09-12T08:10:33 UTC`), queried for `session_family = "Upper Pull"`.

| | |
|---|---|
| Recommended `working_sets` (this shadow query) | **9** - matches the actual historical recommendation exactly |
| Comparable pool | `broader_muscle_region_sessions`, 27 sessions, medium similarity confidence |
| Pool median / p25 / p75 working sets | 19.0 / 15.5 / 25.0 |
| **Personal band verdict** | **BELOW PERSONAL RANGE** (9 < 15.5) |
| Local readiness | Back: READY (budget 4.33); Biceps: READY (budget 4.85) |
| `dose_limited_by` | `muscle_readiness_budget` - the per-muscle budget (~9.18 total), not the comparable-session multiplier, was the binding constraint |
| Performance trajectory | STABLE (27 comparable sessions) |
| `dose_classification` | `upper_normal` (WHOOP capacity multiplier 1.12 + a modest recent-load increase - the *modifiers* pushed toward more, even though the *absolute* result still reads below the broader-pool range) |
| Confidence | MEDIUM |

**Answer to the required question**: if Upper Pull had been the correct session family, 9
working sets was **BELOW PERSONAL RANGE** relative to this user's broader-muscle-region
comparable history - but with an essential caveat the raw label alone doesn't convey: the
comparison pool is coarser than an Upper-Pull-specific one (see section 4 - B2 never found
>=3 highly-similar Upper Pull sessions in 30-90 days for this user), and the actual
determining factor that day was the local Back/Biceps readiness budget independently
arriving at ~9 sets, not a session-level shortfall. Both things are true at once and are
reported together, not collapsed into a single verdict.

**TKI-2's conclusion is explicitly preserved, not re-litigated**: TKI-2 found Sep 12's
session-family choice (Upper Pull itself) QUESTIONABLE, given quads/hamstrings' comparative
14d/30d stimulus deficit. TKI-3 says nothing about whether Upper Pull was the right session -
it only evaluates how much, given that Upper Pull was the session being asked about. This
distinction is carried in the shadow object's own `session_selection_note` field.

## 10. Historical backtest (90 days, "Upper Pull" and "Lower Body", 60 samples)

- **0 anomalies** detected: no fatigued/suppressed muscle ever received a nonzero budget, no
  dose exceeded 1.5x its own comparable p75 without the muscle-budget cap explaining it, no
  negative doses.
- **Fallback frequency**: 60/60 samples used `broader_muscle_region_sessions` (see section 4
  - a real, disclosed finding, not a defect in this milestone's own logic).
  `insufficient_evidence` (personal band) never triggered - every sample had >=3 comparable
  sessions, just always from the broader tier.
  `personal_band == below_personal_range` in 60/60 samples (see section 4's explanation -
  structural, not a training-adequacy claim).
- **Dose stability**: several dates correctly produced `working_sets: 0` (e.g. 2026-08-22,
  recovery 47 "moderate", Back `SUPPRESSED`) - spot-checked live and confirmed to be the
  local-fatigue invariant working correctly, not an anomaly: moderate systemic recovery does
  not override a suppressed muscle.
- `dose_classification` tracked WHOOP recovery and recent load sensibly across the 90 days
  (high recovery days skewed `upper_normal`/`normal`; low/very_low recovery days skewed
  `reduced`, several landing at exactly 0 sets when locally fatigued too).

## 11. Temporal validation

Unit-level (in-memory, `test_training_intelligence_dose.py`):
- Future session/muscle-row injected directly into `sessions=`/`muscle_rows=` is excluded -
  proven by comparing `build_shadow_dose` output with and without the future entry and
  asserting byte-identical results.
- Exact `as_of` boundary session is included (no off-by-one exclusion).
- Naive (non-timezone-aware) `as_of` is rejected outright.

**Real finding worth disclosing**: B2's own in-memory window functions
(`_session_window_stats`, `_muscle_exposures`/`_hours`) only enforce a *lower* window bound
once rows are already in hand - they rely entirely on the caller's SQL (`load_session_history`
/`_load_muscle_set_rows`, both bounded `WHERE begin_time <= now`) for the upper bound. `_hours()`
even clamps a future timestamp to `max(0.0, negative)` = 0, which would make a future row look
like it "just happened" if one were ever fed in directly. This is safe today because B2's
live path always sources rows via its own bounded SQL - but it is an implicit assumption, not
a self-enforced guarantee. Rather than modify `training_dose.py` (a live, B3-serving file,
out of this milestone's scope), `build_shadow_dose` adds its own defensive `as_of` filter on
any explicitly-injected `sessions`/`muscle_rows`, so **this shadow layer specifically** is
temporally safe by construction regardless of caller input or B2's internal assumptions.
Flagged here as a latent property of B2 worth a look if it or a similar function is ever
called with externally-supplied rows in the future - not fixed, since fixing it means editing
a live B3-serving module.

Live-Postgres validation (`test_training_intelligence_dose_postgres.py`, 2 tests, opt-in,
run this session): a future workout, inserted for real and then rolled back, produces a
byte-identical `build_shadow_dose` result (through the real, unmodified SQL path) to the
same call without it - proving the live path's existing SQL bound holds end-to-end.

## 12. Tests

`test_training_intelligence_dose.py`: **27 tests, all passing** - tolerance-band
classification (6), historical dose calculation over the real B2 engine (4), minimum-
evidence fallback (2), systemic/local readiness combinations including the central
fatigue-override invariant (4), performance-response classification (3), goal context (3),
temporal leakage (3), extreme/outlier history and dose-classification thresholds (2).

`test_training_intelligence_dose_postgres.py`: 2 opt-in real-SQL tests, **run live this
session: 2 passed, 0 failed, 0 skipped.**

Full backend suite after this milestone: **537 passed, 44 skipped, 0 failed** (was 510
before TKI-3; +27 new passing tests, 0 regressions, +2 new opt-in Postgres tests already
run and passing rather than skipped in this session).

## 13. Performance

| operation | time |
|---|---|
| `load_session_history` (90-day, real query) | 0.313 s |
| `select_comparable_sessions` (in-memory, no query) | 0.0004 s |
| `build_shadow_dose` (complete, real DB) | 1.90 s |

**Obvious N+1-shaped behavior worth reporting, not fixed this milestone**: a complete
`build_shadow_dose` call currently issues roughly six separate DB round trips (WHOOP
readiness, B1 muscle readiness, B2 session history, B2 muscle-set rows, goal lookup, and
TKI-2's own ledger cross-check) - at least two of which (B2's internal muscle-set query and
TKI-2's ledger query) join nearly-identical `tonal_workouts`/`tonal_sets`/`tonal_movements`
data with very similar bounds. This is acceptable for the current admin-diagnostic-only use
(not on any live request path), but would be worth consolidating (e.g. a single shared
fetch reused by both) before any future milestone puts this on a request path that must stay
fast.

## 14. Limitations / data-quality issues carried forward

1. **Fallback-tier reliance** (section 4/10): this user's real workouts rarely match B2's
   high-similarity tier well enough to avoid the broader-region fallback - a real property
   of this user's Tonal logging habits (mostly `workout_type: "Custom"`), not fixed here.
2. **`below_personal_range` is currently uninformative for this user** because of #1 - the
   label is technically correct but should not be read as "this user is under-trained."
3. The WHOOP recovery-band documentation discrepancy (TKI-2, section 17 of that report)
   remains open and unaddressed - out of TKI-3's scope.
4. The warmup/non-working-set ambiguity (TKI-1/2) remains fundamentally undecidable with
   this schema - unaffected by TKI-3.
5. The N+1-shaped multi-query pattern (section 13) - flagged, not optimized.
6. TKI-3 does not select which session family to evaluate (that remains TKI-4, not built) -
   this is by design per the assignment, not an oversight.
