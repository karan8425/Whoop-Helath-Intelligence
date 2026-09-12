"""TKI-4.1: versioned calibration configuration for the monotony /
rolling-representation / starvation-protection layer added on top of the
unchanged TKI-4 v1 scoring model (scoring.py / scoring_policy.py are NOT
modified by this milestone - see TRAINING_INTELLIGENCE_TKI41_REPORT.md
section "root cause" for why the fix belongs here, one layer above the
original 9-dimension score, rather than inside it).

Every number in this file is a PRODUCT POLICY / CALIBRATION PARAMETER,
not a scientific constant - identical in spirit to scoring_policy.py's
own disclosure. Nothing here is a per-user constant or a hardcoded
observed distribution; every table is keyed by the shared, finite
goal_mode taxonomy (training_intelligence.dose.goal_policy.GOAL_MODES).

Calibration model: monotony_adjustment(family) = clamp(
    -TOTAL_CALIBRATION_ADJUSTMENT_CAP, +TOTAL_CALIBRATION_ADJUSTMENT_CAP,
    -repeat_penalty + rolling_representation_adjustment + starvation_bonus
)
score_total_calibrated = clamp01(score_total + monotony_adjustment)

This is a SOFT influence only: its total magnitude is capped well below
1.0, so a candidate with a strong enough v1 score_total advantage (large
stimulus debt, no eligible alternative, etc.) still wins - matching the
assignment's explicit "soft scoring influence, not a hard ban" and "a
family may still be selected frequently if real data justifies it".

Rest / Active Recovery is exempt from every adjustment in this file -
repeating Rest is not the pathology this milestone addresses (section 8
of the TKI-4.1 assignment: recovery mode may legitimately select Rest
repeatedly).
"""

from __future__ import annotations

from training_intelligence.dose.goal_policy import (
    GOAL_MODES, LEAN_CUT, LEAN_BULK, STRENGTH, MAINTENANCE, GENERAL_FITNESS, RECOVERY,
)

CALIBRATION_POLICY_VERSION = 1

# ----------------------------------------------------------------------
# Section 3/4: consecutive-selection monotony dampener.
#
# Operates on the SELECTOR'S OWN recent decision stream (an optional,
# caller-supplied `recent_selections` list - see monotony.py), not on
# real Tonal history: the pathology this addresses is the shadow model
# re-selecting the exact same family many consecutive (hypothetical)
# days running, independent of whether that recommendation was ever
# actually followed. Real-history-based signals are handled separately
# below (section 5/6) because they measure a different thing.
# ----------------------------------------------------------------------

# CALIBRATION PARAMETER: a single immediate repeat (this family was also
# selected on the single preceding decision) costs nothing - "first
# repeat: small or no penalty" per the assignment.
CONSECUTIVE_REPEAT_FREE_STREAK = 1

# CALIBRATION PARAMETER: each additional consecutive day beyond the free
# streak adds this much penalty, up to the cap below. Chosen so that a
# close (~0.01-0.05) score_total margin - the common case observed in
# the real 90-day backtest's Lower Body streak - is typically overcome
# within 2-4 additional consecutive days, while a large, well-justified
# margin (a family with no eligible competitor, or a much larger debt
# lead) is not.
CONSECUTIVE_REPEAT_PENALTY_PER_DAY = 0.03

# CALIBRATION PARAMETER: absolute ceiling on the consecutive-repeat
# penalty, reached after ~9 consecutive days - "long streak: meaningful
# penalty" without ever approaching a hard ban (score_total's own scale
# is [0,1]; this alone can never zero out a legitimately dominant score).
CONSECUTIVE_REPEAT_PENALTY_CAP = 0.24

# CALIBRATION PARAMETER (section 8): per-goal-mode tolerance multiplier
# applied to the consecutive-repeat penalty above. >1.0 = less tolerant
# of repetition (penalty amplified); <1.0 = more tolerant (penalty
# dampened). Deliberately does NOT make every goal mode converge to the
# same behavior - strength/hypertrophy intentionally tolerate repeated
# movement-family/muscle exposure; general_fitness intentionally favors
# broader rotation.
MONOTONY_TOLERANCE_MULTIPLIER = {
    LEAN_CUT: 1.0,
    LEAN_BULK: 0.6,        # hypertrophy_gain: repeat productive exposures more readily
    STRENGTH: 0.5,         # intentional movement-family specificity/repetition
    MAINTENANCE: 1.0,
    GENERAL_FITNESS: 1.3,  # broader balance is the explicit objective
    RECOVERY: 1.0,         # lifting families are already de-emphasized elsewhere for recovery
}

# ----------------------------------------------------------------------
# Section 5: rolling representation, from REAL recent Tonal session-
# family history (training_intelligence.stimulus.session_family), not
# the selector's own hypothetical output. Complements (does not replace)
# the existing `program_balance` scoring dimension, which only looks at
# a single 14-day window with no cross-window blending.
# ----------------------------------------------------------------------

# CALIBRATION PARAMETER: the windows evaluated. All three fit inside the
# ledger_rows lookback shadow_selection.py already loads once per call
# (30 days) - no additional database query is introduced.
ROLLING_REPRESENTATION_WINDOWS_DAYS = (7, 14, 30)

# CALIBRATION PARAMETER: a window with fewer than this many total real
# sessions across all families carries too little signal to judge over/
# under-representation from - it is skipped (cold-start guard), never
# treated as "family X had 0% share, therefore starved".
ROLLING_REPRESENTATION_MIN_TOTAL_SESSIONS = 3

# CALIBRATION PARAMETER: bounds on the rolling-representation adjustment
# contributed by any single window, before averaging across windows that
# had enough signal to count.
ROLLING_OVERREPRESENTATION_MAX_PENALTY = 0.10
ROLLING_UNDERREPRESENTATION_MAX_BONUS = 0.08

# ----------------------------------------------------------------------
# Section 6: starvation protection - a distinct, stricter, discrete
# bonus for the specific case of a family being entirely absent from
# real training for a meaningful period, gated on it still being
# eligible AND meaningfully dosable (never overrides readiness/dose
# invariants - see candidates.py, unmodified).
# ----------------------------------------------------------------------

# CALIBRATION PARAMETER: "meaningfully dosable" for starvation purposes
# is a stricter bar than plain eligibility's MIN_PRODUCTIVE_UPPER_BOUND_
# WORKING_SETS (3, in scoring_policy.py) - starvation pressure should not
# be spent nudging a family that is only barely above the eligibility
# floor.
STARVATION_MIN_DOSE_UPPER_BOUND_WORKING_SETS = 5

# CALIBRATION PARAMETER: a family with zero real sessions anywhere in
# the (already-loaded) 30-day ledger window is treated as "absent for a
# meaningful period". This is a floor, not a claim about a scientifically
# correct starvation threshold.
STARVATION_ABSENCE_WINDOW_DAYS = 30

# CALIBRATION PARAMETER (section 15 cold-start guard): starvation is a
# claim about one family being neglected RELATIVE TO the user's other
# training - it requires some minimum amount of real training to exist
# at all in the window. A genuinely brand-new user with zero history for
# every family must get zero starvation bonus everywhere (no fake
# certainty that a specific family is "neglected" when nothing has ever
# been observed) - reuses the same floor as the rolling-representation
# signal for consistency.
STARVATION_MIN_TOTAL_SESSIONS_FOR_SIGNAL = ROLLING_REPRESENTATION_MIN_TOTAL_SESSIONS

STARVATION_BONUS = 0.10

# ----------------------------------------------------------------------
# Overall bound - keeps this whole layer a SOFT influence, never able to
# invert a large, well-justified v1 score_total margin.
# ----------------------------------------------------------------------
TOTAL_CALIBRATION_ADJUSTMENT_CAP = 0.30
