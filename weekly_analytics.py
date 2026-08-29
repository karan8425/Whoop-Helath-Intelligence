from datetime import date, timedelta

from db import get_conn


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


def _round(value, digits=2):
    if value is None:
        return None
    return round(float(value), digits)


def _pct_change(current, baseline):
    if (
        current is None
        or baseline is None
        or float(baseline) == 0
    ):
        return None

    return (
        (float(current) - float(baseline))
        / float(baseline)
        * 100.0
    )


def _absolute_change(current, baseline):
    if current is None or baseline is None:
        return None

    return float(current) - float(baseline)


def _period_bounds(end_date, days):
    start_date = end_date - timedelta(days=days - 1)
    return start_date, end_date


def _previous_period_bounds(current_start, days):
    end_date = current_start - timedelta(days=1)
    start_date = end_date - timedelta(days=days - 1)
    return start_date, end_date


def _aggregate_period(start_date, end_date):
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

                    COUNT(recovery_score)::int AS recovery_days,
                    AVG(recovery_score) AS recovery_score,

                    COUNT(hrv_rmssd_milli)::int AS hrv_days,
                    AVG(hrv_rmssd_milli) AS hrv_rmssd_milli,

                    COUNT(resting_heart_rate)::int AS resting_hr_days,
                    AVG(resting_heart_rate) AS resting_heart_rate,

                    COUNT(sleep_duration_hours)::int AS sleep_days,
                    AVG(sleep_duration_hours) AS sleep_duration_hours,

                    COUNT(sleep_performance_percentage)::int
                        AS sleep_performance_days,
                    AVG(sleep_performance_percentage)
                        AS sleep_performance_percentage,

                    COUNT(sleep_consistency_percentage)::int
                        AS sleep_consistency_days,
                    AVG(sleep_consistency_percentage)
                        AS sleep_consistency_percentage,

                    COUNT(sleep_efficiency_percentage)::int
                        AS sleep_efficiency_days,
                    AVG(sleep_efficiency_percentage)
                        AS sleep_efficiency_percentage,

                    COUNT(cycle_strain)::int AS cycle_strain_days,
                    AVG(cycle_strain) AS average_cycle_strain,
                    SUM(cycle_strain) AS total_cycle_strain,

                    COUNT(*) FILTER (
                        WHERE has_workout
                    )::int AS workout_days,

                    COALESCE(
                        SUM(workout_count),
                        0
                    )::int AS workout_count,

                    COALESCE(
                        SUM(workout_total_duration_hours),
                        0
                    ) AS workout_total_duration_hours,

                    COALESCE(
                        SUM(workout_total_strain),
                        0
                    ) AS workout_total_strain

                FROM whoop_daily_metrics
                WHERE metric_date BETWEEN %s AND %s
                """,
                (
                    start_date,
                    end_date,
                ),
            )

            row = cur.fetchone()

    if row is None:
        return None

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "calendar_days": row["calendar_days"],

        "recovery": {
            "average": _round(
                row["recovery_score"],
                1,
            ),
            "days": row["recovery_days"],
        },

        "hrv": {
            "average_ms": _round(
                row["hrv_rmssd_milli"],
                1,
            ),
            "days": row["hrv_days"],
        },

        "resting_heart_rate": {
            "average_bpm": _round(
                row["resting_heart_rate"],
                1,
            ),
            "days": row["resting_hr_days"],
        },

        "sleep": {
            "average_duration_hours": _round(
                row["sleep_duration_hours"],
                2,
            ),
            "duration_days": row["sleep_days"],

            "average_performance_percentage": _round(
                row[
                    "sleep_performance_percentage"
                ],
                1,
            ),
            "performance_days":
                row["sleep_performance_days"],

            "average_consistency_percentage": _round(
                row[
                    "sleep_consistency_percentage"
                ],
                1,
            ),
            "consistency_days":
                row["sleep_consistency_days"],

            "average_efficiency_percentage": _round(
                row[
                    "sleep_efficiency_percentage"
                ],
                1,
            ),
            "efficiency_days":
                row["sleep_efficiency_days"],
        },

        "training": {
            "workout_days":
                row["workout_days"],

            "workout_count":
                row["workout_count"],

            "total_duration_hours": _round(
                row[
                    "workout_total_duration_hours"
                ],
                2,
            ),

            "total_workout_strain": _round(
                row["workout_total_strain"],
                2,
            ),

            "average_cycle_strain": _round(
                row["average_cycle_strain"],
                2,
            ),

            "total_cycle_strain": _round(
                row["total_cycle_strain"],
                2,
            ),

            "cycle_strain_days":
                row["cycle_strain_days"],
        },
    }


def _metric_value(period, metric_name):
    mapping = {
        "recovery_score":
            period["recovery"]["average"],

        "hrv_rmssd_milli":
            period["hrv"]["average_ms"],

        "resting_heart_rate":
            period[
                "resting_heart_rate"
            ]["average_bpm"],

        "sleep_duration_hours":
            period[
                "sleep"
            ]["average_duration_hours"],

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

    return mapping.get(metric_name)


def _metric_observations(period, metric_name):
    mapping = {
        "recovery_score":
            period["recovery"]["days"],

        "hrv_rmssd_milli":
            period["hrv"]["days"],

        "resting_heart_rate":
            period[
                "resting_heart_rate"
            ]["days"],

        "sleep_duration_hours":
            period[
                "sleep"
            ]["duration_days"],

        "sleep_performance_percentage":
            period[
                "sleep"
            ]["performance_days"],

        "sleep_consistency_percentage":
            period[
                "sleep"
            ]["consistency_days"],
    }

    return mapping.get(
        metric_name,
        0,
    )


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

    if current is None or baseline is None:
        return "insufficient_data"

    config = PHYSIOLOGY_METRICS[
        metric_name
    ]

    direction = config["direction"]

    if "threshold_absolute" in config:

        change = _absolute_change(
            current,
            baseline,
        )

        threshold = config[
            "threshold_absolute"
        ]

        if direction == "higher":

            if change >= threshold:
                return "improving"

            if change <= -threshold:
                return "deteriorating"

        else:

            if change <= -threshold:
                return "improving"

            if change >= threshold:
                return "deteriorating"

        return "stable"

    pct_change = _pct_change(
        current,
        baseline,
    )

    if pct_change is None:
        return "insufficient_data"

    threshold = config[
        "threshold_pct"
    ]

    if direction == "higher":

        if pct_change >= threshold:
            return "improving"

        if pct_change <= -threshold:
            return "deteriorating"

    else:

        if pct_change <= -threshold:
            return "improving"

        if pct_change >= threshold:
            return "deteriorating"

    return "stable"


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
    and biggest constraint. It is not intended
    as a medical or physiological score.
    """

    if current is None or baseline is None:
        return None

    config = PHYSIOLOGY_METRICS[
        metric_name
    ]

    direction = config["direction"]

    if "threshold_absolute" in config:

        threshold = config[
            "threshold_absolute"
        ]

        if threshold == 0:
            return None

        raw = (
            float(current)
            - float(baseline)
        ) / threshold

    else:

        pct_change = _pct_change(
            current,
            baseline,
        )

        if pct_change is None:
            return None

        threshold = config[
            "threshold_pct"
        ]

        if threshold == 0:
            return None

        raw = (
            pct_change
            / threshold
        )

    if direction == "lower":
        raw *= -1

    return raw


def _comparison_record(
    metric_name,
    current_period,
    previous_period,
    baseline_30,
    baseline_90,
):
    config = PHYSIOLOGY_METRICS[
        metric_name
    ]

    current = _metric_value(
        current_period,
        metric_name,
    )

    previous = _metric_value(
        previous_period,
        metric_name,
    )

    thirty = _metric_value(
        baseline_30,
        metric_name,
    )

    ninety = _metric_value(
        baseline_90,
        metric_name,
    )

    observations = _metric_observations(
        current_period,
        metric_name,
    )

    status = _classify_metric(
        metric_name,
        current,
        thirty,
    )

    return {
        "metric_name":
            metric_name,

        "label":
            config["label"],

        "unit":
            config["unit"],

        "direction":
            config["direction"],

        "current_7_day_average":
            current,

        "current_observations":
            observations,

        "current_coverage_percentage":
            round(
                (
                    observations
                    / 7
                    * 100.0
                ),
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


def _overall_trajectory(comparisons):
    usable = [
        x
        for x in comparisons
        if x["status"]
        != "insufficient_data"
    ]

    improving = sum(
        1
        for x in usable
        if x["status"]
        == "improving"
    )

    deteriorating = sum(
        1
        for x in usable
        if x["status"]
        == "deteriorating"
    )

    stable = sum(
        1
        for x in usable
        if x["status"]
        == "stable"
    )

    if (
        improving >= 2
        and deteriorating >= 2
    ):
        trajectory = "mixed"

    elif (
        improving >= 2
        and improving > deteriorating
    ):
        trajectory = "improving"

    elif (
        deteriorating >= 2
        and deteriorating > improving
    ):
        trajectory = "deteriorating"

    else:
        trajectory = "stable"

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
            len(usable),
    }


def _select_key_signals(comparisons):
    usable = [
        x
        for x in comparisons
        if x.get("_severity")
        is not None
    ]

    positive = [
        x
        for x in usable
        if x["_severity"] > 0
    ]

    negative = [
        x
        for x in usable
        if x["_severity"] < 0
    ]

    strongest_positive = None
    biggest_constraint = None

    if positive:

        best = max(
            positive,
            key=lambda x:
                x["_severity"],
        )

        strongest_positive = {
            "metric":
                best["metric_name"],

            "label":
                best["label"],

            "status":
                best["status"],

            "current":
                best[
                    "current_7_day_average"
                ],

            "baseline_30":
                best["baseline_30"],

            "pct_vs_30":
                best["pct_vs_30"],
        }

    if negative:

        worst = min(
            negative,
            key=lambda x:
                x["_severity"],
        )

        biggest_constraint = {
            "metric":
                worst["metric_name"],

            "label":
                worst["label"],

            "status":
                worst["status"],

            "current":
                worst[
                    "current_7_day_average"
                ],

            "baseline_30":
                worst["baseline_30"],

            "pct_vs_30":
                worst["pct_vs_30"],
        }

    return {
        "strongest_positive_signal":
            strongest_positive,

        "biggest_constraint":
            biggest_constraint,
    }


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
                if not key.startswith("_")
            }
        )

    return clean


def weekly_health_summary(
    end_date=None,
):
    """
    Build deterministic rolling weekly analytics.

    end_date:
        Optional datetime.date.

        If omitted, the latest metric_date in
        whoop_daily_metrics is used.
    """

    with get_conn() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT MAX(metric_date)
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
                "No WHOOP daily metrics are available.",
        }


    if end_date is None:

        end_date = latest_date

    elif isinstance(
        end_date,
        str,
    ):

        end_date = date.fromisoformat(
            end_date
        )


    if end_date > latest_date:

        end_date = latest_date


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


    current_period = _aggregate_period(
        current_start,
        current_end,
    )

    previous_period = _aggregate_period(
        previous_start,
        previous_end,
    )

    baseline_30 = _aggregate_period(
        baseline_30_start,
        baseline_30_end,
    )

    baseline_90 = _aggregate_period(
        baseline_90_start,
        baseline_90_end,
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


    trajectory = _overall_trajectory(
        comparisons
    )

    key_signals = _select_key_signals(
        comparisons
    )


    # -------------------------------------------------------------
    # Coverage
    # -------------------------------------------------------------

    recovery_days = (
        current_period[
            "recovery"
        ]["days"]
    )

    sleep_days = (
        current_period[
            "sleep"
        ]["duration_days"]
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
        },

        "coverage":
            coverage,

        "trajectory":
            trajectory,

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