from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from db import get_conn

from goals import (
    get_active_goal,
)


EASTERN = ZoneInfo(
    "America/New_York"
)

LEGACY_WINDOWS = (
    7,
    14,
    30,
    90,
)

BODY_COMPOSITION_HORIZONS = (
    28,
    90,
    180,
    365,
)

CURRENT_SMOOTHING_DAYS = 7

MIN_GOAL_PHASE_AGE_DAYS = 7

HUME_BUNDLE_ID = (
    "com.elink.fittrackhealth"
)

FITDAYS_BUNDLE_ID = (
    "cn.fitdays.fitdays"
)

KG_TO_LB = 2.2046226218


TREND_TOLERANCES = {
    "body_weight": 0.25,
    "body_fat_percentage": 0.30,
    "lean_body_mass": 0.25,
    "body_fat_mass": 0.25,
}


# ---------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------

def _pct_change(
    current,
    baseline,
):
    if (
        current is None
        or baseline is None
        or baseline == 0
    ):
        return None

    return (
        (
            current - baseline
        )
        / baseline
        * 100.0
    )


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


def _iso(
    value,
):
    if value is None:
        return None

    return value.isoformat()


def _weekly_rate(
    change,
    horizon_days,
):
    """
    The trend compares:

        reference 7-day window
        versus
        current 7-day window

    Their approximate centers are separated by
    horizon_days - CURRENT_SMOOTHING_DAYS.
    """

    if change is None:
        return None

    effective_days = (
        horizon_days
        - CURRENT_SMOOTHING_DAYS
    )

    if effective_days <= 0:
        return None

    effective_weeks = (
        effective_days
        / 7.0
    )

    return (
        change
        / effective_weeks
    )


def _goal_progress_percentage(
    start,
    current,
    target,
):
    """
    Generic progress formula that works whether the target
    is above or below the phase-start value.

    raw_progress_percentage may be:
        < 0   = worse than phase start
        0-100 = progressing between start and target
        > 100 = target surpassed

    progress_percentage is clamped to 0-100 for UI bars.
    """

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

    start = float(
        start
    )

    current = float(
        current
    )

    target = float(
        target
    )

    denominator = (
        target - start
    )

    if abs(
        denominator
    ) < 0.000001:

        return {
            "available": False,
            "raw_progress_percentage": None,
            "progress_percentage": None,
            "state": "invalid_goal_range",
        }

    raw_progress = (
        (
            current - start
        )
        / denominator
        * 100.0
    )

    clamped_progress = max(
        0.0,
        min(
            100.0,
            raw_progress,
        ),
    )

    if raw_progress >= 100:
        state = (
            "target_reached"
        )

    elif raw_progress < 0:
        state = (
            "regressed_beyond_phase_start"
        )

    else:
        state = (
            "in_progress"
        )

    return {
        "available":
            True,

        "raw_progress_percentage":
            _round(
                raw_progress,
                1,
            ),

        "progress_percentage":
            _round(
                clamped_progress,
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

    start = float(
        start
    )

    target = float(
        target
    )

    if target > start:
        return (
            "increase"
        )

    if target < start:
        return (
            "decrease"
        )

    return (
        "maintain"
    )


def _goal_status_for_direction(
    measured_direction,
    goal_direction,
):
    """
    Measurement direction comes entirely from observed data.

    Goal direction is used only to interpret whether that
    observed movement is favorable.
    """

    if measured_direction in (
        None,
        "insufficient_data",
    ):
        return (
            "insufficient_data"
        )

    if measured_direction == (
        "stable"
    ):
        return (
            "stable"
        )

    if goal_direction == (
        "maintain"
    ):
        return (
            "regressing"
        )

    if goal_direction == (
        "decrease"
    ):
        if measured_direction == (
            "decreasing"
        ):
            return (
                "progressing"
            )

        return (
            "regressing"
        )

    if goal_direction == (
        "increase"
    ):
        if measured_direction == (
            "increasing"
        ):
            return (
                "progressing"
            )

        return (
            "regressing"
        )

    return (
        "insufficient_data"
    )


def _direction_from_change(
    metric_name,
    change,
):
    return (
        _trend_label(
            metric_name,
            change,
        )
    )


# ---------------------------------------------------------
# Activity
# ---------------------------------------------------------

def _activity_baselines():
    """
    Calculate activity averages over the preceding
    7 / 14 / 30 / 90 calendar days.

    Today's partial activity is NOT included.
    """

    today = (
        datetime.now(
            timezone.utc
        )
        .astimezone(
            EASTERN
        )
        .date()
    )

    result = {}

    with get_conn() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    activity_date,
                    steps,
                    active_energy_kcal,
                    resting_energy_kcal,
                    walking_running_distance_km
                FROM apple_health_daily_activity
                WHERE activity_date = %s
                """,
                (
                    today,
                ),
            )

            current = (
                cur.fetchone()
            )

            result["current"] = (
                dict(current)
                if current
                else None
            )

            baselines = {}

            for window in (
                LEGACY_WINDOWS
            ):

                cur.execute(
                    """
                    SELECT
                        AVG(steps)
                            AS steps,

                        AVG(active_energy_kcal)
                            AS active_energy_kcal,

                        AVG(resting_energy_kcal)
                            AS resting_energy_kcal,

                        AVG(
                            walking_running_distance_km
                        )
                            AS walking_running_distance_km,

                        COUNT(steps)
                            AS step_days

                    FROM apple_health_daily_activity

                    WHERE activity_date >=
                        %s - (
                            %s * INTERVAL '1 day'
                        )
                      AND activity_date < %s
                    """,
                    (
                        today,
                        window,
                        today,
                    ),
                )

                row = (
                    cur.fetchone()
                )

                baselines[
                    str(window)
                ] = {
                    "steps":
                        _round(
                            row[
                                "steps"
                            ],
                            0,
                        ),

                    "active_energy_kcal":
                        _round(
                            row[
                                "active_energy_kcal"
                            ]
                        ),

                    "resting_energy_kcal":
                        _round(
                            row[
                                "resting_energy_kcal"
                            ]
                        ),

                    "walking_running_distance_km":
                        _round(
                            row[
                                "walking_running_distance_km"
                            ]
                        ),

                    "days_available":
                        int(
                            row[
                                "step_days"
                            ]
                            or 0
                        ),

                    "coverage_percentage":
                        round(
                            (
                                (
                                    row[
                                        "step_days"
                                    ]
                                    or 0
                                )
                                / window
                            )
                            * 100.0,
                            1,
                        ),
                }

    if result[
        "current"
    ]:

        current_steps = (
            result[
                "current"
            ][
                "steps"
            ]
        )

        for window in (
            LEGACY_WINDOWS
        ):

            baseline_steps = (
                baselines[
                    str(window)
                ][
                    "steps"
                ]
            )

            baselines[
                str(window)
            ][
                "steps_pct_vs_baseline"
            ] = _round(
                _pct_change(
                    current_steps,
                    baseline_steps,
                ),
                1,
            )

    result[
        "baselines"
    ] = baselines

    return result


# ---------------------------------------------------------
# Hume current measurements
# ---------------------------------------------------------

def _latest_hume_sample(
    metric_name,
):
    with get_conn() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    value,
                    unit,
                    observed_at,
                    source_name,
                    source_bundle_id

                FROM apple_health_body_samples

                WHERE metric_name = %s
                  AND source_bundle_id = %s

                ORDER BY observed_at DESC

                LIMIT 1
                """,
                (
                    metric_name,
                    HUME_BUNDLE_ID,
                ),
            )

            return (
                cur.fetchone()
            )


def _legacy_hume_windows(
    metric_name,
    latest,
):
    """
    Preserve output currently consumed by goal_progress.py.
    """

    latest_time = (
        latest[
            "observed_at"
        ]
    )

    windows = {}

    with get_conn() as conn:
        with conn.cursor() as cur:

            for window in (
                LEGACY_WINDOWS
            ):

                cur.execute(
                    """
                    SELECT
                        AVG(value)
                            AS baseline,

                        COUNT(*)
                            AS observations,

                        MIN(observed_at)
                            AS oldest,

                        MAX(observed_at)
                            AS newest

                    FROM apple_health_body_samples

                    WHERE metric_name = %s
                      AND source_bundle_id = %s
                      AND observed_at >=
                          %s - (
                              %s
                              * INTERVAL '1 day'
                          )
                      AND observed_at < %s
                    """,
                    (
                        metric_name,
                        HUME_BUNDLE_ID,
                        latest_time,
                        window,
                        latest_time,
                    ),
                )

                row = (
                    cur.fetchone()
                )

                baseline = (
                    row[
                        "baseline"
                    ]
                )

                observations = int(
                    row[
                        "observations"
                    ]
                    or 0
                )

                windows[
                    str(window)
                ] = {
                    "baseline":
                        _round(
                            baseline
                        ),

                    "observations":
                        observations,

                    "pct_vs_baseline":
                        _round(
                            _pct_change(
                                latest[
                                    "value"
                                ],
                                baseline,
                            ),
                            1,
                        ),

                    "oldest":
                        _iso(
                            row[
                                "oldest"
                            ]
                        ),

                    "newest":
                        _iso(
                            row[
                                "newest"
                            ]
                        ),
                }

    return windows


# ---------------------------------------------------------
# Daily body-composition aggregation
# ---------------------------------------------------------

def _source_daily_history(
    metric_name,
    source_bundle_id,
    start_time=None,
    end_time=None,
):
    """
    Return one value per local calendar day for one source.

    Multiple measurements on the same local date are averaged.
    """

    conditions = [
        "metric_name = %s",
        "source_bundle_id = %s",
    ]

    parameters = [
        metric_name,
        source_bundle_id,
    ]

    if start_time is not None:
        conditions.append(
            "observed_at >= %s"
        )

        parameters.append(
            start_time
        )

    if end_time is not None:
        conditions.append(
            "observed_at <= %s"
        )

        parameters.append(
            end_time
        )

    where_clause = (
        " AND ".join(
            conditions
        )
    )

    query = f"""
        SELECT
            (
                observed_at
                AT TIME ZONE
                'America/New_York'
            )::date
                AS measurement_date,

            AVG(value)
                AS value,

            COUNT(*)
                AS observations,

            MIN(observed_at)
                AS first_observed_at,

            MAX(observed_at)
                AS last_observed_at

        FROM apple_health_body_samples

        WHERE {where_clause}

        GROUP BY
            measurement_date

        ORDER BY
            measurement_date ASC
    """

    with get_conn() as conn:
        with conn.cursor() as cur:

            cur.execute(
                query,
                tuple(
                    parameters
                ),
            )

            return (
                cur.fetchall()
            )


def _history_payload(
    rows,
    convert_to_lb=False,
):
    result = []

    for row in rows:

        value = float(
            row[
                "value"
            ]
        )

        item = {
            "date":
                row[
                    "measurement_date"
                ].isoformat(),

            "value":
                _round(
                    value
                ),

            "observations":
                int(
                    row[
                        "observations"
                    ]
                    or 0
                ),

            "first_observed_at":
                _iso(
                    row[
                        "first_observed_at"
                    ]
                ),

            "last_observed_at":
                _iso(
                    row[
                        "last_observed_at"
                    ]
                ),
        }

        if convert_to_lb:
            item[
                "value_lb"
            ] = _round(
                value
                * KG_TO_LB,
                1,
            )

        result.append(
            item
        )

    return result


def _hume_daily_history(
    metric_name,
    latest_time,
    history_days=365,
):
    rows = (
        _source_daily_history(
            metric_name,
            HUME_BUNDLE_ID,
            start_time=(
                latest_time
                - timedelta(
                    days=history_days
                )
            ),
            end_time=latest_time,
        )
    )

    return (
        _history_payload(
            rows,
            convert_to_lb=(
                metric_name
                in (
                    "body_weight",
                    "lean_body_mass",
                )
            ),
        )
    )


def _daily_average_in_window(
    metric_name,
    source_bundle_id,
    start_time,
    end_time,
):
    """
    Calculate an average of daily averages rather than every
    raw measurement.
    """

    with get_conn() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                WITH daily AS (
                    SELECT
                        (
                            observed_at
                            AT TIME ZONE
                            'America/New_York'
                        )::date
                            AS measurement_date,

                        AVG(value)
                            AS daily_value,

                        COUNT(*)
                            AS raw_observations,

                        MIN(observed_at)
                            AS first_observed_at,

                        MAX(observed_at)
                            AS last_observed_at

                    FROM apple_health_body_samples

                    WHERE metric_name = %s
                      AND source_bundle_id = %s
                      AND observed_at >= %s
                      AND observed_at < %s

                    GROUP BY
                        measurement_date
                )

                SELECT
                    AVG(daily_value)
                        AS average_value,

                    COUNT(*)
                        AS measurement_days,

                    COALESCE(
                        SUM(raw_observations),
                        0
                    )
                        AS raw_observations,

                    MIN(first_observed_at)
                        AS oldest,

                    MAX(last_observed_at)
                        AS newest

                FROM daily
                """,
                (
                    metric_name,
                    source_bundle_id,
                    start_time,
                    end_time,
                ),
            )

            return (
                cur.fetchone()
            )


# ---------------------------------------------------------
# Hume trend calculations
# ---------------------------------------------------------

def _trend_label(
    metric_name,
    change,
):
    """
    Raw measurement direction only.

    Goal interpretation happens later.
    """

    if change is None:
        return (
            "insufficient_data"
        )

    tolerance = (
        TREND_TOLERANCES.get(
            metric_name,
            0.0,
        )
    )

    if change > tolerance:
        return (
            "increasing"
        )

    if change < -tolerance:
        return (
            "decreasing"
        )

    return (
        "stable"
    )


def _trend_horizon(
    metric_name,
    latest_time,
    horizon_days,
):
    current_start = (
        latest_time
        - timedelta(
            days=CURRENT_SMOOTHING_DAYS
        )
    )

    current_end = (
        latest_time
        + timedelta(
            microseconds=1
        )
    )

    reference_start = (
        latest_time
        - timedelta(
            days=horizon_days
        )
    )

    reference_end = (
        reference_start
        + timedelta(
            days=CURRENT_SMOOTHING_DAYS
        )
    )

    current = (
        _daily_average_in_window(
            metric_name,
            HUME_BUNDLE_ID,
            current_start,
            current_end,
        )
    )

    reference = (
        _daily_average_in_window(
            metric_name,
            HUME_BUNDLE_ID,
            reference_start,
            reference_end,
        )
    )

    current_average = (
        current[
            "average_value"
        ]
        if current
        else None
    )

    reference_average = (
        reference[
            "average_value"
        ]
        if reference
        else None
    )

    current_days = int(
        (
            current[
                "measurement_days"
            ]
            if current
            else 0
        )
        or 0
    )

    reference_days = int(
        (
            reference[
                "measurement_days"
            ]
            if reference
            else 0
        )
        or 0
    )

    current_raw = int(
        (
            current[
                "raw_observations"
            ]
            if current
            else 0
        )
        or 0
    )

    reference_raw = int(
        (
            reference[
                "raw_observations"
            ]
            if reference
            else 0
        )
        or 0
    )

    sufficient_data = (
        current_average is not None
        and reference_average is not None
        and current_days >= 2
        and reference_days >= 2
    )

    if not sufficient_data:
        change = None
        pct_change = None
        rate_per_week = None

    else:
        change = (
            float(
                current_average
            )
            - float(
                reference_average
            )
        )

        pct_change = (
            _pct_change(
                float(
                    current_average
                ),
                float(
                    reference_average
                ),
            )
        )

        rate_per_week = (
            _weekly_rate(
                change,
                horizon_days,
            )
        )

    return {
        "horizon_days":
            horizon_days,

        "current_window_days":
            CURRENT_SMOOTHING_DAYS,

        "current_average":
            _round(
                current_average
            ),

        "current_measurement_days":
            current_days,

        "current_observations":
            current_raw,

        "current_oldest":
            _iso(
                current[
                    "oldest"
                ]
                if current
                else None
            ),

        "current_newest":
            _iso(
                current[
                    "newest"
                ]
                if current
                else None
            ),

        "reference_average":
            _round(
                reference_average
            ),

        "reference_measurement_days":
            reference_days,

        "reference_observations":
            reference_raw,

        "reference_oldest":
            _iso(
                reference[
                    "oldest"
                ]
                if reference
                else None
            ),

        "reference_newest":
            _iso(
                reference[
                    "newest"
                ]
                if reference
                else None
            ),

        "change":
            _round(
                change
            ),

        "pct_change":
            _round(
                pct_change,
                1,
            ),

        "rate_per_week":
            _round(
                rate_per_week
            ),

        "trend":
            (
                _trend_label(
                    metric_name,
                    change,
                )
                if sufficient_data
                else "insufficient_data"
            ),

        "sufficient_data":
            sufficient_data,
    }


def _hume_metric_trend(
    metric_name,
):
    latest = (
        _latest_hume_sample(
            metric_name
        )
    )

    if not latest:
        return {
            "available":
                False,

            "reason":
                (
                    "No Hume measurements "
                    "are available."
                ),
        }

    latest_time = (
        latest[
            "observed_at"
        ]
    )

    legacy_windows = (
        _legacy_hume_windows(
            metric_name,
            latest,
        )
    )

    current_7d = (
        _daily_average_in_window(
            metric_name,
            HUME_BUNDLE_ID,
            latest_time
            - timedelta(
                days=CURRENT_SMOOTHING_DAYS
            ),
            latest_time
            + timedelta(
                microseconds=1
            ),
        )
    )

    trend_horizons = {}

    for horizon in (
        BODY_COMPOSITION_HORIZONS
    ):
        trend_horizons[
            str(horizon)
        ] = _trend_horizon(
            metric_name,
            latest_time,
            horizon,
        )

    history = (
        _hume_daily_history(
            metric_name,
            latest_time,
            history_days=365,
        )
    )

    current_7d_value = (
        current_7d[
            "average_value"
        ]
        if current_7d
        else None
    )

    result = {
        "available":
            True,

        "current_value":
            _round(
                latest[
                    "value"
                ]
            ),

        "unit":
            latest[
                "unit"
            ],

        "observed_at":
            latest_time.isoformat(),

        "source_name":
            latest[
                "source_name"
            ],

        "source_bundle_id":
            latest[
                "source_bundle_id"
            ],

        "windows":
            legacy_windows,

        "current_7d_average":
            _round(
                current_7d_value
            ),

        "current_7d_measurement_days":
            int(
                (
                    current_7d[
                        "measurement_days"
                    ]
                    if current_7d
                    else 0
                )
                or 0
            ),

        "current_7d_observations":
            int(
                (
                    current_7d[
                        "raw_observations"
                    ]
                    if current_7d
                    else 0
                )
                or 0
            ),

        "trend_horizons":
            trend_horizons,

        "history":
            history,
    }

    if metric_name in (
        "body_weight",
        "lean_body_mass",
    ):

        result[
            "current_value_lb"
        ] = _round(
            float(
                latest[
                    "value"
                ]
            )
            * KG_TO_LB,
            1,
        )

        result[
            "current_7d_average_lb"
        ] = (
            _round(
                float(
                    current_7d_value
                )
                * KG_TO_LB,
                1,
            )
            if current_7d_value
            is not None
            else None
        )

    return result


# ---------------------------------------------------------
# Daily derived composition series
# ---------------------------------------------------------

def _derived_composition_rows(
    source_bundle_id,
):
    """
    Derive fat and lean mass from same-day daily source
    averages.

        fat  = weight × BF%
        lean = weight - fat
    """

    with get_conn() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                WITH weight_daily AS (
                    SELECT
                        (
                            observed_at
                            AT TIME ZONE
                            'America/New_York'
                        )::date
                            AS measurement_date,

                        AVG(value)
                            AS weight_kg,

                        MAX(observed_at)
                            AS observed_at

                    FROM apple_health_body_samples

                    WHERE metric_name =
                        'body_weight'
                      AND source_bundle_id = %s

                    GROUP BY
                        measurement_date
                ),

                body_fat_daily AS (
                    SELECT
                        (
                            observed_at
                            AT TIME ZONE
                            'America/New_York'
                        )::date
                            AS measurement_date,

                        AVG(value)
                            AS body_fat_percentage,

                        MAX(observed_at)
                            AS observed_at

                    FROM apple_health_body_samples

                    WHERE metric_name =
                        'body_fat_percentage'
                      AND source_bundle_id = %s

                    GROUP BY
                        measurement_date
                )

                SELECT
                    w.measurement_date,

                    w.weight_kg,

                    b.body_fat_percentage,

                    (
                        w.weight_kg
                        * b.body_fat_percentage
                        / 100.0
                    )
                        AS body_fat_mass_kg,

                    (
                        w.weight_kg
                        -
                        (
                            w.weight_kg
                            * b.body_fat_percentage
                            / 100.0
                        )
                    )
                        AS lean_body_mass_kg,

                    GREATEST(
                        w.observed_at,
                        b.observed_at
                    )
                        AS observed_at

                FROM weight_daily w

                INNER JOIN body_fat_daily b
                    ON b.measurement_date =
                        w.measurement_date

                ORDER BY
                    w.measurement_date ASC
                """,
                (
                    source_bundle_id,
                    source_bundle_id,
                ),
            )

            return (
                cur.fetchall()
            )


def _derived_metric_analytics(
    derived_metric_name,
):
    """
    Build Hume analytics for either:

        body_fat_mass
        lean_body_mass
    """

    rows = (
        _derived_composition_rows(
            HUME_BUNDLE_ID
        )
    )

    if not rows:
        return {
            "available":
                False,

            "reason":
                (
                    "Weight and body-fat percentage "
                    "do not overlap on any Hume "
                    "measurement dates."
                ),
        }

    if derived_metric_name == (
        "body_fat_mass"
    ):
        value_field = (
            "body_fat_mass_kg"
        )

        tolerance_metric = (
            "body_fat_mass"
        )

        derivation = (
            "Daily-average Hume body weight × "
            "daily-average Hume body-fat percentage"
        )

    elif derived_metric_name == (
        "lean_body_mass"
    ):
        value_field = (
            "lean_body_mass_kg"
        )

        tolerance_metric = (
            "lean_body_mass"
        )

        derivation = (
            "Daily-average Hume body weight − "
            "derived Hume body-fat mass"
        )

    else:
        raise ValueError(
            "Unsupported derived metric."
        )

    latest = (
        rows[
            -1
        ]
    )

    latest_date = (
        latest[
            "measurement_date"
        ]
    )

    latest_value = float(
        latest[
            value_field
        ]
    )

    def average_for_age_range(
        minimum_age,
        maximum_age,
    ):
        values = []

        for row in rows:

            age_days = (
                latest_date
                - row[
                    "measurement_date"
                ]
            ).days

            if (
                age_days >= minimum_age
                and age_days <= maximum_age
            ):
                values.append(
                    float(
                        row[
                            value_field
                        ]
                    )
                )

        if not values:
            return (
                None,
                0,
            )

        return (
            sum(
                values
            )
            / len(
                values
            ),
            len(
                values
            ),
        )

    current_7d_average, current_days = (
        average_for_age_range(
            0,
            CURRENT_SMOOTHING_DAYS - 1,
        )
    )

    trend_horizons = {}

    for horizon in (
        BODY_COMPOSITION_HORIZONS
    ):

        reference_average, reference_days = (
            average_for_age_range(
                horizon
                - CURRENT_SMOOTHING_DAYS,
                horizon - 1,
            )
        )

        sufficient_data = (
            current_7d_average is not None
            and reference_average is not None
            and current_days >= 2
            and reference_days >= 2
        )

        if not sufficient_data:
            change = None
            pct_change = None
            rate_per_week = None

        else:
            change = (
                current_7d_average
                - reference_average
            )

            pct_change = (
                _pct_change(
                    current_7d_average,
                    reference_average,
                )
            )

            rate_per_week = (
                _weekly_rate(
                    change,
                    horizon,
                )
            )

        trend_horizons[
            str(horizon)
        ] = {
            "horizon_days":
                horizon,

            "current_window_days":
                CURRENT_SMOOTHING_DAYS,

            "current_average":
                _round(
                    current_7d_average
                ),

            "current_average_lb":
                (
                    _round(
                        current_7d_average
                        * KG_TO_LB,
                        1,
                    )
                    if current_7d_average
                    is not None
                    else None
                ),

            "current_measurement_days":
                current_days,

            "current_observations":
                current_days,

            "reference_average":
                _round(
                    reference_average
                ),

            "reference_average_lb":
                (
                    _round(
                        reference_average
                        * KG_TO_LB,
                        1,
                    )
                    if reference_average
                    is not None
                    else None
                ),

            "reference_measurement_days":
                reference_days,

            "reference_observations":
                reference_days,

            "change":
                _round(
                    change
                ),

            "change_lb":
                (
                    _round(
                        change
                        * KG_TO_LB,
                        1,
                    )
                    if change
                    is not None
                    else None
                ),

            "pct_change":
                _round(
                    pct_change,
                    1,
                ),

            "rate_per_week":
                _round(
                    rate_per_week
                ),

            "rate_per_week_lb":
                (
                    _round(
                        rate_per_week
                        * KG_TO_LB,
                        2,
                    )
                    if rate_per_week
                    is not None
                    else None
                ),

            "trend":
                (
                    _trend_label(
                        tolerance_metric,
                        change,
                    )
                    if sufficient_data
                    else "insufficient_data"
                ),

            "sufficient_data":
                sufficient_data,
        }

    history_rows = [
        row
        for row in rows
        if (
            latest_date
            - row[
                "measurement_date"
            ]
        ).days <= 365
    ]

    return {
        "available":
            True,

        "derived":
            True,

        "derivation":
            derivation,

        "current_value":
            _round(
                latest_value
            ),

        "current_value_lb":
            _round(
                latest_value
                * KG_TO_LB,
                1,
            ),

        "unit":
            "kg",

        "observed_at":
            _iso(
                latest[
                    "observed_at"
                ]
            ),

        "source_name":
            "Derived from Hume",

        "source_bundle_id":
            HUME_BUNDLE_ID,

        "current_7d_average":
            _round(
                current_7d_average
            ),

        "current_7d_average_lb":
            (
                _round(
                    current_7d_average
                    * KG_TO_LB,
                    1,
                )
                if current_7d_average
                is not None
                else None
            ),

        "current_7d_measurement_days":
            current_days,

        "current_7d_observations":
            current_days,

        "trend_horizons":
            trend_horizons,

        "history": [
            {
                "date":
                    row[
                        "measurement_date"
                    ].isoformat(),

                "value":
                    _round(
                        row[
                            value_field
                        ]
                    ),

                "value_lb":
                    _round(
                        float(
                            row[
                                value_field
                            ]
                        )
                        * KG_TO_LB,
                        1,
                    ),

                "observed_at":
                    _iso(
                        row[
                            "observed_at"
                        ]
                    ),

                "derived":
                    True,
            }
            for row in history_rows
        ],
    }


def _derived_body_fat_mass():
    return (
        _derived_metric_analytics(
            "body_fat_mass"
        )
    )


def _derived_lean_body_mass():
    return (
        _derived_metric_analytics(
            "lean_body_mass"
        )
    )


# ---------------------------------------------------------
# Fitdays -> Hume source-transition calibration
# ---------------------------------------------------------

def _paired_source_days(
    metric_name,
):
    with get_conn() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                WITH daily AS (
                    SELECT
                        (
                            observed_at
                            AT TIME ZONE
                            'America/New_York'
                        )::date
                            AS measurement_date,

                        source_bundle_id,

                        AVG(value)
                            AS daily_value,

                        COUNT(*)
                            AS observations,

                        MIN(observed_at)
                            AS first_observed_at,

                        MAX(observed_at)
                            AS last_observed_at

                    FROM apple_health_body_samples

                    WHERE metric_name = %s
                      AND source_bundle_id IN (
                          %s,
                          %s
                      )

                    GROUP BY
                        measurement_date,
                        source_bundle_id
                ),

                fitdays AS (
                    SELECT
                        measurement_date,
                        daily_value,
                        observations,
                        first_observed_at,
                        last_observed_at

                    FROM daily

                    WHERE source_bundle_id = %s
                ),

                hume AS (
                    SELECT
                        measurement_date,
                        daily_value,
                        observations,
                        first_observed_at,
                        last_observed_at

                    FROM daily

                    WHERE source_bundle_id = %s
                )

                SELECT
                    f.measurement_date,

                    f.daily_value
                        AS fitdays_value,

                    h.daily_value
                        AS hume_value,

                    f.observations
                        AS fitdays_observations,

                    h.observations
                        AS hume_observations,

                    f.first_observed_at
                        AS fitdays_first_observed_at,

                    f.last_observed_at
                        AS fitdays_last_observed_at,

                    h.first_observed_at
                        AS hume_first_observed_at,

                    h.last_observed_at
                        AS hume_last_observed_at

                FROM fitdays f

                INNER JOIN hume h
                    ON h.measurement_date =
                        f.measurement_date

                ORDER BY
                    f.measurement_date ASC
                """,
                (
                    metric_name,
                    FITDAYS_BUNDLE_ID,
                    HUME_BUNDLE_ID,
                    FITDAYS_BUNDLE_ID,
                    HUME_BUNDLE_ID,
                ),
            )

            return (
                cur.fetchall()
            )


def _source_transition_metric(
    metric_name,
):
    rows = (
        _paired_source_days(
            metric_name
        )
    )

    if not rows:
        return {
            "available":
                False,

            "paired_days":
                0,

            "reason":
                (
                    "No same-day Fitdays and Hume "
                    "measurements are available."
                ),
        }

    paired = []

    signed_differences = []
    absolute_differences = []

    for row in rows:

        fitdays_value = float(
            row[
                "fitdays_value"
            ]
        )

        hume_value = float(
            row[
                "hume_value"
            ]
        )

        difference = (
            hume_value
            - fitdays_value
        )

        signed_differences.append(
            difference
        )

        absolute_differences.append(
            abs(
                difference
            )
        )

        pair = {
            "date":
                row[
                    "measurement_date"
                ].isoformat(),

            "fitdays_value":
                _round(
                    fitdays_value
                ),

            "hume_value":
                _round(
                    hume_value
                ),

            "hume_minus_fitdays":
                _round(
                    difference
                ),

            "fitdays_observations":
                int(
                    row[
                        "fitdays_observations"
                    ]
                    or 0
                ),

            "hume_observations":
                int(
                    row[
                        "hume_observations"
                    ]
                    or 0
                ),

            "fitdays_first_observed_at":
                _iso(
                    row[
                        "fitdays_first_observed_at"
                    ]
                ),

            "fitdays_last_observed_at":
                _iso(
                    row[
                        "fitdays_last_observed_at"
                    ]
                ),

            "hume_first_observed_at":
                _iso(
                    row[
                        "hume_first_observed_at"
                    ]
                ),

            "hume_last_observed_at":
                _iso(
                    row[
                        "hume_last_observed_at"
                    ]
                ),
        }

        if metric_name == (
            "body_weight"
        ):
            pair[
                "fitdays_value_lb"
            ] = _round(
                fitdays_value
                * KG_TO_LB,
                1,
            )

            pair[
                "hume_value_lb"
            ] = _round(
                hume_value
                * KG_TO_LB,
                1,
            )

            pair[
                "hume_minus_fitdays_lb"
            ] = _round(
                difference
                * KG_TO_LB,
                1,
            )

        paired.append(
            pair
        )

    mean_difference = (
        sum(
            signed_differences
        )
        / len(
            signed_differences
        )
    )

    mean_absolute_difference = (
        sum(
            absolute_differences
        )
        / len(
            absolute_differences
        )
    )

    result = {
        "available":
            True,

        "paired_days":
            len(
                rows
            ),

        "overlap_start":
            rows[
                0
            ][
                "measurement_date"
            ].isoformat(),

        "overlap_end":
            rows[
                -1
            ][
                "measurement_date"
            ].isoformat(),

        "average_hume_minus_fitdays":
            _round(
                mean_difference
            ),

        "average_absolute_difference":
            _round(
                mean_absolute_difference
            ),

        "minimum_hume_minus_fitdays":
            _round(
                min(
                    signed_differences
                )
            ),

        "maximum_hume_minus_fitdays":
            _round(
                max(
                    signed_differences
                )
            ),

        "pairs":
            paired,
    }

    if metric_name == (
        "body_weight"
    ):

        result[
            "unit"
        ] = "kg"

        result[
            "average_hume_minus_fitdays_lb"
        ] = _round(
            mean_difference
            * KG_TO_LB,
            1,
        )

        result[
            "average_absolute_difference_lb"
        ] = _round(
            mean_absolute_difference
            * KG_TO_LB,
            1,
        )

    elif metric_name == (
        "body_fat_percentage"
    ):

        result[
            "unit"
        ] = (
            "percentage_points"
        )

    return result


def _source_transition_derived_composition():
    with get_conn() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                WITH daily AS (
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
                            AS daily_value

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
                ),

                composed AS (
                    SELECT
                        measurement_date,
                        source_bundle_id,

                        MAX(
                            CASE
                                WHEN metric_name =
                                    'body_weight'
                                THEN daily_value
                            END
                        )
                            AS weight_kg,

                        MAX(
                            CASE
                                WHEN metric_name =
                                    'body_fat_percentage'
                                THEN daily_value
                            END
                        )
                            AS body_fat_percentage

                    FROM daily

                    GROUP BY
                        measurement_date,
                        source_bundle_id
                ),

                fitdays AS (
                    SELECT *
                    FROM composed
                    WHERE source_bundle_id = %s
                      AND weight_kg IS NOT NULL
                      AND body_fat_percentage IS NOT NULL
                ),

                hume AS (
                    SELECT *
                    FROM composed
                    WHERE source_bundle_id = %s
                      AND weight_kg IS NOT NULL
                      AND body_fat_percentage IS NOT NULL
                )

                SELECT
                    f.measurement_date,

                    f.weight_kg
                        AS fitdays_weight_kg,

                    f.body_fat_percentage
                        AS fitdays_body_fat_percentage,

                    h.weight_kg
                        AS hume_weight_kg,

                    h.body_fat_percentage
                        AS hume_body_fat_percentage

                FROM fitdays f

                INNER JOIN hume h
                    ON h.measurement_date =
                        f.measurement_date

                ORDER BY
                    f.measurement_date ASC
                """,
                (
                    FITDAYS_BUNDLE_ID,
                    HUME_BUNDLE_ID,
                    FITDAYS_BUNDLE_ID,
                    HUME_BUNDLE_ID,
                ),
            )

            rows = (
                cur.fetchall()
            )

    if not rows:
        return {
            "available":
                False,

            "paired_days":
                0,

            "reason":
                (
                    "No dates contain both weight and "
                    "body-fat measurements from both "
                    "Fitdays and Hume."
                ),
        }

    fat_differences = []
    lean_differences = []

    pairs = []

    for row in rows:

        fitdays_weight = float(
            row[
                "fitdays_weight_kg"
            ]
        )

        fitdays_bf = float(
            row[
                "fitdays_body_fat_percentage"
            ]
        )

        hume_weight = float(
            row[
                "hume_weight_kg"
            ]
        )

        hume_bf = float(
            row[
                "hume_body_fat_percentage"
            ]
        )

        fitdays_fat = (
            fitdays_weight
            * fitdays_bf
            / 100.0
        )

        hume_fat = (
            hume_weight
            * hume_bf
            / 100.0
        )

        fitdays_lean = (
            fitdays_weight
            - fitdays_fat
        )

        hume_lean = (
            hume_weight
            - hume_fat
        )

        fat_difference = (
            hume_fat
            - fitdays_fat
        )

        lean_difference = (
            hume_lean
            - fitdays_lean
        )

        fat_differences.append(
            fat_difference
        )

        lean_differences.append(
            lean_difference
        )

        pairs.append(
            {
                "date":
                    row[
                        "measurement_date"
                    ].isoformat(),

                "fitdays_fat_mass_lb":
                    _round(
                        fitdays_fat
                        * KG_TO_LB,
                        1,
                    ),

                "hume_fat_mass_lb":
                    _round(
                        hume_fat
                        * KG_TO_LB,
                        1,
                    ),

                "fat_mass_hume_minus_fitdays_lb":
                    _round(
                        fat_difference
                        * KG_TO_LB,
                        1,
                    ),

                "fitdays_derived_lean_mass_lb":
                    _round(
                        fitdays_lean
                        * KG_TO_LB,
                        1,
                    ),

                "hume_derived_lean_mass_lb":
                    _round(
                        hume_lean
                        * KG_TO_LB,
                        1,
                    ),

                "lean_mass_hume_minus_fitdays_lb":
                    _round(
                        lean_difference
                        * KG_TO_LB,
                        1,
                    ),
            }
        )

    average_fat_difference = (
        sum(
            fat_differences
        )
        / len(
            fat_differences
        )
    )

    average_lean_difference = (
        sum(
            lean_differences
        )
        / len(
            lean_differences
        )
    )

    return {
        "available":
            True,

        "derived":
            True,

        "paired_days":
            len(
                rows
            ),

        "overlap_start":
            rows[
                0
            ][
                "measurement_date"
            ].isoformat(),

        "overlap_end":
            rows[
                -1
            ][
                "measurement_date"
            ].isoformat(),

        "average_fat_mass_hume_minus_fitdays_lb":
            _round(
                average_fat_difference
                * KG_TO_LB,
                1,
            ),

        "average_lean_mass_hume_minus_fitdays_lb":
            _round(
                average_lean_difference
                * KG_TO_LB,
                1,
            ),

        "pairs":
            pairs,
    }


def _transition_assessment(
    weight,
    body_fat,
):
    weight_days = (
        weight.get(
            "paired_days",
            0,
        )
        if weight
        else 0
    )

    body_fat_days = (
        body_fat.get(
            "paired_days",
            0,
        )
        if body_fat
        else 0
    )

    paired_days = min(
        weight_days,
        body_fat_days,
    )

    if paired_days == 0:
        return {
            "classification":
                "no_overlap",

            "historical_fitdays_usable_for_context":
                True,

            "historical_fitdays_usable_for_direct_hume_trend":
                False,

            "automatic_normalization_recommended":
                False,

            "reason":
                (
                    "No same-day overlap is available, so "
                    "Fitdays can provide historical context "
                    "but should not be mathematically merged "
                    "with Hume trends."
                ),
        }

    if paired_days < 7:
        return {
            "classification":
                "limited_overlap",

            "historical_fitdays_usable_for_context":
                True,

            "historical_fitdays_usable_for_direct_hume_trend":
                False,

            "automatic_normalization_recommended":
                False,

            "reason":
                (
                    f"{paired_days} paired day(s) are available. "
                    "This is useful for diagnosing the scale "
                    "transition but is too little overlap for "
                    "automatic historical normalization."
                ),
        }

    return {
        "classification":
            "calibration_candidate",

        "historical_fitdays_usable_for_context":
            True,

        "historical_fitdays_usable_for_direct_hume_trend":
            False,

        "automatic_normalization_recommended":
            False,

        "reason":
            (
                "There is enough overlap to investigate "
                "cross-scale calibration, but normalization "
                "should only be enabled after reviewing bias "
                "and stability across the paired measurements."
            ),
    }


def _source_transition_analysis():
    weight = (
        _source_transition_metric(
            "body_weight"
        )
    )

    body_fat = (
        _source_transition_metric(
            "body_fat_percentage"
        )
    )

    derived = (
        _source_transition_derived_composition()
    )

    overlap_dates = []

    for result in (
        weight,
        body_fat,
    ):
        for pair in (
            result.get(
                "pairs",
                []
            )
        ):
            overlap_dates.append(
                pair[
                    "date"
                ]
            )

    return {
        "previous_source": {
            "name":
                "Fitdays",

            "bundle_id":
                FITDAYS_BUNDLE_ID,
        },

        "current_source": {
            "name":
                "Hume",

            "bundle_id":
                HUME_BUNDLE_ID,
        },

        "overlap_start":
            (
                min(
                    overlap_dates
                )
                if overlap_dates
                else None
            ),

        "overlap_end":
            (
                max(
                    overlap_dates
                )
                if overlap_dates
                else None
            ),

        "difference_definition":
            (
                "All signed differences are Hume minus Fitdays."
            ),

        "weight":
            weight,

        "body_fat_percentage":
            body_fat,

        "derived_composition":
            derived,

        "assessment":
            _transition_assessment(
                weight,
                body_fat,
            ),

        "coaching_policy":
            {
                "current_coaching_source":
                    "Hume only",

                "fitdays_changes_current_coaching":
                    False,

                "fitdays_changes_hume_trend_horizons":
                    False,

                "purpose":
                    (
                        "Fitdays is retained for historical "
                        "context and source-transition analysis."
                    ),
            },
    }


# ---------------------------------------------------------
# Source-aware long-term history
# ---------------------------------------------------------

def _source_history_context(
    metric_name,
):
    fitdays_rows = (
        _source_daily_history(
            metric_name,
            FITDAYS_BUNDLE_ID,
        )
    )

    hume_rows = (
        _source_daily_history(
            metric_name,
            HUME_BUNDLE_ID,
        )
    )

    convert_to_lb = (
        metric_name
        == "body_weight"
    )

    return {
        "fitdays": {
            "source_name":
                "Fitdays",

            "source_bundle_id":
                FITDAYS_BUNDLE_ID,

            "history":
                _history_payload(
                    fitdays_rows,
                    convert_to_lb=
                        convert_to_lb,
                ),
        },

        "hume": {
            "source_name":
                "Hume",

            "source_bundle_id":
                HUME_BUNDLE_ID,

            "history":
                _history_payload(
                    hume_rows,
                    convert_to_lb=
                        convert_to_lb,
                ),
        },
    }


def _derived_source_history(
    source_bundle_id,
):
    rows = (
        _derived_composition_rows(
            source_bundle_id
        )
    )

    return {
        "fat_mass": [
            {
                "date":
                    row[
                        "measurement_date"
                    ].isoformat(),

                "value_lb":
                    _round(
                        float(
                            row[
                                "body_fat_mass_kg"
                            ]
                        )
                        * KG_TO_LB,
                        1,
                    ),
            }
            for row in rows
        ],

        "lean_mass": [
            {
                "date":
                    row[
                        "measurement_date"
                    ].isoformat(),

                "value_lb":
                    _round(
                        float(
                            row[
                                "lean_body_mass_kg"
                            ]
                        )
                        * KG_TO_LB,
                        1,
                    ),
            }
            for row in rows
        ],
    }


def _history_views(
    active_goal,
):
    today = (
        datetime.now(
            timezone.utc
        )
        .astimezone(
            EASTERN
        )
        .date()
    )

    views = {
        "4W": {
            "days":
                28,

            "start_date":
                (
                    today
                    - timedelta(
                        days=27
                    )
                ).isoformat(),

            "end_date":
                today.isoformat(),
        },

        "3M": {
            "days":
                90,

            "start_date":
                (
                    today
                    - timedelta(
                        days=89
                    )
                ).isoformat(),

            "end_date":
                today.isoformat(),
        },

        "6M": {
            "days":
                180,

            "start_date":
                (
                    today
                    - timedelta(
                        days=179
                    )
                ).isoformat(),

            "end_date":
                today.isoformat(),
        },

        "1Y": {
            "days":
                365,

            "start_date":
                (
                    today
                    - timedelta(
                        days=364
                    )
                ).isoformat(),

            "end_date":
                today.isoformat(),
        },
    }

    if active_goal:
        views[
            "Goal"
        ] = {
            "days":
                None,

            "start_date":
                active_goal.get(
                    "phase_start_date"
                ),

            "end_date":
                today.isoformat(),
        }

    return views


def _source_aware_history(
    active_goal,
):
    fitdays_derived = (
        _derived_source_history(
            FITDAYS_BUNDLE_ID
        )
    )

    hume_derived = (
        _derived_source_history(
            HUME_BUNDLE_ID
        )
    )

    return {
        "policy": {
            "trend_scoring_source":
                "Hume only",

            "fitdays_role":
                (
                    "Historical context only. "
                    "Fitdays and Hume are not normalized "
                    "into one body-composition series."
                ),

            "tonal_weight_included":
                False,
        },

        "views":
            _history_views(
                active_goal
            ),

        "source_boundary": {
            "previous_source":
                "Fitdays",

            "current_source":
                "Hume",

            "transition_date":
                "2026-08-15",
        },

        "weight":
            _source_history_context(
                "body_weight"
            ),

        "body_fat_percentage":
            _source_history_context(
                "body_fat_percentage"
            ),

        "derived_fat_mass": {
            "fitdays": {
                "source_name":
                    "Derived from Fitdays",

                "source_bundle_id":
                    FITDAYS_BUNDLE_ID,

                "history":
                    fitdays_derived[
                        "fat_mass"
                    ],
            },

            "hume": {
                "source_name":
                    "Derived from Hume",

                "source_bundle_id":
                    HUME_BUNDLE_ID,

                "history":
                    hume_derived[
                        "fat_mass"
                    ],
            },
        },

        "derived_lean_mass": {
            "fitdays": {
                "source_name":
                    "Derived from Fitdays",

                "source_bundle_id":
                    FITDAYS_BUNDLE_ID,

                "history":
                    fitdays_derived[
                        "lean_mass"
                    ],
            },

            "hume": {
                "source_name":
                    "Derived from Hume",

                "source_bundle_id":
                    HUME_BUNDLE_ID,

                "history":
                    hume_derived[
                        "lean_mass"
                    ],
            },
        },
    }


# ---------------------------------------------------------
# Goal-aware trend interpretation
# ---------------------------------------------------------

def _goal_aware_horizons(
    metric_name,
    trend_horizons,
    goal_direction,
    convert_kg_to_lb=False,
):
    result = {}

    for horizon in (
        BODY_COMPOSITION_HORIZONS
    ):

        key = str(
            horizon
        )

        raw = (
            trend_horizons.get(
                key,
                {}
            )
        )

        change = raw.get(
            "change"
        )

        rate = raw.get(
            "rate_per_week"
        )

        measured_direction = (
            raw.get(
                "trend",
                "insufficient_data",
            )
        )

        item = {
            "horizon_days":
                horizon,

            "sufficient_data":
                bool(
                    raw.get(
                        "sufficient_data"
                    )
                ),

            "measured_direction":
                measured_direction,

            "goal_status":
                _goal_status_for_direction(
                    measured_direction,
                    goal_direction,
                ),

            "change":
                change,

            "rate_per_week":
                rate,

            "current_measurement_days":
                raw.get(
                    "current_measurement_days"
                ),

            "reference_measurement_days":
                raw.get(
                    "reference_measurement_days"
                ),
        }

        if convert_kg_to_lb:

            item[
                "change_lb"
            ] = (
                _round(
                    float(
                        change
                    )
                    * KG_TO_LB,
                    1,
                )
                if change
                is not None
                else None
            )

            item[
                "rate_per_week_lb"
            ] = (
                _round(
                    float(
                        rate
                    )
                    * KG_TO_LB,
                    2,
                )
                if rate
                is not None
                else None
            )

        result[
            key
        ] = item

    return result


def _goal_metric_payload(
    metric_name,
    display_name,
    unit,
    start,
    current,
    target,
    trend_horizons,
    trend_values_are_kg=False,
):
    progress = (
        _goal_progress_percentage(
            start,
            current,
            target,
        )
    )

    goal_direction = (
        _goal_direction(
            start,
            target,
        )
    )

    change_since_start = None
    direction_since_start = (
        "insufficient_data"
    )

    if (
        start is not None
        and current is not None
    ):
        change_since_start = (
            float(
                current
            )
            - float(
                start
            )
        )

        if unit == (
            "percent"
        ):
            tolerance = (
                TREND_TOLERANCES[
                    "body_fat_percentage"
                ]
            )

        else:
            tolerance = (
                TREND_TOLERANCES.get(
                    metric_name,
                    0.25,
                )
                * (
                    KG_TO_LB
                    if metric_name
                    in (
                        "body_weight",
                        "body_fat_mass",
                        "lean_body_mass",
                    )
                    else 1.0
                )
            )

        if change_since_start > tolerance:
            direction_since_start = (
                "increasing"
            )

        elif change_since_start < -tolerance:
            direction_since_start = (
                "decreasing"
            )

        else:
            direction_since_start = (
                "stable"
            )

    goal_horizon_status = (
        _goal_status_for_direction(
            direction_since_start,
            goal_direction,
        )
    )

    return {
        "metric":
            metric_name,

        "display_name":
            display_name,

        "unit":
            unit,

        "phase_start_value":
            _round(
                start,
                1,
            ),

        "current_7d_average":
            _round(
                current,
                1,
            ),

        "target_value":
            _round(
                target,
                1,
            ),

        "goal_direction":
            goal_direction,

        "distance_to_target":
            (
                _round(
                    abs(
                        float(
                            current
                        )
                        - float(
                            target
                        )
                    ),
                    1,
                )
                if (
                    current
                    is not None
                    and target
                    is not None
                )
                else None
            ),

        "progress":
            progress,

        "goal_horizon": {
            "change_since_phase_start":
                _round(
                    change_since_start,
                    1,
                ),

            "measured_direction":
                direction_since_start,

            "goal_status":
                goal_horizon_status,
        },

        "trend_horizons":
            _goal_aware_horizons(
                metric_name,
                trend_horizons,
                goal_direction,
                convert_kg_to_lb=
                    trend_values_are_kg,
            ),
    }


# ---------------------------------------------------------
# Body-composition progress
# ---------------------------------------------------------

def _body_composition_progress(
    weight,
    body_fat,
    body_fat_mass,
    derived_lean_mass,
):

    active_goal = (
        get_active_goal()
    )

    if not active_goal:
        return {
            "available":
                False,

            "reason":
                "No active goal profile exists.",
        }

    phase_start_date = (
        active_goal.get(
            "phase_start_date"
        )
    )

    today = (
        datetime.now(
            timezone.utc
        )
        .astimezone(
            EASTERN
        )
        .date()
    )

    phase_age_days = None

    if phase_start_date:

        if isinstance(
            phase_start_date,
            str,
        ):
            parsed_phase_start_date = (
                datetime.strptime(
                    phase_start_date,
                    "%Y-%m-%d",
                ).date()
            )

        else:
            parsed_phase_start_date = (
                phase_start_date
            )

        phase_age_days = max(
            0,
            (
                today
                - parsed_phase_start_date
            ).days,
        )

    phase_status_mature = (
        phase_age_days is not None
        and phase_age_days
        >= MIN_GOAL_PHASE_AGE_DAYS
    )

    start_weight_lb = (
        active_goal.get(
            "phase_start_weight_lb"
        )
    )

    start_body_fat = (
        active_goal.get(
            "phase_start_body_fat_percentage"
        )
    )

    target_weight_lb = (
        active_goal.get(
            "target_weight_lb"
        )
    )

    target_body_fat = (
        active_goal.get(
            "target_body_fat_percentage"
        )
    )

    current_weight_lb = (
        weight.get(
            "current_7d_average_lb"
        )
        if weight.get(
            "available"
        )
        else None
    )

    current_body_fat = (
        body_fat.get(
            "current_7d_average"
        )
        if body_fat.get(
            "available"
        )
        else None
    )

    current_fat_mass_lb = (
        body_fat_mass.get(
            "current_7d_average_lb"
        )
        if body_fat_mass.get(
            "available"
        )
        else None
    )

    current_lean_mass_lb = (
        derived_lean_mass.get(
            "current_7d_average_lb"
        )
        if derived_lean_mass.get(
            "available"
        )
        else None
    )

    start_fat_mass_lb = None
    start_lean_mass_lb = None

    if (
        start_weight_lb is not None
        and start_body_fat is not None
    ):
        start_fat_mass_lb = (
            float(
                start_weight_lb
            )
            * float(
                start_body_fat
            )
            / 100.0
        )

        start_lean_mass_lb = (
            float(
                start_weight_lb
            )
            - start_fat_mass_lb
        )

    target_fat_mass_lb = None
    target_lean_mass_lb = None

    if (
        target_weight_lb is not None
        and target_body_fat is not None
    ):
        target_fat_mass_lb = (
            float(
                target_weight_lb
            )
            * float(
                target_body_fat
            )
            / 100.0
        )

        target_lean_mass_lb = (
            float(
                target_weight_lb
            )
            - target_fat_mass_lb
        )

    weight_metric = (
        _goal_metric_payload(
            metric_name=
                "body_weight",

            display_name=
                "Weight",

            unit=
                "lb",

            start=
                start_weight_lb,

            current=
                current_weight_lb,

            target=
                target_weight_lb,

            trend_horizons=
                weight.get(
                    "trend_horizons",
                    {},
                ),

            trend_values_are_kg=
                True,
        )
    )

    body_fat_metric = (
        _goal_metric_payload(
            metric_name=
                "body_fat_percentage",

            display_name=
                "Body Fat",

            unit=
                "percent",

            start=
                start_body_fat,

            current=
                current_body_fat,

            target=
                target_body_fat,

            trend_horizons=
                body_fat.get(
                    "trend_horizons",
                    {},
                ),

            trend_values_are_kg=
                False,
        )
    )

    fat_mass_metric = (
        _goal_metric_payload(
            metric_name=
                "body_fat_mass",

            display_name=
                "Fat Mass",

            unit=
                "lb",

            start=
                start_fat_mass_lb,

            current=
                current_fat_mass_lb,

            target=
                target_fat_mass_lb,

            trend_horizons=
                body_fat_mass.get(
                    "trend_horizons",
                    {},
                ),

            trend_values_are_kg=
                True,
        )
    )

    lean_mass_metric = (
        _goal_metric_payload(
            metric_name=
                "lean_body_mass",

            display_name=
                "Lean Mass",

            unit=
                "lb",

            start=
                start_lean_mass_lb,

            current=
                current_lean_mass_lb,

            target=
                target_lean_mass_lb,

            trend_horizons=
                derived_lean_mass.get(
                    "trend_horizons",
                    {},
                ),

            trend_values_are_kg=
                True,
        )
    )

    primary_metrics = (
        body_fat_metric,
        fat_mass_metric,
        lean_mass_metric,
        weight_metric,
    )

    goal_statuses = [
        metric[
            "goal_horizon"
        ][
            "goal_status"
        ]
        for metric in primary_metrics
        if metric[
            "goal_horizon"
        ][
            "goal_status"
        ]
        != "insufficient_data"
    ]

    if not phase_status_mature:
        overall_goal_status = (
            "insufficient_data"
        )

    elif not goal_statuses:
        overall_goal_status = (
            "insufficient_data"
        )

    elif (
        "progressing"
        in goal_statuses
        and "regressing"
        in goal_statuses
    ):
        overall_goal_status = (
            "mixed"
        )

    elif (
        "regressing"
        in goal_statuses
    ):
        overall_goal_status = (
            "regressing"
        )

    elif (
        "progressing"
        in goal_statuses
    ):
        overall_goal_status = (
            "progressing"
        )

    else:
        overall_goal_status = (
            "stable"
        )

    horizon_overall = {}

    for horizon in (
        BODY_COMPOSITION_HORIZONS
    ):

        key = str(
            horizon
        )

        statuses = [
            metric[
                "trend_horizons"
            ][
                key
            ][
                "goal_status"
            ]
            for metric in primary_metrics
            if (
                key
                in metric[
                    "trend_horizons"
                ]
                and metric[
                    "trend_horizons"
                ][
                    key
                ][
                    "goal_status"
                ]
                != "insufficient_data"
            )
        ]

        if not statuses:
            status = (
                "insufficient_data"
            )

        elif (
            "progressing"
            in statuses
            and "regressing"
            in statuses
        ):
            status = (
                "mixed"
            )

        elif (
            "regressing"
            in statuses
        ):
            status = (
                "regressing"
            )

        elif (
            "progressing"
            in statuses
        ):
            status = (
                "progressing"
            )

        else:
            status = (
                "stable"
            )

        horizon_overall[
            key
        ] = {
            "status":
                status,

            "metric_statuses":
                statuses,
        }

    return {
        "available":
            True,

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

            "phase_start_recorded_at":
                active_goal.get(
                    "phase_start_recorded_at"
                ),

            "phase_end_date":
                active_goal.get(
                    "phase_end_date"
                ),

            "phase_age_days":
                phase_age_days,

            "minimum_status_age_days":
                MIN_GOAL_PHASE_AGE_DAYS,

            "status_mature":
                phase_status_mature,
        },

        "methodology": {
            "current_value":
                (
                    "Current goal calculations use the "
                    "recent 7-day Hume daily-average mean "
                    "rather than a single scale reading."
                ),

            "trend":
                (
                    "Trend direction is calculated from "
                    "actual Hume measurements. The goal is "
                    "used only to interpret whether the "
                    "measured direction is progressing, "
                    "stable, or regressing."
                ),

            "progress":
                (
                    "Goal completion compares the phase-start "
                    "measurement with the current smoothed "
                    "measurement and target."
                ),

            "negative_progress":
                (
                    "Raw progress may fall below zero when "
                    "the current value has moved farther "
                    "from the target than the phase-start "
                    "value."
                ),

            "lean_mass":
                (
                    "Current Hume lean mass is derived as "
                    "body weight minus derived fat mass "
                    "because Hume is not publishing a direct "
                    "lean-body-mass HealthKit measurement."
                ),

            "target_composition":
                (
                    "Target fat mass and target lean mass "
                    "are mathematically derived from target "
                    "weight and target body-fat percentage."
                ),

            "source_policy":
                (
                    "Current trend scoring is Hume-only. "
                    "Fitdays remains historical context and "
                    "is not normalized into Hume."
                ),

            "goal_phase_maturity":
                (
                    "Overall goal status is withheld until "
                    f"the active goal phase is at least "
                    f"{MIN_GOAL_PHASE_AGE_DAYS} days old. "
                    "Individual metric calculations remain "
                    "available during the initial period."
                ),
        },

        "overall_goal_status":
            overall_goal_status,

        "horizon_status":
            horizon_overall,

        "metrics": {
            "weight":
                weight_metric,

            "body_fat_percentage":
                body_fat_metric,

            "fat_mass":
                fat_mass_metric,

            "lean_mass":
                lean_mass_metric,
        },

        "target_composition": {
            "target_weight_lb":
                _round(
                    target_weight_lb,
                    1,
                ),

            "target_body_fat_percentage":
                _round(
                    target_body_fat,
                    1,
                ),

            "target_fat_mass_lb":
                _round(
                    target_fat_mass_lb,
                    1,
                ),

            "target_lean_mass_lb":
                _round(
                    target_lean_mass_lb,
                    1,
                ),
        },

        "current_composition": {
            "weight_7d_average_lb":
                _round(
                    current_weight_lb,
                    1,
                ),

            "body_fat_7d_average_percentage":
                _round(
                    current_body_fat,
                    1,
                ),

            "fat_mass_7d_average_lb":
                _round(
                    current_fat_mass_lb,
                    1,
                ),

            "lean_mass_7d_average_lb":
                _round(
                    current_lean_mass_lb,
                    1,
                ),
        },

        "phase_start_composition": {
            "weight_lb":
                _round(
                    start_weight_lb,
                    1,
                ),

            "body_fat_percentage":
                _round(
                    start_body_fat,
                    1,
                ),

            "derived_fat_mass_lb":
                _round(
                    start_fat_mass_lb,
                    1,
                ),

            "derived_lean_mass_lb":
                _round(
                    start_lean_mass_lb,
                    1,
                ),
        },

        "historical_context":
            _source_aware_history(
                active_goal
            ),
    }


# ---------------------------------------------------------
# Public response
# ---------------------------------------------------------

def apple_health_trends():
    """
    Unified body/activity analytics.

    Current coaching remains Hume-only.

    Fitdays remains historical context and source-transition
    evidence. It is not mathematically merged into Hume
    trend calculations.
    """

    activity = (
        _activity_baselines()
    )

    weight = (
        _hume_metric_trend(
            "body_weight"
        )
    )

    body_fat = (
        _hume_metric_trend(
            "body_fat_percentage"
        )
    )

    measured_lean_mass = (
        _hume_metric_trend(
            "lean_body_mass"
        )
    )

    body_fat_mass = (
        _derived_body_fat_mass()
    )

    derived_lean_mass = (
        _derived_lean_body_mass()
    )

    source_transition = (
        _source_transition_analysis()
    )

    body_composition_progress = (
        _body_composition_progress(
            weight,
            body_fat,
            body_fat_mass,
            derived_lean_mass,
        )
    )

    return {
        "status":
            "ok",

        "methodology": {
            "activity":
                (
                    "Apple Health daily activity. "
                    "Current partial day excluded "
                    "from historical baselines."
                ),

            "weight":
                (
                    "Current trend calculations use "
                    "Hume-only measurements."
                ),

            "body_fat":
                (
                    "Current trend calculations use "
                    "Hume-only measurements to prevent "
                    "cross-device bias."
                ),

            "lean_mass":
                (
                    "Direct Hume measured lean mass remains "
                    "separate. A derived Hume lean-mass "
                    "series is also calculated from weight "
                    "minus fat mass."
                ),

            "body_fat_mass":
                (
                    "Derived from same-day daily-average "
                    "Hume body weight and Hume body-fat "
                    "percentage."
                ),

            "daily_aggregation":
                (
                    "Multiple measurements from the same "
                    "source on the same local calendar day "
                    "are averaged before smoothed trend "
                    "calculations."
                ),

            "source_transition":
                (
                    "Fitdays history is analyzed separately "
                    "against overlapping Hume measurements. "
                    "It does not alter current Hume coaching "
                    "or Hume trend calculations."
                ),

            "goal_progress":
                (
                    "Goal completion and recent trend are "
                    "separate. Recent trend is calculated "
                    "from actual measurements; the active "
                    "goal determines whether that movement "
                    "is favorable or unfavorable."
                ),

            "legacy_baseline_windows":
                list(
                    LEGACY_WINDOWS
                ),

            "body_composition_horizons":
                list(
                    BODY_COMPOSITION_HORIZONS
                ),

            "body_composition_view_labels":
                [
                    "4W",
                    "3M",
                    "6M",
                    "1Y",
                    "Goal",
                ],

            "current_smoothing_days":
                CURRENT_SMOOTHING_DAYS,

            "trend_method":
                (
                    "Recent 7-day daily-average mean "
                    "compared with a 7-day reference "
                    "daily-average mean near the beginning "
                    "of each selected horizon."
                ),

            "rate_method":
                (
                    "Rate per week uses the approximate "
                    "separation between the centers of the "
                    "reference and current smoothing windows."
                ),
        },

        "activity":
            activity,

        "body_composition": {
            "weight":
                weight,

            "body_fat_percentage":
                body_fat,

            "body_fat_mass":
                body_fat_mass,

            # Existing measured field retained for backward
            # compatibility. Hume currently does not publish it.
            "lean_body_mass":
                measured_lean_mass,

            # New authoritative derived Hume lean-mass series.
            "derived_lean_body_mass":
                derived_lean_mass,
        },

        "body_composition_progress":
            body_composition_progress,

        "source_transition":
            source_transition,
    }