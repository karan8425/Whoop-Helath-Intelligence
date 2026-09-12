# Training Intelligence TKI-4 Report — Session Selection / Scoring V1

SHADOW MODE ONLY. Nothing in this milestone reads from, writes to, or is
reachable from any production or iOS code path. `integrations.tonal.
training_priority.build_training_priority()` (the live B3 session-selection
engine) is untouched, unaware this module exists, and continues to be the
sole source of the session recommendation shown to the user.

## 1. What TKI-4 answers

"What should this user train today?" — as a deterministic, explainable,
versioned shadow ranking over the same `SESSION_TEMPLATES` family taxonomy
B3 already uses, using: goal context (TKI-3/Goal Policy), weekly stimulus
debt (TKI-2), local muscle readiness (B1), systemic readiness (WHOOP
recovery), days-since-trained, recent program balance, recent performance
trend (`progressive_overload.trajectory`), and TKI-3 dose feasibility. Rest
is a first-class, always-present outcome, not a fallback bolted on at the
end.

## 2. Architecture (layers A–G)

| Layer | Module | Responsibility |
|---|---|---|
| A. Candidate generation | `selection/candidates.py` | One candidate per `SESSION_TEMPLATES` family (7 lifting families) |
| B. Eligibility | `selection/candidates.py` | Explicit, named exclusion reasons only — never "less debt than a peer" |
| C. Scoring | `selection/scoring.py` | 9 bounded `[0,1]` dimensions per eligible candidate |
| D. Goal-policy weighting | `selection/scoring_policy.py` | Goal-mode-specific weights, applied as a weighted average |
| E. Tie-break | `selection/shadow_selection.py::_sort_key` | Explicit, versioned, deterministic descending order |
| F. Final ranking | `selection/shadow_selection.py::build_shadow_selection` | Produces the full shadow object |
| G. Explanation | `_explanation_factors` / `selection_explanation` | Per-candidate and per-selection provenance |

Reused, not reimplemented: `SESSION_TEMPLATES` (B3), `build_shadow_dose`
(TKI-3, itself reusing B2), `calculate_muscle_readiness` (B1),
`_latest_readiness` (B3's WHOOP band), `load_rows`/`session_family_windows`
(TKI-2), `resolve_goal_mode`/`get_goal_policy` (TKI-3's Goal Policy layer).
No parallel taxonomy, dose model, or goal-resolution logic was introduced.

### Eligibility (never scoring's job)

A candidate is excluded only for one of four explicit reasons:
`insufficient_local_readiness` (fewer than `minimum_eligible` non-
SUPPRESSED/FATIGUED muscles), `incompatible_training_constraint` (Full
Body needs ≥1 eligible upper + ≥1 eligible lower muscle; Core +
Accessories needs Core eligible), `insufficient_feasible_dose` (TKI-3's
feasible upper bound < 3 working sets), or `recovery_mode_restriction`
(a >0.6-systemic-cost family while `goal_mode=recovery` and systemic
readiness is `low`/`very_low`). A muscle with no readiness history at all
is not excluded — it is scored with an explicit "unknown" quality
(`0.3`), never silently treated as Fresh.

### The 9 scoring dimensions

`stimulus_debt`, `local_readiness`, `days_since_trained`,
`program_balance`, `systemic_capacity`, `goal_relevance`, `performance`,
`dose_feasibility`, `schedule_fit` — each `[0,1]`, computed in
`scoring.py`. `stimulus_debt` is normalized **relative to the other
eligible candidates in the same call**, never against an absolute
population reference (section 6 of the assignment). `schedule_fit` is a
documented, constant `0.5` placeholder: no dedicated training-frequency/
schedule system exists yet to wire it to — this is disclosed, not
fabricated.

`score_total` is a **weighted average** (`Σ weight·component / Σ
weight`), not a weighted sum, so it stays in `[0,1]` regardless of which
goal mode's weights are active.

### Tie-break order (versioned, explicit)

`-score_total, -stimulus_debt, -local_readiness, -program_balance,
-days_since_trained, -dose_feasibility, session_family (ascending)`.

### Rest / Active Recovery

Always generated as its own candidate (`_rest_candidate`), never merely a
fallback when nothing else is eligible. Its score accumulates from four
independent, named bonuses: very-low/low systemic readiness, no eligible
lifting candidate at all, no eligible candidate with a productive
feasible dose, and `goal_mode=recovery`.

## 3. What was deliberately NOT done (per the task's explicit prohibitions)

- No lean_cut-specific or any other goal-specific hardcoded logic —
  verified by `GoalPolicyVariationTests`/`MultiGoalInvariantTests` (in-
  memory) and the live multi-goal validation below (real data).
- No hardcoded Sep-12 special case — the Sep-12 diagnostic below runs the
  exact same `build_shadow_selection()` any other `as_of` would.
- No optimization around total tonnage — `stimulus_debt` uses the
  existing per-muscle `stimulus_sets_14d` metric from TKI-2/TKI-3, never
  a volume/tonnage sum.
- No silent override of fatigued muscles because WHOOP recovery is high —
  eligibility gates on **local** muscle readiness independent of
  systemic readiness; `LocalFatigueSuppressionTests` proves a fatigued
  family cannot win via debt or be rescued by a high WHOOP score.
- No LLM involvement anywhere in scoring or selection.
- No B3/B4 output changed; no Production or iOS file touched; TKI-5
  (progression) not started.

## 4. Tests

- `test_training_intelligence_selection.py` — **26 in-memory tests**:
  scoring-policy structure, local-fatigue suppression (2 tests),
  readiness-combination handling (3), stimulus-debt/neglect scoring (2),
  dose-feasibility gating, goal-policy weight variation, 6-goal-mode
  invariants, Rest/Recovery outcomes (3), cold-start (3), tie-break/
  determinism, and temporal-leakage guards (future rows/naive `as_of`
  rejected).
- `test_training_intelligence_selection_postgres.py` — **2 opt-in
  real-Postgres tests** (new): an end-to-end run through the real SQL
  paths (`load_session_history`/`_load_muscle_set_rows`/`load_rows`, not
  injected fixtures) producing a valid selection, and a temporal-leakage
  test proving a workout inserted in the future does not change the
  selection or any candidate's score.
- Ran together with the existing opt-in suites
  (`test_training_intelligence_dose_postgres.py`,
  `test_training_intelligence_postgres.py`) against the Development
  database: **7 passed**, 0 failed.
- Full backend suite (two-batch pattern, per repo convention):
  `pytest -q --ignore=test_daily_pipeline_cache.py` → **564 passed, 48
  skipped**; `pytest -q test_daily_pipeline_cache.py` → **24 passed**.
  Total **588 passed, 48 skipped, 0 failed** — 0 regressions (48 skipped
  is 2 more than the pre-TKI-4 baseline of 46, exactly the 2 new opt-in
  Postgres tests skipped by default).
  - Note: a first full-suite run showed 10 unrelated failures in two
    pre-existing test files (`test_goal_progress_event_loop_offload.py`,
    `test_whoop_oauth_routes.py`). Root-caused to my own shell's dummy
    `SESSION_SECRET`/`ADMIN_PASSWORD`/`APPLE_HEALTH_INGEST_KEY` exports
    winning over those files' `os.environ.setdefault(...)` calls (first
    writer wins across a shared test process) — not a TKI-4 code defect.
    Re-ran with env values aligned to those files' own fixtures; all
    passed cleanly.
- `test_training_intelligence_dose.py` + `test_training_intelligence_goal_policy.py`
  (52 tests, TKI-3) still pass unchanged after `shadow_dose.py`'s
  backward-compatible `readiness=`/`muscle_readiness_result=`/
  `active_goal=` extension.

## 5. Sep-12-2026 shadow diagnostic (real Development data)

```
as_of:              2026-09-12T08:00:00+00:00
goal_mode:          lean_cut (resolved from the active goal profile)
systemic_capacity:  recovery_score=66.0, readiness_band=moderate
selected:           Lower Body  (score 0.709)
```

Full ranking (all 7 lifting families were eligible; 0 excluded):

| Rank | Family | Score |
|---|---|---|
| 1 | Lower Body | 0.709 |
| 2 | Upper Pull | 0.7051 |
| 3 | Upper Push | 0.6627 |
| 4 | Upper Mixed | 0.6552 |
| 5 | Full Body | 0.6509 |
| 6 | Core + Accessories | 0.6338 |
| 7 | Chest + Biceps | 0.6308 |
| — | Rest / Active Recovery | 0.0 |

Winner's explanation: `stimulus_debt=1.00, local_readiness=1.00,
days_since_trained=0.60, program_balance=0.86; systemic_capacity=0.50,
goal_relevance=0.60 (goal_mode=lean_cut), performance=0.50,
dose_feasibility=0.65, schedule_fit=0.50`; TKI-3 feasible dose range
13–13 → goal-adjusted 13 working sets. Called twice at the identical
`as_of`: selection and every score were bit-for-bit identical
(deterministic).

## 6. Multi-goal validation (same `as_of`, same real data, all 6 modes)

| goal_mode | selected | eligible lifting candidates | systemic_capacity |
|---|---|---|---|
| lean_cut | Lower Body | 7 | moderate |
| lean_bulk | Lower Body | 7 | moderate |
| strength | Lower Body | 7 | moderate |
| maintenance | **Upper Pull** | 7 | moderate |
| general_fitness | Lower Body | 7 | moderate |
| recovery | **Upper Pull** | 7 | moderate |

- **Factual/systemic inputs identical across all 6 modes**, as required:
  exactly 1 distinct `systemic_capacity` reading, exactly 1 distinct
  eligible-family set (`{Chest + Biceps, Core + Accessories, Full Body,
  Lower Body, Rest, Upper Mixed, Upper Pull, Upper Push}`) — goal mode
  never altered eligibility (the only permitted goal-driven eligibility
  effect, `recovery_mode_restriction`, did not trigger here since
  systemic readiness was `moderate`, not `low`/`very_low`).
- Selection legitimately **differs by goal mode** (Lower Body vs. Upper
  Pull) purely through `goal_relevance` weighting — Upper Pull's
  `compound`+`isolation` tag mix scores relatively better under
  `maintenance`/`recovery`'s weight profile than under the other four
  modes, without any mode inventing history or bypassing TKI-3's
  feasible-dose gate. This is the corrected architecture from TKI-3 (a
  posture *within* a shared feasible space, not pure narration) operating
  correctly one layer up, at session selection.

## 7. 90-day historical backtest (real data, daily at 08:00 UTC, default goal_mode)

```
days evaluated:               91
elapsed:                      195.55s (2.15s/day)
family distribution:          Lower Body 43, Upper Push 14, Rest 11,
                               Full Body 10, Chest + Biceps 9,
                               Upper Pull 3, Upper Mixed 1,
                               Core + Accessories 0
rest days:                    11 (12.1%)
readiness_violation_count:    0  (no ineligible family was ever selected)
score-tie days (top-2 equal): 1 of 91
longest repeated-family streak: Lower Body, 16 consecutive days
other streaks >=4 days:       Lower Body x4, Upper Push x6, Rest x4
```

**Invariants held for all 91 days**: no eligible-but-excluded family was
ever selected; Rest was always present as a candidate and won on exactly
the days its bonuses justified; tie-breaking was exercised and
deterministic on the 1 tied day; no future data leaked (see §8).

**Calibration finding, disclosed honestly**: `Lower Body` was selected on
47.3% of days, including one uninterrupted 16-day streak, while `Core +
Accessories` was never selected once in 91 days. This traces to this
user's real Tonal history genuinely under-representing lower-body work,
which correctly drives `stimulus_debt`/`days_since_trained` high for
Lower Body — the mechanism is doing what it was built to do. But the
current v1 weighting does not include any explicit repeat-day/monotony
dampener, so once a family's debt-driven advantage is large, ordinary
day-to-day `program_balance` movement is not enough to unseat it for
extended stretches. This is a real scoring-calibration gap for a v1, not
an eligibility or invariant defect — see §10.

## 8. Temporal correctness

- `build_shadow_selection` rejects a naive (timezone-unaware) `as_of`
  (in-memory `TemporalLeakageTests` + reused throughout).
- In-memory: a session/muscle-row dated after `as_of` does not change any
  candidate's score or the final selection.
- **Real Postgres** (`test_training_intelligence_selection_postgres.py::
  test_future_workout_does_not_influence_real_selection`): inserted a
  workout 5 days after `as_of` directly through the real SQL path (no
  injected fixtures) — the selected family and every `ranked_eligible`
  score were identical before and after the insert. Passed live against
  the Development database.

## 9. Cold-start / multi-user safety

- No WHOOP data: `systemic_capacity` uses the documented `unknown`
  modifier (`0.5`, neutral) rather than assuming high or low readiness.
- No local-readiness history for a muscle: `local_readiness` scores
  `0.3` (explicit uncertainty), never `1.0` (Fresh) — proven by
  `ColdStartTests` and never contradicted by the real-data runs (`data_
  quality.selection_confidence` surfaces this per-call).
- No recent session-family history at all: `program_balance` returns a
  neutral `0.5` rather than dividing by zero or crashing.
- Zero Tonal history at all: every candidate still gets an eligibility
  decision and (if eligible) a full 9-dimension score; Rest is still
  generated. Verified in-memory (`ColdStartTests`) — the real 90-day
  window happened to always have some history, so this path's live
  confirmation rests on the in-memory tests plus the documented fallback
  chain shared with TKI-2/TKI-3.

## 10. Performance

| Operation | Cold (first call) | Warm (repeat, same `as_of`) |
|---|---|---|
| Single `build_shadow_selection()` (7 candidates + Rest) | 1.98s | 1.89s |
| 90-day backtest (91 calls) | — | 2.15s/day average |

Each call fetches WHOOP readiness, muscle readiness, active goal, session
history, muscle-set rows, and ledger rows **exactly once** and reuses
them across all 7 candidate families and Rest (the N+1 query pattern this
avoids — 7x redundant fetches of each shared input — was closed by
`shadow_dose.py`'s backward-compatible `readiness=`/
`muscle_readiness_result=`/`active_goal=` parameters). ~2s/call is
acceptable for an admin-only diagnostic endpoint polled on demand; it is
not intended for, and is not on, any live request path.

## 11. Endpoint

`GET /training-intelligence/selection?as_of=&goal_mode=` — new,
`require_admin`-gated, added directly after the existing TKI-3
`/training-intelligence/dose` endpoint in `main.py`, following its exact
pattern (ISO-8601 `as_of` parsing/defaulting, `ValueError` → 400,
everything else → 500). `goal_mode` is optional and overrides the
resolved goal policy for diagnostic/multi-goal-validation use, exactly as
used in §6 above. Verified `import main` succeeds cleanly with the new
route wired in.

## 12. Limitations / disclosed gaps carried forward

- `schedule_fit` is a constant placeholder (`0.5`) — no dedicated
  training-frequency/schedule system exists yet.
- `secondary_muscles` is always `[]` in the finalized candidate schema —
  `SESSION_TEMPLATES` does not itself distinguish primary/secondary
  muscles at the family level (only individual exercises do, via TKI-2's
  mapping layer).
- The Lower Body dominance/16-day streak finding in §7 is a genuine
  calibration signal, not a mechanism failure — flagged rather than
  smoothed over.
- Cold-start behavior (zero history) is validated in-memory but not
  against a real zero-history Development account, since none was
  available in the live 90-day window.

## 13. Final verdict

**TKI-4 DEVELOPMENT PARTIAL — SCORING CALIBRATION REQUIRED**

The architecture is sound and every hard invariant held: eligibility is
strictly separated from scoring, no goal mode alters eligibility or
bypasses TKI-3's feasible-dose gate, fatigued muscles cannot be rescued
by high systemic readiness, Rest is a first-class always-present
outcome, tie-breaking is deterministic, cold-start paths degrade to
explicit neutral/uncertain values rather than guessing, and temporal
leakage is proven closed both in-memory and against real Postgres. All
33 new tests (26 in-memory + 2 new opt-in Postgres, plus 5 pre-existing
opt-in Postgres tests re-verified) pass, and the full 588-test suite
shows zero regressions.

What keeps this at PARTIAL rather than PASS is the real 90-day backtest
finding in §7: Lower Body won 47.3% of days with an uninterrupted
16-day streak, and Core + Accessories never won once — a real
monotony/coverage gap in the v1 weighting (no repeat-day dampener) that
should be addressed before this scoring layer is trusted as a candidate
for anything beyond shadow diagnostics. No B3/B4 behavior was changed;
no Production or iOS file was touched; TKI-5 was not started.
