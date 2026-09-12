"""Versioned scoring configuration for TKI-4 session selection.

Every number in this file is a PRODUCT POLICY decision (or, where noted,
an evidence-informed default), never presented as a scientific finding -
matching the discipline established in training_intelligence.knowledge
and training_intelligence.dose.goal_policy. Nothing here is a per-user
constant; every table is keyed by the shared, finite goal_mode taxonomy
(training_intelligence.dose.goal_policy.GOAL_MODES) or by the shared,
finite SESSION_TEMPLATES taxonomy - a second user on the same goal mode
reads the identical row.

Scoring model: score_total(family) = sum(weight[dim] * component[dim]),
where each component is bounded to [0.0, 1.0] (see scoring.py) and each
weight is a small positive multiplier (typically 0.5-2.0) expressing how
much that dimension matters for a given goal mode. Weights never make a
component negative or unbounded - they only change relative emphasis.
"""

from __future__ import annotations

from training_intelligence.dose.goal_policy import GOAL_MODES, LEAN_CUT, LEAN_BULK, STRENGTH, MAINTENANCE, GENERAL_FITNESS, RECOVERY

SCORING_POLICY_VERSION = 1

# The 9 scoring dimensions, in the order the assignment lists them.
SCORE_DIMENSIONS = (
    "stimulus_debt",
    "local_readiness",
    "days_since_trained",
    "program_balance",
    "systemic_capacity",
    "goal_relevance",
    "performance",
    "dose_feasibility",
    "schedule_fit",
)

_NEUTRAL_WEIGHTS = {dimension: 1.0 for dimension in SCORE_DIMENSIONS}

# Per-goal-mode weight table. Every entry starts from _NEUTRAL_WEIGHTS and
# overrides only the dimensions that goal mode is documented (in
# TRAINING_INTELLIGENCE_KNOWLEDGE_BASE_V1.md / the TKI-4 assignment) to
# emphasize or de-emphasize. Deliberately conservative multipliers
# (0.5-1.6) - this is a starting product-policy calibration, not a tuned
# result; see TRAINING_INTELLIGENCE_TKI4_REPORT.md's limitations section.
SCORING_WEIGHTS = {
    LEAN_CUT: {
        **_NEUTRAL_WEIGHTS,
        "program_balance": 1.4,     # preserve broad muscle stimulus
        "local_readiness": 1.3,     # avoid unnecessary repeated local fatigue
        "stimulus_debt": 1.1,
    },
    LEAN_BULK: {
        **_NEUTRAL_WEIGHTS,
        "stimulus_debt": 1.6,       # stronger emphasis on unresolved debt
        "program_balance": 0.8,     # tolerate repeated targeted exposure
        "dose_feasibility": 1.2,    # compatible with an upper dose posture
    },
    STRENGTH: {
        **_NEUTRAL_WEIGHTS,
        "performance": 1.5,         # movement-specificity/progression emphasis
        "program_balance": 0.7,     # tolerate repetition for specificity
        "dose_feasibility": 1.3,    # a session must be meaningfully dosable
    },
    MAINTENANCE: {
        **_NEUTRAL_WEIGHTS,
        "program_balance": 1.2,     # sufficient coverage
        "stimulus_debt": 0.6,       # lower urgency
        "dose_feasibility": 0.7,    # a lower minimum burden is acceptable
    },
    GENERAL_FITNESS: {
        **_NEUTRAL_WEIGHTS,
        "program_balance": 1.4,     # broad balance is the main objective
    },
    RECOVERY: {
        **_NEUTRAL_WEIGHTS,
        "local_readiness": 1.6,     # strongly favor low-fatigue compatibility
        "systemic_capacity": 1.4,   # systemic state weighs heavily toward rest
        "dose_feasibility": 0.5,    # a session need not be heavily dosed
        "stimulus_debt": 0.5,       # urgency is de-emphasized
    },
}


def get_scoring_weights(goal_mode) -> dict:
    """Returns the weight table for a canonical goal_mode, or the neutral
    (all-1.0) table when goal_mode is None/unrecognized - never guesses a
    goal-specific emphasis for an unresolved goal."""
    return dict(SCORING_WEIGHTS.get(goal_mode, _NEUTRAL_WEIGHTS))


# ----------------------------------------------------------------------
# Session-family metadata (product policy, versioned) - NOT a competing
# taxonomy: keyed by the exact family names in
# integrations.tonal.training_priority.SESSION_TEMPLATES.
# ----------------------------------------------------------------------

# Approximate relative systemic cost of a family (more primary muscles /
# larger movement patterns -> higher cost). Used only to scale the
# systemic_capacity component - never to pick a muscle group by itself
# (every eligible family still receives a systemic_capacity contribution;
# this only changes its magnitude).
FAMILY_SYSTEMIC_COST = {
    "Upper Push": 0.6,
    "Upper Pull": 0.5,
    "Chest + Biceps": 0.45,
    "Lower Body": 0.7,
    "Upper Mixed": 0.8,
    "Core + Accessories": 0.3,
    "Full Body": 1.0,
}

# Coarse tags used for the goal_relevance component - deliberately small
# (2 tags) rather than a full per-family-per-goal matrix, to keep this
# auditable. "compound" = multi-region/larger movement families;
# "isolation" = smaller, narrower-region families.
FAMILY_TAGS = {
    "Upper Push": ("compound",),
    "Upper Pull": ("compound",),
    "Chest + Biceps": ("isolation",),
    "Lower Body": ("compound",),
    "Upper Mixed": ("compound",),
    "Core + Accessories": ("isolation",),
    "Full Body": ("compound",),
}

# Per-goal-mode bonus (added to the neutral 0.5 goal_relevance baseline)
# for each tag a candidate family carries. Bounded so goal_relevance
# itself stays within [0.0, 1.0] - see scoring.py's clamp.
GOAL_RELEVANCE_TAG_BONUS = {
    LEAN_CUT: {"compound": 0.10, "isolation": 0.0},
    LEAN_BULK: {"compound": 0.05, "isolation": 0.05},
    STRENGTH: {"compound": 0.20, "isolation": -0.10},
    MAINTENANCE: {"compound": 0.0, "isolation": 0.0},
    GENERAL_FITNESS: {"compound": 0.05, "isolation": 0.05},
    RECOVERY: {"compound": -0.20, "isolation": -0.05},
}

# Minimum upper-bound feasible working sets (TKI-3's feasible_dose_range)
# for a candidate to be considered dosable at all. Below this, the
# candidate is INELIGIBLE (section 12), not merely low-scoring. Reuses
# the same order of magnitude as B2's own CONSERVATIVE_MUSCLE_SET_
# BASELINE (3.0/muscle) rather than inventing an unrelated number.
MIN_PRODUCTIVE_UPPER_BOUND_WORKING_SETS = 3

# Rest/Active Recovery candidate: bonus applied to its score under
# specific, explainable conditions (section 17) - never a silent
# override, always a component visible in the shadow output.
REST_BASELINE_SCORE = 0.0
REST_BONUS_VERY_LOW_SYSTEMIC = 0.6
REST_BONUS_LOW_SYSTEMIC = 0.3
REST_BONUS_NO_ELIGIBLE_LIFTING_CANDIDATE = 1.0
REST_BONUS_NO_PRODUCTIVE_DOSE_ANYWHERE = 0.8
REST_BONUS_RECOVERY_GOAL_MODE = 0.35
