# Intelligence Architecture M1.0 Report — V2.1 Baseline Freeze + Decision Ledger Foundation

Shadow-observation infrastructure only. V2.1's decision logic was not
read for behavioral changes - only its return value is now, optionally,
recorded after the fact. No candidate generation, ranking, Decision
Utility, or ML was implemented. No Today Plan/mobile/iOS/Production/
Render change.

## 1. Starting SHA

`7437e50` (`develop`) - Program Intelligence V2.1: progression
evidence-pipeline fixes. Working tree was clean; `origin/develop` was
still at `f28caa7` (the V2.1 push from the prior session had been
blocked by the permission classifier and never completed) - HEAD 1
commit ahead of origin, not dirty.

## 2. Frozen V2.1 SHA

`7437e5015743036339f6677f45825a10593cc405`

## 3. Tag status

Annotated tag `program-intelligence-baseline-v2.1-frozen` created
(tag object `23a5135a1303806c865a278cbc72e0f6dadc8425`), pointing at
`7437e50`. No prior tag of this name existed - created fresh, not
recreated over an existing one. Message: "Frozen Program Intelligence
V2.1 baseline for future replay and architecture comparisons."

Smallest authoritative pre-freeze validation run: `test_training_
intelligence_calibration_v4.py` + `test_training_intelligence_program_
adaptation.py` (the two pure test files directly covering V2.1's
progression-evidence fix and the V2 adaptation engine it sits on) - 68
passed, 0 failed, confirming the baseline was healthy before tagging.
V2.1's own completion report (`TRAINING_INTELLIGENCE_PROGRAM_LAYER_
V2_1_REPORT.md`) verdict: `PROGRAM INTELLIGENCE V2.1 DEVELOPMENT PASS
— EVIDENCE CALIBRATION VALIDATED`.

## 4. Current V2.1 entry point

`training_intelligence.programs.adaptation.shadow.build_daily_program_
adaptation(user_id, as_of, ...)` - unchanged, unmodified this
milestone. Inputs: program schedule context (`context.py`, real
enrollment or a non-persistent `enrollment_override`), program
structure (`programs.repository`/`service`, V1 schema), WHOOP systemic
readiness (`integrations.tonal.workout_prescription._latest_
readiness`), local Tonal-derived muscle readiness
(`integrations.tonal.muscle_readiness.calculate_muscle_readiness`),
paired historical load/rep evidence (`calibration.history.load_
history`, `calibration.progression_v2.prescribe_v2`), and workload
sanity (`calibration.workload_v2.workload_sanity_v3`). Output: the
`result` dict documented in the V2/V2.1 reports, whose `result
["decision"]` is the single, selected recommendation. Existing
snapshot/replay object: `training_intelligence.programs.adaptation.
snapshot` (TKI-5.3-style, insert-only, a DIFFERENT, pre-existing,
untouched store - `training_decision_snapshots` table - not the
ledger). No Today Plan interaction: `programs/adaptation/` is not
imported anywhere in `todays_plan.py`/`todays_plan_store.py`, confirmed
again by this milestone's own zero-regression full-suite run.

## 5. New schema

Three tables (`public.recommendation_events`, `public.recommendation_
candidates`, `public.recommendation_outcomes`) - full DDL in section 6.
No existing table was altered.

## 6. Migration files

`supabase/migrations/20260914120000_add_recommendation_ledger.sql` -
idempotent (`CREATE TABLE/INDEX IF NOT EXISTS`, a guarded `DO $$`
block for the one non-idempotent `ALTER TABLE ADD CONSTRAINT`),
matching `20260913120000_add_program_intelligence_v1.sql`'s own stated
convention. Mirrored in Python by `training_intelligence.ledger.
schema.ensure_tables()` (called by the ledger's own tests and by the
live-validation script) - same DDL, safe to run whether or not the
migration file has been separately applied.

## 7. Domain/service files

`training_intelligence/ledger/`: `schema.py` (DDL/`ensure_tables()`),
`hashing.py` (`canonical_json`, `compute_input_hash`, `compute_
decision_id` - pure), `repository.py` (`create_recommendation_event`,
`add_recommendation_candidates`, `create_recommendation_event_with_
candidates`, `record_recommendation_outcome`, `get_recommendation_
event`, `get_recommendation_candidates`, `get_recommendation_outcome`,
`get_recommendation_for_replay`, plus `_validate_candidates`/
`LedgerValidationError`/`LedgerWriteError`), `v2_1_integration.py`
(`record_program_adaptation_recommendation`, `build_ledger_write_
kwargs` - the one narrow V2.1 integration point).

## 8. Exact integration point

`training_intelligence/ledger/v2_1_integration.py`'s `record_program_
adaptation_recommendation()`. It calls the existing, unmodified
`build_daily_program_adaptation()`, waits for the FULL result, and
only then builds and writes the ledger record. Nothing in `programs/
adaptation/shadow.py` (or any file under `programs/adaptation/`) was
edited. Calling this new function is entirely opt-in - no existing
call site (the admin diagnostic endpoint, the historical-replay
scripts, any test) was changed to route through it; V2.1 continues to
be reachable and usable exactly as before, with or without the ledger.

## 9. Confirmation V2.1 behavior unchanged

`git status --short` before staging shows only new, untracked files
(`supabase/migrations/20260914120000_...`, `training_intelligence/
ledger/`, the two new test files, this report) - zero modifications to
any pre-existing file, including every file under `training_
intelligence/programs/`, `training_intelligence/calibration/`, and
`main.py`. The full backend suite's pass count for every pre-existing
test is identical to the V2.1 baseline (section 17) - net change is
exactly +34 new tests, 0 regressions, 0 modified assertions in any
inherited test file.

## 10. State snapshot contract

`v2_1_integration._build_state_snapshot()` curates exactly 7 named
top-level result fields (`active_program`, `nominal_session`,
`candidate_sessions`, `program_progress`, `readiness`, `systemic_
capacity`, `time_context`) plus `engine_version_detail` (all 7 of the
engine's own internal sub-version constants), `as_of`, and `feature_
schema_version` - an explicit allowlist, not "everything the result
returns," so an additive future result field never silently changes
what gets persisted. This is precisely the input set the V2 report
already documents as the engine's real decision inputs (program
schedule, progress, readiness, systemic capacity, time context) -
sufficient to explain "what the engine knew" without a raw dump of
unrelated data.

## 11. Input hash design

`hashing.compute_input_hash(state_snapshot)` = SHA-256 of `hashing.
canonical_json(state_snapshot)` (sorted keys, tight separators,
`default=str` for datetimes/UUIDs/Decimals) - deterministic
regardless of dict insertion order; two decisions with identical
curated state hash identically, any real difference changes the hash.
`hashing.compute_decision_id()` is a separate, ALSO-deterministic
uuid5 of `(recommendation_type, user_id, as_of, engine_name)` - the
natural key used for `ON CONFLICT (decision_id) DO NOTHING` insert-
only idempotency, mirroring `training_decision_snapshots`' own
established convention (re-recording an identical decision is a safe
no-op, not a duplicate row or an error).

## 12. Candidate representation

For V2.1: exactly one row, `candidate_key="v2_1-selected"`, `rank=1`,
`selected=True`, `eligible=True`, `candidate_payload` = the real,
complete `result["decision"]` object (action, selected_session,
confidence, dose, feasible_range, resolved_exercises, workload_sanity,
quality_verdict - everything V2.1 itself produced, verbatim). `feature_
snapshot`/`score_components`/`total_score`/`rejection_reasons` are all
`NULL` - no feature vector or score exists yet for a single-
recommendation engine, and none was invented. These are exactly the
fields a future multi-candidate engine (V3) starts populating, in the
SAME table, with no migration redesign - verified structurally (the
table already accepts N rows per `decision_id`, gated only by the
`UNIQUE(decision_id, candidate_key)` constraint, not a row-count
limit) and behaviorally (nothing in `repository.py` assumes exactly
one candidate; `_validate_candidates` only rejects >1 *selected*, never
>1 candidate).

## 13. Outcome representation

`recommendation_outcomes` is a separate table, one row per
`decision_id` (`UNIQUE` index), written by `record_recommendation_
outcome()` as an `INSERT ... ON CONFLICT (decision_id) DO UPDATE` that
`COALESCE`s each nullable field forward and always bumps `updated_at` -
appended once, updated in place as more is learned, never duplicated,
and never touching `recommendation_events`. No outcome was recorded
this milestone (no iOS UX exists to produce one) - `get_recommendation_
for_replay()`'s `outcome` field is `None` for the live-validated
decision, correctly reported as "absent / not yet observed."

## 14. Database integrity rules

- Primary keys: `id UUID DEFAULT gen_random_uuid()` on all three
  tables.
- FKs: `recommendation_candidates.decision_id` and `recommendation_
  outcomes.decision_id` both `REFERENCES recommendation_events
  (decision_id) ON DELETE CASCADE`; `recommendation_events.selected_
  candidate_id` and `recommendation_outcomes.chosen_candidate_id`
  reference `recommendation_candidates(id)` - the events→candidates
  FK is `DEFERRABLE INITIALLY DEFERRED` (checked at COMMIT, not per-
  statement) precisely because the event and its selected candidate
  are written in the same transaction and either row may exist first.
- Uniqueness: `recommendation_events.decision_id UNIQUE`; `recommendation_
  candidates (decision_id, candidate_key) UNIQUE`; a partial unique
  index `ON recommendation_candidates (decision_id) WHERE selected =
  TRUE` (DB-enforced "at most one selected candidate per decision" -
  verified directly against a raw, Python-validation-bypassing SQL
  INSERT in an opt-in Postgres test); `recommendation_outcomes.
  decision_id UNIQUE`.
- Lookups: `(user_id, as_of DESC)` and `(recommendation_type, as_of
  DESC)` and `(engine_name, engine_version)` indexes on events;
  `(decision_id)` on candidates; `(user_id, observed_at DESC)` on
  outcomes.
- Nullability: every genuinely-optional field (`prior_version`,
  `selected_candidate_id`, `selected_recommendation`, `reason_codes`,
  `validator_result`, `latency_ms`, `rank`, `feature_snapshot`, `score_
  components`, `total_score`, `rejection_reasons`, every outcome field)
  is explicitly `NULL`-able; every field the milestone spec lists as
  required is `NOT NULL`.
- Deletion: `ON DELETE CASCADE` from candidates/outcomes to events for
  referential-integrity safety only - no DELETE endpoint or code path
  exists anywhere in this milestone; these tables are treated as
  append-mostly (events/candidates: insert-only; outcomes: insert-or-
  update-by-decision_id) for the whole lifetime of this feature so far.
- The decision event is never mutated to represent an outcome -
  `create_recommendation_event`/`add_recommendation_candidates` never
  UPDATE `recommendation_events` except the one, same-transaction
  `selected_candidate_id` backfill performed immediately after the
  selected candidate's row is known to exist - not a later mutation.

## 15. Data-minimization review

`state_snapshot`/`candidate_payload` carry only: an opaque `user_id`
(the same `'primary'` placeholder identity already used throughout
`programs.repository`), program/session identifiers and names (training
metadata, not user identity), WHOOP-derived physiological metrics
(recovery score, readiness band - this user's own already-computed
training data, the entire point of this system, not name/email/DOB),
and the recommendation itself (exercises, sets, reps, loads). No
credential, token, or secret is ever passed to the ledger - verified
by a pure test (`test_no_identity_or_secret_fields_in_snapshot`)
scanning a real-shaped snapshot for `email`/`first_name`/`last_name`/
`dob`/`date_of_birth`/`password`/`token`/`secret`/`ssn` markers, and by
inspection: `v2_1_integration.py` never imports anything from `db.py`'s
credential/token functions, `config.py`, or `whoop_profiles`. No LLM
prompt is stored anywhere (no LLM is invoked this milestone).

## 16. Dev live-write validation

Verified live decision: **`decision_id = 9e874a7e-b36b-5d81-b71c-
5638c9317e43`** (`as_of = 2026-09-14T16:41:37.390612+00:00`).

```
DECISION
  decision_id: 9e874a7e-b36b-5d81-b71c-5638c9317e43
  as_of: 2026-09-14 16:41:37.390612+00:00
  engine: training_intelligence.programs.adaptation.shadow.build_daily_program_adaptation
  engine_version: 1
  feature_schema_version: 1
  input_hash: 1c37cdf1bcb1e6318aae64328f784f15eb8d7c50a18cc67b4faa3b085bad78af
  input_hash_verified: True

STATE
  goal: lean_bulk
  program: hypertrophy_upper_lower_4d_v1
  nominal_session: upper_a
  readiness_band: moderate  recovery_score: 66.0
  time_constraint_source: unknown
  source_cutoff: 2026-09-14 16:41:37.390612+00:00

CANDIDATES
  candidate_count: 1
  key=v2_1-selected  selected=True  eligible=True  payload_bytes=12624  action=REDUCE
  V2.1 EXPECTATION candidate_count == 1: True

RECOMMENDATION
  selected_session: upper_a
  movement_count: 6
  working_sets: 15
  reason_code_count: 7
  quality_verdict: QUESTIONABLE

OUTCOME
  absent / not yet observed
```

**Stored-vs-live integrity, isolated correctly** (same `as_of`, write
then immediate read-back within one run, comparing 400 flattened
leaf fields of the decision object): **0 diffs, exact match.**

A first, less careful validation attempt earlier in this milestone
compared a stored payload against a decision recomputed at a
*different* `as_of` minutes later and flagged `False` - investigated
immediately (not hidden): flattening both objects showed 0 type
mismatches and exactly 4 value differences, all in `recency_days`/
`days_since_training` fields that are *supposed* to change with a
different `as_of` by design. Repeating the comparison correctly (same
`as_of`, single run, write-then-read) on two further live decisions
produced a byte-exact match both times (400/400 and, for the earlier
attempt's own decision_id `38b2bad0-...`, consistent with the same
finding) - the ledger's round-trip fidelity is confirmed exact; the
earlier flag was a test-methodology artifact (comparing across time),
not a ledger defect.

## 17. Test totals

New: `test_training_intelligence_recommendation_ledger.py` (23 pure
tests: canonical JSON, input hashing, decision-id determinism, state-
snapshot content/no-PII, ledger-write-kwargs shaping, candidate
validation - malformed/duplicate-key/multiple-selected/empty-batch) -
23/23 pass. `test_training_intelligence_recommendation_ledger_
postgres.py` (11 opt-in Postgres tests: atomic event+candidate commit,
savepoint-isolated failed-batch rollback, FK-violation rejection for
both candidates and outcomes, idempotent duplicate `decision_id`,
DB-level duplicate-candidate-key and multiple-selected-candidate
rejection bypassing Python validation entirely, event/candidate
retrieval, outcome join, unknown-decision replay, verified input hash)
- 11/11 pass against the real Development database. **Full backend
suite: 915 passed (891 in-memory + 24 isolated cache batch), 112
skipped, 0 failed** - net +34 vs the V2.1 baseline (892 passed, 101
skipped: +23 pure ledger tests always-run, +11 opt-in Postgres ledger
tests correctly skipping without `TRAINING_INTELLIGENCE_POSTGRES_
TESTS=1`), zero regressions.

## 18. Performance impact

Measured on the live-validation run: engine call (`build_daily_
program_adaptation`, 17 queries, unchanged from the V2 report's own
measurement) took ~108s wall time; the ledger transaction (1 event
INSERT + 1 candidate INSERT + 1 `selected_candidate_id` UPDATE, all on
one already-open connection) took ~8.9s. **These wall-clock numbers
are far above the V2 report's own ~4.5s/17-query baseline and are
disclosed as environment/network connection-latency variance observed
during this specific session** (the opt-in Postgres test suite
independently showed ~13s per fresh connection during the same
session, consistent with per-connection TLS/auth handshake cost to the
hosted Supabase instance being unusually high right now, not a query-
execution or ledger-code regression) - re-verified by a clean, isolated
timing-free correctness re-run that behaved identically in shape,
just slower in wall time. **The ledger's own incremental cost, in
environment-independent terms, is exactly 3 SQL statements** (2
INSERTs + 1 UPDATE) on the SAME connection the events/candidates write
already opened - no additional connection, no N+1 pattern (candidates
are written in a single small loop bounded by the candidate count,
which is 1 for V2.1 and will be bounded by a small, finite N for a
future multi-candidate engine, never per-slot or per-history-row).

## 19. Known limitations

1. **Wall-clock performance figures from this session are not
   representative of steady-state cost** (section 18) - re-measurement
   under normal connection latency is recommended before this is used
   as a production-readiness signal, though the ledger's own marginal
   statement count (3) is small regardless of connection cost.
2. **`selected_candidate_id`'s FK is `DEFERRABLE INITIALLY DEFERRED`
   but every test in this milestone only ever rolls back (never
   commits)** - the deferred check is therefore exercised by the one
   real, committed live-validation write (which succeeded, `selected_
   candidate_id` correctly points at the one stored candidate's `id` -
   verified in `test_event_and_candidate_retrieval_matches_written_
   shape`), but not by an opt-in test that deliberately forces a
   COMMIT with a dangling reference to prove the deferred check
   actually fires. A reasonable follow-up hardening test for a future
   milestone.
3. **No admin/HTTP endpoint exposes the ledger** - deliberately, per
   Phase 7's "do not expose the ledger publicly." Phase 8's replay
   contract is a Python function only (`get_recommendation_for_
   replay`), not a route.
4. **`record_program_adaptation_recommendation` is not yet called from
   anywhere in the live request path** (the admin diagnostic endpoint,
   Today Plan, or any other route) - this milestone establishes the
   storage contract and proves it end-to-end via a direct script call,
   not via routine, ongoing recording of every real recommendation.
   Wiring it into the actual admin/shadow call path is a natural, but
   separately-scoped, follow-up.
5. **`engine_version` is stored as a single string** (`"1"`, from
   `result["decision_version"]`) while the engine actually carries 7
   independently-versioned sub-components - the full breakdown is
   preserved in `state_snapshot["engine_version_detail"]`, but the
   top-level, indexed `engine_version` column intentionally collapses
   to one headline number for lookup simplicity.

## 20. Explicitly deferred items

Not implemented, not started, no code scaffolding added for: **V3
candidate generation**, **Decision Utility** (explicit or learned),
**moving local fatigue from a hard gate to a ranking penalty**,
**removal of per-movement session confidence**, **removal of
`justification_sufficient`**, **a user parameter store**, **Bayesian/
e1RM statistical progression posteriors**, **ML ranking**, **LLM
integration of any kind** (explanation, parsing, or summarization -
this milestone's ledger stores no prompt and calls no model), **iOS
session rating UX**, **Production deployment**. `recommendation_
candidates`' schema is deliberately already shaped to receive all of
these later (feature_snapshot/score_components/total_score/rejection_
reasons columns exist, unused, today) without a future migration
redesign - but none of the logic that would populate them was written.

## Commit

Explicit files staged (never `git add .`/`git add -A`):
`supabase/migrations/20260914120000_add_recommendation_ledger.sql`,
`training_intelligence/ledger/__init__.py`, `training_intelligence/
ledger/schema.py`, `training_intelligence/ledger/hashing.py`,
`training_intelligence/ledger/repository.py`, `training_intelligence/
ledger/v2_1_integration.py`, `test_training_intelligence_
recommendation_ledger.py`, `test_training_intelligence_recommendation_
ledger_postgres.py`, this report. Committed to `develop`, tag `program-
intelligence-baseline-v2.1-frozen` pushed alongside it. Never merged
to `main`.

## Final verdict

**`INTELLIGENCE M1.0 PASS — V2.1 FROZEN AND DECISION LEDGER VALIDATED`**

All 16 acceptance criteria are met: the exact validated V2.1 commit
(`7437e50`) is tagged and frozen; V2.1's own files are untouched by
this milestone (verified by `git status` and by an unchanged full-
suite pass count for every pre-existing test); the Development
database has three durable, constraint-enforced ledger tables; one
real V2.1 recommendation was written atomically (event + exactly one
selected candidate, in one transaction) and independently retrieved,
proven byte-exact to the live decision object across 400 flattened
fields; the schema already supports N future candidates via the same
`recommendation_candidates` table with zero migration redesign
required; the curated state snapshot, preserved engine/feature-
schema/as-of versions, and a deterministic input hash together satisfy
the replay contract; outcome data is structurally separate from
decision data (a distinct table, never touching `recommendation_
events`); no raw credential or unnecessary identity field is
persisted (verified by direct scan); the full backend suite passes
with zero regressions (915 passed, 112 skipped, +34 new tests); and no
Today Plan, mobile, Production, Render, or iOS surface was touched.
Not starting candidate generation, not starting M1.1, not deploying -
awaiting review.
