from whoop_refresh import read_state, metadata, data_version, same_metric_source, raw_source_signature
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from db import get_conn
from freshness import freshness_status
from todays_plan import build_todays_plan
from activity_plan import build_activity_plan
from training_engine_flag import (
    resolve_training_prescription_engine, resolve_effective_training_engine,
    ENGINE_B3, ENGINE_TKI, ENGINE_TRAINING_INTELLIGENCE,
)


# ============================================================
# CONFIGURATION
# ============================================================

TABLE_NAME = "todays_plan_cache"
# 3 -> 4: the B2 training card now carries the dose_diagnostics payload,
# which a v3 row cached between the engine deploy and the pass-through
# deploy would lack. Bumping forces a clean rebuild of the full B2 shape.
# 4 -> 5: B3 adds exercise-level rep/load/RIR/rest/progression semantics and
# body-composition strategy; cached v4 plans must be rebuilt to expose them.
# 5 -> 6: discard the transient B3 guardrail-error payload cached before the
# deterministic set-repair path was deployed.
# 6 -> 7: rebuild B3 strategy with legacy phase-to-goal compatibility.
# 7 -> 8: B3.0.1 separates local muscle selection from systemic dose and
# rejects the previous recovering-muscle recommendation semantics.
# 8 -> 9: expose normalized session/rotation diagnostics and preserve
# exercise-level progression evidence under low systemic recovery.
# 9 -> 10: reject the cached legacy low-readiness policy description.
# TKI-7 does not need a further PLAN_VERSION bump: the new calibrated
# engine writes into its own brand-new ENGINE_TRAINING_INTELLIGENCE
# cache partition (below) that has never been written to before, so
# there is no stale-shape row in that partition to reject.
PLAN_VERSION = 12

# TKI-6/TKI-7: three engines can now generate today's plan (training_
# engine_flag.py). Reusing the SAME (plan_date, plan_version) UNIQUE
# constraint the cache table already has - no schema change, no new
# table, no migration - each engine gets its own numerically distinct
# cache PARTITION of the identical plan_version integer column, so a
# plan built under one engine can never be served as fresh under
# another. TKI-7's TRAINING_INTELLIGENCE_MOBILE_ENABLED flag is
# resolved via resolve_effective_training_engine() (higher precedence
# than TRAINING_PRESCRIPTION_ENGINE) so enabling/disabling it can never
# collide with, or be masked by, either pre-existing engine's cache.
# Offsets are PRODUCT POLICY / CALIBRATION PARAMETER bookkeeping, not a
# scientific or semantic claim.
ENGINE_PLAN_VERSION_OFFSET = {
    ENGINE_B3: 0,
    ENGINE_TKI: 500,
    ENGINE_TRAINING_INTELLIGENCE: 1000,
}


def _effective_plan_version(engine=None):
    if engine is None:
        engine = ENGINE_B3
    return PLAN_VERSION + ENGINE_PLAN_VERSION_OFFSET.get(engine, 0)

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

        # Transaction ownership belongs to get_conn / request_scoped_connection.


# ============================================================
# READ
# ============================================================

def load_cached_plan(
    plan_date=None,
    plan_version=None,
):

    ensure_table()

    if plan_date is None:
        plan_date = _today_local()

    if plan_version is None:
        plan_version = PLAN_VERSION

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
                    plan_version,
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
        and same_metric_source(stored_source_freshness, source_freshness)
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
    plan_version=None,
):

    ensure_table()

    plan_date = plan.get(
        "plan_date"
    )

    if not plan_date:

        plan_date = str(
            _today_local()
        )

    if plan_version is None:
        plan_version = PLAN_VERSION

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
                    plan_version,
                    _canonical_json(
                        plan
                    ),
                    now,
                    now,
                ),
            )

            row = cur.fetchone()

        # Transaction ownership belongs to get_conn / request_scoped_connection.

    return dict(
        row
    )


# ============================================================
# INVALIDATION
# ============================================================

def invalidate_todays_plan():
    """Invalidates TODAY's cached plan only (unchanged scope/semantics)
    - never a broader/global cache clear. TKI-6: invalidates every known
    engine's cache partition for today, not just B3's - existing callers
    (goals.py, daily_sync.py) call this bare, with no idea which engine
    is currently active, and underlying data changing is equally
    invalidating for whichever engine is active. Still exactly one
    DELETE, still scoped to exactly one plan_date - not a destructive
    clear of any other day or of history."""

    ensure_table()

    plan_date = _today_local()

    all_plan_versions = tuple(
        PLAN_VERSION + offset
        for offset in set(ENGINE_PLAN_VERSION_OFFSET.values())
    )

    with get_conn() as conn:

        with conn.cursor() as cur:

            cur.execute(
                f"""
                DELETE FROM public.{TABLE_NAME}
                WHERE
                    plan_date = %s
                    AND plan_version = ANY(%s)
                """,
                (
                    plan_date,
                    list(all_plan_versions),
                ),
            )

        # Transaction ownership belongs to get_conn / request_scoped_connection.

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

    # TKI-6/TKI-7: resolved ONCE per request. Every cache read/write
    # below uses this same engine's own cache partition (_effective_
    # plan_version), so a plan built under one engine can never be
    # served as a cache hit under another - see ENGINE_PLAN_VERSION_
    # OFFSET above. resolve_effective_training_engine() gives
    # TRAINING_INTELLIGENCE_MOBILE_ENABLED precedence when set.
    engine = resolve_effective_training_engine()
    plan_version = _effective_plan_version(engine)

    if not freshness.get("can_generate_current_recommendation"):

        return _pending_plan(
            plan_date,
            freshness,
        )

    state = read_state(str(plan_date))
    cached = load_cached_plan(plan_date, plan_version)
    if state.get("refresh_in_progress"):
        if cached and (cached.get("plan_payload") or {}).get("status") == "ok":
            return metadata(cached["plan_payload"], state)
        pending = _pending_plan(plan_date, freshness)
        pending["status"] = "pending_freshness"
        return metadata(pending, state)

    if not force_refresh:

        cached = load_cached_plan(
            plan_date,
            plan_version,
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

            payload = cached.get("plan_payload") or {}
            training = payload.get("training") or {}
            previous_activity = training.get("activity_plan") or {}
            goal_context = previous_activity.get("goal_context") or {}
            # Strength and the expensive plan remain cached. Current Apple
            # activity is intentionally recomputed on every endpoint refresh.
            try:
                activity = build_activity_plan(
                    goal=goal_context,
                    strength=training,
                )
                training["activity_plan"] = activity
                training["overall_training_summary"] = activity.get(
                    "overall_training_summary"
                )
            except Exception as exc:
                print(
                    "TODAYS_ACTIVITY_REFRESH "
                    f"status=degraded error_type={type(exc).__name__}",
                    flush=True,
                )
            payload["training"] = training
            return metadata(payload, state)

    # Share the pipeline lock with webhook/cron writers. A request that raced
    # event acceptance must not build from partially ingested physiology.
    from whoop_webhook_store import pipeline_lock
    with pipeline_lock() as acquired:
        state = read_state(str(plan_date))
        cached = load_cached_plan(plan_date, plan_version)
        if not acquired or state.get("refresh_in_progress"):
            state["refresh_in_progress"] = True
            if cached:
                return metadata(cached["plan_payload"], state)
            pending = _pending_plan(plan_date, freshness)
            pending["status"] = "pending_freshness"
            return metadata(pending, state)
        # A different request may have completed the cache while we waited.
        if not force_refresh and _cache_is_fresh(cached, source_freshness):
            return metadata(cached["plan_payload"], state)
        return _build_and_save(plan_date, source_freshness, state, plan_version)


def _build_and_save(plan_date, source_freshness, state, plan_version=None):
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

        return metadata(plan, state)

    plan = dict(plan)
    plan["source_freshness"] = {**source_freshness, "source_revision": raw_source_signature()}

    save_plan(plan, plan_version)

    print(
        "TODAYS_PLAN_CACHE "
        f"status=saved "
        f"date={plan_date}",
        flush=True,
    )

    return metadata(plan, state)
