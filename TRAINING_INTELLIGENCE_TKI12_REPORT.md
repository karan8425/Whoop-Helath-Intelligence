# Training Intelligence TKI-1 + TKI-2 Report

Shadow mode only. Nothing described here changes today's selected workout, TrainingDetail
prescription, or any B3/B4 production recommendation. Reference specification:
`TRAINING_INTELLIGENCE_KNOWLEDGE_BASE_V1.md` (treated as authoritative, not reinterpreted).

## 1. Tonal data model audit (`TONAL_DATA_AUDIT`)

```
workouts_table:                     tonal_workouts (PK activity_id)
sets_table:                         tonal_sets (PK activity_id, set_index)
exercise_identifier:                movement_id (FK -> tonal_movements)
exercise_name:                      tonal_movements.name / short_name
reps_available:                     yes - tonal_sets.rep_count
load_available:                     yes - base_weight / suggested_weight / avg_weight /
                                     max_weight / one_rep_max / volume
completed_state_available:          no per-set flag. Workout-level
                                     tonal_workout_overrides.include_in_training_analysis
                                     (default TRUE) is the existing exclusion mechanism,
                                     with an "abbreviated freestyle session" carve-out
                                     already weighted at 0.25 elsewhere in production
                                     (SUPPLEMENTAL_WEIGHT in muscle_readiness.py)
muscle_mapping_available:           yes - tonal_movements.muscle_groups (JSONB, from
                                     Tonal's own API)
primary_secondary_mapping_available: yes, by existing convention (first recognized
                                     muscle_groups entry = primary, rest = secondary),
                                     established in integrations/tonal/muscle_readiness.py
                                     and reused here, not reinvented
readiness_source:                   integrations.tonal.muscle_readiness.
                                     calculate_muscle_readiness(now=...) - states
                                     SUPPRESSED / FATIGUED / RECOVERING / FRESH / READY
existing_session_taxonomy:          integrations.tonal.training_priority.SESSION_TEMPLATES
                                     (Upper Push, Upper Pull, Chest + Biceps, Lower Body,
                                     Upper Mixed, Core + Accessories, Full Body) - used
                                     today only to score FUTURE candidate sessions; no
                                     existing classifier for HISTORICAL workouts (TKI-2
                                     adds one, reusing these exact template definitions)
history_range:                      not queried live this session (no DB access - see
                                     section 8); sync_tonal.py upserts via ON CONFLICT
                                     with no evident retention cutoff in code
```

Data-quality risks identified from code inspection (not yet confirmed against live data,
see section 8):

- **Unmapped exercises**: possible whenever `tonal_movements.muscle_groups` is empty or
  contains only labels `integrations.tonal.muscle_readiness._normalize_muscle` doesn't
  recognize. Never guessed - reported explicitly by
  `training_intelligence.stimulus.mapping.movement_mapping_report`.
- **Warmup / non-working sets**: **cannot be distinguished** with this schema. There is no
  warmup flag anywhere in `tonal_sets`. The ledger's only proxy is `rep_count > 0`, which
  excludes a genuinely-zero/null-rep row but cannot tell a light warmup with real reps from
  a working set. This is a documented limitation, not a bug.
- **Duplicate ingestion rows**: structurally prevented at the DB layer - `tonal_sets` has a
  composite primary key `(activity_id, set_index)` and `sync_tonal.py` upserts via
  `ON CONFLICT`, so a literal duplicate row cannot exist.
- **Naming variants / duplicate exercise names**: not checked - the mapping join is keyed
  on `movement_id`, not on `name`, so a name collision would not affect muscle-credit
  correctness, but could confuse a future human-facing "which exercises did I do" view.
  Flagged, not fixed (out of scope).
- **Zero-load / unilateral movements**: zero `base_weight`/`volume` is normal for some
  bodyweight-style Tonal movements and is **not** treated as invalidating a set (only
  `rep_count` gates working-set status). `tonal_movements.is_two_sided` /
  `is_bilateral` / `is_alternating` exist but are not consumed by TKI-2 - reserved for a
  future dose/progression milestone.

## 2. TKI-1 knowledge architecture

`training_intelligence/knowledge/` - plain JSON, not YAML: PyYAML exists in this
environment's `.venv` but is **not pinned in `requirements.txt`**, so depending on it would
be an untracked, fragile Production dependency for no benefit over JSON for fully
machine-authored/machine-read data.

```
training_intelligence/
    knowledge/
        schema.py        - Rule dataclass + validate_rule() (enforces evidence_class
                            can never claim a tier stronger than its source_ids support)
        loader.py         - load_rules() / load_sources(), KNOWLEDGE_VERSION = "TKI_V1"
        sources.json       - 13 sources, exactly TRAINING_INTELLIGENCE_KNOWLEDGE_BASE_V1.md
                            section 18 (no additional/invented sources)
        rules/
            hypertrophy.json        (3 rules)
            strength.json           (3 rules)
            progression.json        (3 rules)
            fatigue.json            (4 rules)
            session_selection.json  (3 rules)
    stimulus/
        policy.py         - versioned, product-policy-labeled constants (reuses the
                            EXISTING SECONDARY_SET_WEIGHT from muscle_readiness.py)
        taxonomy.py         - 10 canonical groups + compatibility mapping onto the
                            existing 9-group B3/B4 taxonomy
        mapping.py          - exercise -> muscle, reusing Tonal's own muscle_groups data
        ledger.py           - as-of-safe rolling 7/14/30-day stimulus ledger
        session_family.py   - historical-workout classification into the EXISTING
                            SESSION_TEMPLATES
        shadow_state.py     - assembles the full shadow object; read-only joins to goal /
                            WHOOP / local readiness
```

`evidence_class` values (schema.py): `evidence_tier1`, `evidence_tier2`, `evidence_tier3`
(claims backed by a matching-tier source - enforced, not just documented),
`product_policy` (may cite any tier as justification but is itself a decision this app
made), `personalized` and `fallback_default` (reserved for TKI-3+, unused so far).

## 3. Rule count

**16 rules** across 5 category files, validated against the source registry at load time
(`test_training_intelligence_knowledge.py`, 15 tests, all passing):

| evidence_class    | count |
|---|---|
| evidence_tier1    | 5 |
| evidence_tier2    | 2 |
| evidence_tier3    | 1 |
| product_policy    | 8 |

## 4. Source registry

**13 sources**, transcribed exactly from `TRAINING_INTELLIGENCE_KNOWLEDGE_BASE_V1.md`
section 18 (ACSM x1, WHO x1, Stronger by Science x3, Barbell Medicine x2, Tonal x3,
WHOOP x3) - organization, tier, title, URL(s), and what it's used for. No copyrighted
program content copied, only provenance.

## 5. Muscle taxonomy

10 canonical groups (`training_intelligence/stimulus/taxonomy.py`), matching
`TRAINING_INTELLIGENCE_KNOWLEDGE_BASE_V1.md` section 6 exactly: chest, back, shoulders,
biceps, triceps, quads, hamstrings, glutes, calves, core.

The existing B3/B4 `PROGRAMMING_MUSCLES` (9 groups, Title Case, **no Calves**) is
untouched and mapped into the canonical taxonomy one-directionally; a startup assertion
fails loudly if a future B3/B4 change ever adds a group this mapping doesn't know about.
**Calves has no existing mapping source** - Tonal's own `muscle_groups` metadata does not
appear to distinguish it (see section 8 for why this isn't confirmed against live data
yet). It remains a canonical group so the ledger's shape is stable, and is expected to
read zero direct/secondary sets until either Tonal exposes it or a curated mapping is
added.

## 6. Exercise mapping coverage

**Not measured against real data this session** - see section 8. The mapping itself
(`training_intelligence/stimulus/mapping.py`) reuses Tonal's own `muscle_groups` metadata
and the exact recognition rule already shipped in
`integrations.tonal.muscle_readiness._normalize_muscle`, so its *behavior* is unit-tested
(11 tests) but its *coverage against this user's actual movement library* is unknown until
run live.

## 7. Unmapped exercises

**Not measured against real data this session.** `movement_mapping_report()` will list
every unmapped movement by `movement_id`/`name` explicitly (never silently dropped) the
first time this runs against the real database.

## 8. Secondary-set policy

`SECONDARY_SET_CREDIT` = **0.35**, imported directly from
`integrations.tonal.muscle_readiness.SECONDARY_SET_WEIGHT` rather than defined
independently. This was a deliberate choice: the assignment suggested starting at 0.5 "only
if the approved spec/current architecture supports this," and the approved spec itself only
says "partial set credit" with no specific fraction - but the app has **already shipped**
0.35 as this exact policy decision in the muscle-readiness engine that TKI-2 sits next to. A
stimulus ledger that used a different, competing fraction (e.g. 0.5) would silently disagree
with the readiness engine about what a secondary set is worth, which is a worse outcome than
reusing the number already in production. `DIRECT_SET_CREDIT` = 1.0 (also matches the
existing `PRIMARY_SET_WEIGHT`). Both are versioned (`STIMULUS_POLICY_VERSION = 1`) and
labeled `product_policy`, never presented as an ACSM finding.

## 9-11. 7-day / 14-day / 30-day ledger, and 12. Sep 12 diagnostic

**Blocked - no live Development database connection in this session**, for the same reason
as the MORNING-REFRESH-V3 Postgres validation task: `DATABASE_URL` is not present in this
sandboxed session's environment, and this session's permission system blocks direct
Keychain/credential retrieval (a prior, unrelated attempt to look up a keychain entry
inadvertently printed a live connection string to a different transcript, which made the
permission system - appropriately - stricter about this going forward). I did not fabricate
plausible-looking numbers for this section; every number the ledger would report requires
this user's real Tonal/WHOOP history, and inventing one would defeat the entire purpose of
a "does this look credible against real data" checkpoint.

What **is** validated without live data: the ledger's arithmetic, temporal safety, session
classification, and read-only context joins are exercised end-to-end with synthetic
fixtures covering the same shape as the Sep 12 scenario (high WHOOP recovery + a recently
fatigued Back + fresh Lower Body) in `test_muscle_stimulus_ledger.py`'s `ShadowStateTests`
and `SessionFamilyTests`. It correctly abstains from ranking - `shadow_training_state()["ranking"]`
is always `None`.

To get the real Sep 12 numbers and the 7/14/30-day ledger, one of:
- provide the Development `DATABASE_URL` via an approved Bash permission for the Keychain
  entry this project already uses, or
- run `training_intelligence.stimulus.shadow_state.build_shadow_training_state(as_of=...)`
  yourself against the Development database and share the output, or
- enable `TRAINING_INTELLIGENCE_POSTGRES_TESTS=1` with `DATABASE_URL` set and run
  `test_training_intelligence_postgres.py`.

## 13. Historical temporal-leakage validation

Validated at the unit level (not yet against real historical Tonal dates - see above):

- future workout cannot alter historical ledger (`test_future_workout_cannot_alter_historical_ledger`)
- a workout beginning exactly at `as_of` is included; one second after is excluded
  (boundary tests)
- a later same-day workout cannot alter an earlier same-day `as_of` state
  (`test_later_same_day_workout_cannot_alter_earlier_as_of_state`)
- a naive (non-timezone-aware) `as_of` is rejected outright, matching the discipline
  already established for `ReplayContext` in `training_replay.py`

`test_training_intelligence_postgres.py` additionally exercises the real SQL join's
future-workout exclusion (`test_future_workout_excluded_from_real_sql_query`), opt-in,
not run live this session.

## 14. Unit test results

`test_training_intelligence_knowledge.py` + `test_muscle_stimulus_ledger.py`:
**61 passed, 0 failed.** Full backend regression suite after these additions:
**497 passed, 44 skipped, 0 failed** (was 436 passed / 41 skipped before this milestone;
+61 new passing tests, +3 new opt-in Postgres skips, zero regressions elsewhere).

## 15. Postgres opt-in test status

Written (`test_training_intelligence_postgres.py`, 3 tests: future-workout exclusion via
the real SQL join, direct+secondary credit via the real join, and workout-override
exclusion via the real join), gated on `TRAINING_INTELLIGENCE_POSTGRES_TESTS=1`. **Not run
this session** - same DATABASE_URL blocker as above. Uses a transaction that is always
rolled back (never committed) against obviously-namespaced `tki_test_*` fixture ids, so it
cannot pollute real Tonal history even on a future run.

## 16. Performance

In-memory accumulation only (DB query latency itself could not be measured - see above),
against a synthetic full year of Tonal history (52 weeks x 4 workouts x 8 sets = 1,664
sets, deliberately larger than any single rolling window would actually load):

| operation | time |
|---|---|
| 7-day accumulate | 0.20 ms |
| 14-day accumulate | 0.22 ms |
| 30-day accumulate | 0.31 ms |
| combined 3-window ledger (one row-set) | 0.69 ms |
| session-family 3-window aggregation | 0.78 ms |

Negligible. The real cost is the SQL join itself
(`tonal_workouts` JOIN `tonal_sets` JOIN `tonal_movements` LEFT JOIN
`tonal_workout_overrides`, bounded by `begin_time`), which is the **same table set and
join shape** `integrations.tonal.muscle_readiness._load_rows` already uses in the live
Today recommendation path today - no new architectural risk is introduced, but this was not
independently timed against the real database in this session.

## 17. Data-quality concerns

- **WHOOP recovery-band discrepancy**: `TRAINING_INTELLIGENCE_KNOWLEDGE_BASE_V1.md`
  section 8 states Green 67-100 / Yellow 34-66 / Red 0-33, but the already-shipped
  `integrations.tonal.workout_prescription._latest_readiness` (reused as-is here, per "do
  not create a new WHOOP calculation") uses a different 5-band scale
  (high >= 80 / good >= 67 / moderate >= 45 / low >= 25 / very_low < 25). TKI-2 surfaces the
  existing production band unchanged rather than silently reconciling this; flagging it for
  a human decision rather than picking a side.
- **Calves has no mapping source** (section 5 above) - expect zero data until addressed.
- **Warmups are not distinguishable** (section 1 above) - a documented limitation of the
  underlying schema, not something TKI-2 can fix by itself.
- **Real-data validation is entirely outstanding** - every number in sections 9-12 depends
  on a live database connection this session did not have.

## 18. Commits

Two logical commits on `develop` (backend only):
1. **TKI-1**: `training_intelligence/knowledge/` + `TRAINING_INTELLIGENCE_KNOWLEDGE_BASE_V1.md`
   + `test_training_intelligence_knowledge.py`
2. **TKI-2**: `training_intelligence/stimulus/` + `main.py` (admin diagnostic endpoint) +
   `test_muscle_stimulus_ledger.py` + `test_training_intelligence_postgres.py` +
   this report

## Answers

**1. Does the new ledger appear credible against actual Tonal history?**
Unknown - not tested against real data this session. Its logic is internally consistent,
deterministic, temporally safe, and reuses every existing convention it could find
(mapping source, secondary-credit weight, session families, readiness/goal accessors)
rather than inventing competing ones, which is the strongest confidence I can offer without
a live database.

**2. What did it say about Sep 12's 94% Recovery + Upper Pull prescription?**
Nothing yet - blocked on live DB access (section 9-12).

**3. What information is still missing before TKI-3 Personal Dose Model?**
Real per-muscle 7/14/30-day stimulus numbers and mapping coverage against this user's
actual movement library; confirmation that `days_since_trained`/`sessions` behave
sensibly across a real multi-month training history (not just synthetic fixtures);
resolution (or at least an explicit decision) on the WHOOP recovery-band discrepancy noted
above, since TKI-3+ will consume systemic readiness more heavily.

**4. Should we proceed to TKI-3, or does TKI-2 require calibration first?**
Calibration first - the code path is complete and tested, but "does it look credible
against actual Tonal history" (the assignment's own success bar for this milestone,
sections 12/16/21) has not been checked.

## Final verdict

**TKI-1 + TKI-2 DEVELOPMENT PARTIAL — CALIBRATION REQUIRED**
