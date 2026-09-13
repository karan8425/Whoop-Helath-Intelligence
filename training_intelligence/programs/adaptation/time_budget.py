"""Program Intelligence V2 Phase 11 - transparent session-duration
estimator.

Every constant here is a named, versioned PRODUCT POLICY approximation
- never a claim about exercise-science timing, and never a global
45-minute (or any other fixed) session-duration default. Available
duration is runtime/config data only (Phase 3.F / Phase 11): request
override > user enrollment preference > program default > UNKNOWN.
"""
from __future__ import annotations

from training_intelligence.programs.adaptation.taxonomy import (
    FIT_FITS, FIT_TIGHT, FIT_EXCEEDS, FIT_UNKNOWN, TIGHT_FIT_FRACTION,
)

TIME_BUDGET_VERSION = 1

# PRODUCT POLICY / CALIBRATION PARAMETER - a transparent, disclosed
# approximation, not exercise-science fact.
SECONDS_PER_REP = 3.0
TRANSITION_SECONDS_PER_EXERCISE = 90.0
UNILATERAL_SIDE_MULTIPLIER = 2.0


def resolve_available_duration(*, request_override_min=None, user_preference_min=None,
                                program_default_min=None):
    """Section: Phase 3.F / Phase 11 - the ONLY place a duration is ever
    chosen, and only from explicit, disclosed sources. Never a global
    constant. Returns (minutes_or_None, source_label)."""
    if request_override_min is not None:
        return request_override_min, "request_override"
    if user_preference_min is not None:
        return user_preference_min, "user_preference"
    if program_default_min is not None:
        return program_default_min, "program_default"
    return None, "unknown"


def _set_seconds(reps, rest_seconds, is_unilateral):
    working = reps * SECONDS_PER_REP
    if is_unilateral:
        working *= UNILATERAL_SIDE_MULTIPLIER
    return working + (rest_seconds or 0)


def estimate_session_duration_minutes(resolved_exercises, warmup_minutes=None):
    """`resolved_exercises`: [{"sets": int, "rep_min": int, "rep_max": int,
    "rest_seconds_min": int|None, "rest_seconds_max": int|None,
    "is_unilateral": bool}, ...] - the session's actual composition
    (after Tonal resolution), not the raw program slot ranges, so the
    estimate reflects what will really be performed.

    Returns (low_minutes, high_minutes) - a RANGE, since rep count
    itself is a range until execution."""
    low_seconds = high_seconds = 0.0
    for ex in resolved_exercises:
        sets = ex["sets"]
        rest_low = ex.get("rest_seconds_min") or 0
        rest_high = ex.get("rest_seconds_max") or rest_low
        low_seconds += sets * _set_seconds(ex["rep_min"], rest_low, ex.get("is_unilateral", False))
        high_seconds += sets * _set_seconds(ex["rep_max"], rest_high, ex.get("is_unilateral", False))
        low_seconds += TRANSITION_SECONDS_PER_EXERCISE
        high_seconds += TRANSITION_SECONDS_PER_EXERCISE

    warmup_seconds = (warmup_minutes or 0) * 60
    low_seconds += warmup_seconds
    high_seconds += warmup_seconds

    return round(low_seconds / 60.0, 1), round(high_seconds / 60.0, 1)


def classify_fit(low_minutes, high_minutes, available_minutes):
    if available_minutes is None:
        return FIT_UNKNOWN
    if high_minutes <= available_minutes:
        # still TIGHT if the estimate uses nearly all available time
        if high_minutes >= available_minutes * TIGHT_FIT_FRACTION:
            return FIT_TIGHT
        return FIT_FITS
    if low_minutes <= available_minutes:
        return FIT_TIGHT
    return FIT_EXCEEDS


def build_time_context(resolved_exercises, *, available_duration_min, source, warmup_minutes=None):
    low, high = estimate_session_duration_minutes(resolved_exercises, warmup_minutes)
    fit = classify_fit(low, high, available_duration_min)
    return {
        "time_budget_version": TIME_BUDGET_VERSION,
        "estimated_minutes_low": low, "estimated_minutes_high": high,
        "available_duration_min": available_duration_min, "duration_source": source,
        "fit_status": fit,
    }
