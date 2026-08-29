from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from db import get_conn


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

    Therefore rate/week should use that effective
    separation rather than the full horizon.
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
    Preserve the existing output consumed by
    goal_progress.py.
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

    This prevents days containing multiple scale measurements
    from receiving more statistical weight than other days.
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

    return [
        {
            "date":
                row[
                    "measurement_date"
                ].isoformat(),

            "value":
                _round(
                    row[
                        "value"
                    ]
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
        for row in rows
    ]


def _daily_average_in_window(
    metric_name,
    source_bundle_id,
    start_time,
    end_time,
):
    """
    Calculate an average of DAILY averages rather than
    averaging every raw measurement.

    This means a day containing two scale readings counts
    once, exactly like a day containing one reading.
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
    Measurement direction only.

    Goal interpretation belongs in goal_progress.py.
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
        return "increasing"

    if change < -tolerance:
        return "decreasing"

    return "stable"


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

        # Retained for easier compatibility with any
        # consumers already reading this field.
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
# Hume-derived body-fat mass
# ---------------------------------------------------------

def _derived_body_fat_mass():
    """
    Derive Hume fat mass from same-day daily averages:

        weight × body-fat percentage
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

                    (
                        w.weight_kg
                        * b.body_fat_percentage
                        / 100.0
                    )
                        AS body_fat_mass_kg,

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
                    HUME_BUNDLE_ID,
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

            "reason":
                (
                    "Weight and body-fat percentage "
                    "do not overlap on any Hume "
                    "measurement dates."
                ),
        }

    latest = (
        rows[-1]
    )

    latest_date = (
        latest[
            "measurement_date"
        ]
    )

    latest_value = float(
        latest[
            "body_fat_mass_kg"
        ]
    )

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
                            "body_fat_mass_kg"
                        ]
                    )
                )

        if not values:
            return (
                None,
                0,
            )

        return (
            sum(values)
            / len(values),
            len(values),
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

            "current_measurement_days":
                current_days,

            "current_observations":
                current_days,

            "reference_average":
                _round(
                    reference_average
                ),

            "reference_measurement_days":
                reference_days,

            "reference_observations":
                reference_days,

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
                        "body_fat_mass",
                        change,
                    )
                    if sufficient_data
                    else "insufficient_data"
                ),

            "sufficient_data":
                sufficient_data,
        }

    return {
        "available":
            True,

        "derived":
            True,

        "derivation":
            (
                "Daily-average Hume body weight × "
                "daily-average Hume body-fat percentage"
            ),

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
                            "body_fat_mass_kg"
                        ]
                    ),

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


# ---------------------------------------------------------
# Fitdays -> Hume source-transition calibration
# ---------------------------------------------------------

def _paired_source_days(
    metric_name,
):
    """
    Find local calendar dates containing BOTH Fitdays and
    Hume measurements for the requested metric.

    Each source is first averaged within that calendar day.
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

        # Signed difference is always:
        #
        #     Hume - Fitdays
        #
        # Negative therefore means Hume reads lower.
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

    min_difference = min(
        signed_differences
    )

    max_difference = max(
        signed_differences
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
                min_difference
            ),

        "maximum_hume_minus_fitdays":
            _round(
                max_difference
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
    """
    Compare derived fat and lean mass on dates where BOTH
    sources contain weight and body-fat percentage.

    This is diagnostic only.

    It does not replace measured lean body mass and it does
    not alter coaching calculations.
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
    """
    Conservative diagnostic assessment.

    Three overlapping days are useful for understanding the
    scale transition, but they are not enough to justify
    automatic normalization of historical Fitdays data.
    """

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
                        "Fitdays is currently retained only "
                        "for historical context and source-"
                        "transition analysis."
                    ),
            },
    }


# ---------------------------------------------------------
# Public response
# ---------------------------------------------------------

def apple_health_trends():
    """
    Unified body/activity analytics.

    Current coaching remains Hume-only.

    Fitdays is exposed separately as historical source-
    transition context and does not affect Hume calculations.
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

    lean_mass = (
        _hume_metric_trend(
            "lean_body_mass"
        )
    )

    body_fat_mass = (
        _derived_body_fat_mass()
    )

    source_transition = (
        _source_transition_analysis()
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
                    "Hume-only measured lean mass. "
                    "If Hume does not publish this "
                    "HealthKit metric, direct measured "
                    "lean-mass analytics remain unavailable."
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

            "legacy_baseline_windows":
                list(
                    LEGACY_WINDOWS
                ),

            "body_composition_horizons":
                list(
                    BODY_COMPOSITION_HORIZONS
                ),

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

            "lean_body_mass":
                lean_mass,
        },

        "source_transition":
            source_transition,
    }