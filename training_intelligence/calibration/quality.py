"""TKI-5.3 (section 14): deterministic, versioned comparable-session
quality scoring - replaces binary strict-tier inclusion with a
continuous weight so a session that is "close but not exact" can still
contribute (down-weighted) evidence instead of being excluded outright.

PRODUCT POLICY / CALIBRATION PARAMETER throughout - no learned/opaque
weights, nothing here is a physiological claim.
"""
from __future__ import annotations

from training_intelligence.stimulus.taxonomy import to_canonical
from integrations.tonal.training_priority import SESSION_TEMPLATES
from training_intelligence.calibration.history import pattern, COMPOUNDS

QUALITY_POLICY_VERSION = 1

# Sums to 1.0 - a reweighting changes emphasis, never total scale.
QUALITY_WEIGHTS = {
    "family_match": 0.30,
    "primary_muscle_overlap": 0.20,
    "pattern_overlap": 0.15,
    "exercise_count_similarity": 0.10,
    "set_count_similarity": 0.10,
    "recency": 0.10,
    "data_completeness": 0.05,
}

# CALIBRATION PARAMETER: sessions scoring below this are excluded from
# the envelope entirely (a floor, not a claim about relevance below it).
MIN_QUALITY_TO_INCLUDE = 0.35

# CALIBRATION PARAMETER: recency half-life in days - a comparable
# session's recency sub-score decays toward 0 but never reaches it,
# matching "recent stimulus is context, not the only evidence."
RECENCY_HALF_LIFE_DAYS = 180


def _similarity(a, b, scale):
    if a is None or b is None:
        return 0.5
    return max(0.0, 1.0 - abs(a - b) / scale)


def session_quality_score(session, family, as_of, session_patterns=None):
    """One [0,1] score per comparable-session candidate. `session_
    patterns` (optional): the target session's own set of movement
    patterns, for pattern-overlap scoring - falls back to a neutral
    0.5 sub-score when not supplied (e.g. scoring a pool before any
    candidate movements are known yet)."""
    targets = {to_canonical(m) for m in SESSION_TEMPLATES[family]["muscles"]}
    session_muscles = set(session.get("primary_counts") or {})

    family_match = 1.0 if session.get("family") == family else 0.0

    overlap = len(session_muscles & targets)
    primary_muscle_overlap = overlap / max(len(targets), 1)

    if session_patterns:
        session_own_patterns = set(session.get("movement_patterns") or [])
        pattern_overlap = (
            len(session_own_patterns & session_patterns) / max(len(session_patterns), 1)
            if session_own_patterns else 0.5
        )
    else:
        pattern_overlap = 0.5

    exercise_count_similarity = _similarity(session.get("exercise_count"), session.get("target_exercise_count"), 4)
    set_count_similarity = _similarity(session.get("set_count"), session.get("target_set_count"), 12)

    days_ago = max(0.0, (as_of - session["begin_time"]).total_seconds() / 86400.0)
    recency = 0.5 ** (days_ago / RECENCY_HALF_LIFE_DAYS)

    data_completeness = session.get("known_workload_fraction", 0.0)

    components = {
        "family_match": family_match,
        "primary_muscle_overlap": primary_muscle_overlap,
        "pattern_overlap": pattern_overlap,
        "exercise_count_similarity": exercise_count_similarity,
        "set_count_similarity": set_count_similarity,
        "recency": recency,
        "data_completeness": data_completeness,
    }
    score = sum(QUALITY_WEIGHTS[k] * v for k, v in components.items())
    return round(score, 4), components


def scored_comparable_sessions(sessions, family, as_of, window_days=None,
                                target_exercise_count=None, target_set_count=None):
    """Returns [(session, score, components), ...] sorted by score
    descending, filtered to MIN_QUALITY_TO_INCLUDE and (if given) the
    window, and to sessions at or before as_of. Does not replace
    training_intelligence.calibration.history.comparable_sessions()'s
    own tiered hierarchy (still used for capacity_reference/composition,
    unchanged) - this is an ADDITIVE, continuous scoring path used by
    workload_sanity_v2's envelope construction."""
    from datetime import timedelta
    eligible = [s for s in sessions if s["begin_time"] <= as_of]
    if window_days is not None:
        eligible = [s for s in eligible if s["begin_time"] >= as_of - timedelta(days=window_days)]
    scored = []
    for session in eligible:
        enriched = dict(session, target_exercise_count=target_exercise_count, target_set_count=target_set_count)
        score, components = session_quality_score(enriched, family, as_of)
        if score >= MIN_QUALITY_TO_INCLUDE:
            scored.append((session, score, components))
    return sorted(scored, key=lambda t: (-t[1], -t[0]["begin_time"].timestamp()))
