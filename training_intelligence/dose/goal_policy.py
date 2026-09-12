"""Versioned goal-mode policy layer for the personalized training dose model.

The dose model itself (integrations.tonal.training_dose.compute_dose_target)
is, and must remain, goal-agnostic: it derives entirely from this user's own
historical sessions, WHOOP systemic readiness, and local muscle readiness -
never from which goal is active. This module does not change any of that,
and neither does the goal-agnostic FEASIBLE DOSE RANGE
(training_intelligence.dose.shadow_dose._feasible_dose_range) built from it -
that range (min/max justified working sets today) is identical across every
goal mode, for the same history/readiness state.

What THIS module provides is a versioned, table-driven description of how
each goal MODE selects a POSTURE inside that already-established,
goal-agnostic range - never a new capacity estimate, never wider than the
range, never able to override a local-fatigue-driven collapse of the range
to zero. Two kinds of fields:

  - `range_position_fraction` (0.0-1.0): the ONE numeric lever goal mode is
    allowed to apply. 0.0 = the range's own lower bound, 1.0 = its own upper
    bound. This is where "same user + same history + same readiness may
    produce a different final recommended dose under lean_cut vs
    hypertrophy_gain vs strength vs maintenance" actually happens - see
    training_intelligence/dose/shadow_dose.py's
    _feasible_dose_range/_goal_adjusted_working_sets and
    test_training_intelligence_goal_policy.py's
    test_goal_mode_never_exceeds_feasible_range.
  - Everything else (training_objective, volume_posture,
    progression_emphasis, fatigue_tolerance, intensity_volume_tradeoff,
    maintenance_vs_growth_priority) remains purely descriptive/narrative
    framing, consumed for explanation only - it is not (yet) wired to a
    second numeric lever, to avoid inventing more machinery than this
    milestone asked for.

Six goal modes are defined, matching
TRAINING_INTELLIGENCE_KNOWLEDGE_BASE_V1.md section 5's "Supported goal
modes" list exactly:

    lean_cut      - fat loss with lean-mass preservation
    lean_bulk     - hypertrophy / lean gain (alias: hypertrophy_gain)
    strength      - strength as the primary outcome
    maintenance   - hold current capability, no directed progression
    general_fitness - broad, non-specialized training
    recovery      - return-to-training / rebuilding tolerance
                    (alias: return_to_training)

Only four of these (lean_cut, lean_bulk, maintenance, general_fitness) are
reachable today from the real `health_goal_profiles` schema (phase/
goal_type); "strength" and "recovery" have no goal_type yet. This module
still defines full policies for all six so the training-intelligence
engine does not require a code change when the goal-setting system
eventually adds them - only a new _GOAL_TYPE_TO_MODE entry. Until then, a
goal_mode can also be supplied directly (bypassing the inference), which
is exactly what lets this be tested and used for goal modes the live goal
system cannot yet produce.

Multi-user note: nothing here is a per-user constant. Every field is a
property of the GOAL MODE (a finite, shared taxonomy), never of a
specific person; a second user on a different goal mode gets a different
policy entry from the same table, not a different code path.
"""

from __future__ import annotations

# v2: added range_position_fraction - the one numeric lever goal mode may
# apply, selecting a posture within the (unchanged) goal-agnostic feasible
# dose range rather than only narrating an unused training_objective.
GOAL_POLICY_VERSION = 2

LEAN_CUT = "lean_cut"
LEAN_BULK = "lean_bulk"
STRENGTH = "strength"
MAINTENANCE = "maintenance"
GENERAL_FITNESS = "general_fitness"
RECOVERY = "recovery"

GOAL_MODES = (LEAN_CUT, LEAN_BULK, STRENGTH, MAINTENANCE, GENERAL_FITNESS, RECOVERY)

# Alternate names a caller might reasonably supply for the same mode.
_MODE_ALIASES = {
    "hypertrophy_gain": LEAN_BULK,
    "hypertrophy": LEAN_BULK,
    "return_to_training": RECOVERY,
}

# Real health_goal_profiles.goal_type -> canonical training goal_mode.
# Only lean_cut/lean_bulk/maintenance/general_fitness-shaped goal_types
# exist in the goal system today (see goal_pace_config.GOAL_TYPE_TO_PHASE)
# - "strength" and "recovery" are defined above for when/if the goal
# system adds them, and are reachable today only via an explicit
# goal_mode override.
_GOAL_TYPE_TO_MODE = {
    "lose_body_fat": LEAN_CUT,
    "recomposition": LEAN_CUT,
    "build_muscle": LEAN_BULK,
    "improve_fitness": GENERAL_FITNESS,
    "maintain": MAINTENANCE,
}

# Fallback when goal_type is missing/unrecognized: derive from the coarser
# `phase` column instead of guessing further.
_PHASE_TO_MODE = {
    "lean_cut": LEAN_CUT,
    "lean_bulk": LEAN_BULK,
    "maintenance": MAINTENANCE,
}

GOAL_POLICIES = {
    LEAN_CUT: {
        "training_objective": "preserve_or_gain_lean_mass",
        "range_position_fraction": 0.35,
        "stimulus_posture": "preserve",
        "volume_posture": "prefer_moderate_avoid_unnecessary_increase",
        "progression_emphasis": "load_and_quality_over_added_volume",
        "fatigue_tolerance": "moderate",
        "intensity_volume_tradeoff": "favor_intensity_preserve_quality",
        "maintenance_vs_growth_priority": "maintenance",
        "notes": (
            "Fat loss with lean-mass preservation: maintain meaningful resistance "
            "intensity, avoid unnecessary large volume increases in an energy "
            "deficit, prioritize high-quality hard sets over junk volume - so this "
            "mode targets the lower-middle of the feasible range, not its ceiling."
        ),
    },
    LEAN_BULK: {
        "training_objective": "maximize_hypertrophy_stimulus",
        "range_position_fraction": 0.80,
        "stimulus_posture": "expand",
        "volume_posture": "accept_upper_personal_range",
        "progression_emphasis": "volume_and_reps_over_load",
        "fatigue_tolerance": "high",
        "intensity_volume_tradeoff": "favor_volume",
        "maintenance_vs_growth_priority": "growth",
        "notes": (
            "Hypertrophy/lean gain: targets the upper portion of the feasible "
            "range - a well-recovered upper-range dose is consistent with the "
            "goal, not a signal to intervene."
        ),
    },
    STRENGTH: {
        "training_objective": "maximize_strength_expression",
        "range_position_fraction": 0.25,
        "stimulus_posture": "peak_intensity",
        "volume_posture": "prefer_lower_volume_higher_intensity",
        "progression_emphasis": "load_progression_priority",
        "fatigue_tolerance": "low",
        "intensity_volume_tradeoff": "favor_intensity",
        "maintenance_vs_growth_priority": "growth",
        "notes": (
            "Strength as the primary outcome: targets the lower portion of the "
            "feasible working-set range - heavier relative loading and preserved "
            "recovery matter more than additional working sets. Load/intensity "
            "progression itself is B3/progressive_overload's concern, not "
            "recomputed here."
        ),
    },
    MAINTENANCE: {
        "training_objective": "hold_current_capability",
        "range_position_fraction": 0.50,
        "stimulus_posture": "maintain",
        "volume_posture": "prefer_moderate",
        "progression_emphasis": "maintenance_only",
        "fatigue_tolerance": "moderate",
        "intensity_volume_tradeoff": "balanced",
        "maintenance_vs_growth_priority": "maintenance",
        "notes": (
            "No directed progression is required; targets the range's midpoint - "
            "consistency matters more than growth."
        ),
    },
    GENERAL_FITNESS: {
        "training_objective": "broad_non_specialized_conditioning",
        "range_position_fraction": 0.50,
        "stimulus_posture": "maintain",
        "volume_posture": "prefer_moderate",
        "progression_emphasis": "balanced",
        "fatigue_tolerance": "moderate",
        "intensity_volume_tradeoff": "balanced",
        "maintenance_vs_growth_priority": "maintenance",
        "notes": "No specialized emphasis; targets the range's midpoint.",
    },
    RECOVERY: {
        "training_objective": "rebuild_tolerance_safely",
        "range_position_fraction": 0.10,
        "stimulus_posture": "reduce_and_rebuild",
        "volume_posture": "prefer_lower_range",
        "progression_emphasis": "gentle_reintroduction",
        "fatigue_tolerance": "low",
        "intensity_volume_tradeoff": "minimal_favor_recovery",
        "maintenance_vs_growth_priority": "recovery_first",
        "notes": (
            "Return-to-training: deliberately targets near the range's lower "
            "bound - a below-typical dose is expected and appropriate here, not "
            "a shortfall to correct."
        ),
    },
}


def normalize_goal_mode(value: str | None) -> str | None:
    """Resolves an alias to its canonical mode; returns the value unchanged
    if it is already canonical, or None if it is neither."""
    if value is None:
        return None
    if value in GOAL_MODES:
        return value
    return _MODE_ALIASES.get(value)


def resolve_goal_mode(goal: dict) -> str | None:
    """goal: the dict returned by goals.get_active_goal() (or {} / None).
    Never guesses beyond goal_type, then phase, in that order - returns
    None (not a silently-assumed default) if neither resolves."""
    goal = goal or {}
    from_type = _GOAL_TYPE_TO_MODE.get(goal.get("goal_type"))
    if from_type:
        return from_type
    return _PHASE_TO_MODE.get(goal.get("phase"))


def get_goal_policy(goal_mode: str | None) -> dict:
    """Returns the policy dict for a canonical (or alias) goal_mode.
    Returns an explicit, clearly-labeled "unresolved" policy (including
    range_position_fraction=None, which shadow_dose.py's
    _goal_adjusted_working_sets treats as "use the feasible range's own
    midpoint") - never a silently-assumed default mode - when goal_mode
    is None or unrecognized."""
    canonical = normalize_goal_mode(goal_mode)
    if canonical is None:
        return {
            "training_objective": None,
            "range_position_fraction": None,
            "stimulus_posture": None,
            "volume_posture": None,
            "progression_emphasis": None,
            "fatigue_tolerance": None,
            "intensity_volume_tradeoff": None,
            "maintenance_vs_growth_priority": None,
            "notes": f"No active/recognized goal mode ({goal_mode!r}) - policy not applied.",
        }
    return dict(GOAL_POLICIES[canonical])
