# Training Intelligence Knowledge Base V1

**Purpose:** Define a deterministic, evidence-informed training decision system for the WHOOP Health Intelligence app, with Tonal as the strength-training execution platform and WHOOP as the systemic-readiness layer.

**Status:** Design specification. Not yet implemented.

## 1. Design principle

The training engine must not ask only, “How recovered am I today?”

It must answer, in order:

1. What is the user trying to achieve?
2. What stimulus is missing across the current training week?
3. Which muscle groups are locally available to train?
4. How much systemic training capacity is available today?
5. What session best satisfies the weekly plan without violating local fatigue?
6. What dose is appropriate relative to the user’s own successful history?
7. Which Tonal movements best deliver that dose?
8. What progression should be attempted today?
9. Does the final plan pass consistency and safety checks?

WHOOP informs **how much systemic stress is appropriate**.  
Tonal history/local readiness informs **what should be trained**.  
The user’s goal and weekly training state determine **why the session exists**.

---

# 2. Evidence hierarchy

## Tier 1 — Consensus / position stands
Use for hard guardrails and default training principles.

- American College of Sports Medicine (ACSM), 2026 Resistance Training Position Stand
- World Health Organization physical activity guidance

## Tier 2 — Evidence-based programming interpretation
Use for practical programming logic.

- Stronger by Science
- Barbell Medicine

## Tier 3 — Equipment/platform-specific knowledge
Use for Tonal exercise selection, progression context, and device-specific execution.

- Tonal official programming and training-goal documentation
- WHOOP official Recovery / Strain documentation

## Tier 4 — Personalized evidence
Eventually outranks generic defaults when enough high-quality user history exists.

Examples:
- Recent comparable-session volume
- Exercise-level performance
- Weekly set tolerance
- Recovery response
- Strength trend
- Completion/adherence
- Body-composition response

Generic guidance initializes the model. Personal response calibrates it.

---

# 3. Core evidence-backed principles

## 3.1 Consistency and full-body coverage
ACSM’s 2026 position stand emphasizes training all major muscle groups at least twice weekly and individualizing programs around goals and adherence.

**Engine implication:** Never optimize one day in isolation. Maintain a rolling weekly stimulus ledger by muscle group.

## 3.2 Hypertrophy volume
ACSM’s 2026 guidance highlights approximately 10 weekly sets per muscle group as a practical hypertrophy target.

**Engine implication:** ~10 sets/week/muscle is a starting anchor, not an immutable prescription. Personal history and response should modify it.

## 3.3 Strength loading
ACSM recommends heavier loading around 80% 1RM and multiple sets when strength is the primary outcome.

**Engine implication:** Strength-focused movements should preserve meaningful loading even during a fat-loss phase, unless recovery/local readiness argues otherwise.

## 3.4 Failure is not mandatory
ACSM’s 2026 review reports that momentary muscular failure is not consistently necessary for results in healthy adults.

**Engine implication:** Do not require failure. Use RIR/RPE-style effort targets and progressive overload.

## 3.5 Progressive overload has multiple forms
Stronger by Science describes progression through reps, load, sets, proximity to failure, and other variables.

**Engine implication:** Prefer the least disruptive progression that increases stimulus:
1. Improve reps within the target range.
2. Increase resistance after the rep target is consistently achieved.
3. Add a set only when weekly stimulus is insufficient and recovery permits.
4. Change exercise only when needed for balance, stagnation, discomfort, or program design.

## 3.6 Start from tolerated training
Barbell Medicine recommends beginning near volumes the individual has previously tolerated and progressed on, then adjusting according to performance and fatigue.

**Engine implication:** Historical successful sessions should be the default dose anchor. Do not replace personal history with generic volume formulas when sufficient user data exists.

## 3.7 Fitness-fatigue balance
Training produces both adaptation and fatigue.

**Engine implication:** More volume is not automatically better. The goal is the highest useful stimulus that can be recovered from while maintaining progression.

## 3.8 WHOOP Recovery is systemic readiness, not muscle selection
WHOOP describes high Recovery as greater capacity for training load and low Recovery as a reason to reduce strain.

**Engine implication:** WHOOP may scale dose/intensity, but it must not select a fatigued muscle merely because systemic Recovery is high.

## 3.9 Daily movement and strength are separate decisions
WHO guidance supports regular aerobic activity plus resistance training.

**Engine implication:** Daily activity/conditioning has its own adaptive target. Do not use strength tonnage as a substitute for movement, or steps as a substitute for resistance training.

---

# 4. Four-layer architecture

## Layer A — Knowledge
Stable source-backed facts.

Examples:
- Major muscle groups should receive regular resistance training.
- Hypertrophy responds to sufficient weekly volume.
- Strength responds to heavier loading.
- Failure is not required.
- Higher systemic readiness can permit higher training stress.

## Layer B — Policy
Product decisions made by this app.

Examples:
- Primary muscle = 1.0 set credit.
- Secondary muscle = partial set credit.
- Prefer fresh muscles with stimulus debt.
- Use comparable historical session volume as the dose anchor.
- A single WHOOP score cannot override local fatigue.

Policy values must be versioned and testable.

## Layer C — Personal model
Learned characteristics for the user.

Examples:
- Productive weekly set range by muscle
- Typical successful session dose
- Exercise-specific working loads
- Recovery time after hard sessions
- Frequency tolerance
- Response to high/low volume
- Performance during calorie deficit

## Layer D — Daily state
Today’s facts.

Examples:
- Goal / phase
- WHOOP Recovery
- HRV/RHR deviation
- Sleep
- Recent strain
- Muscle readiness
- Recent Tonal sessions
- Weekly set ledger
- Steps/activity
- Planned training days remaining
- Injury/pain/manual exclusions

---

# 5. Goal model

Every prescription must begin with a goal.

Supported goal modes:

- Hypertrophy
- Strength
- Fat loss with lean-mass preservation
- General fitness
- Recovery / return-to-training

For the current lean-cut goal:

**Primary strength objective:** preserve or improve strength and lean mass while body fat declines.

Policy consequences:
- Maintain meaningful resistance intensity.
- Avoid unnecessary large volume increases during an energy deficit.
- Prioritize high-quality hard sets.
- Use body-composition and performance trends to detect possible under-recovery.
- Do not turn a cut into primarily cardio.

---

# 6. Weekly stimulus ledger

Track for every major muscle group:

- direct sets
- secondary/equivalent sets
- sessions trained
- days since trained
- last-session dose
- recent comparable volume
- performance trend
- readiness
- target weekly stimulus
- stimulus debt/surplus

Suggested muscle groups:
- Chest
- Back
- Biceps
- Triceps
- Shoulders
- Quads
- Hamstrings
- Glutes
- Calves
- Abs/Core

The session selector should optimize the week, not merely rotate names such as Push/Pull/Legs.

---

# 7. Local muscle readiness

Treat Tonal’s heatmap/readiness as a **local workload-recovery signal**, not a medical measurement.

States:

### Fresh
Eligible for normal or high-volume work.

### Recovering
Eligible for moderate work if program value is high.

### Fatigued
Avoid as a primary high-dose target unless:
- no better fresh alternative exists,
- the planned stimulus is intentionally light,
- and the engine explains why.

Critical invariant:

> A high WHOOP Recovery score cannot independently convert a locally fatigued muscle into a high-volume target.

---

# 8. Systemic readiness

WHOOP Recovery should scale available systemic dose.

WHOOP public guidance:
- Green: 67–100
- Yellow: 34–66
- Red: 0–33

The application should additionally compare today with the user’s own:
- 7-day Recovery baseline
- 30-day Recovery baseline
- HRV deviation
- RHR deviation
- sleep debt
- recent cumulative strain

Suggested product interpretation:

### Very high systemic readiness
Strong capacity for planned training stress.

### Normal systemic readiness
Execute normal planned dose.

### Reduced systemic readiness
Reduce dose/intensity or select lower-cost training.

### Poor systemic readiness
Rest, active recovery, or very low-cost training depending on context.

The exact dose multiplier must be calibrated from user history, not hard-coded solely from WHOOP percentage.

---

# 9. Session selection hierarchy

Score candidate sessions using:

1. Goal relevance
2. Weekly stimulus debt
3. Local muscle readiness
4. Program balance
5. Days since muscle trained
6. Systemic readiness
7. Recent performance
8. Schedule / remaining training days
9. Exercise availability
10. User constraints

Example:

WHOOP Recovery = 94
Back = fatigued
Quads/Hamstrings/Glutes = fresh
Chest = fresh
Lower body = behind weekly target

Result:
A lower-body or other high-value fresh-muscle session should usually outrank an Upper Pull session.

If Upper Pull still wins, the engine must expose the reason.

---

# 10. Session dose

Do not use total tonnage as the primary dose target.

Primary dose dimensions:

1. Hard/effective working sets
2. Target RIR
3. Rep range
4. Relative resistance / exercise-specific load
5. Exercise count
6. Session duration
7. Comparable-session workload
8. Muscle-group stimulus

Tonnage remains a descriptive and longitudinal metric within comparable movements/session types.

## Personalized anchor

For each session family/muscle group calculate:

- median successful working sets
- recent productive volume range
- recent exercise loads
- completed rep ranges
- strength trend
- recovery after similar sessions

Use these as the starting dose.

Generic rules should be fallback behavior only.

---

# 11. Progression engine

Per exercise:

### Step 1 — Maintain movement
Prefer stable exercise selection long enough to measure progress.

### Step 2 — Rep progression
If the user completed the prior prescription with acceptable effort:
increase reps within target range.

### Step 3 — Load progression
When the upper end of the rep range is repeatedly achieved:
increase Tonal resistance modestly.

### Step 4 — Set progression
Only add working sets when:
- weekly stimulus is below target,
- performance is stable/improving,
- recovery is adequate,
- and recent training is tolerated.

### Step 5 — Hold / regress
If performance or recovery worsens:
hold load, reduce reps/sets, or substitute as appropriate.

Do not increase every variable simultaneously.

---

# 12. Body-composition feedback

Training dose should react to longitudinal goal progress.

For a lean cut:

### Weight/fat decreasing; strength stable
Maintain training strategy.

### Weight/fat decreasing; strength improving
Maintain. Do not increase volume merely because progress is good.

### Weight decreasing; strength repeatedly declining
Investigate:
- calorie deficit severity
- protein
- sleep
- recovery
- excessive volume
- insufficient resistance intensity

Protect training quality before blindly adding volume.

### Lean mass trend declining
Increase caution:
- protect strength stimulus
- review calorie deficit and protein
- avoid unnecessary fatigue
- verify body-composition signal across multiple observations before reacting

Single body-composition readings should not drive major changes.

---

# 13. Readiness × goal decision matrix

## High systemic + Fresh target muscles
Normal-to-high personalized dose.
Progression opportunity.

## High systemic + Fatigued intended muscles
Select another high-value fresh region when possible.
Do not “spend” readiness on fatigued tissue just because Recovery is green.

## Moderate systemic + Fresh muscles
Normal or slightly reduced dose.
Maintain stimulus.

## Moderate systemic + Recovering muscles
Moderate dose.
Favor quality over volume.

## Low systemic + Fresh muscles
Reduced systemic cost:
fewer sets, lower effort, easier session, or recovery work.

## Low systemic + Fatigued muscles
Rest / mobility / low-intensity activity is favored.

---

# 14. Daily movement / conditioning

Movement should be governed separately from lifting.

Inputs:
- 7/14/30-day step baseline
- body-composition goal
- recent cardio volume
- strength day vs rest day
- WHOOP recovery
- current-day accumulated steps
- schedule
- trend in fatigue/recovery

Rules:
- Do not collapse a normal user’s step target to a very low number without an explicit recovery or workload reason.
- High Recovery should normally preserve or modestly increase activity opportunity, not suppress it.
- Strength sessions do not automatically replace basic daily movement.
- Zone 2 should complement resistance training rather than compete with recovery.

---

# 15. Consistency invariants

A generated workout must pass these checks before publication.

### Invariant 1 — Goal consistency
Session materially supports the active goal.

### Invariant 2 — Weekly balance
The selected muscles are not repeatedly favored while other major fresh muscles accumulate large stimulus deficits.

### Invariant 3 — Local readiness
A fatigued muscle cannot be the dominant target when a similarly important fresh muscle is available without an explicit reason.

### Invariant 4 — Systemic-dose consistency
A “Push” recommendation must not silently produce a recovery-sized workout.

### Invariant 5 — Historical-dose consistency
If projected dose is materially below or above the user’s comparable successful history, require an explanation.

### Invariant 6 — Progression consistency
Progressive overload must be based on comparable exercise history and should not simultaneously increase load, reps, and sets without justification.

### Invariant 7 — Recovery protection
Low recovery can reduce dose. High recovery can increase opportunity, but never overrides local fatigue or program balance.

### Invariant 8 — Explainability
Every session must preserve the major factors responsible for:
- session selection
- dose
- progression
- movement target

---

# 16. Proposed recommendation object

```json
{
  "goal": {
    "phase": "lean_cut",
    "priority": "preserve_lean_mass"
  },
  "readiness": {
    "systemic": {
      "status": "high",
      "whoop_recovery": 94
    },
    "local": {
      "fresh": ["chest", "quads", "hamstrings", "glutes", "calves"],
      "recovering": ["shoulders", "triceps"],
      "fatigued": ["back"]
    }
  },
  "weekly_state": {
    "muscle_stimulus": {},
    "priority_deficits": []
  },
  "selection": {
    "session_type": "lower",
    "reason_codes": [
      "HIGH_SYSTEMIC_READINESS",
      "LOWER_BODY_FRESH",
      "LOWER_BODY_STIMULUS_DEBT",
      "BACK_LOCAL_FATIGUE"
    ]
  },
  "dose": {
    "target_sets": 12,
    "historical_comparable_sets": 11,
    "dose_modifier": "high_readiness"
  },
  "progression": [],
  "validation": {
    "passed": true,
    "warnings": []
  }
}
```

Numbers above are illustrative; production values must be computed from actual history.

---

# 17. Knowledge rule format

Every machine-readable rule should have:

```yaml
rule_id: TRAINING_LOCAL_001
name: Prefer fresh muscle over fatigued muscle
evidence_class: product_policy
sources:
  - WHOOP Recovery guidance
  - fitness-fatigue programming framework
conditions:
  - systemic_readiness == high
  - intended_primary_muscle == fatigued
  - fresh_alternative_with_program_value == true
action:
  - re_score_session_candidates
explanation:
  - "Systemic readiness is high, but the planned target is locally fatigued."
confidence: high
version: 1
```

This separates scientific evidence from product interpretation.

---

# 18. Source registry

## ACSM
2026 Resistance Training Guidelines / Position Stand  
https://acsm.org/resistance-training-guidelines-update-2026/  
https://www.acsm.org/wp-content/uploads/2026/03/Resistance-Training-Position-Stand-infographic.pdf

## Stronger by Science
Complete Strength Training Guide  
https://www.strongerbyscience.com/complete-strength-training-guide/

Progressive Overload Strategies  
https://www.strongerbyscience.com/progressive-overload-strategies/

Powerbuilding  
https://www.strongerbyscience.com/how-to-powerbuild/

## Barbell Medicine
Strength Training Programming  
https://www.barbellmedicine.com/blog/strength-training-programming/

Powerlifting programming / individual volume discussion  
https://www.barbellmedicine.com/training-programs/powerlifting/

## Tonal
Training Programs  
https://tonal.com/pages/training-programs

Training Goal Progress Metrics  
https://tonal.com/blogs/all/training-goal-progress-key-metrics

90-Day Muscle Building Plan  
https://tonal.com/blogs/all/workout-plan-build-muscle

## WHOOP
Recovery guidance  
https://support.whoop.com/s/article/WHOOP-Recovery

Training Zones / Recovery and Strain  
https://www.whoop.com/us/en/thelocker/whoop-training-zones-optimal-overreaching-restoring/

WHOOP Strain  
https://www.whoop.com/us/en/thelocker/how-does-whoop-strain-work-101/

## WHO
Physical Activity Guidance  
https://www.who.int/initiatives/behealthy/physical-activity

---

# 19. Implementation roadmap

### TKI-1 — Knowledge schema
Create versioned machine-readable principles/rules/source registry.

### TKI-2 — Weekly stimulus ledger
Build direct/equivalent muscle-set accounting from Tonal history.

### TKI-3 — Personal dose model
Learn comparable-session working-set and load ranges.

### TKI-4 — Session scoring
Replace simplistic rotation with goal + stimulus debt + local readiness + systemic capacity scoring.

### TKI-5 — Progression model
Exercise-level rep/load/set progression from Tonal history.

### TKI-6 — Validation/invariants
Reject internally inconsistent prescriptions.

### TKI-7 — Backtest
Replay 90+ days and measure:
- muscle balance
- dose stability
- local-fatigue violations
- progression quality
- high-recovery opportunity utilization
- low-recovery protection

### TKI-8 — Daily movement integration
Use activity history and recovery without coupling step targets to lifting tonnage.

---

# 20. Success condition

The training engine is ready when it can explain:

> “Given your goal, weekly stimulus balance, local muscle readiness, systemic recovery, and prior Tonal performance, this is the most valuable session today; this is the appropriate dose; and this is the progression you should attempt.”

That is the standard for Training Intelligence V1.
