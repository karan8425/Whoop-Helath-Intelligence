from goals import get_active_goal
from goal_progress import goal_progress
from apple_health_trends import apple_health_trends


KG_TO_LB = 2.2046226218

PROTEIN_KCAL_PER_GRAM = 4
CARB_KCAL_PER_GRAM = 4
FAT_KCAL_PER_GRAM = 9


# ============================================================
# CONFIGURATION
# ============================================================

PHASE_CONFIG = {
    "lean_cut": {
        "protein_target_g": 185,
        "protein_range_g": [180, 185],
        "carbohydrate_target_g": 150,
        "fat_target_g": 20,
    },
    "maintenance": None,
    "muscle_gain": None,
}


ACTIVITY_ADJUSTMENTS = {
    "below_target": -100,
    "close": 0,
    "on_track": 100,
    "insufficient_data": 0,
    "not_configured": 0,
}


# ============================================================
# HELPERS
# ============================================================

def _round_to_5(value):

    return int(
        5 * round(
            float(value) / 5
        )
    )


def _clamp(
    value,
    minimum,
    maximum,
):

    return max(
        minimum,
        min(
            maximum,
            value,
        ),
    )


def _current_weight_lb(
    trends,
):

    body = (
        trends.get(
            "body_composition"
        )
        or {}
    )

    weight = (
        body.get(
            "weight"
        )
        or {}
    )

    if not weight.get(
        "available"
    ):
        return None

    value = weight.get(
        "current_value"
    )

    if value is None:
        return None

    return (
        float(value)
        * KG_TO_LB
    )


def _activity_context(
    progress,
):

    activity = (
        progress.get(
            "activity"
        )
        or {}
    )

    return {
        "status":
            activity.get(
                "status",
                "insufficient_data",
            ),

        "average_steps_7d":
            activity.get(
                "average_steps_7d"
            ),

        "target_steps":
            activity.get(
                "target_steps"
            ),

        "percentage_of_target":
            activity.get(
                "percentage_of_target"
            ),
    }


def _protein_target(
    goal,
):

    configured = goal.get(
        "protein_target_grams"
    )

    if configured is not None:

        return _round_to_5(
            configured
        )

    return 180


def _fat_target(
    weight_lb,
    phase_config,
):

    calculated = (
        weight_lb
        * phase_config[
            "fat_grams_per_lb"
        ]
    )

    target = max(
        calculated,
        phase_config[
            "minimum_fat_grams"
        ],
    )

    return _round_to_5(
        target
    )


def _base_calories(
    weight_lb,
    phase_config,
):

    calculated = (
        weight_lb
        * phase_config[
            "calories_per_lb"
        ]
    )

    calculated = _clamp(
        calculated,
        phase_config[
            "minimum_calories"
        ],
        phase_config[
            "maximum_calories"
        ],
    )

    return _round_to_5(
        calculated
    )


def _activity_adjustment(
    activity_status,
):

    return ACTIVITY_ADJUSTMENTS.get(
        activity_status,
        0,
    )


def _progress_adjustment(
    phase,
    progress,
):

    phase_age = (
        progress.get(
            "phase_age_days"
        )
    )

    direction = (
        progress.get(
            "direction"
        )
    )

    # Do not modify nutrition during the initial
    # baseline-building period.
    if (
        phase_age is None
        or phase_age < 7
    ):
        return 0

    if phase == "lean_cut":

        if direction == "off_track":
            return -100

        if direction == "on_track":
            return 0

        return 0

    if phase == "lean_bulk":

        if direction == "off_track":
            return 100

        return 0

    return 0


def _macro_calculation(
    calories,
    protein_g,
    fat_g,
):

    protein_calories = (
        protein_g
        * PROTEIN_KCAL_PER_GRAM
    )

    fat_calories = (
        fat_g
        * FAT_KCAL_PER_GRAM
    )

    remaining = (
        calories
        - protein_calories
        - fat_calories
    )

    carb_g = max(
        0,
        remaining
        / CARB_KCAL_PER_GRAM,
    )

    carb_g = _round_to_5(
        carb_g
    )

    calculated_calories = (
        protein_g
        * PROTEIN_KCAL_PER_GRAM
        + fat_g
        * FAT_KCAL_PER_GRAM
        + carb_g
        * CARB_KCAL_PER_GRAM
    )

    return {
        "protein_g":
            protein_g,

        "fat_g":
            fat_g,

        "carbs_g":
            carb_g,

        "calculated_macro_calories":
            calculated_calories,
    }


# ============================================================
# NUTRITION PRIORITY
# ============================================================

def _nutrition_priority(
    phase,
    activity_status,
    progress_direction,
):

    if phase == "lean_cut":

        if activity_status == "below_target":

            return (
                "Prioritize protein and maintain the configured "
                "movement target rather than creating a larger "
                "calorie restriction."
            )

        if progress_direction == "off_track":

            return (
                "Keep protein high and tighten calorie consistency. "
                "Do not compensate with aggressive restriction."
            )

        return (
            "Maintain high protein, preserve training quality, "
            "and keep the calorie deficit gradual."
        )

    if phase == "lean_bulk":

        return (
            "Prioritize protein and sufficient carbohydrate intake "
            "to support progressive strength training."
        )

    return (
        "Maintain protein consistency and balance carbohydrate "
        "intake with training and activity."
    )


# ============================================================
# RATIONALE
# ============================================================

def _rationale(
    phase,
    weight_lb,
    protein_g,
    fat_g,
    activity,
    activity_adjustment,
    progress_adjustment,
):

    reasons = [
        (
            f"Nutrition prescription is calibrated for the "
            f"{phase.replace('_', ' ')} phase."
        ),

        (
            f"Current Hume weight used for calorie calibration "
            f"is approximately {weight_lb:.1f} lb."
        ),

        (
            f"Protein is anchored at {protein_g} g/day to support "
            f"lean-mass retention and strength training."
        ),

        (
            f"Fat is maintained at approximately {fat_g} g/day "
            f"rather than using an excessively low-fat target."
        ),
    ]

    average_steps = (
        activity.get(
            "average_steps_7d"
        )
    )

    target_steps = (
        activity.get(
            "target_steps"
        )
    )

    if (
        average_steps is not None
        and target_steps is not None
    ):

        reasons.append(
            (
                f"Recent activity averages approximately "
                f"{average_steps:,} steps/day versus the "
                f"{target_steps:,}-step target."
            )
        )

    if activity_adjustment:

        reasons.append(
            (
                f"Activity contributes a "
                f"{activity_adjustment:+d} kcal adjustment."
            )
        )

    if progress_adjustment:

        reasons.append(
            (
                f"Established goal progress contributes a "
                f"{progress_adjustment:+d} kcal adjustment."
            )
        )

    return reasons


# ============================================================
# PUBLIC PRESCRIPTION
# ============================================================

def build_nutrition_prescription(
    goal=None,
    trends=None,
    progress=None,
):

    if goal is None:
        goal = (
            get_active_goal()
        )

    if not goal:
        return {
            "status":
                "not_ready",

            "reason":
                "No active goal is configured.",
        }

    if trends is None:
        trends = (
            apple_health_trends()
        )

    if progress is None:
        progress = (
            goal_progress(
                goal=goal,
                trends=trends,
            )
        )

    phase = (
        goal.get(
            "phase"
        )
        or "maintenance"
    )

    phase_config = (
        PHASE_CONFIG.get(
            phase
        )
    )

    if not phase_config:
        return {
            "status": "not_ready",
            "phase": phase,
            "reason": "Nutrition targets are not configured for this phase.",
        }

    activity = (
        _activity_context(
            progress
        )
    )

    activity_status = (
        activity.get(
            "status"
        )
    )

    protein_g = phase_config["protein_target_g"]
    protein_range = list(phase_config["protein_range_g"])
    fat_g = phase_config["fat_target_g"]
    carb_g = phase_config["carbohydrate_target_g"]
    calorie_target = (
        protein_g * PROTEIN_KCAL_PER_GRAM
        + carb_g * CARB_KCAL_PER_GRAM
        + fat_g * FAT_KCAL_PER_GRAM
    )
    activity_delta = 0
    progress_delta = 0
    current_weight_lb = _current_weight_lb(trends)

    progress_direction = (
        progress.get(
            "direction"
        )
    )

    return {
        "status":
            "ok",

        "phase":
            phase,

        "calorie_target":
            calorie_target,

        "protein_target_g": protein_g,
        "protein_range_g": protein_range,
        "carbohydrate_target_g": carb_g,
        "fat_target_g": fat_g,

        "macro_targets": {
            "protein_g": protein_g,
            "protein_range_g": protein_range,
            "carbohydrate_g": carb_g,
            "fat_g": fat_g,
        },

        "macros": {
            "protein_g":
                protein_g,

            "fat_g":
                fat_g,

            "carbs_g":
                carb_g,
        },

        "macro_calorie_check":
            calorie_target,

        "current_weight_lb":
            (
                round(current_weight_lb, 1)
                if current_weight_lb is not None
                else None
            ),

        "intake_tracking": {
            "status": "not_connected",
            "adherence": "not_connected",
        },

        "adherence_status": "not_connected",

        "activity": {
            **activity,

            "calorie_adjustment":
                activity_delta,
        },

        "goal_progress": {
            "direction":
                progress_direction,

            "phase_age_days":
                progress.get(
                    "phase_age_days"
                ),

            "calorie_adjustment":
                progress_delta,
        },

        "nutrition_priority":
            _nutrition_priority(
                phase,
                activity_status,
                progress_direction,
            ),

        "rationale":
            [
                "Targets use the configured lean-cut prescription.",
                "Calories are derived directly from protein, carbohydrate, and fat targets.",
                "Recovery does not modify the user-defined macro targets.",
                "Nutrition intake is not connected, so adherence is not inferred.",
            ],

        "guardrails": [
            (
                "Calories are derived from the configured macros, "
                "not maintained as an independent target."
            ),

            (
                "Protein remains the primary macro priority "
                "during a lean cut."
            ),

            (
                "Recovery and daily activity do not silently alter "
                "the configured macro targets."
            ),

            (
                "Intake adherence remains not connected until a "
                "nutrition data source is integrated."
            ),
        ],
    }


# ============================================================
# LOCAL VALIDATION
# ============================================================

def main():

    result = (
        build_nutrition_prescription()
    )

    print()
    print(
        "NUTRITION PRESCRIPTION V1"
    )
    print(
        "=" * 78
    )

    print(
        f"Status: "
        f"{result.get('status')}"
    )

    if (
        result.get(
            "status"
        )
        != "ok"
    ):

        print(
            f"Reason: "
            f"{result.get('reason')}"
        )

        return

    print(
        f"Phase: "
        f"{result.get('phase')}"
    )

    print(
        f"Current weight: "
        f"{result.get('current_weight_lb')} lb"
    )

    print(
        f"Calories: "
        f"{result.get('calorie_target')} kcal"
    )

    macros = (
        result.get(
            "macros"
        )
        or {}
    )

    print(
        f"Protein: "
        f"{macros.get('protein_g')} g"
    )

    print(
        f"Carbs: "
        f"{macros.get('carbs_g')} g"
    )

    print(
        f"Fat: "
        f"{macros.get('fat_g')} g"
    )

    print(
        f"Macro calorie check: "
        f"{result.get('macro_calorie_check')} kcal"
    )

    print()
    print(
        "Priority:"
    )

    print(
        result.get(
            "nutrition_priority"
        )
    )

    print()
    print(
        "Rationale:"
    )

    for reason in (
        result.get(
            "rationale"
        )
        or []
    ):

        print(
            f"  • {reason}"
        )

    print(
        "=" * 78
    )


if __name__ == "__main__":
    main()
