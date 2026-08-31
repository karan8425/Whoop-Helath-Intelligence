from datetime import datetime, timezone

from goals import get_active_goal
from apple_health_trends import apple_health_trends
from body_composition_progress import body_composition_progress
from integrations.tonal.strength_adherence import strength_adherence


KG_TO_LB = 2.2046226218
MIN_PHASE_AGE_DAYS = 7


def _round(value, digits=1):
    if value is None:
        return None

    return round(
        float(value),
        digits,
    )


def _phase_age_days(
    phase_start_date
):
    if not phase_start_date:
        return None

    start = datetime.strptime(
        phase_start_date,
        "%Y-%m-%d",
    ).date()

    today = datetime.now(
        timezone.utc
    ).date()

    return max(
        0,
        (today - start).days,
    )


def _progress_percentage(
    start,
    current,
    target,
):
    if (
        start is None
        or current is None
        or target is None
    ):
        return None, None

    start = float(start)
    current = float(current)
    target = float(target)

    denominator = (
        start - target
    )

    if abs(
        denominator
    ) < 0.000001:

        return (
            100.0,
            100.0,
        )

    raw = (
        (start - current)
        / denominator
        * 100
    )

    dashboard = max(
        0.0,
        min(
            100.0,
            raw,
        ),
    )

    return (
        round(
            dashboard,
            1,
        ),
        round(
            raw,
            1,
        ),
    )


def _derived_composition_goal_metric(
    body_progress,
    metric_name,
):
    metric = (
        body_progress.get("metrics", {}).get(metric_name, {})
        if body_progress
        else {}
    )
    progress = metric.get("progress", {})
    goal = metric.get("horizons", {}).get("Goal", {})

    return {
        "start_lb": metric.get("phase_start_value"),
        "current_lb": metric.get("goal_current_value"),
        "target_lb": metric.get("target_value"),
        "progress_percentage": progress.get(
            "progress_percentage"
        ),
        "raw_progress_percentage": progress.get(
            "raw_progress_percentage"
        ),
        "direction": metric.get("goal_direction"),
        "state": progress.get("state", "insufficient_data"),
        "status": goal.get("status", "insufficient_data"),
    }


def _body_fat_direction(
    body_fat
):
    if not body_fat:
        return "insufficient_data"

    if not body_fat.get(
        "available"
    ):
        return "insufficient_data"

    current = body_fat.get(
        "current_value"
    )

    windows = body_fat.get(
        "windows",
        {},
    )

    window_7 = windows.get(
        "7",
        {},
    )

    window_30 = windows.get(
        "30",
        {},
    )

    observations_7 = (
        window_7.get(
            "observations"
        )
        or 0
    )

    observations_30 = (
        window_30.get(
            "observations"
        )
        or 0
    )

    baseline_7 = (
        window_7.get(
            "baseline"
        )
    )

    baseline_30 = (
        window_30.get(
            "baseline"
        )
    )

    if (
        current is None
        or baseline_7 is None
        or baseline_30 is None
        or observations_7 < 3
        or observations_30 < 5
    ):
        return "insufficient_data"

    current = float(
        current
    )

    baseline_7 = float(
        baseline_7
    )

    baseline_30 = float(
        baseline_30
    )

    tolerance = 0.15

    if (
        current
        < baseline_7
        - tolerance
        and baseline_7
        <= baseline_30
    ):
        return "toward_goal"

    if (
        current
        > baseline_7
        + tolerance
        and baseline_7
        >= baseline_30
    ):
        return "away_from_goal"

    return "stable"


def _activity_status(
    activity,
    daily_step_target,
):
    if not daily_step_target:
        return {
            "status":
                "not_configured",

            "average_steps_7d":
                None,

            "target_steps":
                None,

            "percentage_of_target":
                None,
        }

    baselines = activity.get(
        "baselines",
        {},
    )

    window_7 = baselines.get(
        "7",
        {},
    )

    steps = window_7.get(
        "steps"
    )

    days_available = (
        window_7.get(
            "days_available"
        )
        or 0
    )

    if (
        steps is None
        or days_available < 4
    ):
        return {
            "status":
                "insufficient_data",

            "average_steps_7d":
                steps,

            "target_steps":
                daily_step_target,

            "percentage_of_target":
                None,
        }

    steps = float(
        steps
    )

    target = float(
        daily_step_target
    )

    percentage = (
        steps
        / target
        * 100
        if target > 0
        else None
    )

    if percentage is None:
        status = (
            "not_configured"
        )

    elif percentage >= 100:
        status = "on_track"

    elif percentage >= 85:
        status = "close"

    else:
        status = "below_target"

    return {
        "status":
            status,

        "average_steps_7d":
            round(
                steps
            ),

        "target_steps":
            int(
                target
            ),

        "percentage_of_target":
            _round(
                percentage,
                1,
            ),
    }


def _overall_direction(
    phase_age_days,
    body_fat_direction,
    activity_status,
):
    # Do not label a newly started phase as
    # on-track, mixed or off-track.
    if (
        phase_age_days is None
        or phase_age_days
        < MIN_PHASE_AGE_DAYS
    ):
        return "insufficient_data"

    if (
        body_fat_direction
        == "insufficient_data"
    ):
        return "insufficient_data"

    if (
        body_fat_direction
        == "toward_goal"
    ):

        if activity_status in (
            "on_track",
            "close",
        ):
            return "on_track"

        return "mixed"

    if (
        body_fat_direction
        == "away_from_goal"
    ):
        return "off_track"

    if (
        body_fat_direction
        == "stable"
    ):

        if activity_status == "on_track":
            return "mixed"

        return "off_track"

    return "insufficient_data"


def _summary(
    phase,
    direction,
    phase_age_days,
    body_fat_direction,
    activity_status,
):
    phase_name = {
        "lean_cut":
            "Lean Cut",

        "maintenance":
            "Maintenance",

        "lean_bulk":
            "Lean Bulk",
    }.get(
        phase,
        phase
        or "Current phase",
    )

    if (
        phase_age_days is not None
        and phase_age_days
        < MIN_PHASE_AGE_DAYS
    ):
        if activity_status == "on_track":
            return (
                f"{phase_name} has just started. "
                "Activity is on track, but there is not yet "
                "enough post-phase body-composition data to "
                "determine whether you are moving toward or "
                "away from the goal."
            )

        return (
            f"{phase_name} has just started. "
            "More post-phase data is needed before "
            "progress direction can be evaluated."
        )

    if direction == "on_track":
        return (
            f"{phase_name} is moving toward the configured goal. "
            "Recent body-fat direction and activity are supportive."
        )

    if direction == "mixed":
        return (
            f"{phase_name} has mixed signals. "
            "Some behaviors or measurements are supportive, "
            "but progress is not yet consistently moving toward the goal."
        )

    if direction == "off_track":
        return (
            f"{phase_name} is not currently trending toward the "
            "configured goal based on the available body-composition "
            "and activity signals."
        )

    if (
        body_fat_direction
        == "insufficient_data"
        and activity_status
        == "on_track"
    ):
        return (
            f"Activity is supporting the {phase_name}, "
            "but more Hume body-composition history is needed "
            "before confirming the direction of progress."
        )

    return (
        f"More reliable data is needed before determining "
        f"whether the {phase_name} is moving toward or away from the goal."
    )


def goal_progress(
    goal=None,
    trends=None,
    body_progress=None,
    strength=None,
):

    if goal is None:
        goal = get_active_goal()

    if not goal:
        return {
            "status":
                "no_active_goal",

            "message":
                (
                    "Create an active goal "
                    "before calculating progress."
                ),
        }

    if trends is None:
        trends = (
            apple_health_trends()
        )

    if body_progress is None:
        body_progress = body_composition_progress()

    body_composition = trends.get(
        "body_composition",
        {},
    )
    

    activity = trends.get(
        "activity",
        {},
    )

    weight = body_composition.get(
        "weight",
        {},
    )

    body_fat = body_composition.get(
        "body_fat_percentage",
        {},
    )

    current_weight_kg = (
        weight.get(
            "current_value"
        )
        if weight.get(
            "available"
        )
        else None
    )

    current_weight_lb = (
        float(
            current_weight_kg
        )
        * KG_TO_LB
        if current_weight_kg
        is not None
        else None
    )

    current_body_fat = (
        body_fat.get(
            "current_value"
        )
        if body_fat.get(
            "available"
        )
        else None
    )

    start_weight = goal.get(
        "phase_start_weight_lb"
    )

    start_body_fat = goal.get(
        "phase_start_body_fat_percentage"
    )

    target_weight = goal.get(
        "target_weight_lb"
    )

    target_body_fat = goal.get(
        "target_body_fat_percentage"
    )

    phase_age_days = (
        _phase_age_days(
            goal.get(
                "phase_start_date"
            )
        )
    )

    (
        weight_progress,
        raw_weight_progress,
    ) = _progress_percentage(
        start_weight,
        current_weight_lb,
        target_weight,
    )

    (
        body_fat_progress,
        raw_body_fat_progress,
    ) = _progress_percentage(
        start_body_fat,
        current_body_fat,
        target_body_fat,
    )

    body_fat_direction = (
        _body_fat_direction(
            body_fat
        )
    )

    activity_result = (
        _activity_status(
            activity,
            goal.get(
                "daily_step_target"
            ),
        )
    )

    direction = (
        _overall_direction(
            phase_age_days,
            body_fat_direction,
            activity_result[
                "status"
            ],
        )
    )

    if strength is None:
        strength = strength_adherence(
            goal.get(
                "strength_sessions_per_week"
            )
        )

    return {
        "status":
            "ok",

        "phase":
            goal.get(
                "phase"
            ),

        "direction":
            direction,

        "phase_start_date":
            goal.get(
                "phase_start_date"
            ),

        "phase_age_days":
            phase_age_days,

        "minimum_phase_age_days":
            MIN_PHASE_AGE_DAYS,

        "body_fat": {
            "start_percentage":
                _round(
                    start_body_fat,
                    2,
                ),

            "current_percentage":
                _round(
                    current_body_fat,
                    2,
                ),

            "target_percentage":
                _round(
                    target_body_fat,
                    2,
                ),

            "progress_percentage":
                body_fat_progress,

            "raw_progress_percentage":
                raw_body_fat_progress,

            "direction":
                body_fat_direction,
        },

        "weight": {
            "start_lb":
                _round(
                    start_weight,
                    1,
                ),

            "current_lb":
                _round(
                    current_weight_lb,
                    1,
                ),

            "target_lb":
                _round(
                    target_weight,
                    1,
                ),

            "progress_percentage":
                weight_progress,

            "raw_progress_percentage":
                raw_weight_progress,
        },

        "fat_mass":
            _derived_composition_goal_metric(
                body_progress,
                "fat_mass",
            ),

        "lean_mass":
            _derived_composition_goal_metric(
                body_progress,
                "lean_mass",
            ),

        "activity":
            activity_result,

        "strength":
            strength,

        "protein": {
            "status":
                "not_connected",

            "target_grams_per_day":
                goal.get(
                    "protein_target_grams"
                ),
        },

        "summary":
            _summary(
                goal.get(
                    "phase"
                ),
                direction,
                phase_age_days,
                body_fat_direction,
                activity_result[
                    "status"
                ],
            ),

        "data_notes": [
            (
                "The first 7 days of a new phase "
                "are treated as a baseline-building period."
            ),
            (
                "Body composition uses Hume-only measurements "
                "to reduce cross-device bias."
            ),
            (
                "Body-fat direction is based on personal trends, "
                "not population norms."
            ),
            (
                "Strength adherence uses distinct qualifying "
                "Tonal sessions from the latest 7 calendar days."
                if strength.get("status") not in (
                    "not_connected",
                    "not_configured",
                )
                else (
                    "Strength adherence is not connected to the "
                    "progress engine."
                    if strength.get("status") == "not_connected"
                    else "Strength adherence has no configured weekly goal."
                )
            ),
            (
                "Protein adherence is not yet connected to the "
                "progress engine."
            ),
        ],
    }
