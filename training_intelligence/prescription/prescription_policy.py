"""Versioned configuration for TKI-5 exercise selection / progressive-
overload prescription. Every number here is a PRODUCT POLICY /
CALIBRATION PARAMETER, never a scientific constant - identical
discipline to scoring_policy.py and calibration_policy.py.

Nothing here is a per-user constant; every table is keyed by the shared,
finite goal_mode taxonomy (training_intelligence.dose.goal_policy.
GOAL_MODES) - a second user on the same goal mode reads the identical
row.
"""

from __future__ import annotations

from training_intelligence.dose.goal_policy import (
    LEAN_CUT, LEAN_BULK, STRENGTH, MAINTENANCE, GENERAL_FITNESS, RECOVERY,
)

PRESCRIPTION_MODEL_VERSION = 1

# ----------------------------------------------------------------------
# Session composition (sections 7/8): how many exercises, how many sets
# each gets by default before B3's own _set_allocation redistributes
# them across compound/isolation movements.
# ----------------------------------------------------------------------

# CALIBRATION PARAMETER: floor/ceiling on exercise count regardless of
# dose or goal mode. The ceiling matches B3's own existing SESSION_RULES
# max_exercises (5) - not a new number, the same product ceiling B3
# already uses for a full session.
MIN_EXERCISES = 2
MAX_EXERCISES = 5

# CALIBRATION PARAMETER: the working-set-per-exercise assumption used
# only to derive how many exercise SLOTS a given TKI-3 dose deserves
# (never to invent sets beyond the dose - B3's own _set_allocation still
# performs the actual, dose-bound distribution across those slots).
DEFAULT_SETS_PER_EXERCISE = 3.0

# CALIBRATION PARAMETER (section 15): a goal mode may shift how many
# exercise slots a session uses within [MIN_EXERCISES, MAX_EXERCISES] -
# e.g. strength favors fewer, more focused compound slots; general_
# fitness/hypertrophy favor broader exercise variety. This never changes
# the total working-set BUDGET (TKI-3's dose remains authoritative) -
# only how many movements share it.
EXERCISE_COUNT_DELTA_BY_GOAL_MODE = {
    LEAN_CUT: 0,
    LEAN_BULK: 1,          # hypertrophy_gain: broader stimulus across more movements
    STRENGTH: -1,          # fewer, more focused compound slots
    MAINTENANCE: 0,
    GENERAL_FITNESS: 1,    # broader variety is the explicit objective
    RECOVERY: -1,          # a reduced session footprint
}

# ----------------------------------------------------------------------
# Rep-range posture (sections 9/15/16): where within B3's OWN already-
# computed [minimum, maximum] rep_range (integrations.tonal.
# progressive_overload.CONFIG["rep_range"], unmodified) a goal mode
# prefers to sit. 0.0 = the low end (strength-leaning), 1.0 = the high
# end (higher-rep hypertrophy-leaning). This NEVER widens the range or
# overrides a HOLD/PROGRESS_REPS decision's own floor - it only picks a
# posture within the range B3 already established, exactly as TKI-3's
# goal policy picks a dose posture within the personalized feasible
# range rather than inventing capacity.
REP_POSITION_FRACTION_BY_GOAL_MODE = {
    LEAN_CUT: 0.5,
    LEAN_BULK: 0.65,
    STRENGTH: 0.15,
    MAINTENANCE: 0.5,
    GENERAL_FITNESS: 0.55,
    RECOVERY: 0.3,
}

# CALIBRATION PARAMETER: the goal-mode rep posture above only ever
# applies when B3's own progression_state is one of these - REDUCE and
# REBUILD are safety/data-floor states goal policy must never touch
# (section 15: "Goal mode must NOT ... override fatigue ... invent
# capacity"), and PROGRESS_LOAD's target_reps_per_set is intentionally
# reset to the range floor by B3 itself as part of a load increase.
GOAL_POSTURE_ELIGIBLE_STATES = ("HOLD", "PROGRESS_REPS")

# ----------------------------------------------------------------------
# Generic-placeholder defense (section 21). integrations.tonal.
# movement_performance._load_recent_sets already excludes
# m.is_generic/m.custom_movement at the SQL level (confirmed via code
# audit - see TRAINING_INTELLIGENCE_TKI5_REPORT.md), so these names
# cannot normally reach a profile at all. This list is a second,
# redundant, cheap defensive layer - never relied upon as the primary
# mechanism - in case a profile ever reaches this module through a path
# that does not apply that filter.
GENERIC_PLACEHOLDER_NAMES = frozenset({"Handle Move", "Bar Move"})

# ----------------------------------------------------------------------
# TKI-5.1 addendum: feasible exercise-count range + dose-absorption
# waterfall (training_intelligence.prescription.composition). Exercise
# count is now an OUTCOME of the usable candidate pool + personal
# history, not a preference computed first and then hoped to fit -
# DEFAULT_SETS_PER_EXERCISE/EXERCISE_COUNT_DELTA_BY_GOAL_MODE above are
# reused unchanged, but only as one INPUT to that preference, always
# capped by how many candidates genuinely exist (composition.py).
# ----------------------------------------------------------------------

COMPOSITION_MODEL_VERSION = 1

# CALIBRATION PARAMETER: how far back to look for the user's own
# comparable-session exercise-count/set-count structure. 60 days (double
# TKI-2's own 30-day ledger default) to give the median a fairer sample
# without requiring a second, unbounded historical query.
HISTORICAL_STRUCTURE_LOOKBACK_DAYS = 60

# CALIBRATION PARAMETER: fewer real comparable sessions than this and
# personal historical structure is not used to inform the preferred
# exercise count - falls through to the goal-policy/dose default instead
# (section 5's fallback hierarchy). Matches the same minimum-comparable-
# session floor already used elsewhere in this package (TKI-2's ledger,
# TKI-4.1's rolling representation) rather than inventing a new number.
MIN_COMPARABLE_SESSIONS_FOR_HISTORICAL_STRUCTURE = 3

# CALIBRATION PARAMETER (section 10, tier A): a movement's personal
# tolerated set ceiling, when enough personal history exists for it, is
# its own historical recent_sets_per_session plus this margin - allows
# modest, evidence-informed absorption above what was merely typical,
# never an unbounded amount.
PERSONAL_TOLERATED_SETS_MARGIN = 1

# CALIBRATION PARAMETER (section 10, tier D - versioned product-policy
# fallback, used only when no personal/pattern/family history exists for
# a movement): matches B3's OWN existing _set_allocation() remainder-
# distribution ceiling (it never raises an allocation above 4 sets while
# distributing surplus) - not a new number invented for TKI-5.1.
PER_MOVEMENT_ABSORPTION_CAP_FALLBACK = 4

# CALIBRATION PARAMETER (section 12/27): the dose-absorption waterfall
# may never redirect more than this fraction of the TOTAL target dose
# into a single movement, regardless of how much personal-history
# headroom that movement has - a hard guard against "excessive set
# dumping into one accessory movement" even when the per-movement cap
# above would otherwise allow it.
MAX_REALLOCATION_SHARE_PER_MOVEMENT = 0.4

# CALIBRATION PARAMETER: a movement must already carry at least this
# many allocated sets before the waterfall will consider adding MORE to
# it - prevents redirecting surplus onto a movement that was barely
# included in the first place.
MIN_ALLOCATED_SETS_TO_BE_REALLOCATION_TARGET = 2

# Progression states B3's own progressive_overload.prescribe() may
# return that indicate the movement is NOT a safe target for additional,
# reallocated sets today (section 8/11) - reused verbatim from that
# module's own vocabulary, not redefined here.
NON_ABSORBING_PROGRESSION_STATES = ("REDUCE", "REBUILD")
