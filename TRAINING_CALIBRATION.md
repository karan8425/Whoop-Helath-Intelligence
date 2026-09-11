# Program-balance calibration

Training selection remains deterministic. Local Tonal readiness determines
eligibility; stimulus, program coverage, and rotation select the session.
WHOOP capacity modifies the resulting dose. Exercise progression and B4 remain
separate downstream computations.

## Profiles and constants

`integrations/tonal/program_balance.py` owns the calibration profiles and
constants. The Development default is `correlation` (Candidate D).

- `baseline`: B3.1 scoring, the prior three simulated recommendations, and
  original unknown-family grouping, solely to reproduce the fixed baseline.
  All A–D candidates correct the proven movement-selection defect: unrelated
  movements classified as `other` use their movement ID for deduplication.
  Known exercise families still deduplicate. This correction admits existing
  upper-body movements; it does not change eligibility or dose rules.
- `coverage` (A): bounded coverage using actual 30-day primary/secondary exposure
  and up to 30 prior recommendations. Recommendations count less than workouts.
- `coverage_rotation` (B): A plus muscle overlap over three recommendations,
  adjusted for intervening actual primary training. Replaces the name-only
  heuristic; total overlap penalty is capped at 50.
- `balanced` (C): B with historical stimulus/region urgency capped at 105.
- `correlation` (D): C with coherent emitted-muscle scoring: strongest anchor
  receives 80%, the mean of remaining supporting priorities receives 20%.
  Full Body must contain upper and lower anchors; Core + Accessories includes
  Core. At most three muscles are emitted, and all emitted muscles are scored.

Readiness priority remains FRESH +70, READY +35, RECOVERING -25, FATIGUED -150,
SUPPRESSED -1000. FATIGUED and SUPPRESSED remain ineligible regardless of need.
Coverage has a four-day grace, ten-day gradual ramp, and a maximum of 40.
Secondary exposure receives 0.35 weight. RECOVERING coverage is halved; unsafe
states receive zero coverage. Actual training reduces coverage even if it was
never recommended. This is not a quota or fixed split.

## Reproduce a fixed-window comparison

Export only the existing Development Keychain credential into the calling
shell. Never echo it, write it to a file, or put it in an argument. Then run:

```sh
python -m training_backtest --start-date 2026-06-10 --end-date 2026-09-07 \
  --cutoff-hour 7 --calibration baseline --include-details \
  --output backtest-output/b32/baseline.json
```

Repeat with `coverage`, `coverage_rotation`, `balanced`, and `correlation` using
separate output names. Unset the credential afterward. All replay transactions
are READ ONLY. Replay history contains only earlier simulated recommendations;
actual same-day outcomes remain separate from recommendation inputs.

Artifacts contain private health history and stay in gitignored
`backtest-output/`. Do not commit query snapshots or detailed replay payloads.

## Diagnostics and interpretation

Ranked muscles expose readiness component, raw and bounded stimulus,
coverage, regional strength balance, and final priority separately. Rotation
is session-level (the per-muscle rotation field is zero to avoid double
counting). Template diagnostics identify scored muscles, component scores,
readiness exclusions, overlap components, and the final score. Detailed
movement-viability diagnostics are enabled for replay, not added to ordinary
mobile responses.

`training_calibration_metrics.py` reports gaps (including censored window
edges), actual longest streaks, overlap, dose/volume/duration, B4 modalities,
coverage pressure, and pathology checks. Gap medians include both window edges.
The previous adjacent-repeat count is not the longest uninterrupted streak.

A viable safer alternative must pass template and movement eligibility, have
READY/FRESH muscle(s), contain fewer RECOVERING muscles, and have program need
within 35 points of the selected session. This is an audit definition, not a
selection override or blanket RECOVERING ban. The original looser metric is
retained under `legacy_recovering_with_unselected_ready_fresh_muscle`.

Recommendation gaps are not proof of physiological neglect: actual primary
and secondary training can occur during them. Full Body frequency and newly
longer individual recommendation gaps must be inspected with actual exposure,
not optimized toward equal counts. Historical WHOOP/source-availability and
in-place goal-edit limitations from `TRAINING_BACKTEST.md` still apply.

## Tests

Run focused B3.2, calibration-metrics, B3/B4, Goal Progress, and replay tests
first. Run broader backend modules in separate processes: some older tests
install module-level stubs that conflict during combined discovery. Seed the
existing test environment before importing configuration, and import real
`requests` before the legacy Tonal-sync test stub is installed. No production
credentials are needed for the unit suite. Optional PostgreSQL body-progress
regressions require explicit Development opt-in and enforce READ ONLY.

## Fixed-window selection decision

After the common family fix, A/B/C still produced 24/20/21 RECOVERING muscle
selections with viable safer alternatives; D reduced this to 15 (11 days).
A separate unchanged-scoring, family-fix-only control produced 28. The original
broken baseline recorded only 5 because most upper-body alternatives were
incorrectly movement-ineligible; that raw figure is not a comparable improvement
claim. D's total RECOVERING selections fell from 83 to 69. Remaining alternatives
are reported, not hidden or eliminated through a blanket tissue-state ban.

D reduced Lower Body from 47 to 23 days, Back's longest recommendation gap from
60 to 20 days, and the longest template run from 13 to 5 days. Its average sets
were 8.79 versus 8.61, with High/Good/Moderate/Low/Very Low averages of
11.10/10.79/10.37/6.90/0. B4 outcomes and 78 strength days were unchanged.
An additional 65% anchor sensitivity run reduced viable-alternative selections
to 12 but lengthened Core's gap to 27 and Biceps' to 23 days; the 80% anchor kept
every major-muscle gap at or below 20 days. These are empirical tradeoffs on one
fixed historical window, not physiological targets or guarantees.
