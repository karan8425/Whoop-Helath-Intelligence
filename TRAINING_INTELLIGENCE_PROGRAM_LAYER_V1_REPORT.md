# Program Intelligence Layer V1 Report

Foundational, additive, isolated. Nothing in `training_intelligence/
programs/` is imported by `main.py`'s live routes beyond the 3 new
admin-only, read-only endpoints added this milestone; `/api/v1/todays-
plan`, `todays_plan.py`, `todays_plan_store.py`, and every TKI-5.x/6/7
module are completely untouched.

## 1. Starting/ending commit

Starting HEAD: `9de2143` (verified clean, in sync with `origin/develop`
before any edit). Ending commit: see section "Commit" below.

## 2. Files changed

New: `supabase/migrations/20260913120000_add_program_intelligence_v1.sql`,
`training_intelligence/programs/__init__.py`, `models.py`, `taxonomy.py`,
`repository.py`, `service.py`, `tonal_mapping.py`, `seed.py`,
`mapping_seed.py`, `test_training_intelligence_programs.py`,
`test_training_intelligence_programs_postgres.py`,
`TRAINING_INTELLIGENCE_PROGRAM_LAYER_V1_REPORT.md`. Modified:
`main.py` (3 new admin-only GET routes + a `Query` import - no existing
route touched).

## 3. Migration/schema summary

Seven tables, applied to the Development database and verified live
(`information_schema`/`pg_indexes` checks): `training_programs`,
`training_program_weeks`, `training_program_sessions`,
`training_session_slots`, `program_movement_mappings`,
`user_training_programs`, `user_program_session_state`. Explicit FKs
(`ON DELETE CASCADE` from program -> weeks/sessions -> slots),
CHECK constraints (status enums, `set_min <= set_max`, `rep_min <=
rep_max`, `rir_min <= rir_max`, positive durations/versions, bounded
`days_per_week`/`day_of_week`), UNIQUE constraints (`slug`,
`(program_id, session_key)`, `(session_id, slot_index)`,
`(tonal_movement_id, movement_pattern, primary_muscle, exercise_role)`),
and a **partial unique index** enforcing at most one `active`
enrollment per `user_id` (DB-enforced, not application-checked).
`training_program_sessions`'s `sequence_index` uniqueness uses an
expression index (`COALESCE(program_week_id, program_id)`) so
phase/week structure stays fully optional. Idempotent
(`CREATE TABLE/INDEX IF NOT EXISTS`) and applied directly against the
Development database via the existing Keychain-scoped `DATABASE_URL`
procedure - never printed, never written to disk.

## 4. Existing Tonal tables reused

`public.tonal_movements` is the sole FK target
(`program_movement_mappings.tonal_movement_id -> tonal_movements.
movement_id`, UUID) - no duplicate movement-identity table was
created. `mapping_seed.py` reuses, unchanged: `training_intelligence.
calibration.history.pattern()` (movement-pattern classification) and
`training_intelligence.stimulus.mapping.classify_muscle_groups()`
(primary/secondary muscle classification) - the exact same functions
TKI-5.2's paired-progression comparator already uses. Generic/custom
movements (`is_generic`/`custom_movement`) are excluded everywhere,
matching the existing TKI-5.x exclusion convention exactly.

## 5. Taxonomy changes

**Reused verbatim, no fork**: `training_intelligence.dose.goal_policy.
GOAL_MODES` (goals), `training_intelligence.stimulus.taxonomy.
CANONICAL_MUSCLES` (the 10-group muscle vocabulary), `integrations.
tonal.training_priority.SESSION_TEMPLATES` keys (session families),
`training_intelligence.calibration.history.COMPOUNDS` (the 6 compound
movement patterns). A self-check
(`taxonomy.assert_no_conflicting_aliases()`, run at import time)
asserts these stay mirrored and that no competing alias is ever
introduced.

**One explicit reconciliation, not a silent fork**: the spec's example
goal name "hypertrophy_gain" does **not** exist as a separate value -
the existing `lean_bulk` already documents `training_objective =
"maximize_hypertrophy_stimulus"` in `goal_policy.py`. The hypertrophy
seed program is stored with `goal_mode = "lean_bulk"`, not a new
conflicting alias.

**New, with no existing equivalent to conflict with**: `EXPERIENCE_
LEVELS` (beginner/intermediate/advanced), `SPLIT_TYPES` (upper_lower/
full_body/push_pull_legs/body_part/hybrid), `ISOLATION_MOVEMENT_
PATTERNS` (elbow_flexion, elbow_extension, lateral_raise, rear_delt_
isolation, calf_raise, core_flexion, core_anti_extension, core_anti_
rotation - `history.pattern()` only has one coarse "isolation" bucket
and never distinguished these), `MAPPING_QUALITY` (DIRECT/CLOSE/
FUNCTIONAL/UNSUITABLE).

## 6. Seed program structures

Two ORIGINAL programs (`seed.py`), sharing session-building code
(`_upper_a_slots`/`_upper_b_slots`/`_lower_a_slots`/`_lower_b_slots`,
each parameterized by `goal_mode` - no duplicated slot-list code
between the two programs):

- **`hypertrophy_upper_lower_4d_v1`** (`goal_mode=lean_bulk`,
  intermediate, upper_lower, 4 days/week): Upper A (session_family
  "Upper Mixed"), Lower A ("Lower Body"), Upper B ("Upper Mixed"),
  Lower B ("Lower Body").
- **`lean_cut_upper_lower_4d_v1`** (`goal_mode=lean_cut`, same
  structural backbone): the SAME 4 sessions/slot patterns, with
  primary/secondary-compound `set_max` trimmed by 1
  (`_SET_MAX_TRIM_BY_GOAL`) - directly reflecting `goal_policy.py`'s
  own documented LEAN_CUT posture ("avoid unnecessary large volume
  increases... targets the lower-middle of the feasible range"), not
  a new or competing claim.

Neither program's description/reference_notes reproduce any published
program's specific text (Phase 6 - see section "Confirmations" below).
`default_session_duration_min` and `duration_weeks` are both `NULL`
for both programs - no session-duration value (45 minutes or
otherwise) is hard-coded anywhere; the only place a duration can ever
be recorded is `user_training_programs.preferred_session_duration_min`,
an explicit runtime/user preference.

## 7. Number of slots

23 slots per program (6 + 5 + 6 + 6 across the 4 sessions), **46
slots total** across both seed programs - verified live via
`service.get_program_structure()`.

## 8. Mapping coverage

194 `program_movement_mappings` rows generated from real, already-
synced `tonal_movements` data (84 DIRECT, 108 CLOSE, 2 FUNCTIONAL - 0
UNSUITABLE were generated, since the classifier only ever proposes a
mapping where a genuine pattern+muscle match exists) across the exact
20 distinct `(movement_pattern, primary_muscle, exercise_role)`
combinations the two seed programs' 46 slots require.

| | Slots (of 46) |
|---|---|
| Slots with a DIRECT candidate | 44 |
| DIRECT-only... (CLOSE-only, no DIRECT) | 2 |
| FUNCTIONAL-only (no DIRECT/CLOSE) | 0 |
| **Unmapped** | **0** |

The 2 CLOSE-only slots are both the `lateral_raise/shoulders/isolation`
slot (Upper B, one per program) - only one real movement ("Lateral
Raise" itself) is a bilateral DIRECT match; "Front Raise"/"Barbell
Front Raise" were deliberately classified FUNCTIONAL (not CLOSE/DIRECT)
since they work a different movement plane, so they never silently
outrank an actual lateral raise. Zero required slots are unmapped -
live-verified by `test_every_required_slot_has_at_least_one_candidate`
(opt-in Postgres) walking every required slot in both real seed
programs.

## 9. Sample candidate output

Live output from `service`/`tonal_mapping` against the real Development
Tonal catalog (top result per target shown; full ranked lists include
6-14 candidates each):

- **horizontal_press / chest / primary_compound** (11 candidates):
  "Bench Press" - DIRECT, score 336.0, reasons: `horizontal_press
  pattern match`, `chest primary-muscle match`, `primary_compound role
  match`, `mapping_quality=DIRECT`, `confidence=0.90`.
- **horizontal_pull / back / primary_compound** (8 candidates):
  "Half Roll Down with Wide Row" - DIRECT, score 336.0 (same reason
  shape as above, pattern/muscle/role substituted).
- **squat_lunge / quads / primary_compound (knee-dominant lower)**
  (14 candidates): "Squat Jack" / "Barbell Front Squat" - DIRECT,
  score 336.0; "Bodyweight Alternating Reverse Lunge" - CLOSE, score
  228.0 (unilateral/alternating variant of the identical pattern+
  muscle match).
- **hinge / hamstrings / primary_compound (hip hinge)** (6
  candidates): "Neutral Grip Deadlift" / "Barbell RDL" - DIRECT, score
  336.0; "Barbell Single-Leg RDL" - CLOSE, score 228.0.
- **elbow_flexion / biceps / isolation** (11 candidates): "Barbell
  Biceps Curl" / "Hammer Curl" / "Reverse Grip Biceps Curl" - all
  DIRECT, score 336.0.

Every result carries its own `reasons` list explaining the match -
never a bare score.

## 10. Query count/performance for loading one complete program

`service.get_program_structure('hypertrophy_upper_lower_4d_v1')`:
**4 SQL queries** (program lookup, weeks, sessions, and one batched
`session_id = ANY(...)` query for all 23 slots across all 4 sessions -
never one query per session or per slot). Measured wall time: ~506ms
for the full load over a single connection - consistent with this
environment's per-connection round-trip baseline observed throughout
this project's live-DB work (not a per-query cost; the query count
itself is the scalability guarantee, not the absolute latency here).

## 11. Test counts

New: `test_training_intelligence_programs.py` (16 pure/in-memory tests
- taxonomy alignment/no-conflicting-alias checks, Tonal-mapping-engine
DIRECT>CLOSE>FUNCTIONAL ordering, UNSUITABLE-ranks-lowest, required-
accessory demotion, generic/custom exclusion, deterministic ordering,
personal-usage tie-break-only behavior, explainable reasons) - all
pass. `test_training_intelligence_programs_postgres.py` (20 opt-in
Postgres tests - schema/constraint enforcement, repository list/load/
ordering, real-seed-program coverage, user-enrollment state incl. the
DB-enforced at-most-one-active-per-user index, and the temporal-
signature-readiness check) - all pass.

**Full backend suite**: 836 passed (up from the pre-milestone 820: +16
new pure tests), 89 skipped (up from 69: +20 new opt-in Postgres
tests), 0 failed, plus `test_daily_pipeline_cache.py`'s separate batch
(24 passed, unchanged) - **860 passed total, 0 regressions** from the
pre-existing TKI-5.x/6/7 baseline.

## 12. Known limitations

1. `exercise_role` (primary_compound vs. secondary_compound) is
   correctly modeled as a program-design choice, not an intrinsic
   movement property - the SAME movement pool maps to both roles.
   This is a deliberate design decision (documented in
   `mapping_seed.py`), not an oversight, but it means the mapping
   table does not by itself express "this specific movement is only
   ever secondary" - that remains a slot-level choice.
2. `movement_subpattern` sub-classification (elbow_flexion vs.
   extension, lateral vs. front raise, the three core sub-patterns) is
   a NEW, name-keyword heuristic layered on top of (never modifying)
   `history.pattern()`'s existing coarse buckets - documented as
   PRODUCT POLICY, not a physiological claim, and not exhaustively
   validated against every one of the 138 "unknown"-pattern real
   movements this survey surfaced (only the ~55 relevant to the two
   seed programs' actual slots were classified).
3. No `training_program_weeks` rows were seeded for either program
   (both programs repeat indefinitely, per their `duration_weeks =
   NULL`) - the phased-program path is schema-complete but untested
   against a real phased program in this milestone.
4. `user_training_programs`/`user_program_session_state` are
   single-tenant-compatible (`user_id` defaults to the placeholder
   `'primary'`, matching every other table in this schema having no
   real multi-user convention today) - genuinely supporting multiple
   real users is a data/access-control milestone, not a schema change,
   later.
5. No daily-adaptation logic exists yet (by design - explicitly out of
   scope this milestone). `service.py`'s functions return program
   knowledge and static Tonal-mapping candidates only.

## 13. Explicit confirmations

- **No external workout content was copied**: both seed programs are
  original abstract slot definitions (movement pattern + primary
  muscle + exercise role + rep/set/RIR/rest ranges); no exercise names,
  authored program text, or copyrighted descriptions from Muscle &
  Strength or any other source were stored or scraped. `source_type =
  'original'` for both; `reference_notes` describes general, widely-
  taught training principles and this codebase's own `goal_policy.py`
  documentation, not a specific publication.
- **No Production changes**: every file touched lives in the
  Development repo only; no Production database, Production Render
  service, or `main` branch was accessed or modified.
- **No Render deployment**: this migration was applied directly to the
  Development database via the Keychain-scoped `DATABASE_URL`
  procedure - no Render deploy of any kind occurred.
- **No iOS changes**: zero files in `HealthInteligence-dev` were read
  or modified this milestone.
- **Current Today Plan behavior unchanged**: confirmed by the full
  backend suite passing with the exact same pre-milestone pass count
  plus only the new tests added (no existing test's outcome changed);
  no training-intelligence mobile feature flag was touched, and
  nothing in `training_intelligence/programs/` is imported by
  `todays_plan.py`, `todays_plan_store.py`, or any TKI-5.x/6/7 module.

## Commit

Explicit files staged (never `git add .`/`git add -A`):
`supabase/migrations/20260913120000_add_program_intelligence_v1.sql`,
`training_intelligence/programs/__init__.py`, `models.py`,
`taxonomy.py`, `repository.py`, `service.py`, `tonal_mapping.py`,
`seed.py`, `mapping_seed.py`,
`test_training_intelligence_programs.py`,
`test_training_intelligence_programs_postgres.py`, `main.py`,
`TRAINING_INTELLIGENCE_PROGRAM_LAYER_V1_REPORT.md`. Pushed to
`origin/develop`, never merged to `main`.

## Final verdict

**PROGRAM INTELLIGENCE V1 DEVELOPMENT PASS — FOUNDATION VALIDATED**

All 12 acceptance criteria are met with live evidence: program
definitions persist in Postgres (7-table schema, applied and
verified); sessions are abstract training intent (slots carry no
exercise name, only pattern/muscle/role/range); Tonal mapping resolves
every one of the 46 real slots to existing canonical `tonal_movements`
rows deterministically and explainably (0 unmapped); no user-specific
constant is embedded (no hard-coded tonnage, no hard-coded 45 minutes,
no single fixed workout structure - `preferred_session_duration_min`
is the only place a duration is ever set, and only at runtime); the
two seed programs work end-to-end (4 sessions, 23 slots each, live-
verified through the full repository/service/mapping stack); existing
TKI/Today Plan behavior is unchanged (860 passed, 0 regressions); the
Development database migration applied and was verified live; and no
Production/Render/iOS change occurred. Not proceeding to Program
Intelligence V2 automatically, per instruction.
