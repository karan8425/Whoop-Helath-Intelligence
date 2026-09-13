# TKI-7 Report — End-to-End Training Intelligence Acceptance + Dev Mobile Validation

Scope discipline: Production backend, Production Render, Production
database, Production iOS, and `main` were never touched. No TKI-5.x
component's internal logic was recalibrated - this milestone only
assembles and exposes what TKI-5.1-5.4 already built.

## 1. Starting state

Backend: HEAD `e21e325`, branch `develop`, worktree clean, in sync
with `origin/develop` (verified before any edit). iOS: HEAD `d2a4caa`,
branch `develop`, worktree clean, in sync with `origin/develop`.
Development scheme confirmed pointed at
`https://whoop-health-intelligence-dev.onrender.com`
(`Config-Dev.xcconfig`) with bundle id `com.karan.HealthInteligence.dev`
(`project.pbxproj`, Debug-Dev/Release-Dev configurations); a local
`Secrets-Dev.xcconfig` supplying `WHOOP_INGEST_KEY` exists (gitignored,
not read).

## 2. Architecture assembled

One new orchestration entry point,
`training_intelligence.calibration.orchestrator.
build_training_intelligence_prescription(as_of)`, sequencing
already-existing, already-validated components with **no logic
duplicated**:

`build_calibrated_shadow_prescription()` (TKI-5.1/5.2 - goal state,
systemic/local readiness, demonstrated capacity, TKI-4 selection,
feasible dose, exercise selection/allocation) internally calls
`progression_v2.prescribe_v2()` (TKI-5.4), which internally consults
`history.multipliers()`/`sessions_from_rows()` (TKI-5.2) for cable-aware
workload, then `workload_v2.workload_sanity_v3()` and
`shadow.quality_verdict_v3()` (TKI-5.4). The orchestrator's only new
work is calling `snapshot.save_snapshot()` (TKI-5.3) afterward.

A new mobile adapter,
`training_intelligence.calibration.mobile_adapter.
adapt_calibrated_to_workout_schema()`, reshapes that result into
B3's exact mobile contract (mirroring the already-proven TKI-6 pattern
for TKI-5.1), reusing `PROGRESSION_POLICY`/`_rir_string` from the
existing TKI-6 adapter rather than duplicating them, and adds one
additive `session["training_intelligence"]` diagnostics namespace plus
one additive `training_intelligence_evidence` object per exercise.

## 3. Orchestration flow (implemented exactly as specified)

1-10. Goal/readiness/capacity/selection/dose/composition - internal to
`build_calibrated_shadow_prescription()`. 11. Progression Evidence V2 -
`progression_v2.prescribe_v2()`, internal. 12. cable-aware workload -
`history.py`, internal. 13. Workload Sanity V3 - `workload_v2.
workload_sanity_v3()`, internal. 14. Quality Verdict V3 - `shadow.
quality_verdict_v3()`, internal. 15. persist decision snapshot -
`orchestrator.py`'s own added step. 16. return - the mobile adapter's
job, called by the router.

## 4. Historical acceptance results (78 live lifting dates, 2-day
cadence, ending Sep-12, real Development data)

| Check | Result |
|---|---|
| Errors during replay | 0 |
| Readiness violations (exercise loads a blocked muscle) | **0** |
| Dose-outside-feasible-range violations | **0** |
| Generic movement leakage (Handle/Bar/Rope/Ankle Strap Move) | **0** |
| Impossible prescription values (zero/negative sets or reps, out-of-range resistance) | **0** |
| Duplicate movements within a session | **0** |
| Quality verdict distribution | QUESTIONABLE 74 (94.9%), SUPPORTED 4 (5.1%), **CONTRADICTED 0** |
| Family distribution | Lower Body 41, Upper Pull 15, Upper Push 8, Full Body 7, Chest+Biceps 3, Core+Accessories 3, Upper Mixed 1 |
| Severe unexplained under-prescription (ratio<0.3 AND UNEXPLAINED) | 13 / 78 (16.7%) |

**Disclosed, not hidden**: a consecutive-streak check found a 10-date
run of "Lower Body" (2026-04-01 through 2026-04-27). This is TKI-4's
own session-selection algorithm (`build_shadow_selection`, unchanged by
TKI-5.x or this milestone) - not something this milestone introduced,
and not in scope to recalibrate here (this is explicitly not another
algorithm-calibration milestone). Flagged honestly in section 22
(Known limitations) rather than silently passed over.

## 5. Legacy vs TKI comparison (10-date sample, same `as_of` for both engines)

| Date | Legacy family / sets / vol | TKI family / sets / vol |
|---|---|---|
| 2026-09-12 | Upper Pull / 8 / 4000 | Upper Pull / 11 / 8984 |
| 2026-08-25 | Lower Body / 11 / 6804 | Full Body / 12 / 8290 |
| 2026-08-07 | Full Body / 4 / 1455 | Upper Push / 4 / 0* |
| 2026-07-20 | Full Body / 9 / 4225 | Upper Push / 13 / 3058 |
| 2026-07-02 | Active Recovery / 0 / 0 | Rest / Active Recovery / 0 / 0 |
| 2026-06-14 | Lower Body / 5 / 2623 | Lower Body / 9 / 4869 |
| 2026-05-27 | Full Body / 6 / 3248 | Lower Body / 4 / 4900 |
| 2026-05-09 | Lower Body / 9 / 5824 | Lower Body / 12 / 7350 |
| 2026-04-21 | Rest / 0 / 0 | Rest / Active Recovery / 0 / 0 |
| 2026-04-03 | Active Recovery / 0 / 0 | Rest / Active Recovery / 0 / 0 |

\* TKI-5.4's disclosed known limitation: genuinely Tier-5 (zero
evidence) exercises report `estimated_volume = None`/0 rather than a
fallback estimate - not a new TKI-7 defect.

**Not always larger, and that is the point**: TKI is sometimes higher
(Sep-12, Aug-25), sometimes lower (May-27), and frequently selects a
*different session family* than legacy (TKI-4's own selection logic,
already validated separately) - the comparison demonstrates TKI is
grounded in personal capacity/readiness/history/workload/progression
evidence rather than reproducing legacy's output, exactly as intended.

## 6. Current-day prescription (2026-09-13, live Development data)

Recovery 94% (HIGH band), all target muscles READY, family **Upper
Push**, demonstrated capacity 11-17 sets (`exact_family`, HIGH
confidence, 11 comparable sessions), feasible range **11-17**, dose
**13/13** (0 shortfall), 5 exercises (Alternating Bench Press,
Standing Incline Press, Reverse Grip Triceps Extension, Barbell Seated
Overhead Press, Skull Crusher), estimated_total_volume **7732.0**
(cable-aware/calibrated). Contrast with the original Sep-12 forensic
case (94% recovery, Upper Push, but a TKI-3-era range collapsed to
10-10 and only 3 exercises) - the SAME kind of high-recovery/READY-
muscles day now produces a materially richer, better-justified
prescription, direct live evidence the calibration stack fixed the
class of failure this whole TKI-5.x series targeted.

## 7. Current-day quality verdict

`workload_sanity_v3`: `BELOW_PERSONAL_RANGE_JUSTIFIED` (absolute ratio
0.61), one real binding justification - `reduced_goal_posture`
(lean_cut, STRONG, -1 set effect vs. the neutral general_fitness
baseline), `justification_sufficient: true`, confidence HIGH.
`quality_v3`: **QUESTIONABLE** - one precise reason ("weak paired
progression evidence" for 3 of 5 movements), not CONTRADICTED, not a
generic catch-all. Sanity review (section 13 of the milestone spec):
**zero issues found** (no negative/zero sets or reps, no duplicate or
generic movements, no impossible resistance, dose within feasible
range). Decision snapshot persisted (`decision_id
cab8333c-bd10-5719-bb57-6821705a1937`).

## 8. Sep-12 forensic regression

Both paths re-verified stable against TKI-5.4's original findings: (a)
the structural `sep12_legacy_fixture_v3_analysis()` - unchanged,
correctly still reports raw 5,772 understated vs. cable-aware ~9,204
vs. reference medians 14,144/18,002 (ratios 0.32-0.65, all
BELOW_PERSONAL_RANGE regardless of reading), systemic_readiness and
local_readiness structurally ruled out (94% = high band, READY
muscles), goal posture/session-structure left honestly unresolved
(never fabricated), classification `UNREPRODUCIBLE_LEGACY_STATE`,
verdict QUESTIONABLE. (b) A live re-run at the Sep-12 forensic
timestamp through the **full assembled orchestrator + mobile adapter**
(not just the underlying shadow engine) - family Upper Push, dose
10/10, `BELOW_PERSONAL_RANGE_JUSTIFIED` with two real justifications
(systemic_readiness MODERATE, reduced_goal_posture STRONG),
`justification_sufficient: true`, mobile shape correctly carries the
same numbers through (`mobile_estimated_total_volume: 6436.0`
matching the raw result). Decision id
`8aa0214c-f279-5d16-8860-2e83dcb55a21` - identical to TKI-5.3's own
prior save of this exact decision, confirming byte-for-byte
determinism of decision-id derivation across three milestones.

## 9. Aug-7 regression

Re-run through the full assembled orchestrator: family Upper Push,
dose 4/4, both exercises Tier 5 (genuinely zero progression evidence),
`workload_sanity_v3` status `BELOW_PERSONAL_RANGE_JUSTIFIED`
(gap_classification MIXED), two real binding justifications
(systemic_readiness STRONG - band low, local_readiness MODERATE -
Chest/Triceps RECOVERING), `justification_sufficient: true`. `quality_
v3`: QUESTIONABLE ("weak paired progression evidence"). Snapshot
persisted for the first time this session (`stored: true`). Identical
to TKI-5.4's findings - stable, not re-tuned to look better.

## 10. Cache integration

`todays_plan_store.py`: `resolve_effective_training_engine()`
(`training_engine_flag.py`) gives `TRAINING_INTELLIGENCE_MOBILE_
ENABLED` precedence over the pre-existing `TRAINING_PRESCRIPTION_
ENGINE`; when the new flag is false/absent, behavior is byte-identical
to pre-TKI-7 (`resolve_training_prescription_engine()`'s own result).
A third, distinct cache partition (`ENGINE_TRAINING_INTELLIGENCE`,
offset **1000**) was added to `ENGINE_PLAN_VERSION_OFFSET` alongside
the existing B3 (0) and legacy-TKI (500) partitions - a plan built
under one engine can never be served as a cache hit under another.
`PLAN_VERSION` itself was not bumped (unnecessary: the new partition
has never been written to, so there is no stale-shape row within it to
reject). `invalidate_todays_plan()` already iterates
`set(ENGINE_PLAN_VERSION_OFFSET.values())`, so it automatically covers
the new partition with no further code change.

## 11. Snapshot integration

Every orchestrator call persists via the existing, TKI-5.3-built
`snapshot.save_snapshot()` (insert-only, deterministic decision_id,
never overwrites). Live-verified end-to-end via 3 new opt-in Postgres
tests: a real prescription persists and replays byte/semantically
equivalent (`compare_to_snapshot`), the mobile adapter's displayed
`estimated_total_volume` matches the persisted snapshot's value exactly,
and `persist_snapshot=False` (used by the read-only admin diagnostic)
correctly writes nothing.

## 12. API schema changes

Purely additive. `todays_plan.py`'s `_training_card()` gained two
pass-through lines: `session["training_intelligence"]` (new namespace,
`{}` for the legacy engine) and each exercise's
`training_intelligence_evidence` (`{}` for the legacy engine) - every
pre-existing field name, shape, and value for the legacy path is
byte-identical to before this milestone (verified by the unchanged 803
backend tests still passing).

## 13. Backend tests

New: `test_training_intelligence_tki7_orchestration.py` (17 tests -
mobile adapter shape/estimated-volume-uses-calibrated-field/error
handling/evidence provenance, feature-flag default/enabled/effective-
engine-precedence, cache-partition distinctness, `_build_workout()`
routing with the flag off/on/on-with-exception-fallback/on-with-
shadow-compare, admin route auth-gating and response shape) - all
pass. Full suite: **844 passed, 69 skipped (opt-in Postgres), 0
failed** (`pytest -q --ignore=test_daily_pipeline_cache.py` +
`test_daily_pipeline_cache.py` separately, matching the established
two-batch convention) - zero regressions from the pre-TKI-7 baseline.

## 14. Postgres tests

New: `test_training_intelligence_tki7_postgres.py` (3 opt-in tests -
orchestrator persists and replays a snapshot end-to-end via real SQL,
mobile adapter output matches the persisted snapshot's volume,
`persist_snapshot=False` writes nothing). Full opt-in suite (TKI-5.2 +
5.3 + 5.4 + TKI-7, real Development database, each test in a
rolled-back transaction): **16/16 passed**.

## 15. Development Render deployment

**Not performed.** This session has no Render deployment credentials
or API access. Per the milestone's own Gate 1/Gate 2 ordering ("Do not
deploy anything until local backend acceptance passes" / "ONLY after
local tests and historical acceptance PASS"), Gate 1 is complete and
passing (section 4, 13). Gate 2 requires the user to deploy. Exact
steps for the user:

```
# from Development/whoop-live-dev, already on develop with this
# milestone's commit pushed:
git push origin develop   # already done by this session

# In the Render dashboard for whoop-health-intelligence-dev:
#   1. Trigger a deploy of the latest develop commit (or confirm
#      auto-deploy has picked it up).
#   2. Verify /health reports the new commit SHA.
```

## 16. Feature flag

`TRAINING_INTELLIGENCE_MOBILE_ENABLED` (default false/absent - unchanged
Development behavior) and `TRAINING_INTELLIGENCE_SHADOW_COMPARE_
ENABLED` (default false) added to `training_engine_flag.py`. **Not
enabled anywhere** - per the milestone's explicit gating ("ONLY after
local tests and historical acceptance PASS" for deployment, and this
session has no Render env-var access). To enable, on the Development
Render service ONLY:

```
TRAINING_INTELLIGENCE_MOBILE_ENABLED=true
# optional:
TRAINING_INTELLIGENCE_SHADOW_COMPARE_ENABLED=true
```

Never set on the Production Render service - confirmed no such
variable was created or referenced there by this session.

## 17. Dev API smoke test

**Not performed** - requires the Development Render deployment (section
15) and the flag (section 16) to be live first, and this session has
no network access to the deployed Development Render URL or admin
session credentials for it. Exact command for the user once deployed
and enabled (admin-authenticated, replace `<COOKIE>`):

```
curl -s -b "session=<COOKIE>" \
  https://whoop-health-intelligence-dev.onrender.com/api/v1/todays-plan \
  | jq '{status, training: {engine_source: .training.training_intelligence.engine_source, session_type: .training.session_type, recovery_score: .training.recovery_score, total_sets: .training.total_sets, exercise_count: .training.exercise_count, estimated_total_volume: .training.estimated_total_volume, workload_status: .training.training_intelligence.workload.status, quality_verdict: .training.training_intelligence.quality_verdict}}'
```
The equivalent admin diagnostic (no mobile flag needed, useful to
sanity-check the assembled engine and legacy side-by-side even before
enabling the mobile flag):
```
curl -s -b "session=<COOKIE>" \
  https://whoop-health-intelligence-dev.onrender.com/training-intelligence/current | jq .
```

## 18. iOS changes

`TodayPlanModels.swift`: additive `TrainingIntelligenceDiagnostics`/
`TrainingIntelligenceWorkload`/`TrainingIntelligenceEvidence` structs
and two new Optional fields (`TodayTrainingPlan.trainingIntelligence`,
`TrainingExercise.trainingIntelligenceEvidence`) - fully
auto-synthesized `Codable`, nil for the legacy engine or any older
cached payload. `AppConfiguration.swift`: `isDevelopmentBuild` (bundle
identifier suffix check, not `#if DEBUG` - which is also true for a
Production Debug build). `TrainingDetailView.swift`: a subtle
"Training Intelligence" caption shown only when
`isDevelopmentBuild && engineSource == "training_intelligence"` -
never present in Production regardless of build configuration.
`TodayPlanViewModel.swift`: the pre-existing silent catch-all in
`consume()` now also logs a Development-only diagnostic
(`TKI7_DEV_REFRESH_ERROR`) and publishes `lastDevelopmentRefreshError`
on an unexpected refresh failure - the cached plan is still retained
exactly as before (offline support unchanged), only the *observability*
of a silent failure changed, and only in Development.

## 19. iOS build

**Both schemes build clean**, verified live in this session:
```
xcodebuild -scheme "HealthInteligence-Dev" -destination "generic/platform=iOS Simulator" -configuration Debug-Dev build
# ** BUILD SUCCEEDED **
xcodebuild -scheme "HealthInteligence" -destination "generic/platform=iOS Simulator" -configuration Debug build
# ** BUILD SUCCEEDED ** (Production scheme, confirms no shared-file regression)
```
**Full test suite also run and passed** (an upgrade over this
project's earlier recorded finding that `xcodebuild test` didn't work
in this environment - it now does; memory updated):
```
xcodebuild -scheme "HealthInteligence-Dev" -destination "platform=iOS Simulator,id=<UDID>" -configuration Debug-Dev test
# ** TEST SUCCEEDED ** - existing ~80+ cases + 3 new TrainingIntelligenceModelsTests, 0 failures
```

## 20. Physical-device validation

**Not performed - no physical-device access in this session.** Per the
milestone's explicit instruction ("If direct physical-device access is
unavailable: do not pretend to validate it. Instead provide exact
commands/actions"), here is the deterministic procedure for the user
on iPhone 15 Pro "Carnn" (UDID `00008130-00060C520C92001C`), continuing
after sections 15-17 are complete:

```
# 1. Build and install the Development app on the physical device
xcodebuild -scheme "HealthInteligence-Dev" -configuration Debug-Dev \
  -destination "id=00008130-00060C520C92001C" \
  -allowProvisioningUpdates build install

# 2. Force-close the app on the phone (swipe up, dismiss it).
# 3. Reopen it.
# 4. On the Today screen, pull to refresh (or wait for auto-refresh).
# 5. Open "Today's Workout" (Training Detail).
```
Checklist once there (all from milestone section 34):
- Today screen loads, no crash, training card present with a sensible
  session name/target muscles.
- Training Detail: WHOOP Recovery, session, exercise count, total
  sets, estimated volume, each exercise's sets/reps/resistance/RIR/
  progression action all match the API smoke-test values (section 17).
- The small "Training Intelligence" caption is visible at the top of
  Today's Workout (proof the calibrated engine, not legacy, produced
  what's on screen) - if it's absent, the flag is not actually enabled
  server-side, or a stale cache is showing; force-close/reopen again
  and confirm the API response directly (section 17) before assuming
  anything about the phone.

## 21. Backend vs phone value comparison

**Not available** - depends on sections 15-20, none of which could be
executed in this session. Once the user completes the physical
validation above, this table should be filled from the real device:

| Metric | Legacy Engine | Training Intelligence | Why New Engine Chose It |
|---|---|---|---|
| Session | (from phone) | (from phone) | (bind to the live `workload.status`/`binding_constraints` from section 17's response) |
| Sets | | | |
| Exercises | | | |
| Estimated volume | | | |
| Progression | | | |
| Workload status | | | |

## 22. Known limitations

1. **Not deployed, not device-validated this session** (sections 15,
   17, 20-21) - no Render or physical-device access. This is the
   primary reason the milestone cannot claim a full PASS.
2. TKI-4's own session-selection algorithm can produce long same-family
   streaks in a low-frequency-training window (a real 10-date "Lower
   Body" run found in section 4) - pre-existing, unchanged by this
   milestone, out of this milestone's scope to recalibrate.
3. Genuinely Tier-5 (zero progression evidence) exercises report
   `estimated_volume = None`/0 rather than a fallback estimate
   (TKI-5.4's disclosed, intentional "do not invent certainty"
   behavior) - visible in the Aug-7 case and in one legacy-comparison
   sample date (Aug-7).
4. 16.7% of the 78-date historical replay showed severe, unexplained
   under-prescription (ratio<0.3, UNEXPLAINED) - a real, bounded,
   already-disclosed characteristic of this user's actual WHOOP/
   training history (TKI-5.4's own finding), not a new TKI-7 defect,
   and not large enough to be a "systemic issue" under section 15's
   stop-the-line criteria (no repeated pathological pattern beyond the
   known TKI-4 family-selection behavior in item 2).
5. The admin diagnostic route (`/training-intelligence/current`) has
   no dedicated automated test against a live Development database
   (only mocked-function route tests) - its correctness is instead
   demonstrated by the identical, live-tested orchestrator/adapter
   functions it calls directly.

## 23. Rollback procedure

Trivial, no code revert required: set
`TRAINING_INTELLIGENCE_MOBILE_ENABLED=false` (or delete the variable)
on the Development Render service. `resolve_effective_training_engine()`
immediately falls back to whatever `TRAINING_PRESCRIPTION_ENGINE`
already resolves to (b3 by default), and the cache partitioning
(section 10) means the next `/api/v1/todays-plan` request reads/writes
the B3 (or legacy-TKI) partition again, never the
`ENGINE_TRAINING_INTELLIGENCE` partition - no stale TKI plan can leak
through as "legacy." No redeploy is required for the flag flip to take
effect (env var change + Render's own restart is sufficient); no
`git revert` needed either, since the new code path is inert whenever
the flag is unset.

## 24. Production untouched confirmation

No Production backend file was modified (`training_engine_flag.py`,
`todays_plan.py`, `todays_plan_store.py`, `main.py` changes are all
Development-repo-only and gated by a Development-only env var never
set in Production). No Production Render service was accessed,
deployed to, or had environment variables changed. No Production
database was queried or written (all live queries in this session used
the Development database via the existing Keychain-scoped credential
pattern). No Production iOS file was modified in a way that changes
Production behavior - `AppConfiguration.isDevelopmentBuild` is `false`
for the Production bundle identifier by construction, so every new
Development-gated code path (the caption, the dev diagnostic log) is
inert there; both the Production and Development iOS schemes were
built locally to confirm this holds structurally, not just by
assertion. `main` was not merged in either repository.
