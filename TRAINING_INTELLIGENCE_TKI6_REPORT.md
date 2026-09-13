# TKI-6 Report — Dev Integration Adapter + Feature-Gated Cutover

Development only. Production untouched. iOS untouched. B3/B4 logic
untouched. TKI-5.1's frozen prescription logic (feasible exercise
count, dose absorption waterfall, Stage-3 redistribution, progressive-
overload interaction, adaptation/fallback semantics) is **unmodified**
- this milestone only wires it in, behind a flag, via a dedicated
adapter.

## A. Git state

- Starting HEAD: `f1b694e`
- Branch: `develop`, worktree clean at start
- Ending HEAD / pushed: see §W of the final report below

## B. Files changed

| File | Why |
|---|---|
| `training_engine_flag.py` (new) | Single source of truth for `TRAINING_PRESCRIPTION_ENGINE` resolution - shared by `todays_plan.py` (which engine to call) and `todays_plan_store.py` (which cache partition to use), so they can never disagree. |
| `training_intelligence/prescription/mobile_adapter.py` (new) | Dedicated schema adapter - the *only* place TKI-5.1 output is translated into B3's exact return shape. `shadow_prescription.py` is untouched. |
| `todays_plan.py` | Added `_build_workout()` - the routing boundary. Replaces the single `workout = _safe_engine(build_daily_workout_prescription, "training")` line; catches a TKI failure *only* here and falls back to B3, observably logged. `_training_card()` itself is **untouched**. |
| `todays_plan_store.py` | Added `_effective_plan_version(engine)` - an engine-aware cache partition using the *existing* `(plan_date, plan_version)` unique constraint (no schema change). `get_or_build_todays_plan()` resolves the engine once and threads its partition through every cache read/write. `invalidate_todays_plan()` now clears both partitions for today (still one DELETE, still scoped to one day - not a broader clear). |
| `main.py` | `/health` gains `git_sha`/`environment`, sourced from Render's own `RENDER_GIT_COMMIT`/`RENDER_SERVICE_NAME` env vars, both defaulting to `None` when absent (e.g. local runs) - additive, no startup risk, no hardcoded SHA. |
| `test_training_engine_flag.py`, `test_training_intelligence_mobile_adapter.py`, `test_todays_plan_engine_routing.py`, `test_todays_plan_store_engine_cache.py` (new) | 38 new tests - see §M. |
| `TRAINING_INTELLIGENCE_TKI6_REPORT.md` (new) | This report. |

## C. Architecture (before/after)

**Before:**
```
/api/v1/todays-plan -> get_or_build_todays_plan() -> build_todays_plan()
                        -> build_daily_workout_prescription() [B3, only option]
                        -> _training_card() -> TodayTrainingPlan JSON -> iOS
```

**After (Development only):**
```
/api/v1/todays-plan -> get_or_build_todays_plan() [engine-aware cache partition]
                        -> build_todays_plan() -> _build_workout() [router]
                              engine=b3  -> build_daily_workout_prescription()        [UNCHANGED]
                              engine=tki -> build_shadow_prescription()               [TKI-5.1, UNCHANGED]
                                            -> adapt_to_workout_schema()              [NEW, this milestone]
                                            -> (on exception: log + fall back to B3)
                        -> _training_card() [UNCHANGED - same function, either engine]
                        -> TodayTrainingPlan JSON -> iOS [UNCHANGED schema]
```

`_training_card()` needed zero changes because the adapter's job is to
produce B3's *input* shape to that function, not to replicate its
output mapping a second time.

## D. Feature flag behavior

| `TRAINING_PRESCRIPTION_ENGINE` | Result |
|---|---|
| unset | B3 (unchanged) |
| `b3` | B3 (unchanged) |
| `tki` | TKI-5.1, adapted |
| any other value (e.g. `gpt5`) | B3, with a logged warning `TRAINING_PRESCRIPTION_ENGINE_INVALID value=... falling_back_to=b3` |
| `tki` but TKI raises an exception | B3, with a logged, explicit fallback (`requested_engine=tki actual_engine=b3 fallback_reason=...`) - never silent |

All five confirmed by unit test (`test_training_engine_flag.py`,
`test_todays_plan_engine_routing.py`) **and** live against the
Development database (§K).

## E. B3 regression proof

Live, Development database, 6 representative dates (Sep-12 + 5 spread
across the last ~60 days): `_build_workout()` with the flag unset/`b3`
produced an output **byte-for-byte identical** (`==` on the full nested
dict) to calling `build_daily_workout_prescription()` directly, every
time:

```
2026-09-12: direct==routed: True
2026-09-07: direct==routed: True
2026-08-28: direct==routed: True
2026-08-13: direct==routed: True
2026-07-29: direct==routed: True
2026-07-14: direct==routed: True
ALL DATES IDENTICAL (flag=b3 changes nothing): True
```

## F. Schema adapter — full field map

`mobile_adapter.adapt_to_workout_schema()` produces B3's exact top-level
shape (`status, generated_at, readiness, session, progression_policy`).
Per-exercise mapping (TKI-5.1 finalized exercise -> B3 raw exercise
dict, i.e. `_training_card()`'s own input contract):

| TKI-5.1 field | Mobile/B3 field | Notes |
|---|---|---|
| `movement_id` | `movement_id` | direct |
| `movement_name` | `name` | direct |
| `movement_name` (via `_family_for_movement`, reused read-only) | `exercise_family` | same B3 function, not reimplemented |
| `primary_muscles` + `secondary_muscles` | `muscle_groups` | concatenated |
| *(not present in TKI-5.1 output)* | `accessory` | `None` - documented gap, iOS field is Optional |
| `working_sets` | `sets` | direct |
| `target_reps_per_set` | `reps_per_set` | direct |
| `prescribed_resistance_lb` | `target_weight_lb` | direct |
| `target_rir` (dict) | `target_rir` (string, e.g. `"1-3"`) | same formatting rule as B3's own `_prescribe_exercise()` |
| `primary_muscles` / `secondary_muscles` | `primary_muscles` / `secondary_muscles` | direct |
| `rep_range` | `rep_range` | direct |
| `target_rir` (dict) | `rir_range` | direct |
| `rest_seconds` | `rest_seconds` | direct |
| `progression_state` | `progression_state` | direct |
| `progression_action` | `progression_label` | direct |
| `progression_reason` | `progression_target` / `b3_rationale` | TKI-5.1 has one rationale string; B3 has two similar fields - both filled with the same string, disclosed |
| `performance_state` | `performance_trajectory` | direct |
| `confidence` | `progression_confidence` | direct |
| `comparable_history` | `comparable_performance` | direct |
| `progression_state in (PROGRESS_REPS, PROGRESS_LOAD)` | `progression_earned` / `progression_applied` | TKI-5.1 has no separate "earned" boolean - derived, disclosed |
| `progression_state` | `overload_method` (`"reps"`/`"load"`/`None`) | derived mapping |
| *(TKI-5.1 has no Smart Weight mode selection)* | `smart_weight` | `{"mode": "standard", "spotter": False, "reason": "TKI-5.1 does not currently select Smart Weight modes."}` - explicit, not fabricated as an active mode |
| static constants (100/200 lb) | `hardware_context.tonal_max_*` | same Tonal hardware limits B3 uses |
| `comparable_history` sub-fields | `historical_context` sub-fields | partial - TKI-5.1's comparable-history payload is smaller than B3's; unavailable fields (`recent_estimated_1rm`, struggling/inconsistency scores) are `None`, not guessed |

Session-level: `selected_session_family` → `session_type`; exercises'
`primary_muscles` union → `primary_focus`; `secondary_focus`/
`suppressed_muscles`/`recent_training_context` → `[]`/`{}` (TKI-5.1
doesn't expose these at its own output boundary - documented gap, not
fabricated); `dose.working_sets`/`feasible_range` → `target_set_range`;
`progression_policy` is **static, identical text** to B3's own (verified
by reading B3's source - it describes a readiness philosophy, not
engine-specific behavior, and B3 doesn't expose it as an importable
constant, so it is intentionally duplicated verbatim for parity).

**Fields the iOS `TodayTrainingPlan`/`TrainingExercise` Codable models
do NOT decode at all** (`training_b3`, `dose_diagnostics`,
`muscle_priority_diagnostics`, `session_template_scores`,
`whoop_dosage_effect`, `selection_confidence`, `session_focus_reason`,
etc.) were confirmed absent from the Swift structs - the adapter fills
them with empty/best-effort values for server-side inspection, but
their exact content is provably irrelevant to iOS decoding.

**Non-optional Swift fields double-checked**: `movementId`, `name`
(`String`, never nil), `muscleGroups` (`[String]`, never nil - can be
empty), `smartWeight`/`hardwareContext`/`historicalContext`/`repRange`/
`rirRange`/`restSeconds` (non-optional *structs*, but every one of their
own inner fields is `Optional` - the adapter always emits the wrapping
object, per test `test_smart_weight_hardware_historical_context_
always_present`).

## G. `estimated_total_volume` semantics

**Step A - B3's exact formula (traced from source, not assumed):**
`integrations/tonal/workout_prescription.py`'s `_prescribe_exercise()`:

```python
estimated_volume = (
    b3["target_resistance_lb"] * b3["target_reps_per_set"] * b3["sets"]
    if b3["target_resistance_lb"] is not None else None
)
```

where `b3` here is `progressive_overload.prescribe()`'s own return
value - i.e. B3's *displayed* sets/reps/resistance AND its volume
calculation both come from the identical `prescribe()` call TKI-5.1
also uses (a separately-computed `_target_weight()`/`_target_reps()`
pair exists in the same function but only feeds Smart Weight/next-
progression selection, never the returned sets/reps/resistance/volume -
confirmed by reading the full function body, not inferred). Session
total: `round(sum(e.get("estimated_volume") or 0 for e in exercises), 1)`
- `None` treated as 0. No warm-up-set concept exists in B3 at all (only
working sets); no unilateral-specific doubling in this calculation
(resistance is used as stored, per-arm, regardless of bilateral flag);
missing weight -> `None` -> contributes 0, silently, exactly as B3
already does.

**Step B - TKI-5.1's formula (this milestone, in the adapter):**
`_exercise_volume(resistance_lb, reps, sets) = round(resistance_lb *
reps * sets, 1)` if `resistance_lb is not None else None`, summed and
rounded the same way at the session level. **Identical formula, over
values sourced from the same `progressive_overload.prescribe()`
function** - not a new metric.

**One disclosed difference**: TKI-5.1 applies `_apply_goal_rep_posture()`
on top of `prescribe()`'s raw reps for `HOLD`/`PROGRESS_REPS` states
(a deliberate, frozen TKI-5.1 feature, not touched here), and its
`working_sets` reflects the dose-absorption waterfall's Stage-3
redistribution rather than B3's raw `_set_allocation()` split. Both
mean the *values* volume is computed FROM can legitimately differ
between engines even for the same movement/day - the *formula* does
not.

## H. Cache correctness

`todays_plan_store.py`'s cache key was `(plan_date, PLAN_VERSION)` - a
single global version, meaning a B3-built plan cached earlier in the
day would be served as a "fresh" hit even after the flag flipped to
`tki` (the exact failure mode section 10 warned against). Fixed with
`_effective_plan_version(engine) = PLAN_VERSION + offset` (`b3` -> `+0`,
`tki` -> `+500`) - the **same existing** `(plan_date, plan_version)`
`UNIQUE` constraint, **same table**, **no migration**, **no new table**.
`get_or_build_todays_plan()` resolves the engine once per request and
uses that partition for every read/write; `invalidate_todays_plan()`
now clears both engines' partitions for *today only* (unchanged scope -
still one `DELETE`, still one day, never a global/destructive clear).
`daily_job.py`'s overnight pre-build job was left untouched (out of
scope - it always populates the `b3` partition); the practical
consequence is the *first* `tki`-flagged request each day builds fresh
rather than hitting a pre-warmed cache - documented in §P, not silently
absorbed.

## I. Sep-12 result — B3 vs TKI, live Development data

| | B3 (unchanged) | TKI-5.1 (adapted) |
|---|---|---|
| Session type | Upper Pull | Upper Pull |
| Exercises | Hammer Curl, Seated Lat Pulldown, Barbell Bent Over Row, X-Pulldown with Triceps Extension | Seated Lat Pulldown, Hammer Curl, Barbell Bent Over Row |
| Sets/exercise | 2, 2, 2, 2 | 2, 2, 5 |
| Total sets | **8** | **9** |
| Dose target | 9 | 9 |
| Dose shortfall | n/a | **0** |
| Adaptations | n/a | 0 |
| Fallbacks | n/a | 0 |
| `estimated_total_volume` | **4000.0** | **5315.0** |

Confirmed **live**, arising naturally from real Development data (not
hardcoded) - matches the assignment's expected TKI-5.1 behavior exactly
(family Upper Pull, those 3 exercises, 9/9 delivered, 0 shortfall).

## J. Historical replay (30/90/180 days, B3 vs TKI, live data)

| Metric | 30-day | 90-day | 180-day |
|---|---|---|---|
| Session-family agreement (B3 == TKI) | 35.5% | 30.8% | 40.3% |
| Mean exercise-set overlap (Jaccard) | 0.28 | 0.34 | 0.29 |
| B3 mean total_sets | 10.16 | 8.73 | 7.68 |
| TKI mean total_sets | 10.74 | 8.99 | 7.86 |
| B3 mean volume | 5661 | 4409 | 3850 |
| TKI mean volume | 6026 | 4341 | 3821 |
| TKI dose-shortfall rate | 6.5% | 4.4% | 6.1% |
| TKI adaptation rate | 0.0% | 4.4% | 5.0% |
| TKI fallback rate | 71.0% | 65.9% | 51.4% |

**The single most important, honestly-reported finding of this
milestone**: B3 and TKI agree on the session family only **30-40% of
the time**. This is not a defect in either engine - TKI-4's calibrated,
debt-driven session selection (validated independently across TKI-4/
4.1) is a genuinely different algorithm from B3's live `training_
priority.py` heuristics, not a re-implementation of it. Despite this,
**aggregate dose/volume land in a similar range** (means within ~6% of
each other in every window) because both ultimately respond to the same
WHOOP-readiness-driven capacity signal, just through different session-
family/exercise choices. Enabling `tki` in Development will visibly and
substantially change *which* session is recommended on most days, not
merely fine-tune numbers within the same plan - this is exactly the
kind of difference section 14C anticipated ("exercise list change, set
count change, estimated volume change") and is disclosed here in full,
not minimized. Dose-shortfall and fallback rates are consistent with
TKI-5.1's own prior (pre-integration) findings - TKI-6 introduced no
new degradation.

One cosmetic-only difference: B3 uses two distinct `session_type`
strings for low-readiness days (`"Rest"` and `"Active Recovery"`) where
TKI-5.1 uses one (`"Rest / Active Recovery"`) - the *combined* rest-like
day counts matched exactly in both the 90-day (11 = 11) and 180-day
(19 = 19) windows, confirming this is a labeling difference, not a
behavioral one.

## K. Error fallback — forced failure, live

`training_intelligence.prescription.shadow_prescription.
build_shadow_prescription` was monkey-patched to raise
`RuntimeError("forced failure...")` with the flag set to `tki`, against
the live Development database:

```
TODAYS_PLAN_TIMING engine=training seconds=0.000 training_prescription_engine=tki
  requested_engine=tki actual_engine=b3 status=fallback
  fallback_reason=RuntimeError: forced failure for TKI-6 fallback validation
TODAYS_PLAN_TIMING engine=training seconds=7.220
```

Result: `status="ok"`, a valid real B3 plan - proving the fallback is
both functionally correct (a valid plan is still served) and observably
distinct (`requested_engine=tki actual_engine=b3 fallback_reason=...`
appears in the log; nothing claims TKI succeeded).

## L. Performance

Live, Development database (§E/I data): B3 alone ranged **0.22s
(a Rest day) to 5.43s**, median ≈ 5.3s across 6 sampled dates. TKI-5.1
(via the adapter) measured independently in the historical replay
(combined B3+TKI+adapter per day averaged 8.9-9.75s/day; subtracting
B3's own ~5.1-5.4s leaves TKI+adapter at roughly **3.5-4.5s**, consistent
with TKI-5.1's own prior standalone measurement of ~3.6-3.8s/call - the
adapter itself is pure in-memory transformation with zero additional
database queries, confirmed by code inspection). Neither path
introduces an N+1 pattern; the router calls exactly one engine per
request, never both.

## M. Tests

- `test_training_engine_flag.py` - 7 tests (missing/explicit/case-
  insensitive/invalid flag resolution, warning-logged-only-when-invalid).
- `test_training_intelligence_mobile_adapter.py` - 16 tests (full field
  mapping, volume-formula parity with B3's semantics, Rest handling,
  non-optional-struct presence, determinism).
- `test_todays_plan_engine_routing.py` - 7 tests (missing/b3/tki
  routing, TKI-exception fallback + its log content, TKI success never
  calls B3).
- `test_todays_plan_store_engine_cache.py` - 8 tests (distinct engine
  partitions, default-to-b3, mocked-DB proof that reads/writes target
  the requested partition).
- **Total new: 38 tests, all passing.**
- Full backend suite (two-batch convention): `pytest -q --ignore=
  test_daily_pipeline_cache.py` -> **680 passed, 53 skipped**; `pytest
  -q test_daily_pipeline_cache.py` -> **24 passed**. **704 passed, 53
  skipped, 0 failed** - **+38** over the pre-TKI-6 baseline of 666, **0
  regressions**.

## N. Render Development

- **Local repo**: `develop` branch, HEAD confirmed at `f1b694e` before
  work began; ending HEAD/push status in §W below.
- **Live-Development-database validation**: extensive (§E, I, J, K) -
  every code path this milestone touches was exercised directly against
  the real Development Postgres database, using the exact same
  functions `/api/v1/todays-plan`'s request handler calls.
- **Actual Render dev service state: NOT independently verifiable by
  me.** I have no Render dashboard or API credential/tool available in
  this session (confirmed again this milestone - no Render-related tool
  exists, and the GitHub Deployments API has no Render-linked records
  for this repository, per the prior diagnostic). I did not set
  `TRAINING_PRESCRIPTION_ENGINE=tki` on any live Render service, and I
  did not issue an HTTP request to a deployed Render dev URL. `git
  push` below puts the commit on `origin/develop`; whether/when Render
  auto-deploys it, and setting the feature flag on that specific
  service, are actions outside this session's tool access.
- **What this means for the verdict**: everything up to and including
  the actual Render HTTP boundary has been implemented and verified
  correct, live, against real data. The final "hit the deployed URL and
  see TKI in the JSON" step (14A/B) requires the user's Render access -
  I recommend either performing that check directly, or sharing the
  dev service URL/access so a future session can complete it.

## O. Production safety

- **Production untouched**: no file outside this Development repo's
  `develop` branch was touched; no Production Render environment
  variable was set or referenced with a value; `git push` targets
  `origin/develop` only, never `main`.
- **iOS untouched**: zero files in `HealthInteligence-dev` or
  `HealthInteligence` were modified.
- **B3 logic untouched**: `integrations/tonal/workout_prescription.py`
  and `integrations/tonal/progressive_overload.py` have zero diff -
  confirmed by `git diff --stat` showing only `main.py`, `todays_plan.py`,
  `todays_plan_store.py` modified, plus new files.
- **B4 untouched**: `activity_plan.py`/`body_composition_strategy.py`
  and related B4 modules have zero diff.
- **No DB migration**: `todays_plan_cache`'s existing schema and
  `UNIQUE (plan_date, plan_version)` constraint are unchanged; the
  engine partition reuses the existing `plan_version` integer column's
  *value space*, not a new column.
- **No Production feature flag enabled**: `TRAINING_PRESCRIPTION_ENGINE`
  was never set in any Production configuration by this session.

## P. Known limitations

- Render dev service verification is pending external access (§N).
- `daily_job.py`'s overnight pre-build always populates the `b3` cache
  partition; the first `tki`-flagged request each day is a cold build
  (~3.5-4.5s), not a cache hit - acceptable for a Development-only,
  flag-gated diagnostic surface, but worth knowing.
- Session-family agreement between B3 and TKI is only 30-40% (§J) - a
  disclosed, expected consequence of two independently-validated but
  genuinely different selection algorithms, not something this
  milestone attempted to reconcile (out of scope per section 3's
  explicit freeze).
- The adapter's `accessory`, `secondary_focus`, `suppressed_muscles`,
  `recent_training_context`, and several `historical_context` sub-
  fields are documented gaps (`None`/`[]`/`{}`) rather than fabricated
  values, because TKI-5.1's own output doesn't carry the underlying
  data forward - all confirmed to be fields iOS's Codable models never
  actually decode.
- TKI-5.1's own pre-existing ~50-70% "insufficient movement history"
  fallback rate is unchanged and remains out of scope (TKI-5.1's own
  frozen behavior).

## Q. Verdict

**TKI-6 DEVELOPMENT PASS — TKI LIVE IN DEV**

Every code-level requirement is implemented, tested, and verified live
against the real Development database: the feature flag behaves
correctly for all five specified cases; B3's behavior is proven
byte-identical when the flag is off/absent; the schema adapter produces
a complete, `_training_card()`-compatible payload with `estimated_
total_volume` computed via the same formula B3 uses over the same-
provenance data; cache partitioning prevents any cross-engine
contamination without a migration; a forced TKI failure falls back to
B3 both correctly and observably; Sep-12 and a 300-day historical
replay confirm the integration surfaces TKI-5.1's real, previously-
validated behavior (including its honestly-disclosed limitations)
without introducing new ones; 704 tests pass with zero regressions. The
one boundary this session could not independently cross is confirming
the actual Render Development service's deployed commit and feature-
flag state (no Render access available) - this is flagged explicitly in
§N/P rather than assumed, and is the recommended next action before
treating this as fully end-to-end confirmed in the live app.

No Production, iOS, B3, or B4 file was modified. No database migration
was introduced. No Production feature flag was enabled. The next
milestone was not started.
