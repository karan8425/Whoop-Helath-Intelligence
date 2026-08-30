from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from db import get_conn
from goals import get_active_goal


EASTERN = ZoneInfo(
    "America/New_York"
)

HUME_BUNDLE_ID = (
    "com.elink.fittrackhealth"
)

FITDAYS_BUNDLE_ID = (
    "cn.fitdays.fitdays"
)

KG_TO_LB = 2.2046226218

CURRENT_SMOOTHING_DAYS = 7


HORIZONS = {
    "1W": 7,
    "4W": 28,
    "3M": 90,
    "6M": 180,
    "1Y": 365,
}


TREND_TOLERANCES = {
    "weight": 0.25,
    "body_fat_percentage": 0.30,
    "lean_mass": 0.25,
}


# ============================================================
# GENERIC HELPERS
# ============================================================

def _round(
    value,
    digits=1,
):
    if value is None:
        return None

    return round(
        float(value),
        digits,
    )


def _goal_progress(
    start,
    current,
    target,
):
    if (
        start is None
        or current is None
        or target is None
    ):
        return {
            "available": False,
            "raw_progress_percentage": None,
            "progress_percentage": None,
            "state": "insufficient_data",
        }

    start = float(start)
    current = float(current)
    target = float(target)

    denominator = (
        target - start
    )

    if abs(denominator) < 0.000001:
        return {
            "available": False,
            "raw_progress_percentage": None,
            "progress_percentage": None,
            "state": "invalid_goal_range",
        }

    raw_progress = (
        (current - start)
        / denominator
        * 100.0
    )

    clamped = max(
        0.0,
        min(
            100.0,
            raw_progress,
        ),
    )

    if raw_progress >= 100:
        state = "target_reached"

    elif raw_progress < 0:
        state = "regressing"

    else:
        state = "in_progress"

    return {
        "available": True,

        "raw_progress_percentage":
            _round(
                raw_progress,
                1,
            ),

        "progress_percentage":
            _round(
                clamped,
                1,
            ),

        "state":
            state,
    }


def _goal_direction(
    start,
    target,
):
    if (
        start is None
        or target is None
    ):
        return None

    if target < start:
        return "decrease"

    if target > start:
        return "increase"

    return "maintain"


def _trend_direction(
    change,
    tolerance,
):
    if change is None:
        return "insufficient_data"

    if change > tolerance:
        return "increasing"

    if change < -tolerance:
        return "decreasing"

    return "stable"


def _goal_status(
    measured_direction,
    goal_direction,
):
    if measured_direction == "insufficient_data":
        return "insufficient_data"

    if measured_direction == "stable":
        return "stable"

    if goal_direction == "decrease":

        if measured_direction == "decreasing":
            return "progressing"

        return "regressing"

    if goal_direction == "increase":

        if measured_direction == "increasing":
            return "progressing"

        return "regressing"

    if goal_direction == "maintain":
        return "stable"

    return "insufficient_data"


# ============================================================
# DATABASE
# ============================================================

def _load_daily_body_history():
    """
    One aggregated database query.

    Returns one daily-average row for each:
        source
        metric
        local calendar date

    Only weight and body-fat percentage are needed.
    Lean mass is derived in Python.
    """

    with get_conn() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    (
                        observed_at
                        AT TIME ZONE
                        'America/New_York'
                    )::date
                        AS measurement_date,

                    source_bundle_id,

                    metric_name,

                    AVG(value)
                        AS daily_value,

                    COUNT(*)
                        AS observations

                FROM apple_health_body_samples

                WHERE source_bundle_id IN (
                    %s,
                    %s
                )
                  AND metric_name IN (
                    'body_weight',
                    'body_fat_percentage'
                )

                GROUP BY
                    measurement_date,
                    source_bundle_id,
                    metric_name

                ORDER BY
                    measurement_date ASC,
                    source_bundle_id ASC,
                    metric_name ASC
                """,
                (
                    FITDAYS_BUNDLE_ID,
                    HUME_BUNDLE_ID,
                ),
            )

            return cur.fetchall()


# ============================================================
# HISTORY BUILDING
# ============================================================

def _build_source_history(
    rows,
    source_bundle_id,
):
    daily = {}

    for row in rows:

        if (
            row["source_bundle_id"]
            != source_bundle_id
        ):
            continue

        date = (
            row[
                "measurement_date"
            ]
        )

        if date not in daily:
            daily[date] = {}

        daily[date][
            row["metric_name"]
        ] = float(
            row["daily_value"]
        )

    weight = []
    body_fat = []
    lean_mass = []

    for date in sorted(
        daily.keys()
    ):

        values = daily[date]

        weight_kg = (
            values.get(
                "body_weight"
            )
        )

        body_fat_percentage = (
            values.get(
                "body_fat_percentage"
            )
        )

        if weight_kg is not None:

            weight.append(
                {
                    "date":
                        date.isoformat(),

                    "value":
                        _round(
                            weight_kg
                            * KG_TO_LB,
                            1,
                        ),
                }
            )

        if body_fat_percentage is not None:

            body_fat.append(
                {
                    "date":
                        date.isoformat(),

                    "value":
                        _round(
                            body_fat_percentage,
                            1,
                        ),
                }
            )

        if (
            weight_kg is not None
            and body_fat_percentage
            is not None
        ):

            fat_mass_kg = (
                weight_kg
                * body_fat_percentage
                / 100.0
            )

            lean_mass_kg = (
                weight_kg
                - fat_mass_kg
            )

            lean_mass.append(
                {
                    "date":
                        date.isoformat(),

                    "value":
                        _round(
                            lean_mass_kg
                            * KG_TO_LB,
                            1,
                        ),

                    "derived":
                        True,
                }
            )

    return {
        "weight": weight,
        "body_fat_percentage": body_fat,
        "lean_mass": lean_mass,
    }


# ============================================================
# SERIES HELPERS
# ============================================================

def _dated_series(
    series,
):
    result = []

    for item in series:

        try:
            date = datetime.strptime(
                item["date"],
                "%Y-%m-%d",
            ).date()

        except Exception:
            continue

        result.append(
            {
                **item,
                "_date": date,
            }
        )

    return result


def _average(
    values,
):
    if not values:
        return None

    return (
        sum(values)
        / len(values)
    )


def _average_between(
    series,
    start_date,
    end_date,
):
    values = [
        float(
            item["value"]
        )
        for item in series
        if (
            start_date
            <= item["_date"]
            <= end_date
        )
    ]

    return (
        _average(values),
        len(values),
    )


def _current_average(
    series,
    latest_date,
):
    start = (
        latest_date
        - timedelta(
            days=(
                CURRENT_SMOOTHING_DAYS
                - 1
            )
        )
    )

    return _average_between(
        series,
        start,
        latest_date,
    )


# ============================================================
# HORIZON ANALYTICS
# ============================================================

def _horizon_analysis(
    series,
    label,
    horizon_days,
    tolerance,
    goal_direction,
):
    if not series:
        return {
            "label": label,
            "days": horizon_days,
            "sufficient_data": False,
            "status": "insufficient_data",
        }

    dated = (
        _dated_series(
            series
        )
    )

    if not dated:
        return {
            "label": label,
            "days": horizon_days,
            "sufficient_data": False,
            "status": "insufficient_data",
        }

    latest_date = (
        dated[-1][
            "_date"
        ]
    )

    current_average, current_days = (
        _current_average(
            dated,
            latest_date,
        )
    )

    # --------------------------------------------------------
    # 1 WEEK
    #
    # Compare current 7-day mean with immediately preceding
    # 7-day mean.
    # --------------------------------------------------------

    if horizon_days == 7:

        reference_end = (
            latest_date
            - timedelta(
                days=7
            )
        )

        reference_start = (
            reference_end
            - timedelta(
                days=6
            )
        )

    # --------------------------------------------------------
    # LONGER HORIZONS
    #
    # Compare current 7-day mean with a 7-day window near
    # the beginning of the selected horizon.
    # --------------------------------------------------------

    else:

        reference_start = (
            latest_date
            - timedelta(
                days=(
                    horizon_days
                    - 1
                )
            )
        )

        reference_end = (
            reference_start
            + timedelta(
                days=6
            )
        )

    reference_average, reference_days = (
        _average_between(
            dated,
            reference_start,
            reference_end,
        )
    )

    sufficient = (
        current_average is not None
        and reference_average is not None
        and current_days >= 2
        and reference_days >= 2
    )

    if not sufficient:

        return {
            "label": label,

            "days":
                horizon_days,

            "sufficient_data":
                False,

            "current_average":
                _round(
                    current_average,
                    1,
                ),

            "reference_average":
                _round(
                    reference_average,
                    1,
                ),

            "current_measurement_days":
                current_days,

            "reference_measurement_days":
                reference_days,

            "measured_direction":
                "insufficient_data",

            "status":
                "insufficient_data",
        }

    change = (
        current_average
        - reference_average
    )

    percentage_change = (
        (
            change
            / reference_average
        )
        * 100.0
        if reference_average != 0
        else None
    )

    measured_direction = (
        _trend_direction(
            change,
            tolerance,
        )
    )

    status = (
        _goal_status(
            measured_direction,
            goal_direction,
        )
    )

    return {
        "label":
            label,

        "days":
            horizon_days,

        "sufficient_data":
            True,

        "current_average":
            _round(
                current_average,
                1,
            ),

        "reference_average":
            _round(
                reference_average,
                1,
            ),

        "change":
            _round(
                change,
                1,
            ),

        "percentage_change":
            _round(
                percentage_change,
                1,
            ),

        "current_measurement_days":
            current_days,

        "reference_measurement_days":
            reference_days,

        "measured_direction":
            measured_direction,

        "status":
            status,

        "reference_start_date":
            reference_start.isoformat(),

        "reference_end_date":
            reference_end.isoformat(),

        "current_end_date":
            latest_date.isoformat(),
    }


# ============================================================
# GOAL-HORIZON ANALYTICS
# ============================================================

def _goal_horizon(
    start,
    current,
    target,
):
    if (
        start is None
        or current is None
        or target is None
    ):
        return {
            "label": "Goal",
            "sufficient_data": False,
            "status": "insufficient_data",
        }

    goal_direction = (
        _goal_direction(
            start,
            target,
        )
    )

    change = (
        float(current)
        - float(start)
    )

    measured_direction = (
        _trend_direction(
            change,
            0.25,
        )
    )

    return {
        "label":
            "Goal",

        "sufficient_data":
            True,

        "phase_start_value":
            _round(
                start,
                1,
            ),

        "current_value":
            _round(
                current,
                1,
            ),

        "target_value":
            _round(
                target,
                1,
            ),

        "change_since_phase_start":
            _round(
                change,
                1,
            ),

        "measured_direction":
            measured_direction,

        "status":
            _goal_status(
                measured_direction,
                goal_direction,
            ),
    }


# ============================================================
# METRIC PAYLOAD
# ============================================================

def _metric_payload(
    key,
    display_name,
    unit,
    series,
    phase_start,
    target,
):
    dated = (
        _dated_series(
            series
        )
    )

    if not dated:

        return {
            "available": False,

            "metric":
                key,

            "display_name":
                display_name,

            "unit":
                unit,

            "reason":
                "No Hume measurements are available.",
        }

    latest = (
        dated[-1]
    )

    latest_date = (
        latest["_date"]
    )

    current_average, current_days = (
        _current_average(
            dated,
            latest_date,
        )
    )

    current_value = (
        current_average
        if current_average is not None
        else float(
            latest["value"]
        )
    )

    goal_direction = (
        _goal_direction(
            phase_start,
            target,
        )
    )

    tolerance = (
        TREND_TOLERANCES[
            key
        ]
    )

    horizons = {}

    for label, days in (
        HORIZONS.items()
    ):

        horizons[label] = (
            _horizon_analysis(
                series=series,
                label=label,
                horizon_days=days,
                tolerance=tolerance,
                goal_direction=
                    goal_direction,
            )
        )

    horizons["Goal"] = (
        _goal_horizon(
            phase_start,
            current_value,
            target,
        )
    )

    progress = (
        _goal_progress(
            phase_start,
            current_value,
            target,
        )
    )

    distance_to_target = None

    if (
        current_value is not None
        and target is not None
    ):

        distance_to_target = (
            abs(
                float(current_value)
                - float(target)
            )
        )

    return {
        "available":
            True,

        "metric":
            key,

        "display_name":
            display_name,

        "unit":
            unit,

        "latest_value":
            _round(
                latest["value"],
                1,
            ),

        "latest_date":
            latest[
                "date"
            ],

        "current_7d_average":
            _round(
                current_value,
                1,
            ),

        "current_7d_measurement_days":
            current_days,

        "phase_start_value":
            _round(
                phase_start,
                1,
            ),

        "target_value":
            _round(
                target,
                1,
            ),

        "distance_to_target":
            _round(
                distance_to_target,
                1,
            ),

        "goal_direction":
            goal_direction,

        "progress":
            progress,

        "horizons":
            horizons,

        "history":
            [
                {
                    "date":
                        item["date"],

                    "value":
                        _round(
                            item["value"],
                            1,
                        ),
                }
                for item in series
            ],
    }


# ============================================================
# PUBLIC API
# ============================================================

def body_composition_progress():
    """
    Lightweight body-composition analytics specifically for
    the mobile progress dashboard.

    Current scoring:
        Hume only

    Historical chart context:
        Hume + Fitdays kept separate

    No normalization is performed between sources.
    """

    rows = (
        _load_daily_body_history()
    )

    hume = (
        _build_source_history(
            rows,
            HUME_BUNDLE_ID,
        )
    )

    fitdays = (
        _build_source_history(
            rows,
            FITDAYS_BUNDLE_ID,
        )
    )

    active_goal = (
        get_active_goal()
    )

    if not active_goal:

        return {
            "status":
                "not_ready",

            "reason":
                "No active goal is configured.",
        }

    start_weight = (
        active_goal.get(
            "phase_start_weight_lb"
        )
    )

    start_body_fat = (
        active_goal.get(
            "phase_start_body_fat_percentage"
        )
    )

    target_weight = (
        active_goal.get(
            "target_weight_lb"
        )
    )

    target_body_fat = (
        active_goal.get(
            "target_body_fat_percentage"
        )
    )

    start_lean = None
    target_lean = None

    if (
        start_weight is not None
        and start_body_fat is not None
    ):

        start_lean = (
            float(start_weight)
            * (
                1.0
                - (
                    float(start_body_fat)
                    / 100.0
                )
            )
        )

    if (
        target_weight is not None
        and target_body_fat is not None
    ):

        target_lean = (
            float(target_weight)
            * (
                1.0
                - (
                    float(target_body_fat)
                    / 100.0
                )
            )
        )

    weight = (
        _metric_payload(
            key="weight",
            display_name="Weight",
            unit="lb",
            series=
                hume["weight"],
            phase_start=
                start_weight,
            target=
                target_weight,
        )
    )

    body_fat = (
        _metric_payload(
            key=
                "body_fat_percentage",

            display_name=
                "Body Fat",

            unit=
                "percent",

            series=
                hume[
                    "body_fat_percentage"
                ],

            phase_start=
                start_body_fat,

            target=
                target_body_fat,
        )
    )

    lean_mass = (
        _metric_payload(
            key=
                "lean_mass",

            display_name=
                "Lean Body Mass",

            unit=
                "lb",

            series=
                hume[
                    "lean_mass"
                ],

            phase_start=
                start_lean,

            target=
                target_lean,
        )
    )

    return {
        "status":
            "ok",

        "generated_at":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "phase": {
            "goal_id":
                active_goal.get(
                    "id"
                ),

            "phase":
                active_goal.get(
                    "phase"
                ),

            "phase_start_date":
                active_goal.get(
                    "phase_start_date"
                ),

            "phase_end_date":
                active_goal.get(
                    "phase_end_date"
                ),
        },

        "source_policy": {
            "current_scoring_source":
                "Hume",

            "historical_context_sources":
                [
                    "Fitdays",
                    "Hume",
                ],

            "sources_normalized":
                False,

            "tonal_weight_included":
                False,
        },

        "view_labels":
            [
                "1W",
                "4W",
                "3M",
                "6M",
                "1Y",
                "Goal",
            ],

        "metrics": {
            "weight":
                weight,

            "body_fat_percentage":
                body_fat,

            "lean_mass":
                lean_mass,
        },

        "historical_context": {
            "fitdays": {
                "weight":
                    fitdays[
                        "weight"
                    ],

                "body_fat_percentage":
                    fitdays[
                        "body_fat_percentage"
                    ],

                "lean_mass":
                    fitdays[
                        "lean_mass"
                    ],
            },

            "hume": {
                "weight":
                    hume[
                        "weight"
                    ],

                "body_fat_percentage":
                    hume[
                        "body_fat_percentage"
                    ],

                "lean_mass":
                    hume[
                        "lean_mass"
                    ],
            },
        },

        "methodology": {
            "current_value":
                (
                    "Current goal calculations use "
                    "the recent 7-day Hume daily-average "
                    "mean."
                ),

            "one_week":
                (
                    "The 1W view compares the current "
                    "7-day average with the immediately "
                    "preceding 7-day average."
                ),

            "longer_horizons":
                (
                    "4W, 3M, 6M and 1Y compare the "
                    "current 7-day average with a "
                    "7-day reference window near the "
                    "beginning of the selected horizon."
                ),

            "goal_progress":
                (
                    "Goal progress compares the phase-start "
                    "value with the current smoothed value "
                    "and target. Raw progress may be below "
                    "zero when the metric has moved farther "
                    "away from the goal."
                ),

            "lean_mass":
                (
                    "Lean body mass is derived from Hume "
                    "weight and body-fat percentage. The "
                    "lean-mass target is derived from target "
                    "weight and target body-fat percentage."
                ),
        },
    }


# ============================================================
# LOCAL VALIDATION
# ============================================================

def main():
    result = (
        body_composition_progress()
    )

    print()
    print(
        "BODY COMPOSITION PROGRESS"
    )
    print("=" * 78)

    print(
        "Status:",
        result.get(
            "status"
        ),
    )

    metrics = (
        result.get(
            "metrics"
        )
        or {}
    )

    for key in (
        "weight",
        "body_fat_percentage",
        "lean_mass",
    ):

        metric = (
            metrics.get(key)
            or {}
        )

        print()
        print(
            metric.get(
                "display_name",
                key,
            )
        )

        print(
            "Current:",
            metric.get(
                "current_7d_average"
            ),
        )

        print(
            "Start:",
            metric.get(
                "phase_start_value"
            ),
        )

        print(
            "Target:",
            metric.get(
                "target_value"
            ),
        )

        print(
            "Progress:",
            (
                metric.get(
                    "progress"
                )
                or {}
            ).get(
                "raw_progress_percentage"
            ),
        )

    print()
    print("=" * 78)


if __name__ == "__main__":
    main()