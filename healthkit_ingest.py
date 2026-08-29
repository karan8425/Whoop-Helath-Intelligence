import os
import secrets

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request
from psycopg.types.json import Jsonb

from db import get_conn


# ============================================================
# CONFIGURATION
# ============================================================

APPLE_HEALTH_INGEST_KEY = os.getenv(
    "APPLE_HEALTH_INGEST_KEY",
    "",
)

EASTERN = ZoneInfo(
    "America/New_York"
)

PREFERRED_BODY_SOURCE_BUNDLE_IDS = {
    "com.elink.fittrackhealth",
}

PREFERRED_BODY_SOURCE_NAMES = {
    "hume",
}


# ============================================================
# AUTHENTICATION
# ============================================================

def require_ingest_key(
    request: Request,
):

    if not APPLE_HEALTH_INGEST_KEY:

        raise HTTPException(
            status_code=503,
            detail=(
                "APPLE_HEALTH_INGEST_KEY "
                "is not configured."
            ),
        )

    supplied = request.headers.get(
        "authorization",
        "",
    )

    if not secrets.compare_digest(
        supplied,
        f"Bearer {APPLE_HEALTH_INGEST_KEY}",
    ):

        raise HTTPException(
            status_code=401,
            detail="Invalid ingest key.",
        )


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def init_apple_health_tables():

    with get_conn() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS
                apple_health_body_samples (
                    sample_id UUID PRIMARY KEY,
                    metric_name TEXT NOT NULL,
                    value DOUBLE PRECISION NOT NULL,
                    unit TEXT NOT NULL,
                    observed_at TIMESTAMPTZ NOT NULL,
                    source_name TEXT,
                    source_bundle_id TEXT,
                    raw_json JSONB NOT NULL,
                    received_at TIMESTAMPTZ
                        NOT NULL
                        DEFAULT NOW()
                )
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_apple_health_body_metric_date
                ON apple_health_body_samples(
                    metric_name,
                    observed_at DESC
                )
                """
            )

            # Helps historical diagnostics grouped by source.
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_apple_health_body_metric_source_date
                ON apple_health_body_samples(
                    metric_name,
                    source_bundle_id,
                    observed_at DESC
                )
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS
                apple_health_daily_activity (
                    activity_date DATE PRIMARY KEY,
                    steps DOUBLE PRECISION,
                    active_energy_kcal DOUBLE PRECISION,
                    resting_energy_kcal DOUBLE PRECISION,
                    walking_running_distance_km
                        DOUBLE PRECISION,
                    raw_json JSONB NOT NULL,
                    received_at TIMESTAMPTZ
                        NOT NULL
                        DEFAULT NOW()
                )
                """
            )


# ============================================================
# BODY SAMPLE UPSERT
# ============================================================

def _upsert_body(
    cur,
    samples,
):

    for sample in samples:

        cur.execute(
            """
            INSERT INTO apple_health_body_samples (
                sample_id,
                metric_name,
                value,
                unit,
                observed_at,
                source_name,
                source_bundle_id,
                raw_json
            )
            VALUES (
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s
            )
            ON CONFLICT(sample_id)
            DO UPDATE SET
                metric_name =
                    EXCLUDED.metric_name,
                value =
                    EXCLUDED.value,
                unit =
                    EXCLUDED.unit,
                observed_at =
                    EXCLUDED.observed_at,
                source_name =
                    EXCLUDED.source_name,
                source_bundle_id =
                    EXCLUDED.source_bundle_id,
                raw_json =
                    EXCLUDED.raw_json,
                received_at =
                    NOW()
            """,
            (
                sample.get(
                    "sample_id"
                ),
                sample.get(
                    "metric_name"
                ),
                sample.get(
                    "value"
                ),
                sample.get(
                    "unit"
                ),
                sample.get(
                    "observed_at"
                ),
                sample.get(
                    "source_name"
                ),
                sample.get(
                    "source_bundle_id"
                ),
                Jsonb(
                    sample
                ),
            ),
        )


# ============================================================
# ACTIVITY UPSERT
# ============================================================

def _upsert_activity(
    cur,
    activity,
):

    cur.execute(
        """
        INSERT INTO apple_health_daily_activity (
            activity_date,
            steps,
            active_energy_kcal,
            resting_energy_kcal,
            walking_running_distance_km,
            raw_json,
            received_at
        )
        VALUES (
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            NOW()
        )
        ON CONFLICT(activity_date)
        DO UPDATE SET
            steps =
                EXCLUDED.steps,
            active_energy_kcal =
                EXCLUDED.active_energy_kcal,
            resting_energy_kcal =
                EXCLUDED.resting_energy_kcal,
            walking_running_distance_km =
                EXCLUDED.walking_running_distance_km,
            raw_json =
                EXCLUDED.raw_json,
            received_at =
                NOW()
        """,
        (
            activity.get(
                "activity_date"
            ),
            activity.get(
                "steps"
            ),
            activity.get(
                "active_energy_kcal"
            ),
            activity.get(
                "resting_energy_kcal"
            ),
            activity.get(
                "walking_running_distance_km"
            ),
            Jsonb(
                activity
            ),
        ),
    )


# ============================================================
# INGESTION
# ============================================================

def ingest_healthkit_payload(
    payload: dict,
):

    init_apple_health_tables()

    body = (
        payload.get(
            "body_samples"
        )
        or []
    )

    current_activity = (
        payload.get(
            "daily_activity"
        )
    )

    history = (
        payload.get(
            "daily_activity_history"
        )
        or []
    )

    with get_conn() as conn:
        with conn.cursor() as cur:

            _upsert_body(
                cur,
                body,
            )

            if current_activity:

                _upsert_activity(
                    cur,
                    current_activity,
                )

            for activity in history:

                _upsert_activity(
                    cur,
                    activity,
                )

    return {
        "status":
            "ok",

        "body_samples_received":
            len(
                body
            ),

        "activity_days_received":
            (
                len(
                    history
                )
                + (
                    1
                    if current_activity
                    else 0
                )
            ),
    }


# ============================================================
# SOURCE CLASSIFICATION
# ============================================================

def _preferred(
    name,
    bundle,
):

    normalized_name = (
        name
        or ""
    ).strip().lower()

    return (
        bundle
        in PREFERRED_BODY_SOURCE_BUNDLE_IDS
        or normalized_name
        in PREFERRED_BODY_SOURCE_NAMES
    )


def _classify(
    row,
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

    observed = (
        row[
            "observed_at"
        ]
        .astimezone(
            EASTERN
        )
        .date()
    )

    age = (
        today
        - observed
    ).days

    preferred = (
        _preferred(
            row.get(
                "source_name"
            ),
            row.get(
                "source_bundle_id"
            ),
        )
    )

    if not preferred:

        return {
            "classification":
                "non_preferred_source",

            "coaching_eligible":
                False,

            "preferred_source":
                False,

            "observed_local_date":
                observed.isoformat(),

            "age_days":
                age,

            "reason":
                (
                    "Latest sample came from "
                    f"{row.get('source_name') or 'an unknown source'}, "
                    "not Hume."
                ),
        }

    if age == 0:

        return {
            "classification":
                "current",

            "coaching_eligible":
                True,

            "preferred_source":
                True,

            "observed_local_date":
                observed.isoformat(),

            "age_days":
                0,

            "reason":
                (
                    "Preferred Hume source "
                    "and observed today."
                ),
        }

    return {
        "classification":
            "stale",

        "coaching_eligible":
            False,

        "preferred_source":
            True,

        "observed_local_date":
            observed.isoformat(),

        "age_days":
            age,

        "reason":
            (
                "Preferred Hume source but "
                f"measurement is {age} day(s) old."
            ),
    }


# ============================================================
# LATEST APPLE HEALTH DATA
# ============================================================

def latest_apple_health():

    init_apple_health_tables()

    result = {
        "body": {},
        "activity": None,
    }

    with get_conn() as conn:
        with conn.cursor() as cur:

            for metric in (
                "body_weight",
                "body_fat_percentage",
                "lean_body_mass",
            ):

                cur.execute(
                    """
                    SELECT
                        metric_name,
                        value,
                        unit,
                        observed_at,
                        source_name,
                        source_bundle_id,
                        received_at
                    FROM apple_health_body_samples
                    WHERE metric_name = %s
                    ORDER BY observed_at DESC
                    LIMIT 1
                    """,
                    (
                        metric,
                    ),
                )

                row = (
                    cur.fetchone()
                )

                if row:

                    result[
                        "body"
                    ][
                        metric
                    ] = {
                        **row,

                        "observed_at":
                            row[
                                "observed_at"
                            ].isoformat(),

                        "received_at":
                            row[
                                "received_at"
                            ].isoformat(),

                        **_classify(
                            row
                        ),
                    }

            cur.execute(
                """
                SELECT
                    activity_date,
                    steps,
                    active_energy_kcal,
                    resting_energy_kcal,
                    walking_running_distance_km,
                    received_at
                FROM apple_health_daily_activity
                ORDER BY activity_date DESC
                LIMIT 1
                """
            )

            row = (
                cur.fetchone()
            )

            if row:

                today = (
                    datetime.now(
                        timezone.utc
                    )
                    .astimezone(
                        EASTERN
                    )
                    .date()
                )

                age = (
                    today
                    - row[
                        "activity_date"
                    ]
                ).days

                result[
                    "activity"
                ] = {
                    **row,

                    "activity_date":
                        row[
                            "activity_date"
                        ].isoformat(),

                    "received_at":
                        row[
                            "received_at"
                        ].isoformat(),

                    "classification":
                        (
                            "current"
                            if age == 0
                            else "stale"
                        ),

                    "coaching_eligible":
                        age == 0,

                    "age_days":
                        age,
                }

    return result


# ============================================================
# APPLE HEALTH HISTORY SUMMARY
#
# Returns:
#   1. Existing per-metric totals
#   2. Source-level breakdown by metric
#   3. Activity history coverage
#
# Source breakdown is diagnostic only.
# It does not change coaching eligibility.
# ============================================================

def apple_health_history_summary():

    init_apple_health_tables()

    with get_conn() as conn:
        with conn.cursor() as cur:

            # ------------------------------------------------
            # Existing metric-level summary
            # ------------------------------------------------

            cur.execute(
                """
                SELECT
                    metric_name,
                    COUNT(*)
                        AS samples,
                    MIN(observed_at)
                        AS oldest,
                    MAX(observed_at)
                        AS newest,
                    COUNT(*) FILTER (
                        WHERE source_bundle_id =
                            'com.elink.fittrackhealth'
                    )
                        AS hume_samples
                FROM apple_health_body_samples
                GROUP BY metric_name
                ORDER BY metric_name
                """
            )

            body = (
                cur.fetchall()
            )

            # ------------------------------------------------
            # New metric + source breakdown
            # ------------------------------------------------

            cur.execute(
                """
                SELECT
                    metric_name,

                    COALESCE(
                        source_name,
                        'Unknown'
                    )
                        AS source_name,

                    COALESCE(
                        source_bundle_id,
                        'unknown'
                    )
                        AS source_bundle_id,

                    COUNT(*)
                        AS samples,

                    MIN(observed_at)
                        AS oldest,

                    MAX(observed_at)
                        AS newest

                FROM apple_health_body_samples

                GROUP BY
                    metric_name,
                    source_name,
                    source_bundle_id

                ORDER BY
                    metric_name,
                    MIN(observed_at),
                    source_name
                """
            )

            source_rows = (
                cur.fetchall()
            )

            # ------------------------------------------------
            # Activity history
            # ------------------------------------------------

            cur.execute(
                """
                SELECT
                    COUNT(*)
                        AS days,
                    MIN(activity_date)
                        AS oldest,
                    MAX(activity_date)
                        AS newest,
                    COUNT(*) FILTER (
                        WHERE steps IS NOT NULL
                    )
                        AS step_days
                FROM apple_health_daily_activity
                """
            )

            activity = (
                cur.fetchone()
            )

    # --------------------------------------------------------
    # Format source breakdown
    # --------------------------------------------------------

    body_sources = []

    for row in source_rows:

        source_name = (
            row[
                "source_name"
            ]
        )

        source_bundle_id = (
            row[
                "source_bundle_id"
            ]
        )

        body_sources.append(
            {
                "metric_name":
                    row[
                        "metric_name"
                    ],

                "source_name":
                    source_name,

                "source_bundle_id":
                    source_bundle_id,

                "preferred_source":
                    _preferred(
                        source_name,
                        source_bundle_id,
                    ),

                "samples":
                    int(
                        row[
                            "samples"
                        ]
                        or 0
                    ),

                "oldest":
                    (
                        row[
                            "oldest"
                        ].isoformat()
                        if row[
                            "oldest"
                        ]
                        else None
                    ),

                "newest":
                    (
                        row[
                            "newest"
                        ].isoformat()
                        if row[
                            "newest"
                        ]
                        else None
                    ),
            }
        )

    # --------------------------------------------------------
    # Final response
    # --------------------------------------------------------

    return {
        "body_metrics": [
            {
                **row,

                "oldest":
                    (
                        row[
                            "oldest"
                        ].isoformat()
                        if row[
                            "oldest"
                        ]
                        else None
                    ),

                "newest":
                    (
                        row[
                            "newest"
                        ].isoformat()
                        if row[
                            "newest"
                        ]
                        else None
                    ),
            }
            for row in body
        ],

        "body_sources":
            body_sources,

        "activity": {
            **activity,

            "oldest":
                (
                    activity[
                        "oldest"
                    ].isoformat()
                    if activity[
                        "oldest"
                    ]
                    else None
                ),

            "newest":
                (
                    activity[
                        "newest"
                    ].isoformat()
                    if activity[
                        "newest"
                    ]
                    else None
                ),
        },
    }