# Training Intelligence TKI-1 + TKI-2 Report

Shadow mode only. Nothing described here changes today's selected workout, TrainingDetail
prescription, or any B3/B4 production recommendation. Reference specification:
`TRAINING_INTELLIGENCE_KNOWLEDGE_BASE_V1.md` (treated as authoritative, not reinterpreted).

**Updated with live Development database results** (previous revision of this report had
sections 9-12 blocked on database access; that access was subsequently granted via a
single named Keychain entry and this report now reflects real data).

## 1. Tonal data model audit (`TONAL_DATA_AUDIT`)

```
workouts_table:                     tonal_workouts (PK activity_id, uuid)
sets_table:                         tonal_sets (PK activity_id+set_index; movement_id uuid)
exercise_identifier:                movement_id (uuid, FK -> tonal_movements)
exercise_name:                      tonal_movements.name / short_name
reps_available:                     yes - tonal_sets.rep_count
load_available:                     yes - base_weight / suggested_weight / avg_weight /
                                     max_weight / one_rep_max / volume
completed_state_available:          no per-set flag. Workout-level
                                     tonal_workout_overrides.include_in_training_analysis
                                     (default TRUE) is the existing exclusion mechanism
muscle_mapping_available:           yes - tonal_movements.muscle_groups (JSONB, from
                                     Tonal's own API)
primary_secondary_mapping_available: yes, by existing convention (first recognized
                                     muscle_groups entry = primary, rest = secondary)
readiness_source:                   integrations.tonal.muscle_readiness.
                                     calculate_muscle_readiness(now=...)
existing_session_taxonomy:          integrations.tonal.training_priority.SESSION_TEMPLATES
history_range:                      REAL: 2023-07-01 09:40 UTC -> 2026-09-04 12:49 UTC
                                     (~3.2 years, 493 workouts, 10,445 sets)
```

**Live audit results** (real Development database, queried this session):

| metric | value |
|---|---|
| total Tonal workouts | 493 |
| total Tonal sets | 10,445 |
| completed working sets (`rep_count > 0`) | 9,159 |
| null `rep_count` | 0 |
| zero `rep_count` | 1,286 |
| null `base_weight` | 0 |
| zero-volume working sets | 0 |
| unique movements actually used | 134 |
| movements in the full catalog | 331 |
| workout_ids shared by >1 activity_id | 23 groups |
| movements flagged non-two-sided (`is_two_sided != TRUE`) | 197 |

Data-quality findings, confirmed against real data:

- **Duplicate-risk observation, investigated and benign**: 23 `workout_id` values are
  shared across multiple `activity_id`s. The largest group is 348 activity_ids sharing one
  `workout_id`, spanning the *entire* history range (2023-2026) - this is Tonal's generic/
  freestyle placeholder `workout_id`, not duplicate ingestion. The remaining 22 groups each
  have exactly 4 activity_ids spanning many months to over a year - consistent with a
  recurring Tonal *program template* reusing the same `workout_id` across genuinely distinct
  workout instances. `activity_id` (the real primary key) is never duplicated - confirmed no
  actual duplicate-ingestion risk.
- **Warmup / non-working sets**: confirmed still not distinguishable. 1,286 sets (12.3% of
  all sets) have `rep_count = 0` and are correctly excluded as not-a-working-set by the
  ledger; there is no way to tell a genuine light warmup with real reps from a working set.
- **Placeholder/generic movements exist in the catalog**: several `tonal_movements` rows use
  sequential placeholder-looking UUIDs (`00000000-0000-0000-0000-00000000000X`) with generic
  names ("Rope Move", "Bar Move", "Handle Move", "Ankle Strap Move", "Rest") and empty
  `muscle_groups` - these are Tonal-side generic/free-lift attachment placeholders, not real
  named exercises. One of them, **"Handle Move" (`...0002`), has 422 real working-set
  occurrences** and is currently completely unmapped/uncredited to any muscle - see section
  6/7.
- **Unilateral movements**: 197 movements are flagged `is_two_sided != TRUE` in the catalog.
  TKI-2 does not consume this flag (as designed - reserved for a future dose/progression
  milestone) and does not double- or under-count based on laterality; this is worth revisiting
  when TKI-3 gets into per-side dose.

## 2. TKI-1 knowledge architecture

Unchanged from the original implementation - see the previous revision of this report or
`training_intelligence/knowledge/` directly. 16 rules, 13 sources, plain JSON (PyYAML exists
in `.venv` but is unpinned in `requirements.txt`, so not used).

## 3. Rule count

**16 rules**: 5 evidence_tier1, 2 evidence_tier2, 1 evidence_tier3, 8 product_policy.
Validated at load time; `test_training_intelligence_knowledge.py` (15 tests) unaffected by
this calibration run.

## 4. Source registry

**13 sources**, unchanged - see section 4 of the original implementation.

## 5. Muscle taxonomy

10 canonical groups. **Correction to the previous revision of this report**: it previously
speculated "Tonal's own muscle_groups metadata does not appear to distinguish a Calves
group" - **that speculation was wrong**, now that real data is available. Tonal's API *does*
provide `"Calves"` as a raw `muscle_groups` label (confirmed on real movements "Bent Knee
Calf Raise" and "Resisted Calf Raise"). The real root cause is narrower and more actionable:
`integrations.tonal.muscle_readiness._normalize_muscle` (reused here for the primary/
secondary mapping, per "do not invent a parallel taxonomy") only recognizes labels in the
existing 9-group `PROGRAMMING_MUSCLES`, which deliberately has no Calves - so `"Calves"` is
treated as an *unrecognized* label and the set is reported as unmapped, not credited to
`calves`. See section 6/7 for the material impact.

## 6. Exercise mapping coverage

**Real data, two views:**

- **Whole catalog** (331 movements, includes many never actually performed): 316 mapped,
  15 unmapped -> **95.5%** coverage.
- **Actually-used movements only** (134 distinct movements with >=1 real working set):
  129 mapped, 5 unmapped -> **96.3%** coverage by movement count.

Movement-count coverage looks strong, but it understates the impact - see section 7:
one of the 5 unmapped-but-used movements alone accounts for 422 real working sets.

## 7. Unmapped exercises

All 5 unmapped-but-actually-used movements, classified by occurrence (working-set count)
in the query window, per the assignment's own HIGH/MEDIUM/LOW threshold request (HIGH >= 5
occurrences, MEDIUM 2-4, LOW 1):

| movement | raw muscle_groups | occurrences | priority | materially affects totals? |
|---|---|---|---|---|
| Handle Move (`...0002`) | `[]` (empty) | 422 | **HIGH** | **Yes - largest single gap found** |
| Resisted Calf Raise | `["Calves"]` | 75 | **HIGH** | Yes - entire Calves signal (see section 5) |
| Bar Move (`...0003`) | `[]` (empty) | 11 | **HIGH** | Modest but non-trivial |
| Handle Move (`...0009`) | `[]` (empty) | 10 | **HIGH** | Modest but non-trivial |
| Handle Move (`...0008`) | `[]` (empty) | 1 | LOW | Negligible |

**"Handle Move" is the single most material finding of this calibration run.** 422 real
working sets (about 4.6% of all 9,159 completed working sets in the user's entire history)
are currently invisible to the ledger because this generic Tonal placeholder movement
carries no `muscle_groups` metadata at all. This is not something TKI-2 can guess its way
out of ("do not guess mappings during this calibration run" - correctly not attempted here),
but it is squarely a HIGH-priority item for a follow-up: either a small curated override
mapping this placeholder to whatever muscle(s) the surrounding session context suggests, or
accepting a known, quantified blind spot before this ledger drives anything downstream.

## 8. Secondary-set policy sanity check

`SECONDARY_SET_CREDIT = 0.35` (reused from `muscle_readiness.SECONDARY_SET_WEIGHT`).
Direct-only vs. direct+secondary totals, real data:

| muscle | window | direct only | direct + secondary | secondary:direct ratio |
|---|---|---|---|---|
| chest | 30d | 54.0 | 55.4 | 0.03 |
| back | 30d | 36.0 | 36.0 | 0.00 |
| shoulders | 30d | 19.0 | 27.75 | 0.46 |
| biceps | 30d | 18.0 | 28.5 | 0.58 |
| triceps | 30d | 20.0 | 38.2 | 0.91 |
| quads | 30d | 4.0 | 8.55 | 1.14 |
| hamstrings | 30d | 10.0 | 11.4 | 0.14 |
| glutes | 30d | 13.0 | 19.3 | 0.48 |
| core | 30d | 20.0 | 41.35 | 1.07 |

No sign of the failure mode the assignment specifically asked about ("one compound movement
causing implausibly high weekly totals"): the highest ratios (quads 1.14, core 1.07, triceps
0.91) mean secondary credit roughly matches or modestly exceeds direct credit for muscles
that are frequently a *secondary* target across this user's real session mix (e.g. quads as
a secondary target in several lower-body/full-body movements where glutes or hamstrings are
primary) - not one single exercise inflating many muscles at once. **0.35 does not need to
change based on this data**; no calibration action taken, per "do not change 0.35 unless the
data clearly shows a problem."

## 9-11. 7-day / 14-day / 30-day ledger (real data)

**As of the run (2026-09-12T17:25 UTC): the 7-day ledger is entirely zero for every
muscle.** The user's most recent real Tonal workout was **2026-09-04**, ~8 days before this
run - a genuine training gap, not a bug (confirmed against the audit's own
`latest_workout` timestamp).

**14-day ledger** (`total_stimulus_sets`, `sessions`, `days_since_trained`):

| muscle | stimulus sets | sessions | days since trained |
|---|---|---|---|
| chest | 12.0 | 2 | 11.21 |
| back | 8.0 | 1 | 10.15 |
| shoulders | 8.45 | 4 | 8.19 |
| biceps | 12.8 | 3 | 9.05 |
| triceps | 10.2 | 3 | 11.21 |
| quads | 8.55 | 2 | 9.05 |
| hamstrings | 11.4 | 2 | 8.19 |
| glutes | 19.3 | 3 | 9.05 |
| calves | 0.0 | 0 | null |
| core | 31.55 | 6 | 10.15 |

**30-day ledger:**

| muscle | stimulus sets | sessions | days since trained |
|---|---|---|---|
| chest | 55.4 | 5 | 11.21 |
| back | 36.0 | 5 | 10.15 |
| shoulders | 27.75 | 8 | 8.19 |
| biceps | 28.5 | 8 | 9.05 |
| triceps | 38.2 | 8 | 11.21 |
| quads | 8.55 | 2 | 9.05 |
| hamstrings | 11.4 | 2 | 8.19 |
| glutes | 19.3 | 3 | 9.05 |
| calves | 0.0 | 0 | null |
| core | 41.35 | 10 | 10.15 |

**Nothing implausible flagged**: every muscle's 30d total is >= its 14d total (monotonic, as
required); `days_since_trained` is identical for a muscle across 14d/30d whenever no new
session occurred in the 15-30 day range (quads/hamstrings/glutes are numerically identical
between the two windows - correctly indicates no lower-body training happened 15-30 days
ago, not a bug). **`calves` reads zero across every window** - this is the confirmed mapping
gap from section 5/7, not evidence the user does no calf work (they do - 75 real occurrences
of "Resisted Calf Raise" exist, just unmapped).

**Session-family coverage (90-day window, real data)**: 37 distinct workouts classified.
"Other" (13 sessions) is the largest single bucket - meaning over a third of this user's
real sessions don't cleanly match any existing `SESSION_TEMPLATES` muscle combination at the
`minimum_eligible` threshold. Full Body (8), Upper Mixed (7), Lower Body (3), Core +
Accessories (4), Chest + Biceps (1), and Upper Push (1) account for the rest. This is a
descriptive finding, not a defect - the existing `SESSION_TEMPLATES` were designed for
forward-looking prescription, not to exhaustively classify every real historical session
shape, and a large "Other" bucket is expected until/unless a future milestone adds more
templates or a "mixed" classification.

## 12. Sep 12 diagnostic (real data)

Real `whoop_daily_metrics` row for 2026-09-12 found: **recovery_score = 94.0**, matching the
reference screenshot exactly. Anchored `as_of` to the row's actual `source_updated_at`
(2026-09-12T08:10:33 UTC - the real moment that morning's recovery became available).

| | |
|---|---|
| WHOOP recovery | 94.0 (band: `high`) |
| HRV | 75.14 ms |
| Resting HR | 61.0 |
| Sleep duration | 6.20 h |
| Recent sessions (7d) | none - empty `session_history_families_7d` |

Focus muscles at that exact moment - **every one of them**, not just Back, shows zero 7-day
stimulus, no sessions, and `readiness: fresh` (`READY`):

| muscle | stimulus 7d | stimulus 14d | stimulus 30d | days since trained | readiness |
|---|---|---|---|---|---|
| back | 0.0 | 13.0 | 37.4 | null (>14d) | fresh |
| biceps | 0.0 | 14.55 | 31.5 | null (>14d) | fresh |
| chest | 0.0 | 26.0 | 55.4 | null (>14d) | fresh |
| quads | 0.0 | 8.55 | 13.45 | null (>14d) | fresh |
| hamstrings | 0.0 | 11.4 | 17.65 | null (>14d) | fresh |
| glutes | 0.0 | 19.3 | 38.65 | null (>14d) | fresh |

**Was Upper Pull on Sep 12 SUPPORTED, QUESTIONABLE, or CONTRADICTED?**

**QUESTIONABLE.** Local readiness does **not contradict** it - Back was not fatigued, it was
`fresh`/`READY` like every other muscle, because the user hadn't trained *anything* in the
preceding 8 days. Nothing in the local-readiness signal argues against Upper Pull
specifically. But the 14-day and 30-day stimulus numbers show a real, measurable
imbalance: quads (8.55/13.45) and hamstrings (11.4/17.65) sit well below chest (26.0/55.4),
biceps (14.55/31.5), and back (13.0/37.4) over both medium-term windows. That is exactly the
kind of weekly-stimulus-debt signal
`TRAINING_INTELLIGENCE_KNOWLEDGE_BASE_V1.md` section 9's own illustrative example describes
as an argument *for* prioritizing lower body over an upper-body session, even when local
readiness alone doesn't forbid the upper-body choice. TKI-1/TKI-2 do not rank sessions (by
design - see section 15 of the assignment), so this is reported as a diagnostic observation
only, not a claim that Upper Pull was wrong: it was not locally unsafe, but a stimulus-debt-
aware session ranking (a future TKI-4 concern) would have had a legitimate, data-backed case
for favoring Lower Body instead.

## 13. Historical/live temporal-leakage validation

Real Postgres, real SQL, not mocked - all three pass:

- **Future workout cannot alter historical ledger**: confirmed via
  `test_future_workout_excluded_from_real_sql_query` (real join) - `True`
- **Exact `as_of` boundary is inclusive; one second later is excluded**: confirmed via a
  live, rolled-back fixture insertion and two ledger builds one second apart - `True`
- **Later same-day workout cannot alter an earlier same-day `as_of` result**: confirmed via
  a live, rolled-back fixture (a morning and an evening workout on the same day; `as_of` set
  between them) - `True`

All fixture writes for this validation used a transaction that was rolled back at the end
of the same connection (never committed) with deterministic, clearly-fake UUIDs derived
from readable labels via `uuid.uuid5()` - no real Tonal history was touched.

## 14. Unit test results

Unchanged: `test_training_intelligence_knowledge.py` + `test_muscle_stimulus_ledger.py` -
**61 passed, 0 failed.** Full backend suite: **497 passed, 44 skipped, 0 failed.**

## 15. Postgres opt-in test status

**Run live this session: 3 passed, 0 failed, 0 skipped.** (An earlier run in this same
session failed all 3 - not a code defect, but two real bugs in the test fixtures themselves,
both fixed and re-verified: (1) `activity_id`/`movement_id`/`workout_id` are `uuid`-typed
columns, confirmed via `information_schema` - the original fixtures used plain strings and
were switched to deterministic `uuid.uuid5()`-derived values; (2) the original fixture
`as_of` of 2026-01-15 fell inside the user's real training history, so the "should be
exactly zero" assertions correctly failed against real co-mingled data - fixtures were moved
to a year with no possible real data (2035) to guarantee isolation.)

## 16. Performance (real database)

| operation | time |
|---|---|
| `load_rows` (30-day fetch, real join) | 0.395 s |
| `build_ledger_windows` from one preloaded fetch (7/14/30) | 0.003 s |
| `build_muscle_stimulus_ledger` 7d (own query) | 0.325 s |
| `build_muscle_stimulus_ledger` 14d (own query) | 0.290 s |
| `build_muscle_stimulus_ledger` 30d (own query) | 0.374 s |
| `build_shadow_training_state` (full: ledger + families + goal + WHOOP + readiness) | 1.425 s |

The join itself (~0.3-0.4s against 493 real workouts / 10,445 real sets) is the dominant
cost, as expected, and matches the same table/join shape already running in production
(`muscle_readiness._load_rows`) - no new architectural risk. The full shadow-state call at
1.4s is acceptable for its current admin-diagnostic-only use, but would need consolidating
separate goal/WHOOP/readiness round-trips (or caching) before any future milestone puts it
on a live request path.

## 17. WHOOP recovery-band discrepancy (investigated)

**A. Bands in the spec** (`TRAINING_INTELLIGENCE_KNOWLEDGE_BASE_V1.md` section 8):
Green 67-100 / Yellow 34-66 / Red 0-33 (3 bands).

**B. Bands in shipped code** (`integrations.tonal.workout_prescription._latest_readiness`,
reused as-is): high >= 80 / good >= 67 / moderate >= 45 / low >= 25 / very_low < 25
(5 bands).

**C. Which source/document each uses**: the spec cites WHOOP's own public Recovery
guidance directly. The shipped code's 5-band scale is a pre-existing, already-tuned B3/B4
product-policy decision (predates this milestone; not attributed to a specific external
source in the code).

**D. Does the difference affect current TKI shadow output?** For the one real value
observed this session (94.0), **no** - both classify it as the top tier (spec: Green;
shipped: high), so the Sep 12 diagnostic above is not affected. The schemes *can* diverge at
the edges: e.g. a recovery of 30 is "Red" (worst tier) under the spec's 3-band scheme but
only "low" (second-worst of 5, not the worst) under the shipped 5-band scheme. This has not
been observed in real data this session, but is a latent disagreement.

**E. Recommended canonical interpretation for TKI-3**: keep the shipped 5-band scale as
canonical - it is already validated in production and used by B3/B4 today, and TKI-2 must
not create a new WHOOP calculation. Treat the spec's 3-band language as a simplified
narrative description of the same underlying idea (more/less capacity), not a literal
implementation requirement. **No code change made** - this is a documentation-alignment
question for whoever owns `TRAINING_INTELLIGENCE_KNOWLEDGE_BASE_V1.md`, not something to
silently resolve in either direction.

## 18. Calibration concerns carried forward

1. **HIGH priority**: "Handle Move" placeholder (422 real working sets, ~4.6% of all
   completed working sets) is completely unmapped and invisible to every muscle total.
2. **HIGH priority**: Calves is a real, actively-trained canonical group (75 real "Resisted
   Calf Raise" occurrences) that reads zero in every window because the reused
   `_normalize_muscle` doesn't recognize `"Calves"` - a narrower, more fixable version of
   what the previous report speculated.
3. Two smaller "Bar Move"/"Handle Move" placeholders (11 and 10 occurrences) - same root
   cause as #1, lower individual impact.
4. Warmup/non-working-set ambiguity remains fundamentally undecidable with this schema.
5. WHOOP recovery-band scheme disagreement between spec and shipped code (documentation
   question, not a code defect - see section 17).

None of these were fixed in this run, per "do not guess mappings during this calibration
run" and "if calibration is needed, report it rather than editing policy automatically."

## 19. Commits

**No code changes this run** - two pre-existing bugs in `test_training_intelligence_postgres.py`
(UUID-typed fixture columns; a fixture date colliding with real history) were found and
fixed as part of getting the opt-in suite to actually run, and this report was updated with
real-data results. One commit, report + test fixes only:

- `<pending>` **"Fix TKI-2 Postgres opt-in test fixtures; add live-data calibration results"**

## Answers

**1. Does the new ledger appear credible against actual Tonal history?**
**Mostly yes, with one clear, quantified exception.** The core arithmetic (direct/secondary
credit, rolling windows, session/recency counting, session-family classification) all
produced sane, monotonic, non-inflated numbers against 3.2 years and 9,159 real working
sets. The exception is mapping coverage: a generic Tonal placeholder ("Handle Move", 422
real sets) and the entire Calves group are currently invisible to the ledger. That is a
real, material gap, not a logic bug - the accounting is correct for every set it can
classify.

**2. What did it say about Sep 12's 94% Recovery + Upper Pull prescription?**
QUESTIONABLE, not contradicted, not clearly supported. See section 12: local readiness gave
Upper Pull no red flag (nothing was fatigued after an 8-day layoff), but quads/hamstrings
were measurably behind chest/biceps/back on both the 14-day and 30-day stimulus ledgers -
exactly the kind of signal the approved spec's own example says should have favored a
lower-body session, even without local fatigue forcing the issue.

**3. What information is still missing before TKI-3 Personal Dose Model?**
A decision on the two HIGH-priority unmapped-movement gaps (Handle Move, Calves) - TKI-3's
personal dose model would otherwise learn from a ledger that's silently missing ~4.6%+ of
real working-set volume and 100% of real calf training. Also the recovery-band discrepancy
decision (section 17), since TKI-3+ will lean on systemic readiness more heavily.

**4. Should we proceed to TKI-3, or does TKI-2 require calibration first?**
**Calibration first**, specifically the mapping gaps in section 7/18 - not because the
ledger's logic is wrong (it validated cleanly against real data on every axis this run
checked), but because a personal dose model trained on a ledger with a known 4.6%+ blind
spot and a fully-missing muscle group would be learning from incomplete data.

## Final verdict

**TKI-1 + TKI-2 DEVELOPMENT PARTIAL — CALIBRATION REQUIRED**

(Different reason than the previous revision of this report: not "no live data was
checked," but "live data was checked, the mechanism validated cleanly, and it surfaced two
concrete, high-priority mapping gaps that should be resolved - or explicitly accepted - before
this ledger is trusted for anything beyond shadow diagnostics.")
