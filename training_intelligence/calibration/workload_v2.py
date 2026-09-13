"""TKI-5.3: workload sanity v2 - comparable workload evidence built from
quality-weighted comparable sessions, absolute AND per-set ratios, and
deterministic gap decomposition.

Every number here is a PRODUCT ANALYTIC / PERSONAL COMPARISON METRIC for
anomaly detection - never presented as physiological workload, true
training stimulus, effective hypertrophy units, or measured fatigue
(section 11). Nothing here is machine-learned; every weight is a named,
versioned constant.
"""
from __future__ import annotations

from training_intelligence.calibration.quality import (
    QUALITY_POLICY_VERSION, scored_comparable_sessions,
)

WORKLOAD_V2_POLICY_VERSION = 1

# CALIBRATION PARAMETER: minimum quality-weighted effective comparable
# count before a window's distribution is trusted at all. A handful of
# high-quality sessions is preferred over many loosely-matched ones -
# this is why EFFECTIVE (quality-weighted) count is used, not raw count.
MIN_EFFECTIVE_COMPARABLE_COUNT = 2.5

# CALIBRATION PARAMETER: a deviation ratio inside [LOW, HIGH] of the
# comparable median is WITHIN_PERSONAL_RANGE; outside it, BELOW/ABOVE.
WITHIN_RANGE_RATIO_BAND = (0.75, 1.35)

# CALIBRATION PARAMETER: named, evidence-backed reasons a deviation may
# be accepted as JUSTIFIED rather than UNEXPLAINED (section 21) - never
# a bare "based on readiness" string.
ACCEPTED_JUSTIFICATIONS = (
    "local_readiness", "systemic_readiness", "detraining_uncertainty",
    "session_structure", "reduced_goal_posture",
)


def _weighted_quantile(values_and_weights, fraction):
    """Deterministic weighted percentile via cumulative-weight
    interpolation - not a statistical library call, fully inspectable."""
    pairs = sorted(values_and_weights, key=lambda vw: vw[0])
    total = sum(w for _, w in pairs)
    if not pairs or total <= 0:
        return None
    target = fraction * total
    cumulative = 0.0
    for i, (value, weight) in enumerate(pairs):
        cumulative += weight
        if cumulative >= target or i == len(pairs) - 1:
            return value
    return pairs[-1][0]


def _weighted_distribution(values_and_weights):
    if not values_and_weights:
        return {"effective_count": 0.0, "median": None, "p25": None, "p75": None}
    return {
        "effective_count": round(sum(w for _, w in values_and_weights), 2),
        "median": _weighted_quantile(values_and_weights, 0.5),
        "p25": _weighted_quantile(values_and_weights, 0.25),
        "p75": _weighted_quantile(values_and_weights, 0.75),
    }


def workload_envelope_v2(sessions, family, as_of, target_exercise_count=None, target_set_count=None):
    """Multi-window, quality-weighted envelope over ABSOLUTE normalized
    workload AND workload-PER-SET (section 16) - a 10-set session is
    never compared only to an 18-set one on absolute tonnage alone."""
    windows = {}
    for window_days in (30, 90, 365):
        scored = scored_comparable_sessions(sessions, family, as_of, window_days,
                                             target_exercise_count, target_set_count)
        confident = [(s, q) for s, q, _ in scored if s.get("workload_confident")]
        absolute = [(s["normalized_workload"], q) for s, q in confident]
        per_set = [(s["normalized_workload"] / s["set_count"], q) for s, q in confident if s["set_count"]]
        sets_dist = [(s["set_count"], q) for s, q, _ in scored]
        exercises_dist = [(s["exercise_count"], q) for s, q, _ in scored]
        windows[str(window_days)] = {
            "comparable_count": len(scored),
            "effective_comparable_count": round(sum(q for _, q, _ in scored), 2),
            "quality_distribution": {
                "min": round(min((q for _, q, _ in scored), default=0.0), 3),
                "max": round(max((q for _, q, _ in scored), default=0.0), 3),
            },
            "normalized_workload": _weighted_distribution(absolute),
            "workload_per_set": _weighted_distribution(per_set),
            "working_sets": _weighted_distribution(sets_dist),
            "exercises": _weighted_distribution(exercises_dist),
        }
    return {"workload_v2_policy_version": WORKLOAD_V2_POLICY_VERSION,
            "quality_policy_version": QUALITY_POLICY_VERSION, "windows": windows}


def _select_window(envelope):
    """Prefers the narrowest window with enough EFFECTIVE (quality-
    weighted) evidence, checking 90 -> 365 -> 30 - a fixed, documented,
    versioned order, not a search for whichever looks best."""
    for window_days in (90, 365, 30):
        window = envelope["windows"][str(window_days)]
        if (window["normalized_workload"]["effective_count"] >= MIN_EFFECTIVE_COMPARABLE_COUNT
                and window["normalized_workload"]["median"]):
            return window_days, window
    return None, None


def decompose_gap(estimated_workload, delivered_sets, exercise_count, window):
    """Section 16/17: never claims perfect causal attribution - a
    deterministic accounting split into named, bounded contributions."""
    stats = window["normalized_workload"]
    per_set_stats = window["workload_per_set"]
    sets_stats = window["working_sets"]
    exercises_stats = window["exercises"]

    absolute_ratio = round(estimated_workload / stats["median"], 4) if stats["median"] else None
    per_set = (estimated_workload / delivered_sets) if delivered_sets else None
    per_set_ratio = round(per_set / per_set_stats["median"], 4) if (per_set and per_set_stats["median"]) else None
    set_count_ratio = round(delivered_sets / sets_stats["median"], 4) if sets_stats["median"] else None
    exercise_count_ratio = round(exercise_count / exercises_stats["median"], 4) if exercises_stats["median"] else None

    causes = []
    if set_count_ratio is not None and set_count_ratio < 0.85:
        causes.append("dose_contribution")
    if exercise_count_ratio is not None and exercise_count_ratio < 0.85:
        causes.append("exercise_count_contribution")
    if per_set_ratio is not None and per_set_ratio < 0.85:
        causes.append("resistance_or_rep_contribution")
    if not causes and absolute_ratio is not None and absolute_ratio < 0.85:
        causes.append("movement_mix_or_semantics_contribution")

    return {
        "absolute_ratio_to_median": absolute_ratio,
        "workload_per_set_ratio": per_set_ratio,
        "set_count_ratio": set_count_ratio,
        "exercise_count_ratio": exercise_count_ratio,
        "dominant_gap_causes": causes or ["none_below_threshold"],
    }


def workload_sanity_v2(exercises, sessions, family, as_of, target_sets, binding_constraints, goal_reduced):
    """The section-20 output shape. `binding_constraints`: the SAME
    list already computed for the feasible dose range - the only source
    of an accepted justification (section 21), never a bare mention of
    a goal mode."""
    estimated_workload = sum(e["estimated_volume"] or 0 for e in exercises)
    delivered_sets = sum(e["working_sets"] for e in exercises)
    exercise_count = len(exercises)
    # Section 13, applied to TODAY's own prescribed session: the fraction
    # of estimated workload coming from exercises whose multiplier is
    # NOT low-confidence, weighted by each exercise's own contribution -
    # one uncertain accessory movement no longer disqualifies a session
    # otherwise dominated by well-evidenced compound lifts.
    known_workload = sum(e["estimated_volume"] or 0 for e in exercises if e["workload"]["confidence"] != "LOW")
    known_workload_fraction = round(known_workload / estimated_workload, 4) if estimated_workload else 0.0

    envelope = workload_envelope_v2(sessions, family, as_of, exercise_count, delivered_sets)
    window_days, window = _select_window(envelope)

    justifications = [c["constraint"] for c in binding_constraints if c.get("binding")
                       and c["constraint"] in ACCEPTED_JUSTIFICATIONS]
    if goal_reduced:
        justifications.append("reduced_goal_posture")

    if window is None:
        return {
            "workload_v2_policy_version": WORKLOAD_V2_POLICY_VERSION,
            "status": "INSUFFICIENT_DATA",
            "absolute_workload_ratio": None, "workload_per_set_ratio": None,
            "set_count_ratio": None, "exercise_count_ratio": None,
            "known_workload_fraction": known_workload_fraction,
            "comparable_count": 0, "comparable_quality": None,
            "dominant_gap_causes": ["insufficient_comparable_evidence"],
            "justification": justifications or None,
            "confidence": "LOW", "estimated_workload": estimated_workload,
            "reference_window_days": None, "envelope": envelope,
        }

    gap = decompose_gap(estimated_workload, delivered_sets, exercise_count, window)
    ratio = gap["absolute_ratio_to_median"]
    low, high = WITHIN_RANGE_RATIO_BAND
    if ratio is None:
        status = "INSUFFICIENT_DATA"
    elif low <= ratio <= high:
        status = "WITHIN_PERSONAL_RANGE"
    elif ratio < low:
        status = "BELOW_PERSONAL_RANGE_JUSTIFIED" if justifications else "BELOW_PERSONAL_RANGE_UNEXPLAINED"
    else:
        status = "ABOVE_PERSONAL_RANGE_JUSTIFIED" if justifications else "ABOVE_PERSONAL_RANGE_UNEXPLAINED"

    comparable_quality = window["quality_distribution"]
    confidence = (
        "LOW" if known_workload_fraction < 0.6 or window["normalized_workload"]["effective_count"] < MIN_EFFECTIVE_COMPARABLE_COUNT
        else ("HIGH" if window["normalized_workload"]["effective_count"] >= 6 else "MEDIUM")
    )

    return {
        "workload_v2_policy_version": WORKLOAD_V2_POLICY_VERSION,
        "status": status,
        "absolute_workload_ratio": gap["absolute_ratio_to_median"],
        "workload_per_set_ratio": gap["workload_per_set_ratio"],
        "set_count_ratio": gap["set_count_ratio"],
        "exercise_count_ratio": gap["exercise_count_ratio"],
        "known_workload_fraction": known_workload_fraction,
        "comparable_count": window["comparable_count"],
        "comparable_quality": comparable_quality,
        "dominant_gap_causes": gap["dominant_gap_causes"] if status not in ("WITHIN_PERSONAL_RANGE",) else [],
        "justification": justifications or None,
        "confidence": confidence,
        "estimated_workload": estimated_workload,
        "reference_window_days": window_days,
        "envelope": envelope,
    }


# ============================================================
# TKI-5.4 section 28: workload_sanity_v3 - adds real counterfactual
# justification provenance on top of v2's gap decomposition. v2 remains
# unchanged above for continuity/rollback comparison; v3 is what
# shadow.py's quality_verdict_v3 now reads.
# ============================================================

WORKLOAD_V3_POLICY_VERSION = 1

# Maps decompose_gap's dominant_gap_causes into the human-facing gap
# classification TKI-5.4 sections 18/31 ask for. A cause list with more
# than one non-trivial entry is always MIXED - never forced to pick one.
_GAP_CLASSIFICATION_MAP = {
    "dose_contribution": "DOSE_DRIVEN",
    "exercise_count_contribution": "COMPOSITION_DRIVEN",
    "resistance_or_rep_contribution": "INTENSITY_DRIVEN",
    "movement_mix_or_semantics_contribution": "SEMANTICS_DRIVEN",
}


def classify_gap(dominant_gap_causes):
    real_causes = [c for c in dominant_gap_causes if c not in ("none_below_threshold", "insufficient_comparable_evidence")]
    if not dominant_gap_causes or dominant_gap_causes == ["insufficient_comparable_evidence"]:
        return "INSUFFICIENT_EVIDENCE"
    if not real_causes:
        return "NONE"
    if len(real_causes) > 1:
        return "MIXED"
    return _GAP_CLASSIFICATION_MAP.get(real_causes[0], "MIXED")


def workload_sanity_v3(exercises, sessions, family, as_of, target_sets, capacity, readiness_band,
                        local_states, structure_cap, goal_mode, actual_feasible, actual_dose):
    """Section 28. Reuses v2's envelope/gap machinery for the ratio
    dimensions, then replaces v2's naive "constraint.binding == True"
    justification citation with real counterfactual-tested
    justifications (justification_v2.counterfactual_effects) and a
    proportional sufficiency test (justification_v2.
    justification_sufficient, section 21) - status is only *_JUSTIFIED
    when justification_sufficient is True, never merely "a reason
    string exists"."""
    from training_intelligence.calibration.justification_v2 import (
        JUSTIFICATION_POLICY_VERSION, counterfactual_effects, justification_sufficient,
    )

    estimated_workload = sum(e["estimated_volume"] or 0 for e in exercises)
    delivered_sets = sum(e["working_sets"] for e in exercises)
    exercise_count = len(exercises)
    known_workload = sum(e["estimated_volume"] or 0 for e in exercises if e["workload"]["confidence"] != "LOW")
    known_workload_fraction = round(known_workload / estimated_workload, 4) if estimated_workload else 0.0

    envelope = workload_envelope_v2(sessions, family, as_of, exercise_count, delivered_sets)
    window_days, window = _select_window(envelope)

    justifications = counterfactual_effects(
        capacity, sessions, as_of, readiness_band, local_states, structure_cap,
        goal_mode, actual_feasible, actual_dose,
    )

    base = {
        "workload_v3_policy_version": WORKLOAD_V3_POLICY_VERSION,
        "justification_policy_version": JUSTIFICATION_POLICY_VERSION,
        "known_workload_fraction": known_workload_fraction,
        "justifications": justifications,
        "estimated_workload": estimated_workload,
    }

    if window is None:
        return dict(base, status="INSUFFICIENT_DATA", absolute_workload_ratio=None,
                    workload_per_set_ratio=None, set_count_ratio=None, exercise_count_ratio=None,
                    comparable_count=0, comparable_quality=None, gap_classification="INSUFFICIENT_EVIDENCE",
                    dominant_gap_causes=["insufficient_comparable_evidence"],
                    justification_sufficient=False, confidence="LOW", reference_window_days=None, envelope=envelope)

    gap = decompose_gap(estimated_workload, delivered_sets, exercise_count, window)
    ratio = gap["absolute_ratio_to_median"]
    low, high = WITHIN_RANGE_RATIO_BAND
    sufficient = justification_sufficient(justifications, ratio)
    if ratio is None:
        status = "INSUFFICIENT_DATA"
    elif low <= ratio <= high:
        status = "WITHIN_PERSONAL_RANGE"
    elif ratio < low:
        status = "BELOW_PERSONAL_RANGE_JUSTIFIED" if sufficient else "BELOW_PERSONAL_RANGE_UNEXPLAINED"
    else:
        status = "ABOVE_PERSONAL_RANGE_JUSTIFIED" if sufficient else "ABOVE_PERSONAL_RANGE_UNEXPLAINED"

    confidence = (
        "LOW" if known_workload_fraction < 0.6 or window["normalized_workload"]["effective_count"] < MIN_EFFECTIVE_COMPARABLE_COUNT
        else ("HIGH" if window["normalized_workload"]["effective_count"] >= 6 else "MEDIUM")
    )

    return dict(base, status=status, absolute_workload_ratio=gap["absolute_ratio_to_median"],
                workload_per_set_ratio=gap["workload_per_set_ratio"], set_count_ratio=gap["set_count_ratio"],
                exercise_count_ratio=gap["exercise_count_ratio"], comparable_count=window["comparable_count"],
                comparable_quality=window["quality_distribution"],
                gap_classification=classify_gap(gap["dominant_gap_causes"]) if status not in ("WITHIN_PERSONAL_RANGE",) else "NONE",
                dominant_gap_causes=gap["dominant_gap_causes"] if status not in ("WITHIN_PERSONAL_RANGE",) else [],
                justification_sufficient=sufficient, confidence=confidence,
                reference_window_days=window_days, envelope=envelope)
