"""Goal-agnostic demonstrated capacity, with explicit product-policy constraints."""
from math import floor
from statistics import median
from training_intelligence.calibration.history import (
    POLICY_VERSION, comparable_sessions, distribution, quantile,
)
from training_intelligence.dose.goal_policy import get_goal_policy
from training_intelligence.dose.shadow_dose import _goal_adjusted_working_sets

# Policy fractions of demonstrated p25/p75; not physiological facts.
SYSTEMIC = {"high": (1., 1.), "good": (.9, .95), "moderate": (.75, .85),
            "low": (.5, .65), "very_low": (0., 0.), "unknown": (.5, .65)}
LOCAL = {"READY": 1., "FRESH": 1., "RECOVERING": .5,
         "UNKNOWN": .5, "FATIGUED": 0., "SUPPRESSED": 0.}
FALLBACK_CAPACITY = (6, 8, 10)
FALLBACK_FREQUENCY_PER_WEEK = 1.
MIN_PROLONGED_GAP_DAYS = 21.
GAP_P75_MULTIPLE = 3.


def capacity_reference(sessions, family, as_of):
    pool, source = comparable_sessions(sessions, family, as_of)
    counts = [s["set_count"] for s in pool]
    stats = distribution(counts)
    recent = [s["set_count"] for s in pool if (as_of - s["begin_time"]).total_seconds() <= 90 * 86400]
    dates = sorted({s["begin_time"].date() for s in pool})
    gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
    frequency = 7 / median(gaps) if gaps else FALLBACK_FREQUENCY_PER_WEEK
    return {
        "capacity_policy_version": POLICY_VERSION, "source": source,
        "comparable_session_count": len(pool),
        "median_sets": stats["median"] if counts else FALLBACK_CAPACITY[1],
        "p25_sets": stats["p25"] if counts else FALLBACK_CAPACITY[0],
        "p75_sets": stats["p75"] if counts else FALLBACK_CAPACITY[2],
        "recent_median_sets": median(recent) if recent else None,
        "historical_upper_typical": stats["p75"] if counts else FALLBACK_CAPACITY[2],
        "confidence": "HIGH" if len(pool) >= 8 else ("MEDIUM" if pool else "LOW"),
        "personal_frequency_per_week": round(frequency, 3),
        "frequency_source": "personal_inter_session_gaps" if gaps else "versioned_product_policy_fallback",
        "evidence_limit": "Completed personal work is an exposure/tolerance proxy; symptom-free recovery is not established by these records.",
    }


def feasible_capacity(reference, sessions, as_of, readiness_band, local_states, structure_cap=None):
    low, high = reference["p25_sets"], reference["p75_sets"]
    constraints = [{"constraint": "historical_capacity", "lower": low, "upper": high, "binding": True}]
    system = SYSTEMIC.get(readiness_band, SYSTEMIC["unknown"])
    low, high = low * system[0], high * system[1]
    constraints.append({"constraint": "systemic_readiness", "band": readiness_band,
                        "fractions": system, "binding": system != (1., 1.)})
    # Every relevant primary muscle constrains the session; none can be averaged
    # away by a fresh neighbour. Eligibility also checks secondary muscles.
    local = min((LOCAL.get(s, .5) for s in local_states.values()), default=.5)
    low, high = low * local, high * local
    constraints.append({"constraint": "local_readiness", "states": local_states,
                        "fraction": local, "binding": local < 1})
    dates = sorted({s["begin_time"].date() for s in sessions if s["begin_time"] <= as_of})
    gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
    absence = (as_of - max(s["begin_time"] for s in sessions)).total_seconds() / 86400 if sessions else None
    threshold = max(MIN_PROLONGED_GAP_DAYS, GAP_P75_MULTIPLE * (quantile(gaps, .75) or 7))
    if local == 0:
        exposure = "FATIGUED"
    elif absence is None or absence > threshold:
        exposure = "DETRAINED_UNCERTAIN"
        low, high = low * .7, high * .7
        constraints.append({"constraint": "detraining_uncertainty", "binding": True, "fraction": .7})
    elif absence > (median(gaps) if gaps else 7):
        exposure = "RESTED"
    else:
        exposure = "WELL_EXPOSED"
    if structure_cap is not None:
        constraints.append({"constraint": "session_structure", "upper": structure_cap, "binding": structure_cap < high})
        high = min(high, structure_cap)
        low = min(low, high)
    # Floor avoids rounding through a hard upper bound.
    upper = max(0, floor(high))
    lower = min(upper, max(0, floor(low)))
    return {
        "lower_bound_working_sets": lower, "upper_bound_working_sets": upper,
        "binding_constraints": constraints,
        "recent_exposure": {"state": exposure, "days_since_training": absence,
                            "prolonged_absence_threshold_days": threshold,
                            "threshold_source": "personal_gap_p75_with_versioned_policy_floor"},
    }


def choose_dose(feasible, goal_mode):
    return _goal_adjusted_working_sets(feasible, get_goal_policy(goal_mode).get("range_position_fraction"))
