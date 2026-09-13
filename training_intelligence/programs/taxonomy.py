"""Program Intelligence taxonomy - reuses the existing TKI/B3 vocabulary
wherever a direct equivalent exists, and extends (never forks) it where
a genuine gap exists. One vocabulary across readiness, the stimulus
ledger, program intelligence, and Tonal movement mapping.

PRODUCT POLICY / CALIBRATION-STYLE constants throughout: nothing here is
a physiological claim, and no value is a personal target.
"""
from __future__ import annotations

from training_intelligence.dose.goal_policy import GOAL_MODES
from training_intelligence.stimulus.taxonomy import CANONICAL_MUSCLES
from integrations.tonal.training_priority import SESSION_TEMPLATES
from training_intelligence.calibration.history import COMPOUNDS as _EXISTING_COMPOUND_PATTERNS

PROGRAM_TAXONOMY_VERSION = 1

# Reused verbatim from training_intelligence.dose.goal_policy - no new or
# competing goal vocabulary. Note: that taxonomy's "lean_bulk" IS the
# hypertrophy goal (training_objective="maximize_hypertrophy_stimulus");
# there is no separate "hypertrophy_gain" value, so a hypertrophy-focused
# program's goal_mode is stored as "lean_bulk", not a new conflicting
# alias.
GOALS = GOAL_MODES

# New vocabulary - no existing TKI/B3 equivalent to reuse or conflict
# with. Kept intentionally small; not a claim about training science,
# a product classification for program selection/filtering.
EXPERIENCE_LEVELS = ("beginner", "intermediate", "advanced")

SPLIT_TYPES = ("upper_lower", "full_body", "push_pull_legs", "body_part", "hybrid")

# Reused verbatim from integrations.tonal.training_priority.
# SESSION_TEMPLATES - the same Title-Case family names TKI-4's own
# session selection already uses, so a program session whose structure
# matches one of these families is described identically everywhere in
# the app. A program session may still use a name outside this set
# (e.g. a family with no forward-looking-scorer equivalent) - this is a
# reference list, not a CHECK-constrained enum, matching the fact that
# the DB column itself carries no CHECK on session_family.
SESSION_FAMILIES = tuple(SESSION_TEMPLATES.keys())

# Reuses history.py's existing compound-pattern names verbatim
# (horizontal_press, vertical_press, horizontal_pull, vertical_pull,
# hinge, squat_lunge) - the same names TKI-5.2's paired-progression
# comparator already classifies movements into via history.pattern().
# Extended with isolation sub-patterns that taxonomy never distinguished
# (it only has one coarse "isolation" bucket) - a genuine gap, not a
# fork of an existing name.
COMPOUND_MOVEMENT_PATTERNS = tuple(sorted(_EXISTING_COMPOUND_PATTERNS))
ISOLATION_MOVEMENT_PATTERNS = (
    "elbow_flexion",
    "elbow_extension",
    "lateral_raise",
    "rear_delt_isolation",
    "calf_raise",
    "core_flexion",
    "core_anti_extension",
    "core_anti_rotation",
)
MOVEMENT_PATTERNS = COMPOUND_MOVEMENT_PATTERNS + ISOLATION_MOVEMENT_PATTERNS

# training_intelligence.stimulus.taxonomy.CANONICAL_MUSCLES verbatim -
# the same 10-group vocabulary readiness/stimulus-ledger already use.
PRIMARY_MUSCLES = CANONICAL_MUSCLES

EXERCISE_ROLES = (
    "primary_compound",
    "secondary_compound",
    "isolation",
    "unilateral",
    "accessory",
    "core",
    "conditioning",
)

# Ordered worst-to-best is deliberately NOT how this is defined - ranking
# is a mapping-engine concern (tonal_mapping.py), not a taxonomy concern.
# This tuple is the closed domain the DB CHECK constraint also enforces.
MAPPING_QUALITY = ("DIRECT", "CLOSE", "FUNCTIONAL", "UNSUITABLE")
MAPPING_QUALITY_RANK = {"DIRECT": 3, "CLOSE": 2, "FUNCTIONAL": 1, "UNSUITABLE": 0}

PROGRAM_STATUSES = ("draft", "active", "archived")
ENROLLMENT_STATUSES = ("active", "paused", "completed", "cancelled")
SESSION_STATE_STATUSES = ("scheduled", "completed", "skipped", "shifted", "substituted", "cancelled")

SOURCE_TYPES = ("original", "reference_derived", "user_defined")

PHASE_NAMES = ("accumulation", "intensification", "deload", "peak", "maintenance")


def assert_no_conflicting_aliases():
    """Section-required self-check: every value this module treats as
    canonical must not silently collide with a DIFFERENT existing
    meaning elsewhere. Run at import time (cheap, deterministic) so a
    future edit that introduces a real conflict fails loudly, not
    silently."""
    assert set(GOALS) == set(GOAL_MODES), "GOALS must exactly mirror goal_policy.GOAL_MODES"
    assert "hypertrophy_gain" not in GOALS, (
        "hypertrophy_gain is not a canonical goal_mode - use lean_bulk "
        "(goal_policy.py's own hypertrophy goal) to avoid a conflicting alias"
    )
    overlap = set(COMPOUND_MOVEMENT_PATTERNS) & set(ISOLATION_MOVEMENT_PATTERNS)
    assert not overlap, f"movement pattern taxonomy fork detected: {overlap}"


assert_no_conflicting_aliases()
