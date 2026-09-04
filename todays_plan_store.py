import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from db import get_conn
from freshness import freshness_status
from todays_plan import build_todays_plan


# ============================================================
# CONFIGURATION
# ============================================================

TABLE_NAME = "todays_plan_cache"
PLAN_VERSION = 1

LOCAL_TIMEZONE = ZoneInfo(
    "America/New_York"
)

CACHE_MAX_AGE_SECONDS = 60 * 60


# ============================================================
# HELPERS
# ============================================================

def _utc_now():

    return datetime.now(
        timezone.utc
    )


def _today_local():

    return datetime.now(
        LOCAL_TIMEZONE
    ).date()


def _canonical_json(
    payload,
):

    return json.dumps(
        payload,
        sort_keys=True,
        separators=(
            ",",
            ":",
        ),
        default=str,
    )


def _json_value(
    value,
):

    if isinstance(
        value,
        str,
    ):

        try:
            return json.loads(
                value
            )

        except json.JSONDecodeError:
            return value

    return value


# ============================================================
# DATABASE SETUP
# ============================================================

def ensure_table():

    with get_conn() as conn:

        with conn.cursor() as cur:

            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS public.{TABLE_NAME} (
                    id BIGSERIAL PRIMARY KEY,

                    plan_date DATE NOT NULL,

                    plan_version INTEGER NOT NULL,

                    plan_payload JSONB NOT NULL,

                    created_at TIMESTAMPTZ NOT NULL
                        DEFAULT NOW(),

                    updated_at TIMESTAMPTZ NOT NULL
                        DEFAULT NOW(),

                    UNIQUE (
                        plan_date,
                        plan_version
                    )
                )
                """
            )

            cur.execute(
                f"""
                CREATE INDEX IF NOT EXISTS
                    idx_{TABLE_NAME}_plan_date
                ON public.{TABLE_NAME} (
                    plan_date DESC
                )
                """
            )

        conn.commit()


# ============================================================
# READ
# ============================================================

def load_cached_plan(
    plan_date=None,
):

    ensure_table()

    if plan_date is None:
        plan_date = _today_local()

    with get_conn() as conn:

        with conn.cursor() as cur:

            cur.execute(
                f"""
                SELECT
                    id,
                    plan_date,
                    plan_version,
                    plan_payload,
                    created_at,
                    updated_at
                FROM public.{TABLE_NAME}
                WHERE
                    plan_date = %s
                    AND plan_version = %s
                LIMIT 1
                """,
                (
                    plan_date,
                    PLAN_VERSION,
                ),
            )

            row = cur.fetchone()

    if not row:
        return None

    row = dict(
        row
    )

    row[
        "plan_payload"
    ] = _json_value(
        row.get(
            "plan_payload"
        )
    )

    return row


# ============================================================
# FRESHNESS
# ============================================================

def _cache_is_fresh(
    cached,
    source_freshness=None,
):

    if not cached:
        return False

    updated_at = cached.get(
        "updated_at"
    )

    if updated_at is None:
        return False

    if updated_at.tzinfo is None:

        updated_at = (
            updated_at.replace(
                tzinfo=timezone.utc
            )
        )

    age_seconds = (
        _utc_now()
        -
        updated_at
    ).total_seconds()

    time_fresh = (
        age_seconds
        <=
        CACHE_MAX_AGE_SECONDS
    )

    stored_source_freshness = (
        (cached.get("plan_payload") or {}).get(
            "source_freshness"
        )
    )

    return (
        time_fresh
        and source_freshness is not None
        and stored_source_freshness == source_freshness
    )


def _pending_plan(
    plan_date,
    freshness,
):

    status = (
        "pending_freshness"
        if freshness.get("status") == "pending_today"
        else "stale_data"
    )

    return {
        "status": status,
        "plan_date": str(plan_date),
        "freshness": freshness,
        "reason": freshness.get("message"),
    }


# ============================================================
# WRITE
# ============================================================

def save_plan(
    plan,
):

    ensure_table()

    plan_date = plan.get(
        "plan_date"
    )

    if not plan_date:

        plan_date = str(
            _today_local()
        )

    now = _utc_now()

    with get_conn() as conn:

        with conn.cursor() as cur:

            cur.execute(
                f"""
                INSERT INTO public.{TABLE_NAME} (
                    plan_date,
                    plan_version,
                    plan_payload,
                    created_at,
                    updated_at
                )
                VALUES (
                    %s,
                    %s,
                    %s::jsonb,
                    %s,
                    %s
                )
                ON CONFLICT (
                    plan_date,
                    plan_version
                )
                DO UPDATE SET
                    plan_payload =
                        EXCLUDED.plan_payload,

                    updated_at =
                        EXCLUDED.updated_at

                RETURNING
                    id,
                    plan_date,
                    plan_version,
                    plan_payload,
                    created_at,
                    updated_at
                """,
                (
                    plan_date,
                    PLAN_VERSION,
                    _canonical_json(
                        plan
                    ),
                    now,
                    now,
                ),
            )

            row = cur.fetchone()

        conn.commit()

    return dict(
        row
    )


# ============================================================
# INVALIDATION
# ============================================================

def invalidate_todays_plan():

    ensure_table()

    plan_date = _today_local()

    with get_conn() as conn:

        with conn.cursor() as cur:

            cur.execute(
                f"""
                DELETE FROM public.{TABLE_NAME}
                WHERE
                    plan_date = %s
                    AND plan_version = %s
                """,
                (
                    plan_date,
                    PLAN_VERSION,
                ),
            )

        conn.commit()

    return plan_date


# ============================================================
# CACHE SERVICE
# ============================================================

def get_or_build_todays_plan(
    force_refresh=False,
):

    plan_date = _today_local()
    freshness = freshness_status()
    source_freshness = freshness.get("source_freshness") or {}

    if not freshness.get("can_generate_current_recommendation"):

        return _pending_plan(
            plan_date,
            freshness,
        )

    if not force_refresh:

        cached = load_cached_plan(
            plan_date
        )

        if (
            cached
            and
            _cache_is_fresh(
                cached,
                source_freshness,
            )
        ):

            print(
                "TODAYS_PLAN_CACHE "
                f"status=hit "
                f"date={plan_date}",
                flush=True,
            )

            return cached.get(
                "plan_payload"
            )

    print(
        "TODAYS_PLAN_CACHE "
        f"status=miss "
        f"date={plan_date}",
        flush=True,
    )

    plan = build_todays_plan()

    if (
        not isinstance(
            plan,
            dict,
        )
        or
        plan.get(
            "status"
        )
        !=
        "ok"
    ):

        return plan

    plan = dict(plan)
    plan["source_freshness"] = source_freshness

    save_plan(plan)

    print(
        "TODAYS_PLAN_CACHE "
        f"status=saved "
        f"date={plan_date}",
        flush=True,
    )

    return plan
