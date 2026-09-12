"""TKI-4: the 9-dimension session-family scorer - SHADOW MODE ONLY.

Every component is bounded to [0.0, 1.0] and computed from values already
produced by TKI-2 (stimulus ledger, session-family history) or TKI-3
(feasible dose range, performance trajectory) - or, for local_readiness/
days_since_trained, directly from the same B1 muscle_readiness read-only
accessor TKI-2/TKI-3 already use. Nothing here recomputes or overrides
any of those - it only aggregates and normalizes their outputs into a
comparable [0,1] scale per candidate family.

score_total = weighted average of the 9 bounded components, using the
goal-mode-specific weights from scoring_policy.py. A weighted AVERAGE
(divided by the sum of weights), not a weighted sum, so score_total
itself stays in [0,1] regardless of which weights are active - keeping
scores comparable across goal modes and runs.
"""

from __future__ import annotations

from training_intelligence.selection.scoring_policy import (
    SCORE_DIMENSIONS,
    FAMILY_SYSTEMIC_COST,
    FAMILY_TAGS,
    GOAL_RELEVANCE_TAG_BONUS,
    get_scoring_weights,
)

# Versioned normalization constants - product policy, not scientific
# facts. See TRAINING_INTELLIGENCE_TKI4_REPORT.md for provenance.
DAYS_SINCE_TRAINED_CAP_DAYS = 14.0
DOSE_FEASIBILITY_NORMALIZATION_CAP_WORKING_SETS = 20.0

_READINESS_QUALITY = {
    "FRESH": 1.0,
    "READY": 1.0,
    "RECOVERING": 0.5,
    "FATIGUED": 0.0,
    "SUPPRESSED": 0.0,
}
_READINESS_QUALITY_UNKNOWN = 0.3  # explicit uncertainty - never silently "Fresh" (1.0).

_SYSTEMIC_BAND_MODIFIER = {
    "high": 1.0,
    "good": 0.75,
    "moderate": 0.5,
    "low": 0.25,
    "very_low": 0.0,
}
_SYSTEMIC_BAND_MODIFIER_UNKNOWN = 0.5  # cold-start / no WHOOP data - neutral, not assumed high or low.

_PERFORMANCE_SCORE = {
    "IMPROVING": 0.8,
    "STABLE": 0.5,
    "DECLINING": 0.3,
    "INSUFFICIENT_DATA": 0.5,  # uncertainty, not a penalty.
}


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _muscle_readiness_entry(muscle, muscle_readiness_result):
    for entry in muscle_readiness_result.get("muscles", []):
        if entry.get("muscle") == muscle:
            return entry
    return None


def _local_readiness(candidate, muscle_readiness_result):
    muscles = candidate["primary_muscles"]
    if not muscles:
        return _READINESS_QUALITY_UNKNOWN
    values = []
    for muscle in muscles:
        entry = _muscle_readiness_entry(muscle, muscle_readiness_result)
        state = (entry or {}).get("readiness_state")
        values.append(_READINESS_QUALITY.get(state, _READINESS_QUALITY_UNKNOWN))
    return _clamp01(sum(values) / len(values))


def _days_since_trained(candidate, muscle_readiness_result):
    muscles = candidate["primary_muscles"]
    if not muscles:
        return 0.5
    values = []
    for muscle in muscles:
        entry = _muscle_readiness_entry(muscle, muscle_readiness_result)
        hours = (entry or {}).get("hours_since_primary_exposure")
        # No recorded primary exposure ever (None) is factually "at least
        # as long as anything we've measured" - capped, not guessed.
        days = (hours / 24.0) if hours is not None else DAYS_SINCE_TRAINED_CAP_DAYS
        values.append(min(days, DAYS_SINCE_TRAINED_CAP_DAYS) / DAYS_SINCE_TRAINED_CAP_DAYS)
    return _clamp01(sum(values) / len(values))


def _stimulus_debt_raw(candidate):
    """Raw (unbounded, higher = MORE current stimulus = less debt) value
    per candidate - normalized RELATIVE TO THE OTHER CANDIDATES in
    score_candidates(), never against an absolute population reference
    (section 6's explicit requirement)."""
    dose = candidate["dose"]
    if dose is None:
        return None
    ranges = dose["recommended_dose"]["muscle_stimulus_ranges"]
    if not ranges:
        return None
    values = [entry["stimulus_sets_14d"] for entry in ranges.values()]
    return sum(values) / len(values)


def _program_balance(candidate, family_session_counts_14d):
    total = sum(family_session_counts_14d.values())
    if total <= 0:
        return 0.5  # cold start / no recent history at all - neutral.
    share = family_session_counts_14d.get(candidate["session_family"], 0) / total
    # Underrepresented (small share of recent sessions) -> higher score.
    return _clamp01(1.0 - share)


def _systemic_capacity(candidate, readiness):
    band = readiness.get("readiness_band")
    base = _SYSTEMIC_BAND_MODIFIER.get(band, _SYSTEMIC_BAND_MODIFIER_UNKNOWN)
    cost = FAMILY_SYSTEMIC_COST.get(candidate["session_family"], 0.5)
    # Blend toward neutral (0.5) for low-cost families - systemic
    # capacity matters less for a small session, and this never singles
    # out a muscle group, only scales a shared, family-size-based proxy.
    return _clamp01(base * cost + 0.5 * (1.0 - cost))


def _goal_relevance(candidate, goal_mode):
    tags = FAMILY_TAGS.get(candidate["session_family"], ())
    bonuses = GOAL_RELEVANCE_TAG_BONUS.get(goal_mode, {})
    return _clamp01(0.5 + sum(bonuses.get(tag, 0.0) for tag in tags))


def _performance(candidate):
    dose = candidate["dose"]
    if dose is None:
        return 0.5
    trajectory = dose["performance_state"]["trajectory"]
    return _PERFORMANCE_SCORE.get(trajectory, 0.5)


def _dose_feasibility(candidate):
    dose = candidate["dose"]
    if dose is None:
        return 0.0
    upper_bound = dose["feasible_dose_range"]["upper_bound_working_sets"]
    return _clamp01(upper_bound / DOSE_FEASIBILITY_NORMALIZATION_CAP_WORKING_SETS)


def _schedule_fit(candidate):
    # No dedicated training-frequency/schedule system exists yet (section
    # 3/21 audit finding) - this dimension is reserved, deterministic, and
    # neutral for every candidate until a real schedule system exists to
    # wire it to. Documented, not fabricated.
    return 0.5


def score_candidates(candidates, muscle_readiness_result, readiness, goal_mode,
                       family_session_counts_14d):
    """Scores every ELIGIBLE candidate (ineligible ones are left
    unscored - score components are only meaningful once a candidate has
    passed eligibility). Returns the same candidate dicts with
    `score_components` and `score_total` added for eligible ones."""

    weights = get_scoring_weights(goal_mode)
    eligible = [c for c in candidates if c["eligible"]]

    raw_debts = {c["session_family"]: _stimulus_debt_raw(c) for c in eligible}
    known_debts = [v for v in raw_debts.values() if v is not None]
    debt_lo = min(known_debts) if known_debts else 0.0
    debt_hi = max(known_debts) if known_debts else 0.0

    for candidate in candidates:
        if not candidate["eligible"]:
            continue

        raw_debt = raw_debts[candidate["session_family"]]
        if raw_debt is None or debt_hi <= debt_lo:
            stimulus_debt = 0.5  # no discriminating information - neutral, not guessed.
        else:
            # Lower current stimulus (raw_debt near debt_lo) -> higher debt score.
            stimulus_debt = _clamp01((debt_hi - raw_debt) / (debt_hi - debt_lo))

        components = {
            "stimulus_debt": stimulus_debt,
            "local_readiness": _local_readiness(candidate, muscle_readiness_result),
            "days_since_trained": _days_since_trained(candidate, muscle_readiness_result),
            "program_balance": _program_balance(candidate, family_session_counts_14d),
            "systemic_capacity": _systemic_capacity(candidate, readiness),
            "goal_relevance": _goal_relevance(candidate, goal_mode),
            "performance": _performance(candidate),
            "dose_feasibility": _dose_feasibility(candidate),
            "schedule_fit": _schedule_fit(candidate),
        }

        weight_sum = sum(weights[dimension] for dimension in SCORE_DIMENSIONS)
        score_total = sum(
            weights[dimension] * components[dimension] for dimension in SCORE_DIMENSIONS
        ) / weight_sum

        candidate["score_components"] = components
        candidate["score_total"] = round(score_total, 4)
        candidate["scoring_weights_used"] = dict(weights)

    return candidates
