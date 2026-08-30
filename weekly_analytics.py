from datetime import date, timedelta

from db import get_conn

from goals import (
    get_active_goal,
)

from apple_health_trends import (
    apple_health_trends,
)


# ---------------------------------------------------------------------
# Weekly Health Intelligence
# ---------------------------------------------------------------------
#
# Statistical design:
#
# Current period:
#     Rolling 7 calendar days ending on the latest metric_date.
#
# Previous period:
#     The 7 calendar days immediately before the current period.
#
# 30-day baseline:
#     The 30 calendar days immediately before the current period.
#
# 90-day baseline:
#     The 90 calendar days immediately before the current period.
#
# This deliberately prevents the current week from contaminating its
# own comparison baseline.
#
# Missing physiological measurements are excluded from averages.
# Genuine workout-free days remain zero because workout_count is stored
# as zero in whoop_daily_metrics.
#
# Weekly overall status evaluates separate dimensions:
#
#     1. Physiology / recovery trajectory
#     2. Training-load context
#     3. Body-composition goal context
#
# A reduction in training load is not automatically classified as bad.
# It is context used to prevent improved recovery from being mistaken
# for improved overall fitness progress when the athlete simply trained
# materially less.
# ---------------------------------------------------------------------


PHYSIOLOGY_METRICS = {
    "recovery_score": {
        "label": "Recovery",
        "direction": "higher",
        "unit": "%",
        "threshold_pct": 5.0,
    },

    "hrv_rmssd_milli": {
        "label": "HRV",
        "direction": "higher",
        "unit": "ms",
        "threshold_pct": 5.0,
    },

    "resting_heart_rate": {
        "label": "Resting heart rate",
        "direction": "lower",
        "unit": "bpm",
        "threshold_pct": 3.0,
    },

    "sleep_duration_hours": {
        "label": "Sleep duration",
        "direction": "higher",
        "unit": "hours",
        "threshold_absolute": 0.25,
    },

    "sleep_performance_percentage": {
        "label": "Sleep performance",
        "direction": "higher",
        "unit": "%",
        "threshold_absolute": 5.0,
    },

    "sleep_consistency_percentage": {
        "label": "Sleep consistency",
        "direction": "higher",
        "unit": "%",
        "threshold_absolute": 5.0,
    },
}


TRAINING_CHANGE_THRESHOLD_PCT = 20.0

MIN_SLEEP_DURATION_HOURS_FOR_CONSTRAINT = 7.0


# ---------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------


def _round(
    value,
    digits=2,
):
    if value is None:
        return None

    return round(
        float(value),
        digits,
    )


def _pct_change(
    current,
    baseline,
):
    if (
        current is None
        or baseline is None
        or float(baseline) == 0
    ):
        return None

    return (
        (
            float(current)
            - float(baseline)
        )
        / float(baseline)
        * 100.0
    )


def _absolute_change(
    current,
    baseline,
):
    if (
        current is None
        or baseline is None
    ):
        return None

    return (
        float(current)
        - float(baseline)
    )


def _period_bounds(
    end_date,
    days,
):
    start_date = (
        end_date
        - timedelta(
            days=days - 1
        )
    )

    return (
        start_date,
        end_date,
    )


def _previous_period_bounds(
    current_start,
    days,
):
    end_date = (
        current_start
        - timedelta(
            days=1
        )
    )

    start_date = (
        end_date
        - timedelta(
            days=days - 1
        )
    )

    return (
        start_date,
        end_date,
    )


def _weekly_equivalent(
    total_value,
    calendar_days,
):
    """
    Convert a multi-day aggregate into a seven-day equivalent.

    Example:
        30-day workout count = 12

        weekly equivalent =
            12 / 30 * 7
    """

    if (
        total_value is None
        or not calendar_days
    ):
        return None

    return (
        float(total_value)
        / float(calendar_days)
        * 7.0
    )


# ---------------------------------------------------------------------
# Period aggregation
# ---------------------------------------------------------------------


def _aggregate_period(
    start_date,
    end_date,
):
    """
    Aggregate one calendar-date period.

    Physiological NULLs are excluded by PostgreSQL AVG().
    workout_count includes real zero-workout days.
    """

    with get_conn() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    COUNT(*)::int AS calendar_days,

                    COUNT(recovery_score)::int
                        AS recovery_days,

                    AVG(recovery_score)
                        AS recovery_score,

                    COUNT(hrv_rmssd_milli)::int
                        AS hrv_days,

                    AVG(hrv_rmssd_milli)
                        AS hrv_rmssd_milli,

                    COUNT(resting_heart_rate)::int
                        AS resting_hr_days,

                    AVG(resting_heart_rate)
                        AS resting_heart_rate,

                    COUNT(sleep_duration_hours)::int
                        AS sleep_days,

                    AVG(sleep_duration_hours)
                        AS sleep_duration_hours,

                    COUNT(
                        sleep_performance_percentage
                    )::int
                        AS sleep_performance_days,

                    AVG(
                        sleep_performance_percentage
                    )
                        AS sleep_performance_percentage,

                    COUNT(
                        sleep_consistency_percentage
                    )::int
                        AS sleep_consistency_days,

                    AVG(
                        sleep_consistency_percentage
                    )
                        AS sleep_consistency_percentage,

                    COUNT(
                        sleep_efficiency_percentage
                    )::int
                        AS sleep_efficiency_days,

                    AVG(
                        sleep_efficiency_percentage
                    )
                        AS sleep_efficiency_percentage,

                    COUNT(cycle_strain)::int
                        AS cycle_strain_days,

                    AVG(cycle_strain)
                        AS average_cycle_strain,

                    SUM(cycle_strain)
                        AS total_cycle_strain,

                    COUNT(*) FILTER (
                        WHERE has_workout
                    )::int
                        AS workout_days,

                    COALESCE(
                        SUM(workout_count),
                        0
                    )::int
                        AS workout_count,

                    COALESCE(
                        SUM(
                            workout_total_duration_hours
                        ),
                        0
                    )
                        AS workout_total_duration_hours,

                    COALESCE(
                        SUM(
                            workout_total_strain
                        ),
                        0
                    )
                        AS workout_total_strain

                FROM whoop_daily_metrics

                WHERE metric_date
                    BETWEEN %s AND %s
                """,
                (
                    start_date,
                    end_date,
                ),
            )

            row = (
                cur.fetchone()
            )

    if row is None:
        return None

    return {
        "start_date":
            start_date.isoformat(),

        "end_date":
            end_date.isoformat(),

        "calendar_days":
            row[
                "calendar_days"
            ],

        "recovery": {
            "average":
                _round(
                    row[
                        "recovery_score"
                    ],
                    1,
                ),

            "days":
                row[
                    "recovery_days"
                ],
        },

        "hrv": {
            "average_ms":
                _round(
                    row[
                        "hrv_rmssd_milli"
                    ],
                    1,
                ),

            "days":
                row[
                    "hrv_days"
                ],
        },

        "resting_heart_rate": {
            "average_bpm":
                _round(
                    row[
                        "resting_heart_rate"
                    ],
                    1,
                ),

            "days":
                row[
                    "resting_hr_days"
                ],
        },

        "sleep": {
            "average_duration_hours":
                _round(
                    row[
                        "sleep_duration_hours"
                    ],
                    2,
                ),

            "duration_days":
                row[
                    "sleep_days"
                ],

            "average_performance_percentage":
                _round(
                    row[
                        "sleep_performance_percentage"
                    ],
                    1,
                ),

            "performance_days":
                row[
                    "sleep_performance_days"
                ],

            "average_consistency_percentage":
                _round(
                    row[
                        "sleep_consistency_percentage"
                    ],
                    1,
                ),

            "consistency_days":
                row[
                    "sleep_consistency_days"
                ],

            "average_efficiency_percentage":
                _round(
                    row[
                        "sleep_efficiency_percentage"
                    ],
                    1,
                ),

            "efficiency_days":
                row[
                    "sleep_efficiency_days"
                ],
        },

        "training": {
            "workout_days":
                row[
                    "workout_days"
                ],

            "workout_count":
                row[
                    "workout_count"
                ],

            "total_duration_hours":
                _round(
                    row[
                        "workout_total_duration_hours"
                    ],
                    2,
                ),

            "total_workout_strain":
                _round(
                    row[
                        "workout_total_strain"
                    ],
                    2,
                ),

            "average_cycle_strain":
                _round(
                    row[
                        "average_cycle_strain"
                    ],
                    2,
                ),

            "total_cycle_strain":
                _round(
                    row[
                        "total_cycle_strain"
                    ],
                    2,
                ),

            "cycle_strain_days":
                row[
                    "cycle_strain_days"
                ],
        },
    }


# ---------------------------------------------------------------------
# Physiology metric access
# ---------------------------------------------------------------------


def _metric_value(
    period,
    metric_name,
):
    mapping = {
        "recovery_score":
            period[
                "recovery"
            ][
                "average"
            ],

        "hrv_rmssd_milli":
            period[
                "hrv"
            ][
                "average_ms"
            ],

        "resting_heart_rate":
            period[
                "resting_heart_rate"
            ][
                "average_bpm"
            ],

        "sleep_duration_hours":
            period[
                "sleep"
            ][
                "average_duration_hours"
            ],

        "sleep_performance_percentage":
            period[
                "sleep"
            ][
                "average_performance_percentage"
            ],

        "sleep_consistency_percentage":
            period[
                "sleep"
            ][
                "average_consistency_percentage"
            ],
    }

    return (
        mapping.get(
            metric_name
        )
    )


def _metric_observations(
    period,
    metric_name,
):
    mapping = {
        "recovery_score":
            period[
                "recovery"
            ][
                "days"
            ],

        "hrv_rmssd_milli":
            period[
                "hrv"
            ][
                "days"
            ],

        "resting_heart_rate":
            period[
                "resting_heart_rate"
            ][
                "days"
            ],

        "sleep_duration_hours":
            period[
                "sleep"
            ][
                "duration_days"
            ],

        "sleep_performance_percentage":
            period[
                "sleep"
            ][
                "performance_days"
            ],

        "sleep_consistency_percentage":
            period[
                "sleep"
            ][
                "consistency_days"
            ],
    }

    return (
        mapping.get(
            metric_name,
            0,
        )
    )


# ---------------------------------------------------------------------
# Physiology classification
# ---------------------------------------------------------------------


def _classify_metric(
    metric_name,
    current,
    baseline,
):
    """
    Return:

        improving
        stable
        deteriorating
        insufficient_data
    """

    if (
        current is None
        or baseline is None
    ):
        return (
            "insufficient_data"
        )

    config = (
        PHYSIOLOGY_METRICS[
            metric_name
        ]
    )

    direction = (
        config[
            "direction"
        ]
    )

    if (
        "threshold_absolute"
        in config
    ):

        change = (
            _absolute_change(
                current,
                baseline,
            )
        )

        threshold = (
            config[
                "threshold_absolute"
            ]
        )

        if direction == (
            "higher"
        ):

            if change >= threshold:
                return (
                    "improving"
                )

            if change <= -threshold:
                return (
                    "deteriorating"
                )

        else:

            if change <= -threshold:
                return (
                    "improving"
                )

            if change >= threshold:
                return (
                    "deteriorating"
                )

        return (
            "stable"
        )

    pct_change = (
        _pct_change(
            current,
            baseline,
        )
    )

    if pct_change is None:
        return (
            "insufficient_data"
        )

    threshold = (
        config[
            "threshold_pct"
        ]
    )

    if direction == (
        "higher"
    ):

        if pct_change >= threshold:
            return (
                "improving"
            )

        if pct_change <= -threshold:
            return (
                "deteriorating"
            )

    else:

        if pct_change <= -threshold:
            return (
                "improving"
            )

        if pct_change >= threshold:
            return (
                "deteriorating"
            )

    return (
        "stable"
    )


def _severity(
    metric_name,
    current,
    baseline,
):
    """
    Produce a normalized signed signal.

    Positive = favorable.
    Negative = unfavorable.

    Used only to select the strongest signal
    and biggest physiological constraint.
    """

    if (
        current is None
        or baseline is None
    ):
        return None

    config = (
        PHYSIOLOGY_METRICS[
            metric_name
        ]
    )

    direction = (
        config[
            "direction"
        ]
    )

    if (
        "threshold_absolute"
        in config
    ):

        threshold = (
            config[
                "threshold_absolute"
            ]
        )

        if threshold == 0:
            return None

        raw = (
            (
                float(current)
                - float(baseline)
            )
            / threshold
        )

    else:

        pct_change = (
            _pct_change(
                current,
                baseline,
            )
        )

        if pct_change is None:
            return None

        threshold = (
            config[
                "threshold_pct"
            ]
        )

        if threshold == 0:
            return None

        raw = (
            pct_change
            / threshold
        )

    if direction == (
        "lower"
    ):
        raw *= -1

    return raw


def _comparison_record(
    metric_name,
    current_period,
    previous_period,
    baseline_30,
    baseline_90,
):
    config = (
        PHYSIOLOGY_METRICS[
            metric_name
        ]
    )

    current = (
        _metric_value(
            current_period,
            metric_name,
        )
    )

    previous = (
        _metric_value(
            previous_period,
            metric_name,
        )
    )

    thirty = (
        _metric_value(
            baseline_30,
            metric_name,
        )
    )

    ninety = (
        _metric_value(
            baseline_90,
            metric_name,
        )
    )

    observations = (
        _metric_observations(
            current_period,
            metric_name,
        )
    )

    status = (
        _classify_metric(
            metric_name,
            current,
            thirty,
        )
    )

    return {
        "metric_name":
            metric_name,

        "label":
            config[
                "label"
            ],

        "unit":
            config[
                "unit"
            ],

        "direction":
            config[
                "direction"
            ],

        "current_7_day_average":
            current,

        "current_observations":
            observations,

        "current_coverage_percentage":
            round(
                observations
                / 7
                * 100.0,
                1,
            ),

        "previous_7_day_average":
            previous,

        "change_vs_previous_7":
            _round(
                _absolute_change(
                    current,
                    previous,
                ),
                2,
            ),

        "pct_vs_previous_7":
            _round(
                _pct_change(
                    current,
                    previous,
                ),
                1,
            ),

        "baseline_30":
            thirty,

        "change_vs_30":
            _round(
                _absolute_change(
                    current,
                    thirty,
                ),
                2,
            ),

        "pct_vs_30":
            _round(
                _pct_change(
                    current,
                    thirty,
                ),
                1,
            ),

        "baseline_90":
            ninety,

        "pct_vs_90":
            _round(
                _pct_change(
                    current,
                    ninety,
                ),
                1,
            ),

        "status":
            status,

        "_severity":
            _severity(
                metric_name,
                current,
                thirty,
            ),
    }


def _physiology_trajectory(
    comparisons,
):
    usable = [
        item
        for item in comparisons
        if item[
            "status"
        ]
        != "insufficient_data"
    ]

    improving = sum(
        1
        for item in usable
        if item[
            "status"
        ]
        == "improving"
    )

    deteriorating = sum(
        1
        for item in usable
        if item[
            "status"
        ]
        == "deteriorating"
    )

    stable = sum(
        1
        for item in usable
        if item[
            "status"
        ]
        == "stable"
    )

    if (
        improving >= 2
        and deteriorating >= 2
    ):
        trajectory = (
            "mixed"
        )

    elif (
        improving >= 2
        and improving > deteriorating
    ):
        trajectory = (
            "improving"
        )

    elif (
        deteriorating >= 2
        and deteriorating > improving
    ):
        trajectory = (
            "deteriorating"
        )

    else:
        trajectory = (
            "stable"
        )

    return {
        "trajectory":
            trajectory,

        "improving_signals":
            improving,

        "stable_signals":
            stable,

        "deteriorating_signals":
            deteriorating,

        "signals_evaluated":
            len(
                usable
            ),
    }


# ---------------------------------------------------------------------
# Training context
# ---------------------------------------------------------------------


def _training_metric_record(
    current,
    previous,
    baseline_30_weekly,
):
    return {
        "current":
            _round(
                current,
                2,
            ),

        "previous_7":
            _round(
                previous,
                2,
            ),

        "baseline_30_weekly_equivalent":
            _round(
                baseline_30_weekly,
                2,
            ),

        "pct_vs_previous_7":
            _round(
                _pct_change(
                    current,
                    previous,
                ),
                1,
            ),

        "pct_vs_30_weekly_equivalent":
            _round(
                _pct_change(
                    current,
                    baseline_30_weekly,
                ),
                1,
            ),
    }


def _training_context(
    current_period,
    previous_period,
    baseline_30,
):
    current = (
        current_period[
            "training"
        ]
    )

    previous = (
        previous_period[
            "training"
        ]
    )

    baseline = (
        baseline_30[
            "training"
        ]
    )

    baseline_days = (
        baseline_30[
            "calendar_days"
        ]
    )

    baseline_workout_count = (
        _weekly_equivalent(
            baseline[
                "workout_count"
            ],
            baseline_days,
        )
    )

    baseline_workout_days = (
        _weekly_equivalent(
            baseline[
                "workout_days"
            ],
            baseline_days,
        )
    )

    baseline_duration = (
        _weekly_equivalent(
            baseline[
                "total_duration_hours"
            ],
            baseline_days,
        )
    )

    baseline_workout_strain = (
        _weekly_equivalent(
            baseline[
                "total_workout_strain"
            ],
            baseline_days,
        )
    )

    baseline_cycle_strain = (
        _weekly_equivalent(
            baseline[
                "total_cycle_strain"
            ],
            baseline_days,
        )
    )

    workout_count = (
        _training_metric_record(
            current[
                "workout_count"
            ],
            previous[
                "workout_count"
            ],
            baseline_workout_count,
        )
    )

    workout_days = (
        _training_metric_record(
            current[
                "workout_days"
            ],
            previous[
                "workout_days"
            ],
            baseline_workout_days,
        )
    )

    duration = (
        _training_metric_record(
            current[
                "total_duration_hours"
            ],
            previous[
                "total_duration_hours"
            ],
            baseline_duration,
        )
    )

    workout_strain = (
        _training_metric_record(
            current[
                "total_workout_strain"
            ],
            previous[
                "total_workout_strain"
            ],
            baseline_workout_strain,
        )
    )

    cycle_strain = (
        _training_metric_record(
            current[
                "total_cycle_strain"
            ],
            previous[
                "total_cycle_strain"
            ],
            baseline_cycle_strain,
        )
    )

    classification_inputs = [
        workout_count[
            "pct_vs_30_weekly_equivalent"
        ],
        workout_days[
            "pct_vs_30_weekly_equivalent"
        ],
        duration[
            "pct_vs_30_weekly_equivalent"
        ],
        workout_strain[
            "pct_vs_30_weekly_equivalent"
        ],
    ]

    usable = [
        value
        for value
        in classification_inputs
        if value is not None
    ]

    lower_signals = sum(
        1
        for value in usable
        if value
        <= -TRAINING_CHANGE_THRESHOLD_PCT
    )

    higher_signals = sum(
        1
        for value in usable
        if value
        >= TRAINING_CHANGE_THRESHOLD_PCT
    )

    if not usable:
        load_status = (
            "insufficient_data"
        )

    elif lower_signals >= 2:
        load_status = (
            "materially_lower"
        )

    elif higher_signals >= 2:
        load_status = (
            "materially_higher"
        )

    else:
        load_status = (
            "similar"
        )

    return {
        "load_status":
            load_status,

        "classification_threshold_percentage":
            TRAINING_CHANGE_THRESHOLD_PCT,

        "lower_load_signals":
            lower_signals,

        "higher_load_signals":
            higher_signals,

        "signals_evaluated":
            len(
                usable
            ),

        "workout_count":
            workout_count,

        "workout_days":
            workout_days,

        "duration_hours":
            duration,

        "workout_strain":
            workout_strain,

        "cycle_strain":
            cycle_strain,

        "interpretation":
            (
                "Training load is contextual rather than "
                "automatically favorable or unfavorable. "
                "A materially lower week can improve "
                "recovery metrics while representing less "
                "training stimulus."
            ),
    }


# ---------------------------------------------------------------------
# Body-composition context
# ---------------------------------------------------------------------


def _body_composition_context():
    try:

        trends = (
            apple_health_trends()
        )

        progress = (
            trends.get(
                "body_composition_progress"
            )
            or {}
        )

        if not progress.get(
            "available"
        ):
            return {
                "available":
                    False,

                "status":
                    "insufficient_data",

                "reason":
                    progress.get(
                        "reason",
                        (
                            "Body-composition progress "
                            "is unavailable."
                        ),
                    ),
            }

        phase = (
            progress.get(
                "phase"
            )
            or {}
        )

        metrics = (
            progress.get(
                "metrics"
            )
            or {}
        )

        metric_statuses = {}

        for key in (
            "weight",
            "body_fat_percentage",
            "fat_mass",
            "lean_mass",
        ):

            metric = (
                metrics.get(
                    key
                )
                or {}
            )

            metric_statuses[
                key
            ] = (
                metric.get(
                    "goal_horizon",
                    {}
                ).get(
                    "goal_status"
                )
            )

        return {
            "available":
                True,

            "status":
                progress.get(
                    "overall_goal_status",
                    "insufficient_data",
                ),

            "phase_age_days":
                phase.get(
                    "phase_age_days"
                ),

            "minimum_status_age_days":
                phase.get(
                    "minimum_status_age_days"
                ),

            "status_mature":
                bool(
                    phase.get(
                        "status_mature"
                    )
                ),

            "metric_statuses":
                metric_statuses,

            "current_composition":
                progress.get(
                    "current_composition"
                ),

            "target_composition":
                progress.get(
                    "target_composition"
                ),

            "interpretation":
                (
                    "Body-composition status contributes "
                    "to the weekly conclusion only after "
                    "the active goal phase reaches its "
                    "minimum maturity period."
                ),
        }

    except Exception as exc:

        return {
            "available":
                False,

            "status":
                "insufficient_data",

            "reason":
                (
                    "Body-composition analytics "
                    "could not be loaded."
                ),

            "error_type":
                type(
                    exc
                ).__name__,
        }


# ---------------------------------------------------------------------
# Key signals / constraints
# ---------------------------------------------------------------------


def _select_key_signals(
    comparisons,
):
    usable = [
        item
        for item in comparisons
        if item.get(
            "_severity"
        )
        is not None
    ]

    positive = [
        item
        for item in usable
        if item[
            "_severity"
        ] > 0
    ]

    negative = [
        item
        for item in usable
        if item[
            "_severity"
        ] < 0
    ]

    strongest_positive = None
    biggest_constraint = None

    if positive:

        best = max(
            positive,
            key=lambda item:
                item[
                    "_severity"
                ],
        )

        strongest_positive = {
            "metric":
                best[
                    "metric_name"
                ],

            "label":
                best[
                    "label"
                ],

            "status":
                best[
                    "status"
                ],

            "current":
                best[
                    "current_7_day_average"
                ],

            "baseline_30":
                best[
                    "baseline_30"
                ],

            "pct_vs_30":
                best[
                    "pct_vs_30"
                ],

            "reason":
                "largest_positive_personal_baseline_deviation",
        }

    if negative:

        worst = min(
            negative,
            key=lambda item:
                item[
                    "_severity"
                ],
        )

        biggest_constraint = {
            "metric":
                worst[
                    "metric_name"
                ],

            "label":
                worst[
                    "label"
                ],

            "status":
                worst[
                    "status"
                ],

            "current":
                worst[
                    "current_7_day_average"
                ],

            "baseline_30":
                worst[
                    "baseline_30"
                ],

            "pct_vs_30":
                worst[
                    "pct_vs_30"
                ],

            "reason":
                "largest_negative_personal_baseline_deviation",
        }

    sleep_comparison = next(
        (
            item
            for item in comparisons
            if item[
                "metric_name"
            ]
            == "sleep_duration_hours"
        ),
        None,
    )

    if sleep_comparison:

        current_sleep = (
            sleep_comparison.get(
                "current_7_day_average"
            )
        )

        if (
            current_sleep is not None
            and float(
                current_sleep
            )
            < MIN_SLEEP_DURATION_HOURS_FOR_CONSTRAINT
        ):

            if (
                biggest_constraint is None
                or biggest_constraint[
                    "metric"
                ]
                == "sleep_duration_hours"
            ):

                biggest_constraint = {
                    "metric":
                        "sleep_duration_hours",

                    "label":
                        "Sleep duration",

                    "status":
                        sleep_comparison[
                            "status"
                        ],

                    "current":
                        current_sleep,

                    "baseline_30":
                        sleep_comparison[
                            "baseline_30"
                        ],

                    "pct_vs_30":
                        sleep_comparison[
                            "pct_vs_30"
                        ],

                    "reason":
                        "absolute_sleep_duration_constraint",

                    "heuristic_threshold_hours":
                        MIN_SLEEP_DURATION_HOURS_FOR_CONSTRAINT,
                }

    return {
        "strongest_positive_signal":
            strongest_positive,

        "biggest_constraint":
            biggest_constraint,
    }


# ---------------------------------------------------------------------
# Overall weekly interpretation
# ---------------------------------------------------------------------


def _overall_weekly_trajectory(
    physiology,
    training,
    body_composition,
    coverage,
):
    if not coverage[
        "sufficient_for_weekly_review"
    ]:
        return {
            "trajectory":
                "insufficient_data",

            "physiology":
                physiology[
                    "trajectory"
                ],

            "training_load":
                training[
                    "load_status"
                ],

            "body_composition":
                body_composition.get(
                    "status"
                ),

            "reason":
                (
                    "Insufficient WHOOP recovery or sleep "
                    "coverage for a reliable weekly review."
                ),
        }

    physiology_status = (
        physiology[
            "trajectory"
        ]
    )

    training_status = (
        training[
            "load_status"
        ]
    )

    body_status = (
        body_composition.get(
            "status",
            "insufficient_data",
        )
    )

    body_mature = (
        body_composition.get(
            "status_mature",
            False,
        )
    )

    body_conflict = (
        body_mature
        and body_status
        in (
            "regressing",
            "mixed",
        )
    )

    body_positive = (
        body_mature
        and body_status
        == "progressing"
    )

    materially_lower_training = (
        training_status
        == "materially_lower"
    )

    materially_higher_training = (
        training_status
        == "materially_higher"
    )

    if physiology_status == (
        "deteriorating"
    ):

        if body_positive:
            overall = (
                "mixed"
            )

        else:
            overall = (
                "deteriorating"
            )

    elif physiology_status == (
        "mixed"
    ):
        overall = (
            "mixed"
        )

    elif physiology_status == (
        "improving"
    ):

        if (
            materially_lower_training
            or body_conflict
        ):
            overall = (
                "mixed"
            )

        else:
            overall = (
                "improving"
            )

    else:

        if body_conflict:
            overall = (
                "mixed"
            )

        elif (
            body_positive
            and not materially_lower_training
        ):
            overall = (
                "improving"
            )

        elif materially_lower_training:
            overall = (
                "mixed"
            )

        else:
            overall = (
                "stable"
            )

    reasons = []

    if physiology_status == (
        "improving"
    ):
        reasons.append(
            "physiology_improving"
        )

    elif physiology_status == (
        "deteriorating"
    ):
        reasons.append(
            "physiology_deteriorating"
        )

    elif physiology_status == (
        "mixed"
    ):
        reasons.append(
            "physiology_mixed"
        )

    if materially_lower_training:
        reasons.append(
            "training_load_materially_lower"
        )

    elif materially_higher_training:
        reasons.append(
            "training_load_materially_higher"
        )

    if body_mature:

        if body_status == (
            "progressing"
        ):
            reasons.append(
                "body_composition_progressing"
            )

        elif body_status == (
            "regressing"
        ):
            reasons.append(
                "body_composition_regressing"
            )

        elif body_status == (
            "mixed"
        ):
            reasons.append(
                "body_composition_mixed"
            )

    else:
        reasons.append(
            "body_composition_goal_phase_immature"
        )

    return {
        "trajectory":
            overall,

        "physiology":
            physiology_status,

        "training_load":
            training_status,

        "body_composition":
            body_status,

        "body_composition_mature":
            body_mature,

        "reasons":
            reasons,
    }


# ---------------------------------------------------------------------
# Output cleanup
# ---------------------------------------------------------------------


def _remove_internal_fields(
    comparisons,
):
    clean = []

    for item in comparisons:

        clean.append(
            {
                key: value
                for key, value
                in item.items()
                if not key.startswith(
                    "_"
                )
            }
        )

    return clean


# ---------------------------------------------------------------------
# Public weekly analytics
# ---------------------------------------------------------------------


def weekly_health_summary(
    end_date=None,
):
    """
    Build deterministic rolling weekly analytics.

    end_date:
        Optional datetime.date or ISO date string.

        If omitted, the latest metric_date in
        whoop_daily_metrics is used.
    """

    with get_conn() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    MAX(metric_date)
                        AS latest_date

                FROM whoop_daily_metrics
                """
            )

            latest_date = (
                cur.fetchone()[
                    "latest_date"
                ]
            )

    if latest_date is None:

        return {
            "status":
                "no_data",

            "message":
                (
                    "No WHOOP daily metrics "
                    "are available."
                ),
        }

    if end_date is None:

        end_date = (
            latest_date
        )

    elif isinstance(
        end_date,
        str,
    ):

        end_date = (
            date.fromisoformat(
                end_date
            )
        )

    if end_date > latest_date:

        end_date = (
            latest_date
        )

    # -------------------------------------------------------------
    # Current rolling seven days
    # -------------------------------------------------------------

    current_start, current_end = (
        _period_bounds(
            end_date,
            7,
        )
    )

    # -------------------------------------------------------------
    # Immediately preceding seven days
    # -------------------------------------------------------------

    previous_start, previous_end = (
        _previous_period_bounds(
            current_start,
            7,
        )
    )

    # -------------------------------------------------------------
    # Baselines exclude the current seven days.
    # -------------------------------------------------------------

    baseline_30_start, baseline_30_end = (
        _previous_period_bounds(
            current_start,
            30,
        )
    )

    baseline_90_start, baseline_90_end = (
        _previous_period_bounds(
            current_start,
            90,
        )
    )

    current_period = (
        _aggregate_period(
            current_start,
            current_end,
        )
    )

    previous_period = (
        _aggregate_period(
            previous_start,
            previous_end,
        )
    )

    baseline_30 = (
        _aggregate_period(
            baseline_30_start,
            baseline_30_end,
        )
    )

    baseline_90 = (
        _aggregate_period(
            baseline_90_start,
            baseline_90_end,
        )
    )

    comparisons = []

    for metric_name in (
        PHYSIOLOGY_METRICS.keys()
    ):

        comparisons.append(
            _comparison_record(
                metric_name,
                current_period,
                previous_period,
                baseline_30,
                baseline_90,
            )
        )

    physiology_trajectory = (
        _physiology_trajectory(
            comparisons
        )
    )

    training_context = (
        _training_context(
            current_period,
            previous_period,
            baseline_30,
        )
    )

    body_composition_context = (
        _body_composition_context()
    )

    key_signals = (
        _select_key_signals(
            comparisons
        )
    )

    # -------------------------------------------------------------
    # Coverage
    # -------------------------------------------------------------

    recovery_days = (
        current_period[
            "recovery"
        ][
            "days"
        ]
    )

    sleep_days = (
        current_period[
            "sleep"
        ][
            "duration_days"
        ]
    )

    coverage = {
        "calendar_days":
            current_period[
                "calendar_days"
            ],

        "recovery_days":
            recovery_days,

        "recovery_percentage":
            round(
                recovery_days
                / 7
                * 100.0,
                1,
            ),

        "sleep_days":
            sleep_days,

        "sleep_percentage":
            round(
                sleep_days
                / 7
                * 100.0,
                1,
            ),

        "sufficient_for_weekly_review":
            (
                recovery_days >= 5
                and sleep_days >= 5
            ),
    }

    overall_trajectory = (
        _overall_weekly_trajectory(
            physiology_trajectory,
            training_context,
            body_composition_context,
            coverage,
        )
    )

    active_goal = (
        get_active_goal()
    )

    return {
        "status":
            "ok",

        "period_type":
            "rolling_7_day",

        "metric_date":
            end_date.isoformat(),

        "period": {
            "start_date":
                current_start.isoformat(),

            "end_date":
                current_end.isoformat(),
        },

        "comparison_period": {
            "start_date":
                previous_start.isoformat(),

            "end_date":
                previous_end.isoformat(),
        },

        "baseline_definition": {
            "baseline_30":
                (
                    "30 calendar days immediately "
                    "preceding the current 7-day period"
                ),

            "baseline_90":
                (
                    "90 calendar days immediately "
                    "preceding the current 7-day period"
                ),

            "current_period_excluded":
                True,

            "missing_physiology_treated_as_zero":
                False,

            "zero_workout_days_retained":
                True,

            "training_baseline_normalized_to_7_days":
                True,
        },

        "coverage":
            coverage,

        # New authoritative top-level weekly conclusion.
        "overall_trajectory":
            overall_trajectory,

        # Existing physiology-only result retained explicitly.
        "physiology_trajectory":
            physiology_trajectory,

        # Backward-compatible field.
        "trajectory":
            physiology_trajectory,

        "training_context":
            training_context,

        "body_composition_context":
            body_composition_context,

        "goal_context": {
            "active":
                bool(
                    active_goal
                ),

            "phase":
                (
                    active_goal.get(
                        "phase"
                    )
                    if active_goal
                    else None
                ),

            "strength_sessions_per_week":
                (
                    active_goal.get(
                        "strength_sessions_per_week"
                    )
                    if active_goal
                    else None
                ),

            "daily_step_target":
                (
                    active_goal.get(
                        "daily_step_target"
                    )
                    if active_goal
                    else None
                ),

            "protein_target_grams":
                (
                    active_goal.get(
                        "protein_target_grams"
                    )
                    if active_goal
                    else None
                ),
        },

        "key_signals":
            key_signals,

        "current_week":
            current_period,

        "previous_week":
            previous_period,

        "baseline_30":
            baseline_30,

        "baseline_90":
            baseline_90,

        "metric_comparisons":
            _remove_internal_fields(
                comparisons
            ),
    }