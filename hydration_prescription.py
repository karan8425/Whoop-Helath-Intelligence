from goals import get_active_goal
from apple_health_trends import apple_health_trends
from integrations.tonal.workout_prescription import (
    build_daily_workout_prescription,
)


KG_TO_LB = 2.2046226218
ML_TO_FL_OZ = 0.0338140227

# Conservative daily hydration framework.
# Keep calculations internally in milliliters.
BASE_ML_PER_KG = 32.0

MIN_DAILY_ML = 2000
MAX_DAILY_ML = 4500

STRENGTH_SESSION_ML = {
    "Rest": 0,
    "Active Recovery": 250,
    "Moderate": 400,
    "Normal": 500,
    "Push": 650,
}


# ============================================================
# HELPERS
# ============================================================

def _round_to_50(value):

    return int(
        50 * round(
            float(value) / 50
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


def _ml_to_fl_oz(
    value,
):

    if value is None:
        return None

    return (
        float(value)
        * ML_TO_FL_OZ
    )


def _current_weight_kg(
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

    return float(
        value
    )


def _training_context():

    workout = (
        build_daily_workout_prescription()
    )

    if workout.get(
        "status"
    ) != "ok":

        return {
            "available":
                False,

            "training_category":
                None,

            "session_type":
                None,

            "total_sets":
                None,

            "exercise_count":
                None,

            "adjustment_ml":
                0,

            "adjustment_fl_oz":
                0,
        }

    readiness = (
        workout.get(
            "readiness"
        )
        or {}
    )

    session = (
        workout.get(
            "session"
        )
        or {}
    )

    category = (
        readiness.get(
            "training_category"
        )
    )

    adjustment_ml = (
        STRENGTH_SESSION_ML.get(
            category,
            0,
        )
    )

    adjustment_fl_oz = (
        _ml_to_fl_oz(
            adjustment_ml
        )
    )

    return {
        "available":
            True,

        "training_category":
            category,

        "session_type":
            session.get(
                "session_type"
            ),

        "total_sets":
            session.get(
                "total_sets"
            ),

        "exercise_count":
            session.get(
                "exercise_count"
            ),

        # Internal calculation value.
        "adjustment_ml":
            adjustment_ml,

        # User-facing/API value.
        "adjustment_fl_oz":
            round(
                adjustment_fl_oz,
                1,
            ),
    }


# ============================================================
# PUBLIC ENGINE
# ============================================================

def build_hydration_prescription():

    trends = (
        apple_health_trends()
    )

    weight_kg = (
        _current_weight_kg(
            trends
        )
    )

    if weight_kg is None:

        return {
            "status":
                "not_ready",

            "reason":
                (
                    "A current Hume body-weight "
                    "measurement is required."
                ),
        }

    training = (
        _training_context()
    )

    baseline_ml = (
        weight_kg
        * BASE_ML_PER_KG
    )

    baseline_ml = (
        _round_to_50(
            baseline_ml
        )
    )

    training_adjustment_ml = (
        training.get(
            "adjustment_ml",
            0,
        )
    )

    target_ml = (
        baseline_ml
        + training_adjustment_ml
    )

    target_ml = (
        _clamp(
            target_ml,
            MIN_DAILY_ML,
            MAX_DAILY_ML,
        )
    )

    target_ml = (
        _round_to_50(
            target_ml
        )
    )

    baseline_fl_oz = (
        _ml_to_fl_oz(
            baseline_ml
        )
    )

    training_adjustment_fl_oz = (
        _ml_to_fl_oz(
            training_adjustment_ml
        )
    )

    target_fl_oz = (
        _ml_to_fl_oz(
            target_ml
        )
    )

    weight_lb = (
        weight_kg
        * KG_TO_LB
    )

    rationale = [
        (
            "Baseline hydration is calibrated from "
            "current body weight."
        ),

        (
            f"Current Hume weight is approximately "
            f"{weight_lb:.1f} lb."
        ),
    ]

    if (
        training.get(
            "available"
        )
        and training_adjustment_ml > 0
    ):

        rationale.append(
            (
                f"Today's {training.get('training_category')} "
                f"{training.get('session_type')} session adds "
                f"approximately "
                f"{training_adjustment_fl_oz:.0f} fl oz "
                "to the baseline fluid target."
            )
        )

    else:

        rationale.append(
            (
                "No additional strength-training hydration "
                "adjustment is required today."
            )
        )

    return {
        "status":
            "ok",

        "version":
            "1.1",

        "current_weight_kg":
            round(
                weight_kg,
                1,
            ),

        "current_weight_lb":
            round(
                weight_lb,
                1,
            ),

        # ----------------------------------------------------
        # Internal metric values retained for calculations
        # ----------------------------------------------------

        "baseline_target_ml":
            baseline_ml,

        "training_adjustment_ml":
            training_adjustment_ml,

        "daily_target_ml":
            target_ml,

        # ----------------------------------------------------
        # User-facing US fluid-ounce values
        # ----------------------------------------------------

        "baseline_target_fl_oz":
            round(
                baseline_fl_oz,
                1,
            ),

        "training_adjustment_fl_oz":
            round(
                training_adjustment_fl_oz,
                1,
            ),

        "daily_target_fl_oz":
            round(
                target_fl_oz,
                1,
            ),

        "daily_target_display":
            (
                f"{target_fl_oz:.0f} fl oz"
            ),

        "training_context":
            training,

        "priority":
            (
                "Spread fluid intake across the day "
                "and arrive at training well hydrated."
            ),

        "rationale":
            rationale,

        "guardrails": [
            (
                "Hydration targets are approximate planning "
                "targets, not measurements of individual fluid loss."
            ),

            (
                "Recovery score alone does not determine "
                "daily hydration needs."
            ),

            (
                "Exercise adjustments are intentionally modest "
                "until sweat-loss data is available."
            ),

            (
                "Hot weather, prolonged exercise, illness, "
                "or unusually high sweat loss may require "
                "additional fluid and electrolyte planning."
            ),

            (
                "Medical conditions or medications affecting "
                "fluid or electrolyte balance should override "
                "this general wearable-based guidance."
            ),
        ],
    }


# ============================================================
# LOCAL TEST
# ============================================================

def main():

    result = (
        build_hydration_prescription()
    )

    print()
    print(
        "HYDRATION PRESCRIPTION V1.1"
    )
    print(
        "=" * 78
    )

    print(
        "Status:",
        result.get(
            "status"
        ),
    )

    if (
        result.get(
            "status"
        )
        != "ok"
    ):

        print(
            "Reason:",
            result.get(
                "reason"
            ),
        )

        return

    print(
        "Weight:",
        result.get(
            "current_weight_lb"
        ),
        "lb",
    )

    print(
        "Baseline hydration:",
        round(
            result.get(
                "baseline_target_fl_oz"
            )
        ),
        "fl oz",
    )

    print(
        "Training adjustment:",
        round(
            result.get(
                "training_adjustment_fl_oz"
            )
        ),
        "fl oz",
    )

    print(
        "Daily hydration target:",
        result.get(
            "daily_target_display"
        ),
    )

    training = (
        result.get(
            "training_context"
        )
        or {}
    )

    print(
        "Training category:",
        training.get(
            "training_category"
        ),
    )

    print(
        "Session:",
        training.get(
            "session_type"
        ),
    )

    print()
    print(
        "Priority:",
        result.get(
            "priority"
        ),
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