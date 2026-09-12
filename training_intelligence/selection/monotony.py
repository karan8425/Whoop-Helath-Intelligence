"""TKI-4.1: monotony / rolling-representation / starvation-protection
computation - SHADOW MODE ONLY. See calibration_policy.py for every
coefficient (all disclosed PRODUCT POLICY / CALIBRATION PARAMETERS).

This module never touches eligibility (candidates.py, unchanged) and
never touches the original 9-dimension score (scoring.py, unchanged) -
it produces a separate, bounded, additive `monotony_adjustment` per
eligible, non-Rest candidate, applied in shadow_selection.py to derive
score_total_calibrated. Rest / Active Recovery is exempt entirely (see
calibration_policy.py's module docstring).
"""

from __future__ import annotations

from datetime import datetime

from training_intelligence.selection.calibration_policy import (
    CONSECUTIVE_REPEAT_FREE_STREAK,
    CONSECUTIVE_REPEAT_PENALTY_PER_DAY,
    CONSECUTIVE_REPEAT_PENALTY_CAP,
    MONOTONY_TOLERANCE_MULTIPLIER,
    ROLLING_REPRESENTATION_WINDOWS_DAYS,
    ROLLING_REPRESENTATION_MIN_TOTAL_SESSIONS,
    ROLLING_OVERREPRESENTATION_MAX_PENALTY,
    ROLLING_UNDERREPRESENTATION_MAX_BONUS,
    STARVATION_MIN_DOSE_UPPER_BOUND_WORKING_SETS,
    STARVATION_ABSENCE_WINDOW_DAYS,
    STARVATION_MIN_TOTAL_SESSIONS_FOR_SIGNAL,
    STARVATION_BONUS,
    TOTAL_CALIBRATION_ADJUSTMENT_CAP,
)


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def _normalize_recent_selections(recent_selections, as_of: datetime):
    """as_of-safety: defensively drop any entry that is not strictly
    before `as_of`, even though every known caller already only ever
    appends PAST decisions - the same defensive discipline every other
    TKI shadow module in this package applies to caller-injected data."""
    if not recent_selections:
        return []
    cleaned = []
    for entry in recent_selections:
        entry_as_of, family = entry[0], entry[1]
        if entry_as_of is not None and entry_as_of >= as_of:
            continue
        cleaned.append((entry_as_of, family))
    # Most-recent-first, in case the caller did not already sort it.
    cleaned.sort(key=lambda pair: pair[0], reverse=True)
    return cleaned


def consecutive_repeat_streak(family: str, recent_selections) -> int:
    """How many of the immediately preceding (most-recent-first)
    decisions were this exact family, counting backward until the first
    non-match. A Rest/Active Recovery day, or any other family, breaks
    the streak."""
    streak = 0
    for _, selected_family in recent_selections:
        if selected_family == family:
            streak += 1
        else:
            break
    return streak


def repeat_penalty(family: str, recent_selections, goal_mode) -> tuple:
    """Returns (penalty, streak). Soft, bounded, goal-mode-tolerant -
    see calibration_policy.py."""
    streak = consecutive_repeat_streak(family, recent_selections)
    excess = max(0, streak - CONSECUTIVE_REPEAT_FREE_STREAK)
    base = min(CONSECUTIVE_REPEAT_PENALTY_CAP, CONSECUTIVE_REPEAT_PENALTY_PER_DAY * excess)
    multiplier = MONOTONY_TOLERANCE_MULTIPLIER.get(goal_mode, 1.0)
    return base * multiplier, streak


def rolling_representation_adjustment(family: str, family_windows: dict, eligible_family_count: int) -> float:
    """`family_windows`: {window_days: {family: {"sessions": n, ...}}} -
    the real, ACTUAL Tonal session-family history already computed by
    training_intelligence.stimulus.session_family.session_family_windows
    from the ledger rows shadow_selection.py loads once per call (no new
    query). Distinct from repeat_penalty above: this measures real
    training representation, not the selector's own decision stream."""
    if eligible_family_count <= 0:
        return 0.0
    fair_share = 1.0 / eligible_family_count
    contributions = []
    for window_days in ROLLING_REPRESENTATION_WINDOWS_DAYS:
        window = family_windows.get(window_days, {})
        total_sessions = sum(entry.get("sessions", 0) for entry in window.values())
        if total_sessions < ROLLING_REPRESENTATION_MIN_TOTAL_SESSIONS:
            continue  # cold-start guard - not enough real signal this window
        share = window.get(family, {}).get("sessions", 0) / total_sessions
        deviation = share - fair_share
        if deviation > 0:
            contribution = -min(
                ROLLING_OVERREPRESENTATION_MAX_PENALTY,
                deviation * ROLLING_OVERREPRESENTATION_MAX_PENALTY / fair_share,
            )
        else:
            contribution = min(
                ROLLING_UNDERREPRESENTATION_MAX_BONUS,
                -deviation * ROLLING_UNDERREPRESENTATION_MAX_BONUS / fair_share,
            )
        contributions.append(contribution)
    if not contributions:
        return 0.0
    return sum(contributions) / len(contributions)


def starvation_bonus(family: str, candidate: dict, family_windows: dict) -> float:
    """Discrete bonus for the specific, stricter case of zero real
    sessions of this family anywhere in the STARVATION_ABSENCE_WINDOW_
    DAYS window - gated on the candidate already being eligible (local
    readiness/structural constraints already satisfied - unchanged) AND
    meaningfully dosable (TKI-3 feasible upper bound above a stricter
    floor than plain eligibility requires). Never fires for an
    ineligible candidate; never overrides readiness or dose-feasibility
    invariants - see candidates.py, unmodified."""
    if not candidate["eligible"]:
        return 0.0
    dose = candidate["dose"]
    if dose is None:
        return 0.0
    if dose["feasible_dose_range"]["upper_bound_working_sets"] < STARVATION_MIN_DOSE_UPPER_BOUND_WORKING_SETS:
        return 0.0
    window = family_windows.get(STARVATION_ABSENCE_WINDOW_DAYS, {})
    total_sessions = sum(entry.get("sessions", 0) for entry in window.values())
    if total_sessions < STARVATION_MIN_TOTAL_SESSIONS_FOR_SIGNAL:
        # Cold-start guard: too little real training exists anywhere in
        # the window to claim THIS family specifically is being
        # neglected relative to the rest (section 15).
        return 0.0
    sessions = window.get(family, {}).get("sessions", 0)
    if sessions > 0:
        return 0.0
    return STARVATION_BONUS


def compute_monotony_adjustment(
    candidate: dict,
    as_of: datetime,
    goal_mode,
    recent_selections,
    family_windows: dict,
    eligible_family_count: int,
) -> dict:
    """Returns a full breakdown dict for one eligible, non-Rest
    candidate. Every value is bounded and deterministic given its
    inputs; nothing here depends on wall-clock time or call order."""
    family = candidate["session_family"]
    cleaned_recent = _normalize_recent_selections(recent_selections, as_of)

    penalty, streak = repeat_penalty(family, cleaned_recent, goal_mode)
    rolling = rolling_representation_adjustment(family, family_windows, eligible_family_count)
    starvation = starvation_bonus(family, candidate, family_windows)

    raw_total = -penalty + rolling + starvation
    total = _clamp(raw_total, -TOTAL_CALIBRATION_ADJUSTMENT_CAP, TOTAL_CALIBRATION_ADJUSTMENT_CAP)

    return {
        "consecutive_repeat_streak": streak,
        "repeat_penalty": round(penalty, 4),
        "rolling_representation_adjustment": round(rolling, 4),
        "starvation_bonus": round(starvation, 4),
        "monotony_adjustment_total": round(total, 4),
    }
