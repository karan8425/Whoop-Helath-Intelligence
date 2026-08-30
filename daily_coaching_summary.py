from copy import deepcopy


MATERIAL_SLEEP_GAP_HOURS = 0.5
MIN_GOAL_PHASE_AGE_DAYS = 7


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _label(value):
    return str(value or "").strip().lower().replace("_", " ")


def build_daily_coaching_summary(plan):
    """Prioritize an existing deterministic Today's Plan."""

    plan = plan or {}
    training = deepcopy(plan.get("training") or {})
    nutrition = deepcopy(plan.get("nutrition") or {})
    sleep = deepcopy(plan.get("sleep") or {})
    hydration = deepcopy(plan.get("hydration") or {})
    activity = deepcopy(nutrition.get("activity") or {})
    goal = deepcopy(nutrition.get("goal_progress") or {})

    recovery_score = _number(
        sleep.get("recovery_score", training.get("recovery_score"))
    )
    recovery_band = _label(sleep.get("recovery_band"))
    if not recovery_band and recovery_score is not None:
        recovery_band = (
            "red" if recovery_score < 34
            else "yellow" if recovery_score < 67
            else "green"
        )

    sleep_target = _number(
        sleep.get("target_sleep_hours", sleep.get("sleep_target_hours"))
    )
    recent_sleep = _number(
        sleep.get("recent_sleep_average", sleep.get("average_sleep_7d_hours"))
    )
    sleep_gap = (
        sleep_target - recent_sleep
        if sleep_target is not None and recent_sleep is not None
        else None
    )
    material_sleep_gap = (
        sleep_gap is not None
        and sleep_gap >= MATERIAL_SLEEP_GAP_HOURS
    )

    training_category = _label(training.get("category"))
    active_recovery = training_category == "active recovery"
    activity_status = _label(activity.get("status"))
    phase_age = _number(goal.get("phase_age_days"))
    goal_direction = _label(goal.get("direction"))
    goal_baseline_building = (
        phase_age is None
        or phase_age < MIN_GOAL_PHASE_AGE_DAYS
        or goal_direction == "insufficient data"
        or not goal_direction
    )

    candidates = []

    if recovery_band == "red":
        candidates.append((100, "recovery_sleep", "Prioritize recovery and tonight's sleep opportunity."))
    elif material_sleep_gap:
        candidates.append((95, "sleep", "Close the material gap between recent sleep and tonight's target."))
    elif recovery_band == "yellow":
        candidates.append((75, "recovery", "Keep recovery constraints central to today's choices."))

    if active_recovery:
        candidates.append((90 if recovery_band == "red" else 70, "training", "Keep training at the prescribed active-recovery intensity."))

    if activity_status in {"below target", "below_target"}:
        candidates.append((65, "activity", "Bring activity closer to the configured target without overriding recovery."))

    if nutrition.get("available"):
        candidates.append((55, "nutrition", "Follow the configured nutrition prescription, especially the protein target."))

    if (
        not goal_baseline_building
        and goal_direction in {"off track", "away from goal", "regressing"}
    ):
        candidates.append((60, "goal_progress", "Reinforce plan consistency while established goal progress is off track."))

    candidates.sort(key=lambda item: item[0], reverse=True)
    priorities = [
        {"area": area, "priority": text}
        for _, area, text in candidates[:3]
    ]

    actions = []
    if active_recovery:
        actions.append(
            "Keep today's session at active-recovery intensity and do not chase progression."
        )
    if material_sleep_gap:
        target_text = (
            f"{sleep_target:g} hours" if sleep_target is not None else "the prescribed target"
        )
        actions.append(
            f"Create enough sleep opportunity tonight to target {target_text}."
        )
    if not actions and nutrition.get("available"):
        protein = nutrition.get("protein_target_g", nutrition.get("protein_g"))
        actions.append(
            f"Follow the configured nutrition prescription{f', including {protein:g} g protein' if isinstance(protein, (int, float)) else ''}."
        )
    if not actions and hydration.get("available"):
        actions.append("Follow the configured hydration target across the day.")
    if not actions:
        actions.append("Follow today's available deterministic plan and preserve recovery constraints.")

    warnings = []
    if recovery_band == "red":
        warnings.append("Recovery is red; avoid treating today as a progression or overload day.")
    if nutrition.get("intake_tracking_status") == "not_connected":
        warnings.append("Nutrition intake is not connected, so adherence is unknown.")
    if goal_baseline_building:
        warnings.append("Goal progress is baseline-building and cannot yet be classified.")

    if recovery_band == "red" or material_sleep_gap:
        overall_state = "recovery_first"
        headline = "Recovery and sleep matter most today."
    elif active_recovery:
        overall_state = "recovery_training"
        headline = "Keep today's training restorative."
    else:
        overall_state = "follow_plan"
        headline = "Execute the prescribed plan consistently today."

    summary_parts = [headline]
    if active_recovery:
        summary_parts.append("The active-recovery recommendation remains authoritative; do not add overload.")
    if material_sleep_gap:
        summary_parts.append(
            f"Recent sleep is {sleep_gap:.2f} hours below the prescribed target."
        )
    if goal_baseline_building:
        summary_parts.append("The current goal phase is still baseline-building, so classification is withheld.")

    available_signals = sum([
        recovery_score is not None,
        sleep_target is not None,
        bool(training),
        bool(nutrition),
    ])

    return {
        "status": "ok",
        "date": plan.get("plan_date"),
        "overall_state": overall_state,
        "headline": headline,
        "summary": " ".join(summary_parts),
        "top_priorities": priorities,
        "top_actions": actions[:2],
        "training": training,
        "nutrition": nutrition,
        "sleep": sleep,
        "hydration": hydration,
        "activity": activity,
        "goal_progress": {
            **goal,
            "status": (
                "baseline_building"
                if goal_baseline_building
                else goal.get("status", goal.get("direction"))
            ),
        },
        "warnings": warnings,
        "confidence": (
            "high" if available_signals == 4
            else "moderate" if available_signals >= 2
            else "low"
        ),
    }
