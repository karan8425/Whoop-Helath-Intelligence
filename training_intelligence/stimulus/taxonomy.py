"""Canonical muscle taxonomy for Training Intelligence, plus an explicit
compatibility layer onto the existing B3/B4 taxonomy.

TRAINING_INTELLIGENCE_KNOWLEDGE_BASE_V1.md section 6 lists 10 canonical
groups (adds "Calves" to what B3/B4 already tracks). The existing,
production `integrations.tonal.muscle_readiness.PROGRAMMING_MUSCLES` has
only 9 Title-Case groups and no Calves - that engine is untouched by this
milestone, so we do not rename or extend it. Instead this module defines
the 10-group canonical (lowercase) taxonomy the spec calls for, and an
explicit, one-directional mapping from the existing taxonomy into it.

TKI-2.1 CALVES FIX: live-data forensics (calibration run, see
TRAINING_INTELLIGENCE_TKI12_REPORT.md) confirmed the earlier assumption
here was wrong - Tonal's own muscle_groups metadata DOES provide
"Calves" as a raw label (14 real movements carry it, 2 with actual
working-set history). The reason it read as unmapped was narrower:
integrations.tonal.muscle_readiness._normalize_muscle only recognizes
labels present in PROGRAMMING_MUSCLES, which has no Calves entry by
B3/B4's own design - so it correctly (for B3/B4's purposes) returns None
for "Calves", and this module's mapping.classify_muscle_groups treated
that the same as any other unrecognized label.

DIRECT_TONAL_LABEL_TO_CANONICAL below is the fix: a small, TKI-owned,
deterministic table for raw Tonal labels that have a stable canonical
target but no B3/B4-taxonomy equivalent to route through `to_canonical`.
It does not touch muscle_readiness.py, PROGRAMMING_MUSCLES, or any
B3/B4 code path - B3/B4 behavior is unaffected by construction, not just
by testing.
"""

from __future__ import annotations

from integrations.tonal.muscle_readiness import PROGRAMMING_MUSCLES

# The 10 canonical groups from TRAINING_INTELLIGENCE_KNOWLEDGE_BASE_V1.md
# section 6, in the app's lowercase-snake convention.
CANONICAL_MUSCLES = (
    "chest",
    "back",
    "shoulders",
    "biceps",
    "triceps",
    "quads",
    "hamstrings",
    "glutes",
    "calves",
    "core",
)

# One-directional: existing B3/B4 Title-Case group -> canonical group.
# Every entry in PROGRAMMING_MUSCLES must appear here (enforced by the
# assertion below) so this mapping can never silently go stale if B3/B4
# adds a new group.
_EXISTING_TO_CANONICAL = {
    "Chest": "chest",
    "Back": "back",
    "Shoulders": "shoulders",
    "Biceps": "biceps",
    "Triceps": "triceps",
    "Core": "core",
    "Glutes": "glutes",
    "Hamstrings": "hamstrings",
    "Quads": "quads",
}

_missing = set(PROGRAMMING_MUSCLES) - set(_EXISTING_TO_CANONICAL)
if _missing:
    raise RuntimeError(
        "training_intelligence.stimulus.taxonomy is out of date: "
        f"PROGRAMMING_MUSCLES has group(s) with no canonical mapping: {_missing}"
    )

# Canonical groups with no B3/B4-taxonomy source (currently just
# "calves" - see module docstring). This no longer means "no data
# available" for calves specifically: DIRECT_TONAL_LABEL_TO_CANONICAL
# below supplies it via a separate, TKI-owned path.
CANONICAL_GROUPS_WITHOUT_EXISTING_SOURCE = tuple(
    sorted(set(CANONICAL_MUSCLES) - set(_EXISTING_TO_CANONICAL.values()))
)

# Raw Tonal muscle_groups labels recognized directly, bypassing
# _EXISTING_TO_CANONICAL, because they have no B3/B4-taxonomy equivalent
# to route through. Deterministic exact-string match only - no fuzzy
# matching, no spelling variants guessed. Confirmed against real data
# that Tonal emits exactly "Calves" (not "calf"/"Calf"/etc.).
DIRECT_TONAL_LABEL_TO_CANONICAL = {
    "Calves": "calves",
}


def to_canonical(existing_muscle: str) -> str | None:
    """Maps an existing PROGRAMMING_MUSCLES label to its canonical group.
    Returns None for anything not in the existing taxonomy (never guesses)."""
    return _EXISTING_TO_CANONICAL.get(existing_muscle)


def canonical_from_raw_tonal_label(raw_label: str) -> str | None:
    """Direct raw-Tonal-label -> canonical lookup for labels with no
    B3/B4-taxonomy equivalent (see DIRECT_TONAL_LABEL_TO_CANONICAL).
    Returns None for anything not in this table (never guesses)."""
    return DIRECT_TONAL_LABEL_TO_CANONICAL.get(raw_label)
