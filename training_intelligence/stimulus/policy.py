"""Versioned product-policy constants for the muscle stimulus ledger.

Every number here is a PRODUCT POLICY decision (Layer B in
TRAINING_INTELLIGENCE_KNOWLEDGE_BASE_V1.md section 4), not an established
biological fact. Where the app has already made this decision elsewhere in
production code, we import and re-export it rather than defining a second,
possibly-diverging number - a stimulus ledger that disagreed with the
already-shipped muscle-readiness engine about what a "secondary set" is
worth would be far more confusing than reusing the existing constant.
"""

from __future__ import annotations

from integrations.tonal.muscle_readiness import (
    SECONDARY_SET_WEIGHT as _EXISTING_SECONDARY_SET_WEIGHT,
)

# Section 23: all policy assumptions must be versioned for historical
# reproducibility. Bump these whenever the underlying constant changes
# meaning; never mutate old meaning silently.
STIMULUS_POLICY_VERSION = 1
EXERCISE_MAPPING_VERSION = 1

# --------------------------------------------------------------------
# Stimulus-set accounting (section 6 of the TKI-1/TKI-2 assignment)
# --------------------------------------------------------------------

# Primary muscle: 1 completed working set = 1.0 direct set. This is the
# same convention integrations.tonal.muscle_readiness already uses
# (PRIMARY_SET_WEIGHT == 1.0); direct_sets in the ledger is defined to
# match it exactly.
DIRECT_SET_CREDIT = 1.0

# Secondary muscle: reuse the existing, already-shipped product-policy
# value from muscle_readiness.py (0.35) rather than introducing a second,
# competing fraction (e.g. 0.5) that would silently disagree with the
# muscle-readiness engine already in production.
SECONDARY_SET_CREDIT = _EXISTING_SECONDARY_SET_WEIGHT

# --------------------------------------------------------------------
# Hypertrophy reference (section 10) - reference/default, NOT a
# personalized target. Nothing in TKI-2 makes today's workout depend on
# this value.
# --------------------------------------------------------------------
HYPERTROPHY_REFERENCE_SETS_PER_WEEK = 10
HYPERTROPHY_REFERENCE_LABEL = "reference_default_not_personalized_target"

# --------------------------------------------------------------------
# Rolling ledger windows (section 8)
# --------------------------------------------------------------------
LEDGER_WINDOWS_DAYS = (7, 14, 30)
