"""TKI-5.4 (sections 7-8): Smart Weight mode compatibility, versioned as
PRODUCT POLICY - never a physiological equivalence claim.

Grounded in the LIVE B2/B3 Smart-Weight-selection policy already in
production (`integrations/tonal/workout_prescription.py`'s mode-choice
rules): eccentric, progressive, and chains are each unlocked ONLY at
high readiness, only after a movement has "earned" progression, and
only if the user has historically used that mode on that movement -
i.e. the live product already treats these three modes as
STRICTLY-HARDER-THAN-STANDARD variants layered on top of a mastered
standard-mode base weight, never as an easier or unrelated stimulus.
That existing, shipped product judgment is the basis for classifying
them PARTIAL-compatible fallback evidence for a standard-mode
progression target: a completed set in one of these modes at base
weight W is accepted as evidence the user can handle W in standard
mode, one confidence tier down - never as equal-strength evidence.

burnout and flex are NOT unlocked by that same policy and their load
semantics relative to a straight working set are not documented
anywhere in this codebase (burnout is commonly a to-failure technique,
frequently at a reduced load, and flex's semantics are undocumented
here) - so they are classified UNKNOWN, not PARTIAL. This is a
deliberate refusal to invent equivalence, per the explicit instruction
not to assume physiological equivalence without evidence.

This module never decides a prescription target; every classification
here assumes destination mode is "standard" (the only destination this
shadow calibration package ever prescribes - see progression.py's own
module docstring).
"""
from __future__ import annotations

MODE_COMPATIBILITY_POLICY_VERSION = 1

# PRODUCT POLICY: modes shipped in workout_prescription.py as harder-
# than-standard, historically-gated progression tools. A set in any of
# these modes (alone or combined with each other) is PARTIAL evidence
# for a standard-mode target.
PARTIAL_COMPATIBLE_FLAGS = frozenset(("eccentric", "progressive", "chains"))

# PRODUCT POLICY: modes with no documented base-load equivalence to a
# standard working set anywhere in this codebase. Never upgraded to
# PARTIAL without new, explicit evidence.
UNKNOWN_FLAGS = frozenset(("burnout", "flex"))

# One full confidence-tier step down is applied whenever PARTIAL
# fallback evidence is used in place of exact-mode evidence - named so
# it is auditable, not buried in comparison logic.
PARTIAL_CONFIDENCE_PENALTY_TIERS = 1


def classify(source_mode: str, target_mode: str = "standard") -> str:
    """`source_mode` is history.mode(row)'s output: "standard" or a
    "+"-joined combination of MODE_FLAGS. Returns one of EXACT/PARTIAL/
    INCOMPATIBLE/UNKNOWN. `target_mode` is always "standard" in this
    package today; the parameter exists so a future non-standard
    destination is not silently treated as compatible with anything."""
    if target_mode != "standard":
        return "UNKNOWN"
    if source_mode == "standard":
        return "EXACT"
    flags = set(source_mode.split("+"))
    if flags & UNKNOWN_FLAGS:
        return "UNKNOWN"
    if flags and flags <= PARTIAL_COMPATIBLE_FLAGS:
        return "PARTIAL"
    return "UNKNOWN"


def is_usable_evidence(source_mode: str, target_mode: str = "standard") -> bool:
    """EXACT and PARTIAL both contribute usable (differently-weighted)
    evidence; UNKNOWN/INCOMPATIBLE never do."""
    return classify(source_mode, target_mode) in ("EXACT", "PARTIAL")
