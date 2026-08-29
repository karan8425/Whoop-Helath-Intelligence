from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from db import get_conn


EASTERN = ZoneInfo(
    "America/New_York"
)

# Existing windows are preserved because current
# goal_progress.py depends on windows["7"] and windows["30"].
LEGACY_WINDOWS = (
    7,
    14,
    30,
    90,
)

# New body-composition trend horizons.
#
# 7 days is deliberately NOT included here as a user-facing
# trend horizon. Instead, a 7-day average is used internally
# as the smoothed "current" value.
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

KG_TO_LB = 2.2046226218


# Approximate minimum changes required before a smoothed
# body-composition trend is labeled increasing/decreasing.
#
# These are noise-control thresholds, not medical thresholds.
TREND_TOLERANCES = {
    "body_weight": 0.25,
    "body_fat_percentage": 0.30,
    "lean_body_mass": 0.25,
    "body_fat_mass": 0.25,
}


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


def _activity_baselines():
    """
    Calculate activity averages over the preceding
    7 / 14 / 30 / 90 calendar days.

    Today's partial activity is NOT included in the baseline.

    This behavior is intentionally unchanged from the
    previous implementation.
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

            # Current day
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

            # Historical baselines
            baselines = {}

            for window in (
                LEGACY_WINDOWS
            ):

                cur.execute(
                    """
                    SELECT
                        AVG(steps) AS steps,
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
    Preserve the original current-value-versus-prior-window
    calculations.

    goal_progress.py currently consumes these values, so they
    must remain stable until that module is upgraded.
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


def _hume_daily_history(
    metric_name,
    latest_time,
    history_days=365,
):
    """
    Return one Hume value per local calendar day.

    If multiple samples exist on one day, the daily average is
    used. This keeps the future chart stable without blending
    different devices because the query is Hume-only.
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

                    AVG(value)
                        AS value,

                    COUNT(*)
                        AS observations,

                    MIN(observed_at)
                        AS first_observed_at,

                    MAX(observed_at)
                        AS last_observed_at

                FROM apple_health_body_samples

                WHERE metric_name = %s
                  AND source_bundle_id = %s
                  AND observed_at >=
                      %s - (
                          %s
                          * INTERVAL '1 day'
                      )
                  AND observed_at <= %s

                GROUP BY
                    measurement_date

                ORDER BY
                    measurement_date ASC
                """,
                (
                    metric_name,
                    HUME_BUNDLE_ID,
                    latest_time,
                    history_days,
                    latest_time,
                ),
            )

            rows = (
                cur.fetchall()
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


def _average_in_window(
    metric_name,
    start_time,
    end_time,
):
    """
    Average Hume samples in a half-open interval:

        start_time <= observed_at < end_time
    """

    with get_conn() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    AVG(value)
                        AS average_value,

                    COUNT(*)
                        AS observations,

                    MIN(observed_at)
                        AS oldest,

                    MAX(observed_at)
                        AS newest

                FROM apple_health_body_samples

                WHERE metric_name = %s
                  AND source_bundle_id = %s
                  AND observed_at >= %s
                  AND observed_at < %s
                """,
                (
                    metric_name,
                    HUME_BUNDLE_ID,
                    start_time,
                    end_time,
                ),
            )

            return (
                cur.fetchone()
            )


def _trend_label(
    metric_name,
    change,
):
    """
    Measurement direction only.

    This deliberately does NOT say whether the change is good
    or bad. Goal interpretation belongs in goal_progress.py.
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
    """
    Compare a recent smoothed 7-day average against a reference
    7-day average near the beginning of the selected horizon.

    Example for 28 days:

        reference:
            days -28 through -21

        current:
            most recent 7 days

    This is less sensitive to a single anomalous scale reading
    than comparing today's value with one historical value.
    """

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
        _average_in_window(
            metric_name,
            current_start,
            current_end,
        )
    )

    reference = (
        _average_in_window(
            metric_name,
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

    current_observations = int(
        (
            current[
                "observations"
            ]
            if current
            else 0
        )
        or 0
    )

    reference_observations = int(
        (
            reference[
                "observations"
            ]
            if reference
            else 0
        )
        or 0
    )

    if (
        current_average is None
        or reference_average is None
    ):
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

        weeks = (
            horizon_days
            / 7.0
        )

        rate_per_week = (
            change
            / weeks
            if weeks > 0
            else None
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

        "current_observations":
            current_observations,

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

        "reference_observations":
            reference_observations,

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
            _trend_label(
                metric_name,
                change,
            ),

        "sufficient_data":
            (
                current_observations
                >= 2
                and reference_observations
                >= 2
            ),
    }


def _hume_metric_trend(
    metric_name,
):
    """
    Body-composition trends deliberately use Hume only.

    Existing output fields are retained for backward
    compatibility.

    New output adds:
        - current_7d_average
        - trend_horizons
        - history
    """

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
        _average_in_window(
            metric_name,
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

    result = {
        "available":
            True,

        # Existing compatibility fields
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

        # New analytics
        "current_7d_average":
            _round(
                current_7d[
                    "average_value"
                ]
                if current_7d
                else None
            ),

        "current_7d_observations":
            int(
                (
                    current_7d[
                        "observations"
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

        current_7d_value = (
            current_7d[
                "average_value"
            ]
            if current_7d
            else None
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


def _derived_body_fat_mass():
    """
    Derive fat mass from same-day Hume weight and body-fat
    percentage:

        fat mass = weight * body-fat percentage

    Weight is stored in kilograms, so derived fat mass is
    initially kilograms.

    The response clearly identifies the value as derived.
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

                    WHERE metric_name = 'body_weight'
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

    def average_between(
        start_days_ago,
        end_days_ago,
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
                age_days
                >= end_days_ago
                and age_days
                < start_days_ago
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

    current_7d_average, current_count = (
        average_between(
            7,
            -1,
        )
    )

    trend_horizons = {}

    for horizon in (
        BODY_COMPOSITION_HORIZONS
    ):

        reference_average, reference_count = (
            average_between(
                horizon,
                horizon - 7,
            )
        )

        if (
            current_7d_average is None
            or reference_average is None
        ):
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
                change
                / (
                    horizon
                    / 7.0
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

            "current_observations":
                current_count,

            "reference_average":
                _round(
                    reference_average
                ),

            "reference_observations":
                reference_count,

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
                _trend_label(
                    "body_fat_mass",
                    change,
                ),

            "sufficient_data":
                (
                    current_count >= 2
                    and reference_count >= 2
                ),
        }

    return {
        "available":
            True,

        "derived":
            True,

        "derivation":
            (
                "Hume body weight × "
                "Hume body-fat percentage"
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

        "current_7d_observations":
            current_count,

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


def apple_health_trends():
    """
    Unified Apple Health / Hume trend response.

    Activity:
        existing 7/14/30/90 logic

    Body composition:
        legacy windows preserved
        + 7-day smoothed current values
        + 28/90/180/365-day trend horizons
        + daily history for charting
        + derived body-fat mass
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
                    "Hume-only measurements."
                ),

            "body_fat":
                (
                    "Hume-only measurements to "
                    "prevent cross-device bias."
                ),

            "lean_mass":
                (
                    "Hume-only. If unavailable, "
                    "excluded from direct measured "
                    "lean-mass analytics."
                ),

            "body_fat_mass":
                (
                    "Derived from same-day Hume "
                    "body weight and Hume body-fat "
                    "percentage."
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
                    "Recent 7-day average compared "
                    "with a 7-day reference window "
                    "near the beginning of each "
                    "selected horizon."
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
    }