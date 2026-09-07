"""Deterministic exercise-level progression for Training-B3.

Consumes the already-batched 3-6 comparable Tonal sessions attached to each
movement profile. It never treats different movement IDs or Smart Weight modes
as equivalent and never uses set escalation as the first progression lever.
"""

from statistics import median


CONFIG = {
    "rep_range": {"compound": (8, 12), "isolation": (10, 15)},
    "rir": {
        "high": {"compound": (1, 2), "isolation": (0, 2)},
        "good": {"compound": (1, 3), "isolation": (1, 2)},
        "moderate": {"compound": (2, 3), "isolation": (1, 3)},
        "low": {"compound": (3, 4), "isolation": (2, 4)},
    },
    "rest_seconds": {"compound": (120, 180), "isolation": (60, 120)},
    "minimum_comparable_sessions": 2,
    "high_confidence_sessions": 4,
    "rep_noise": 1,
    "decline_fraction": 0.08,
    # Tonal base resistance observed by this integration is represented in lb.
    # Use whole-lb steps; never manufacture unsupported fractional precision.
    "resistance_increment_lb": 1.0,
}


ISOLATION_TERMS = (
    "curl", "extension", "raise", "fly", "kickback", "crunch",
)


def exercise_type(profile):
    name = (profile.get("name") or "").lower()
    return "isolation" if any(term in name for term in ISOLATION_TERMS) else "compound"


def _sessions(profile):
    sessions = list(profile.get("recent_sessions") or [])[:6]
    if not sessions:
        latest = (profile.get("performance") or {}).get("latest_session")
        if latest:
            sessions = [latest]
    return sessions


def _mode(session):
    counts = session.get("mode_counts") or {}
    return max(counts, key=counts.get) if counts else "standard"


def comparable_history(profile):
    sessions = _sessions(profile)
    if not sessions:
        return [], "INSUFFICIENT"
    latest_mode = _mode(sessions[0])
    compatible = [s for s in sessions if _mode(s) == latest_mode]
    count = len(compatible)
    confidence = "HIGH" if count >= CONFIG["high_confidence_sessions"] else (
        "MEDIUM" if count >= CONFIG["minimum_comparable_sessions"] else "LOW"
    )
    return compatible, confidence


def trajectory(sessions):
    if len(sessions) < 3:
        return "INSUFFICIENT_DATA"
    recent = sessions[:2]
    older = sessions[2:]
    recent_volume = median(float(s.get("total_volume") or 0) for s in recent)
    older_volume = median(float(s.get("total_volume") or 0) for s in older)
    if older_volume <= 0:
        return "INSUFFICIENT_DATA"
    change = (recent_volume - older_volume) / older_volume
    if change >= CONFIG["decline_fraction"]:
        return "IMPROVING"
    if change <= -CONFIG["decline_fraction"]:
        return "DECLINING"
    return "STABLE"


def prescribe(profile, readiness_band, set_count):
    kind = exercise_type(profile)
    rep_low, rep_high = CONFIG["rep_range"][kind]
    sessions, confidence = comparable_history(profile)
    trend = trajectory(sessions)
    latest = sessions[0] if sessions else {}
    load = latest.get("median_base_weight")
    total_reps = int(latest.get("total_reps") or 0)
    latest_sets = max(1, int(latest.get("set_count") or set_count or 1))
    reps_per_set = round(total_reps / latest_sets) if total_reps else rep_low
    state = "REBUILD"
    target_load = float(load) if load is not None else None
    target_reps = max(rep_low, min(rep_high, reps_per_set))

    if len(sessions) >= CONFIG["minimum_comparable_sessions"]:
        if trend == "DECLINING" and readiness_band in ("low", "moderate"):
            state = "REDUCE"
            set_count = max(2, set_count - 1)
        elif reps_per_set >= rep_high and readiness_band in ("good", "high") and load is not None:
            state = "PROGRESS_LOAD"
            target_load = min(100.0, float(load) + CONFIG["resistance_increment_lb"])
            target_reps = rep_low
        elif reps_per_set < rep_high and readiness_band in ("good", "high"):
            state = "PROGRESS_REPS"
            target_reps = min(rep_high, max(rep_low, reps_per_set + 1))
        else:
            state = "HOLD"

    rir = CONFIG["rir"].get(readiness_band, CONFIG["rir"]["moderate"])[kind]
    rest = CONFIG["rest_seconds"][kind]
    label = {
        "PROGRESS_REPS": "ADD REPS", "PROGRESS_LOAD": "ADD LOAD",
        "HOLD": "HOLD", "REDUCE": "REDUCE", "REBUILD": "REBUILD",
    }[state]
    rationale = {
        "PROGRESS_REPS": "Comparable sessions support a modest increase in quality reps within the target RIR.",
        "PROGRESS_LOAD": "Recent comparable work reached the top of the rep range; use the next Tonal-compatible load step.",
        "HOLD": "Repeat productive work; the evidence does not yet justify another load or rep increase.",
        "REDUCE": "Repeated performance decline and systemic capacity support reducing fatigue burden first.",
        "REBUILD": "Recent compatible history is insufficient; establish a reliable baseline.",
    }[state]
    return {
        "sets": set_count,
        "rep_range": {"minimum": rep_low, "maximum": rep_high},
        "target_reps_per_set": target_reps,
        "target_resistance_lb": round(target_load, 1) if target_load is not None else None,
        "target_rir": {"minimum": rir[0], "maximum": rir[1]},
        "rest_seconds": {"minimum": rest[0], "maximum": rest[1]},
        "progression_state": state,
        "progression_label": label,
        "progression_target": rationale,
        "trajectory": trend,
        "confidence": confidence,
        "comparable_performance": {
            "count": len(sessions), "smart_weight_mode": _mode(latest) if latest else "not_supported",
            "latest_sets": latest.get("set_count"), "latest_total_reps": latest.get("total_reps"),
            "latest_resistance_lb": latest.get("median_base_weight"),
        },
        "rationale": rationale,
    }
