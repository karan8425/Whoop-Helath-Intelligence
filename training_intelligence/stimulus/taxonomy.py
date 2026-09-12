"""Canonical muscle taxonomy for Training Intelligence, plus an explicit
compatibility layer onto the existing B3/B4 taxonomy.

TRAINING_INTELLIGENCE_KNOWLEDGE_BASE_V1.md section 6 lists 10 canonical
groups (adds "Calves" to what B3/B4 already tracks). The existing,
production `integrations.tonal.muscle_readiness.PROGRAMMING_MUSCLES` has
only 9 Title-Case groups and no Calves - that engine is untouched by this
milestone, so we do not rename or extend it. Instead this module defines
the 10-group canonical (lowercase) taxonomy the spec calls for, and an
explicit, one-directional mapping from the existing taxonomy into it.

"Calves" has no populated mapping source today (Tonal's own muscle_groups
metadata for this user's movements does not appear to distinguish a
Calves group - see the TONAL_DATA_AUDIT in the final report) - it is
still exposed as a canonical group so the ledger's per-muscle shape is
stable and future-proof, but is expected to read zero direct/secondary
sets until either Tonal exposes it or a curated mapping adds it.
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

# Canonical groups with no existing B3/B4 source at all (currently just
# "calves" - see module docstring).
CANONICAL_GROUPS_WITHOUT_EXISTING_SOURCE = tuple(
    sorted(set(CANONICAL_MUSCLES) - set(_EXISTING_TO_CANONICAL.values()))
)


def to_canonical(existing_muscle: str) -> str | None:
    """Maps an existing PROGRAMMING_MUSCLES label to its canonical group.
    Returns None for anything not in the existing taxonomy (never guesses)."""
    return _EXISTING_TO_CANONICAL.get(existing_muscle)
