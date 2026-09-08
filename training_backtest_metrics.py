"""Descriptive aggregate metrics for Training-B3.1 replay output."""

from collections import Counter, defaultdict


def aggregate(days):
    quality = Counter(day["data_quality"]["status"] for day in days)
    distribution = Counter(day["recommendation"].get("training_status") for day in days)
    sessions = Counter(day["recommendation"].get("session_type") for day in days)
    selected = Counter()
    actual = Counter()
    readiness_selected = Counter()
    progression = Counter()
    progression_outcomes = Counter()
    dose_by_recovery = defaultdict(list)
    step_targets = []
    actual_steps = []
    attained = 0
    evaluable_steps = 0
    suspicious = []
    previous = None
    repeats = 0

    for day in days:
        recommendation = day["recommendation"]
        muscles = recommendation.get("selected_muscles") or []
        selected.update(muscles)
        for workout in day["actual_outcome"].get("workouts") or []:
            actual.update(workout.get("muscles") or [])
        input_muscles = (day["inputs"].get("training") or {}).get("ranked_muscles") or []
        state = {row.get("muscle"): row.get("readiness_state") for row in input_muscles}
        readiness_selected.update(state.get(muscle, "UNKNOWN") for muscle in muscles)
        progression.update(
            exercise.get("progression_state") or "UNKNOWN"
            for exercise in recommendation.get("exercises") or []
        )
        progression_outcomes.update(
            row.get("result") or "not_evaluable"
            for row in day.get("comparison", {}).get("progression_follow_up") or []
        )
        recovery = ((day["inputs"].get("whoop") or {}).get("readiness_band") or "unknown")
        sets = recommendation.get("target_sets")
        if sets is not None:
            dose_by_recovery[recovery].append(float(sets))
        if previous and muscles and set(muscles) == set(previous):
            repeats += 1
            suspicious.append({"date": day["replay_date"], "reason": "same selected muscle set as prior replay day"})
        previous = muscles
        if any(state.get(muscle) in ("RECOVERING", "FATIGUED", "SUPPRESSED") for muscle in muscles):
            suspicious.append({"date": day["replay_date"], "reason": "selected muscle had recovering/fatigued/suppressed state"})
        target = (recommendation.get("activity_plan") or {}).get("step_target")
        observed = day["actual_outcome"].get("actual_steps")
        if target is not None:
            step_targets.append(float(target))
        if observed is not None:
            actual_steps.append(float(observed))
        if target and observed is not None:
            evaluable_steps += 1
            attained += int(observed >= target)

    gaps = _longest_gaps(days)
    target_sets = [float(day["recommendation"]["target_sets"]) for day in days if day["recommendation"].get("target_sets") is not None]
    set_ratios = [float(day["comparison"]["recommended_set_ratio_vs_personal_median"])
                  for day in days if day.get("comparison", {}).get("recommended_set_ratio_vs_personal_median") is not None]
    return {
        "coverage": {"requested_days": len(days), "evaluable_days": quality["COMPLETE"],
                     "partial_days": quality["PARTIAL"], "insufficient_days": quality["INSUFFICIENT"]},
        "recommendation_distribution": dict(distribution),
        "template_distribution": dict(sessions),
        "muscle_selection": {"recommended_frequency": dict(selected), "actual_frequency": dict(actual),
                             "readiness_state_distribution": dict(readiness_selected),
                             "consecutive_repeated_selections": repeats, "longest_gap_days": gaps},
        "dose": {"average_recommended_sets": _mean(target_sets),
                 "average_recommended_set_ratio_vs_personal_median": _mean(set_ratios),
                 "average_sets_by_recovery": {key: _mean(values) for key, values in dose_by_recovery.items()}},
        "progression_state_distribution": dict(progression),
        "progression_follow_up_distribution": dict(progression_outcomes),
        "activity": {"average_step_target": _mean(step_targets), "average_actual_steps": _mean(actual_steps),
                     "evaluable_days": evaluable_steps,
                     "target_attainment_rate": round(attained / evaluable_steps, 3) if evaluable_steps else None},
        "data_quality_missing_sources": dict(Counter(source for day in days for source in day["data_quality"]["missing_sources"])),
        "suspicious_days": suspicious,
    }


def _mean(values):
    return round(sum(values) / len(values), 2) if values else None


def _longest_gaps(days):
    dates_by_muscle = defaultdict(list)
    for index, day in enumerate(days):
        for muscle in day["recommendation"].get("selected_muscles") or []:
            dates_by_muscle[muscle].append(index)
    all_muscles = ("Chest", "Back", "Shoulders", "Biceps", "Triceps", "Core", "Glutes", "Hamstrings", "Quads")
    result = {}
    for muscle in all_muscles:
        indexes = dates_by_muscle[muscle]
        boundaries = [-1] + indexes + [len(days)]
        result[muscle] = max((right - left - 1 for left, right in zip(boundaries, boundaries[1:])), default=len(days))
    return result
