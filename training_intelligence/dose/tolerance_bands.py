"""Descriptive personal tolerance bands (section 5 of the TKI-3 assignment).

These are DESCRIPTIVE labels for where a value falls in the user's own
comparable-session history - never medical/safety thresholds, and never a
re-derivation of B2's dose target itself (integrations.tonal.training_dose
already computes the actual working_sets/exercise/volume TARGET; this
module only classifies where that target - or any other observed value -
sits relative to the user's own personal percentile distribution).

Requires a minimum number of comparable sessions before declaring a
personalized band at all (section 5: "Require minimum evidence before
declaring a personalized band"). Below that, the caller must fall back
explicitly per the fallback hierarchy (product policy, then evidence-
informed generic reference) and mark the source - this module never
guesses a band from insufficient data.
"""

from __future__ import annotations

from integrations.tonal.training_dose import (
    MIN_HIGH_QUALITY_COMPARABLE_SESSIONS,
)

BAND_VERSION = 1

BELOW_RANGE = "below_personal_range"
WITHIN_RANGE = "within_personal_range"
UPPER_RANGE = "upper_personal_range"
INSUFFICIENT_EVIDENCE = "insufficient_evidence"

# Minimum comparable sessions required before a personalized band can be
# declared at all - reuses B2's own "high-quality comparable" threshold
# rather than defining a second, competing minimum.
MIN_SESSIONS_FOR_BAND = MIN_HIGH_QUALITY_COMPARABLE_SESSIONS


def classify_band(value, session_count, p25, p50, p75):
    """value: the quantity to classify (e.g. today's recommended working
    sets). p25/p50/p75: the user's own comparable-session percentiles for
    that same quantity. Returns one of the four labels above."""

    if session_count < MIN_SESSIONS_FOR_BAND or p25 is None or p75 is None or value is None:
        return INSUFFICIENT_EVIDENCE

    if value < p25:
        return BELOW_RANGE
    if value > p75:
        return UPPER_RANGE
    return WITHIN_RANGE
