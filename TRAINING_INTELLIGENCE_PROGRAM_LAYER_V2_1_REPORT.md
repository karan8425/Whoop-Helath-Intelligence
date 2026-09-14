# Program Intelligence V2.1 Report — Progression Confidence + Workload Justification

Read-only investigation followed by two narrowly-scoped bug fixes in the
existing progression-evidence pipeline. No session-selection
architecture, no readiness thresholds, no systemic-capacity logic, no
program sequencing, no exercise mapping, and no set-prescription
targets were touched. No Production, Render, or iOS changes.

## 1. Starting commit

`f28caa7` (Program Intelligence V2) — verified clean, in sync with
`origin/develop` before any edit.

## 2. Ending commit

See "Commit" section below.

## 3. Frozen decision/snapshot used

No persisted snapshot existed for the original QUESTIONABLE case
(`persist_snapshot=False` in the review that produced it). Byte-for-
byte replay via `as_of` alone was **not** reproducible: `_latest_
readiness`'s as_of-safety filters on the WHOOP source's own
`source_updated_at`, and a revised recovery-score row for the same
metric_date had landed in Development between the original capture and
this session, shifting the systemic band from `good` to `high` on a
plain re-run — a real, disclosed characteristic of live WHOOP
ingestion (a metric can be revised after its first post, with a
backdated `source_updated_at`), not a V2 defect. The exact original
case was reconstructed deterministically via the engine's own
established `readiness_override` convention, using the precise
readiness values captured in the original review (`recovery_score=78`,
`hrv_rmssd_milli=60.362286`, `resting_heart_rate=62.0`, `sleep_
duration_hours=7.3403…`, `metric_date=2026-09-13`, `training_
category=Normal`), together with the same `enrollment_override`
(shadow context, `sequence_position=None`). This reproduced the
original result **exactly** (deep-equality verified field-by-field)
before any code changed. `as_of = 2026-09-14T01:34:29.786635+00:00`;
program `hypertrophy_upper_lower_4d_v1`; nominal/selected session
`upper_a`; action `KEEP`.

## 4. Exact original QUESTIONABLE causes

`quality_verdict.reasons = ["Weak paired progression evidence for:
['Seated Overhead Press', 'Reverse Grip Barbell Triceps Extension']."]`
— two movements at evidence tier 5 / tier 4 respectively, LOW
confidence, REBUILD action, product-policy-fallback workload
semantics. Workload sanity itself (`WITHIN_PERSONAL_RANGE`, ratio
1.0985) was **not** a cause of the QUESTIONABLE verdict — see section
14.

## 5. Seated Overhead Press progression forensic

Movement `c3387b0d-c4e4-470f-aa7a-1fe20a3ce071`. As-of-safe raw
history: 174 rows, 36 `valid_rows`-eligible across 13 sessions
(remaining 138 excluded for ordinary, correct reasons — warmup,
missing/assisted avg_weight per-row, or sets outside this movement's
`valid_rows` numeric bounds). Of the 13 sessions: 1 was logged
`standard` mode but its single valid set was genuinely spotter-
assisted (avg_weight 41.57 vs base_weight 45.0, ratio 0.924 — a real
assisted rep, correctly excluded); the rest were `chains`/`eccentric`/
`flex`/`burnout`. **Before the fix**: `exact_mode_sessions=0,
compatible_mode_sessions=0` — every PARTIAL-compatible (chains/
eccentric) session's rows were silently discarded before mode
classification (root cause, section 16). **After the fix**: 6
`chains`/`eccentric` groups become usable PARTIAL evidence (capped by
`MAX_COMPATIBLE_SESSIONS=6`), but only 3 of those 6 fall within the
tier-3 load window around the most recent usable session's anchor
weight (this user's historical loads on this movement have shifted
materially over the last year — from the ~32–42 lb range in 2025 to
~28–31 lb in the most recent chains-mode session) — 3 sessions is
below `MIN_SESSIONS_TIER3=4`, so evidence correctly falls to **tier 4
(trend-only)**, confidence **LOW**, action **REBUILD**. This is a
genuine, disclosed evidence gap (reason code C below), not a residual
defect.

**A. current prescription (before → after)**: sets 3 (unchanged),
target reps 8 (unchanged), load `null → 30.0 lb`, confidence `LOW →
LOW` (unchanged), evidence tier `5 → 4`, action `REBUILD → REBUILD`
(unchanged), workload multiplier `1 (product_policy_fallback,
unchanged — see section 10)`.

**B. historical source data (post-fix usable PARTIAL pool, most-recent-
first)**: `57105043` (2026-05-30, chains, 3 sets, loads 28/31/30 lb,
reps 9/8/8, RIR 1.36/1.64/2.92) · `d3e1073e` (2026-03-17, eccentric, 2
sets, 38/38 lb, 8/8 reps) · `de589836` (2026-02-25, eccentric, 3 sets,
38/38/37 lb) · `95d75357` (2026-02-17, split mid-session — 2 `flex`-
mode sets at 38/41 lb correctly excluded [reason: UNKNOWN mode, fix
#2], 2 `eccentric`-mode sets at 37/41 lb correctly retained) ·
`5b9d582a` (2025-12-15, eccentric, 4 sets, 34/41/42/42 lb) · `24730ae6`
(2025-11-24, eccentric, 1 set, 32 lb). All rows real, unassisted,
as-of-safe. Two older PARTIAL sessions (`26c71f20` 2025-11-20,
`99920566` 2025-10-23) exist but are excluded only by the pre-existing,
unchanged `MAX_COMPATIBLE_SESSIONS=6` cap, not by either fix.

**C. exact reason confidence is LOW**: `SMART_WEIGHT_MODE_
FRAGMENTATION` (nearly all history is non-standard mode — this is
real, not a filtering artifact, now that both fixes are in) +
`LOAD_MATCH_FRAGMENTATION` (the anchor weight, 30 lb, only overlaps 3
of the 6 usable PARTIAL sessions within the tier-3 window; the
remaining 3 sit materially above it, 37–42 lb, reflecting a genuine
historical load shift, not noise). Quantified: 6 usable PARTIAL
sessions, 3 within the tier-3 ±15%/±4lb window (need ≥4), 0 usable
EXACT sessions.

## 6. Reverse Grip Barbell Triceps Extension progression forensic

Movement `3ab05f57-b729-4abe-a15e-00a1680f2547`. As-of-safe raw
history: 62 rows, 40 valid across 10 sessions (22 excluded for
ordinary reasons — warmup/assisted/out-of-range). 1 genuine standard-
mode session (2026-08-11, 5 sets, all correctly unassisted) + 6
eccentric sessions + 3 burnout (UNKNOWN, correctly always excluded).
**Before the fix**: `exact_mode_sessions=1, compatible_mode_sessions=2`
— only the 2 eccentric sessions whose individual rows happened to
still pass the (wrongly-scoped) assistance check contributed; the
other 4 were fully discarded. Evidence landed at **tier 4** (trend-
only), LOW confidence, REBUILD. **After the fix**: all 6 eccentric
sessions become usable, and this time **4 of the 6** fall within the
tier-3 window around the anchor (25.5 lb, from the most recent
standard-mode session) — meeting `MIN_SESSIONS_TIER3=4` exactly.
Evidence correctly advances to **tier 3 (mode_compatible_fallback_
used)**, confidence **MEDIUM**, action **ADD REPS**.

**A. current prescription (before → after)**: sets unchanged, target
reps unchanged at 10, load `25.5 lb` (was the tier-4 median-derived
load; now the tier-3 anchor-window load, materially the same figure),
confidence `LOW → MEDIUM`, evidence tier `4 → 3`, action `REBUILD →
ADD REPS`, workload multiplier `1 (product_policy_fallback, unchanged
— see section 10)`.

**B. historical source data (post-fix usable pool)**: exact — `4c743cee`
(2026-08-11, standard, 5 sets, 20/22.5/25.5/26.5/28 lb, all unassisted,
RIR 2.1–3.5). Compatible (eccentric, all 6 now usable) — `d86f6115`
(2026-06-13, 3 sets, 20/24/27.5 lb) · `9de69a9d` (2026-06-06, 4 sets,
17.5/20/21.5/23.5 lb) · `290b8b74` (2026-05-21, 3 sets, 20/21/21 lb) ·
`d3e1073e` (2026-03-17, 4 sets, 22.5/22.5/22.5/22.5 lb) · `5494fc9d`
(2025-12-18, 5 sets, 19/24/26.5/28/29.5 lb) · `b6b41de6` (2025-12-08, 4
sets, 19.5/22.5/24/24 lb).

**C. exact reason confidence was LOW (pre-fix)**: `SMART_WEIGHT_MODE_
FRAGMENTATION` + `FILTERING_BUG` (fix #1) — the 4 sessions that became
usable post-fix (`d86f6115`, `290b8b74`, `d3e1073e`, `b6b41de6`) had
every single row's avg_weight sit 5–9% above base_weight, the expected
eccentric-mode signature, not assistance; the old unconditional
`unassisted()` check discarded them entirely.

## 7. Historical evidence counts (summary)

| Movement | Exact sessions | Compatible sessions (before → after) | Tier (before → after) | Confidence (before → after) |
|---|---|---|---|---|
| Seated Overhead Press | 0 | 0 → 6 | 5 → 4 | LOW → LOW |
| Reverse Grip Triceps Extension | 1 | 2 → 6 | 4 → 3 | LOW → MEDIUM |

## 8. Mode compatibility findings

`mode_compatibility.py`'s policy itself (EXACT=standard, PARTIAL=
eccentric/chains/progressive, UNKNOWN=burnout/flex/anything else) was
**not** modified — it was already correct. The defect was entirely in
how `progression_v2._sessions_by_mode_class` consumed that policy: (1)
it applied a standard-mode-only assistance heuristic to every mode,
discarding PARTIAL evidence the policy explicitly allows; (2) it
classified an entire Tonal `activity_id` from only its first row's
mode, which could silently promote a genuinely UNKNOWN-mode (flex) set
to PARTIAL just because it shared an activity with an eccentric set.
Both fixes restore exactly the compatibility the existing policy
already grants — no equivalence was invented, and UNKNOWN-mode
(burnout/flex) rows remain excluded in every case checked (confirmed
both by the forensic data above and by `ModeScopedAssistanceCheckTests
.test_mixed_mode_session_never_leaks_unknown_mode_rows_as_partial`).

## 9. Paired-set findings

Reverse Grip Triceps Extension's post-fix tier-3 evidence pool shows a
coherent, evidence-grounded picture, not an inferred trend from
incomplete sets: the anchor session (Aug 11, standard mode) itself
shows reps climbing across the set structure (12→12→9→10→8 as load
rises 20→22.5→25.5→26.5→28 lb — a normal within-session fatigue curve,
not evidence of an untested rep ceiling) with RIR 2.1–3.5 throughout
(comfortably away from failure) — genuinely insufficient headroom-at-
ceiling evidence for a load jump, so `ADD REPS` (not `ADD LOAD`) is the
correct, conservative call, and `prescribe_v2` produced exactly that.
Seated Overhead Press's tier-4 pool does not support any progression
claim beyond REBUILD (by design — tier 4 never reaches the `tier in
(1,2,3)` branch in `prescribe_v2` that permits HOLD/ADD_REPS/ADD_LOAD/
REDUCE), which is the correct, non-inflated outcome.

## 10. Workload semantics findings

Audited `history.multipliers()` (the cable-workload-multiplier
evidence pipeline, separate from progression). It is **already**,
deliberately, standard-mode-only (`standard = [r for r in history if
mode(r) == "standard"]` — the module's own docstring: "Learn only
standard-mode one/two cable relationships, never flag-based x2") — no
`unassisted()` call exists in this path, so neither fix touches it,
and it should not: chains/eccentric sets have a fundamentally
different, mode-specific volume-to-load relationship that would
corrupt a cable-count estimate if mixed with standard-mode sets. For
both movements, only 1 distinct standard-mode **session** exists
(Seated Overhead Press: 1 set, assisted; Reverse Grip Triceps
Extension: 1 session, 5 sets) — `MIN_MULTIPLIER_SESSIONS=2` is not met
for either, so `multiplier_source="product_policy_fallback"` is
correctly retained for both. **Conclusion: (A) movement-specific
semantics genuinely cannot yet be estimated for these two movements —
this is not a pipeline defect.** No code change made here.

## 11. Workload comparator distribution

From the frozen case's `workload_sanity.envelope` (90-day window,
selected by `_select_window`): 22 comparable sessions (11.71 effective,
quality-weighted), normalized-workload median 14,776 (p25 12,276 / p75
20,854), workload-per-set median 802.08 (p25 740.66 / p75 938.6),
working-sets median 18 (p25 16 / p75 23), exercise-count median 5 (p25
4 / p75 6). Today's session: 18 sets (ratio to median 1.0), 6
exercises (ratio 1.2), estimated workload 16,232 (ratio to median
1.0985), workload-per-set ratio 1.1243. `known_workload_fraction =
0.9686`.

## 12. Why justification_sufficient was false

`justification_v2.justification_sufficient()` requires either one
STRONG binding justification or ≥2 independent MODERATE-or-stronger
ones. The frozen case's only binding justification was
`systemic_readiness` at strength **WEAK** (`counterfactual_band=high`
vs actual `good`, `set_delta=1`) — correctly insufficient by policy
(a single WEAK justification is explicitly documented as never
enough). This is accurate, not a bug — see section 14 for why it never
actually mattered to the verdict.

## 13. Counterfactual results

| Explanation | Cited? | Binding? | Counterfactual result | Contribution |
|---|---|---|---|---|
| Systemic readiness (good vs high) | yes | yes | +1 set at `high` vs actual `good` band | WEAK — real but small (1 of 18 sets) |
| Reduced goal posture | yes | no | actual dose (18) is *above* the goal-neutral dose (16), not reduced | NONE — cited but not applicable in this direction |
| More working sets | not cited | — | `set_count_ratio = 1.0` — exactly at median | none |
| More exercises | not cited | — | `exercise_count_ratio = 1.2` | small, below the 0.85 deviation threshold used by `decompose_gap` |
| Higher workload-per-set | not cited | — | `workload_per_set_ratio = 1.1243` | small, inside `WITHIN_RANGE_RATIO_BAND` |
| Local/program/progression | not cited | — | no blocking/reduced local state; dose came from `choose_dose`, not workload | not a workload driver here |

The ~10% absolute-workload excess never required an explanation in the
first place — see section 14.

## 14. Normal-range vs material-deviation policy review

**Audited, no defect found.** `WITHIN_RANGE_RATIO_BAND = (0.75, 1.35)`
in `workload_v2.py` already gates this correctly: `status` is set to
`WITHIN_PERSONAL_RANGE` whenever the ratio falls in that band,
**independent of `justification_sufficient`**. The frozen case's ratio
(1.0985) is squarely inside the band, so `status = WITHIN_PERSONAL_
RANGE`. Critically, `quality_verdict_v3` (`shadow.py`) only ever
penalizes a session for workload when `status.endswith("_UNEXPLAINED")`
or `status == "INSUFFICIENT_DATA"` — **neither branch is reachable
when status is `WITHIN_PERSONAL_RANGE`**, so `justification_sufficient
= False` is exposed in the raw `workload_sanity` payload (an honest,
correct field value) but was **never actually consulted** by the
quality-verdict aggregation for this case. The original QUESTIONABLE
verdict's two reasons were both purely progression-evidence-driven;
workload contributed zero reasons. The suspected "every non-median
result requires justification" defect (Phase 8's hypothesis) **does
not exist** in the current code — the gate already correctly
distinguishes normal variation (inside the ratio band, free) from
material deviation (outside the band, requires `justification_
sufficient`) from insufficient evidence. No code change made here.

## 15. Quality-verdict materiality review

**Audited, no additional defect found.** `quality_verdict_v3` already
separates two distinct progression-related messages by materiality:
`"Genuinely insufficient progression history for: [...]"` (tier 5,
truly sparse) vs `"Weak paired progression evidence for: [...]"` (tier
2–4, real-but-imperfect evidence) — it does not currently weight by
each movement's *dose contribution* (e.g. an isolation accessory
carrying 2 of 18 sets vs a primary compound carrying 4 of 18), which
would be a legitimate refinement, but building a proportional-
materiality weighting scheme is a genuine architecture change outside
this milestone's narrow scope (Phase 12 explicitly restricts changes
to the 6 listed evidence-pipeline areas; "materiality-aware session
quality aggregation" is listed, but the current binary "any LOW-
confidence movement adds an issue string" behavior is neither unsafe
nor factually wrong — it is conservative, not defective). No change
made; flagged as a known limitation (section 22) for a future,
separately-scoped milestone.

## 16. Bugs found (both fixed, `training_intelligence/calibration/
progression_v2.py`, `_sessions_by_mode_class`)

1. **FILTERING_BUG** — `unassisted()` (a standard-mode spotter-
   assistance heuristic) was applied unconditionally to every Smart
   Weight mode. `progression.py` (v1) scoped this correctly (`mode(r)
   == "standard" and unassisted(r)`); that scoping was lost when
   `progression_v2.py` generalized matching to every mode, making the
   module's own documented PARTIAL-mode cross-mode fallback
   unreachable in practice for movements whose non-standard-mode sets
   show avg_weight materially above base_weight (confirmed the norm
   for real eccentric/chains data — the mode's own mechanics, not
   assistance). Existing tests never caught this because their shared
   `_row()` fixture helper hardcoded `avg_weight = base_weight` for
   every synthetic row, so the divergence this bug mishandled never
   occurred in any prior test.
2. **SET_STRUCTURE_INCONSISTENCY** — session grouping was keyed by
   `activity_id` alone and classified an entire group from only its
   first row's mode, silently assuming "a session's sets share one
   logged mode combo" (the module's own prior docstring claim) — false
   for real data: one real workout mixed `flex` (UNKNOWN) warm-up sets
   with `eccentric` (PARTIAL) working sets under the same
   `activity_id`. Grouping is now keyed by `(activity_id, mode)`.

## 17. Code changes

- `training_intelligence/calibration/progression_v2.py`: `_sessions_
  by_mode_class` — assistance check now scoped to standard-mode rows
  only; grouping keyed by `(activity_id, mode)` instead of `activity_
  id` alone. `PROGRESSION_EVIDENCE_VERSION` bumped `1 → 2`. Module and
  function docstrings extended with the forensic rationale.
- `test_training_intelligence_calibration_v4.py`: `_row()` helper
  extended with an optional `avg_weight` parameter (previously always
  equal to `base_weight`, which is why the bug was invisible to this
  suite); 3 new tests in a new `ModeScopedAssistanceCheckTests` class.
- `test_training_intelligence_program_adaptation_postgres.py`: the
  snapshot replay-equivalence test's fixed `as_of` was bumped by 30
  minutes (`2036-01-01T08:00 → 08:30`) — the old timestamp collided
  with a snapshot row persisted by pre-V2.1 code in an earlier
  session, which made `load_adaptation_snapshot` load a stale,
  differently-versioned row and fail the equivalence check against a
  fresh replay. This is a real, disclosed limitation of the (unchanged,
  out-of-scope) snapshot module — `decision_id` carries no content/
  schema-version fingerprint — documented in the test and in section
  22, not fixed architecturally per Phase 12's boundary.

No other files were modified. No schema, migration, or database row
changes of any kind.

## 18. Before/after frozen-case comparison

| Field | BEFORE | AFTER |
|---|---|---|
| Seated Overhead Press confidence / tier / action | LOW / 5 / REBUILD | LOW / 4 / REBUILD |
| Reverse Grip Triceps Extension confidence / tier / action | LOW / 4 / REBUILD | MEDIUM / 3 / ADD REPS |
| Session `quality_verdict.verdict` | QUESTIONABLE | QUESTIONABLE |
| `quality_verdict.reasons` | weak evidence for **2** movements | weak evidence for **1** movement (Seated Overhead Press only) |
| `workload_sanity.status` | WITHIN_PERSONAL_RANGE | WITHIN_PERSONAL_RANGE (unchanged) |
| `decision.action` / `selected_session` | KEEP / upper_a | KEEP / upper_a (unchanged) |
| Estimated total volume | 16,232 | 16,626 (dose reallocation reflecting Reverse Grip Triceps Extension's new evidence-backed load) |

**Causal explanation of every change**: the two fixes made 6 real,
previously-discarded PARTIAL-mode sessions usable for both movements.
Seated Overhead Press's usable pool still doesn't cluster tightly
enough around its current anchor weight (a genuine, real load shift
over the past year) to clear the tier-3 bar, so it honestly remains
LOW/REBUILD — evidence-accurate, not under-fixed. Reverse Grip Triceps
Extension's pool does cluster sufficiently (4 of 6 sessions within the
tier-3 window), so it legitimately advances to MEDIUM/ADD REPS. The
session's action (KEEP, upper_a) is entirely unaffected because
progression-evidence tier never enters the decision hierarchy (section
19 confirms this architecturally, not just for this one case).

## 19. Historical replay impact

Re-ran the same 45-date (2-day cadence, June–September 2026)
RETROSPECTIVE PROGRAM SIMULATION used for the V2 report, post-fix:
action distribution, violation counts, duplicate-family streaks,
unresolved-slot counts, and time-fit distribution are **byte-identical
to the pre-fix V2 replay** (REST 37.8%, SUBSTITUTE 22.2%, REDUCE
26.7%, SHIFT 8.9%, KEEP 4.4%; 0 local-readiness violations; 0 dose-
envelope violations; 4 duplicate-family streaks; 0 unresolved required
slots) — direct, quantitative confirmation that neither fix touches
session/action selection (Phase 15's regression-safety requirement).
A post-fix aggregate over the same 45 dates' 160 resolved-exercise
instances: confidence distribution LOW 69 (43.1%), MEDIUM 79 (49.4%),
HIGH 12 (7.5%); evidence tier distribution 1→48, 2→22, 3→21, 4→60,
5→9. (No true "before" figure exists for this exact 45-date/160-
exercise sample without reverting and re-running under the pre-fix
code, which was not done given the milestone's narrow scope and the
already-rigorous exact frozen-case before/after in section 18; TKI-5.4's
own original baseline — a different 78-date/266-exercise sample,
quoted in `progression_v2.py`'s module docstring — measured 74.1% LOW
under the original v1 comparator, for directional context only.)

## 20. Test totals

New: 3 tests in `ModeScopedAssistanceCheckTests`
(`test_training_intelligence_calibration_v4.py`) — all pass, plus the
full pre-existing 36 tests in that file (39/39 total, 0 regressions).
1 pre-existing opt-in Postgres test's fixture was corrected (section
17); the full opt-in Postgres suite for this area (`test_training_
intelligence_calibration_v4_postgres.py` + `test_training_
intelligence_program_adaptation_postgres.py`) is 17/17 passing.
**Full backend suite: 892 passed (868 in-memory + 24 isolated cache
batch), 101 skipped, 0 failed** — net +3 vs the V2 baseline (889),
exactly the 3 new tests added, zero regressions.

## 21. Performance impact

No new SQL queries, no query-shape changes, no N+1 pattern introduced
— both fixes are pure in-memory filtering/grouping changes over
already-loaded rows (the same `rows` list `evidence()` already
received). `_sessions_by_mode_class` remains O(n) over the per-
movement row list it already iterated. No measurable change to the
17-query/~4.5s per-decision baseline established in the V2 report.

## 22. Known limitations

1. **Seated Overhead Press remains LOW-confidence** — correctly so.
   Real evidence does not currently support more; this is not left
   over from an unfixed defect (confirmed by direct forensic
   inspection, section 5).
2. **`_sessions_by_mode_class`'s per-mode session cap
   (`MAX_COMPATIBLE_SESSIONS=6`) can still drop older, potentially
   load-relevant sessions** (two older Seated Overhead Press PARTIAL
   sessions were excluded purely by this pre-existing, unchanged cap)
   — unrelated to either fix, a pre-existing TKI-5.4 design constant,
   out of this milestone's scope.
3. **Workload-multiplier fallback for both movements is a genuine,
   unresolved evidence gap** (section 10) — only 1 standard-mode
   session exists for each; `MIN_MULTIPLIER_SESSIONS=2` is a real,
   deliberate floor this milestone did not touch or lower.
4. **Session quality-verdict aggregation is not dose-materiality-
   weighted** (section 15) — a LOW-confidence 2-set accessory and a
   LOW-confidence 4-set primary compound currently contribute the same
   single issue string. Flagged, not fixed, per Phase 12's scope
   boundary; a legitimate candidate for a future, separately-scoped
   milestone.
5. **The snapshot module's `decision_id` carries no content/schema-
   version fingerprint** (section 17) — a snapshot persisted by an
   older code version will silently be treated as replay-equivalent
   (or, as found here, non-equivalent in a confusing way) by a newer
   version computing the same `(as_of, program_id)` decision. Out of
   this milestone's scope (`snapshot.py` is reused unchanged); worth a
   dedicated fix in a future milestone if snapshot replay across code
   changes becomes a real product need.
6. **No dedicated "Aug-7 workload fixture" exists in this repository**
   — only `sep12_legacy_fixture_v3_analysis`/`sep12_legacy_fixture_
   analysis` (`legacy_fixtures.py`) are persisted, versioned fixtures,
   and both were re-run clean as part of the full test suite
   (`Sep12V3FixtureTests`, included in the 39/39 above). No fabricated
   fixture was created to satisfy this instruction; reported honestly
   rather than invented.
7. **The current live shadow case (Sep-14) surfaces an unrelated,
   pre-existing dose-shortfall flag** (`"Selected movements could not
   safely absorb the chosen dose."`) on a REDUCE day — this is
   `quality_verdict_v3`'s pre-existing `total < target` check
   (TKI-5.4), entirely independent of progression evidence and outside
   this milestone's two named causes; not investigated or changed here.

## 23. Explicit confirmations

- **No Production changes**: every edit is in the Development repo.
- **No Render deployment.**
- **No iOS changes**: zero files in `HealthInteligence-dev` touched.
- **No Today Plan integration**: `training_intelligence/programs/
  adaptation/` remains unimported by `todays_plan.py`/`todays_plan_
  store.py`; full-suite pass count confirms zero behavioral change
  outside the targeted module.
- **No program-selection redesign**: `decision.py`, `feasibility.py`,
  `context.py` were not touched; the 45-date replay's action
  distribution is byte-identical before/after (section 19).
- **No output tuning to Tia**: Tia was never called or referenced.
- **No artificial confidence inflation**: Seated Overhead Press was
  left LOW/REBUILD because real evidence does not support more: no
  threshold was lowered, no window was widened, no synthetic row was
  added. Reverse Grip Triceps Extension's improvement to MEDIUM came
  strictly from previously-discarded, genuinely real historical sets
  becoming visible to the existing, unmodified tiering thresholds.

## Commit

Explicit files staged (never `git add .`/`git add -A`): `training_
intelligence/calibration/progression_v2.py`, `test_training_
intelligence_calibration_v4.py`, `test_training_intelligence_program_
adaptation_postgres.py`, this report. Pushed to `origin/develop`,
never merged to `main`.

## Final validation

**`V2.1 LIVE SHADOW REVIEW — QUESTIONABLE`**

Both the frozen Sep-13 case (KEEP, upper_a) and a fresh current-day
Sep-14 case (REDUCE, upper_a) remain QUESTIONABLE after the fix — for
genuine, accurately-represented reasons in both: the frozen case only
because Seated Overhead Press's real evidence still falls short of
tier 3 (down from 2 flagged movements to 1); the current-day case
additionally surfaces an unrelated, pre-existing, out-of-scope dose-
shortfall flag on a REDUCE day (section 22, item 7). No hard
readiness/sequence/dose-envelope violation exists in either case.

**`PROGRAM INTELLIGENCE V2.1 DEVELOPMENT PASS — EVIDENCE CALIBRATION VALIDATED`**

Two real, narrowly-scoped, verified evidence-pipeline defects were
found and fixed with quantified before/after impact on the exact
original case; one genuinely weak movement was correctly left LOW/
REBUILD rather than forced toward confidence it does not have;
workload-sanity and quality-verdict aggregation were both audited and
found to already correctly distinguish normal variation from material
deviation (no code change needed there); session/action selection is
provably unaffected (byte-identical 45-date replay); the full backend
suite passes with zero regressions (892 passed, 101 skipped, +3 new
tests); and no Production/Render/iOS/Today-Plan boundary was crossed.
The engine now accurately represents the strength of evidence it
actually has — which, for one of the two originally-flagged movements,
is still genuinely LOW, and is correctly reported as such. Not
proceeding to V3 automatically — awaiting review.
