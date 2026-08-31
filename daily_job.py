import asyncio
import json
import traceback
from datetime import datetime, timezone

from psycopg.types.json import Jsonb

from db import get_conn, init_db
from analytics import init_analytics, rebuild_daily_metrics
from baselines import init_baselines, rebuild_baselines
from sync import incremental_sync
from recommendations import daily_recommendation
from freshness import freshness_status

from daily_health_intelligence_store import (
    get_daily_health_intelligence,
)
from todays_plan_store import (
    invalidate_todays_plan,
)


# ============================================================
# DATABASE SETUP
# ============================================================

def init_automation_tables():

    with get_conn() as conn:

        with conn.cursor() as cur:

            cur.execute("""
                CREATE TABLE IF NOT EXISTS whoop_daily_intelligence (
                    metric_date DATE PRIMARY KEY,
                    deterministic_recommendation JSONB NOT NULL,
                    ai_brief JSONB,
                    model TEXT,
                    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS whoop_daily_automation_runs (
                    id BIGSERIAL PRIMARY KEY,
                    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    completed_at TIMESTAMPTZ,
                    status TEXT NOT NULL DEFAULT 'running',
                    metric_date DATE,
                    sync_result JSONB,
                    analytics_result JSONB,
                    baselines_result JSONB,
                    deterministic_recommendation JSONB,
                    ai_result JSONB,
                    freshness_result JSONB,
                    error TEXT
                )
            """)

            cur.execute("""
                ALTER TABLE whoop_daily_automation_runs
                ADD COLUMN IF NOT EXISTS freshness_result JSONB
            """)


# ============================================================
# AUTOMATION RUN AUDIT
# ============================================================

def start_run():

    with get_conn() as conn:

        with conn.cursor() as cur:

            cur.execute("""
                INSERT INTO whoop_daily_automation_runs
                DEFAULT VALUES
                RETURNING id
            """)

            return cur.fetchone()["id"]


def finish_run(
    run_id,
    status,
    metric_date,
    sync_result,
    analytics_result,
    baselines_result,
    deterministic,
    intelligence_result,
    freshness,
):

    with get_conn() as conn:

        with conn.cursor() as cur:

            cur.execute("""
                UPDATE whoop_daily_automation_runs
                SET completed_at = NOW(),
                    status = %s,
                    metric_date = %s,
                    sync_result = %s,
                    analytics_result = %s,
                    baselines_result = %s,
                    deterministic_recommendation = %s,
                    ai_result = %s,
                    freshness_result = %s
                WHERE id = %s
            """, (
                status,
                metric_date,
                Jsonb(sync_result),
                Jsonb(analytics_result),
                Jsonb(baselines_result),
                (
                    Jsonb(deterministic)
                    if deterministic is not None
                    else None
                ),
                (
                    Jsonb(intelligence_result)
                    if intelligence_result is not None
                    else None
                ),
                Jsonb(freshness),
                run_id,
            ))


def fail_run(
    run_id,
    error_text,
):

    with get_conn() as conn:

        with conn.cursor() as cur:

            cur.execute("""
                UPDATE whoop_daily_automation_runs
                SET completed_at = NOW(),
                    status = 'failed',
                    error = %s
                WHERE id = %s
            """, (
                error_text[:10000],
                run_id,
            ))


# ============================================================
# LEGACY DAILY INTELLIGENCE COMPATIBILITY
#
# Keep the historical table populated while the authoritative
# mobile cache remains public.daily_health_intelligence.
# ============================================================

def store_intelligence(
    deterministic,
    intelligence_result,
):

    metric_date = deterministic.get(
        "metric_date"
    )

    if not metric_date:

        raise RuntimeError(
            "Deterministic recommendation "
            "did not contain metric_date."
        )

    brief = (
        intelligence_result.get(
            "brief"
        )
        or {}
    )

    model = (
        intelligence_result.get(
            "model"
        )
    )

    with get_conn() as conn:

        with conn.cursor() as cur:

            cur.execute("""
                INSERT INTO whoop_daily_intelligence (
                    metric_date,
                    deterministic_recommendation,
                    ai_brief,
                    model,
                    generated_at
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    NOW()
                )
                ON CONFLICT(metric_date)
                DO UPDATE SET
                    deterministic_recommendation =
                        EXCLUDED.deterministic_recommendation,

                    ai_brief =
                        EXCLUDED.ai_brief,

                    model =
                        EXCLUDED.model,

                    generated_at =
                        NOW()
            """, (
                metric_date,
                Jsonb(deterministic),
                Jsonb(brief),
                model,
            ))

    return metric_date


# ============================================================
# AUTHORITATIVE DAILY PIPELINE
# ============================================================

def run_daily_pipeline():

    init_db()
    init_analytics()
    init_baselines()
    init_automation_tables()

    run_id = start_run()

    print(
        f"[daily-job] started run_id={run_id}",
        flush=True,
    )

    try:

        # ----------------------------------------------------
        # 1. Synchronize WHOOP
        # ----------------------------------------------------

        print(
            "[daily-job] 1/6 syncing latest WHOOP records",
            flush=True,
        )

        sync_result = asyncio.run(
            incremental_sync()
        )

        # ----------------------------------------------------
        # 2. Rebuild daily metrics
        # ----------------------------------------------------

        print(
            "[daily-job] 2/6 rebuilding daily metrics",
            flush=True,
        )

        analytics_result = (
            rebuild_daily_metrics()
        )

        # ----------------------------------------------------
        # 3. Rebuild baselines
        # ----------------------------------------------------

        print(
            "[daily-job] 3/6 rebuilding personal baselines",
            flush=True,
        )

        baselines_result = (
            rebuild_baselines()
        )

        # ----------------------------------------------------
        # 4. Confirm WHOOP physiology is current
        # ----------------------------------------------------

        print(
            "[daily-job] 4/6 checking WHOOP freshness",
            flush=True,
        )

        freshness = (
            freshness_status()
        )

        print(
            json.dumps(
                {
                    "freshness":
                        freshness
                },
                default=str,
            ),
            flush=True,
        )

        if not freshness[
            "can_generate_current_recommendation"
        ]:

            status = (
                "pending_freshness"
                if freshness["status"]
                == "pending_today"
                else "stale_data"
            )

            finish_run(
                run_id,
                status,
                freshness.get(
                    "latest_physiology_date"
                ),
                sync_result,
                analytics_result,
                baselines_result,
                None,
                None,
                freshness,
            )

            result = {
                "status":
                    status,

                "run_id":
                    run_id,

                "freshness":
                    freshness,

                "sync_new_rows":
                    sync_result.get(
                        "new_rows"
                    ),

                "message": (
                    "No current recommendation generated "
                    "because WHOOP physiology is not ready."
                ),
            }

            print(
                json.dumps(
                    result,
                    default=str,
                ),
                flush=True,
            )

            return result

        # ----------------------------------------------------
        # 5. Deterministic recommendation
        # ----------------------------------------------------

        print(
            "[daily-job] 5/6 calculating deterministic recommendation",
            flush=True,
        )

        deterministic = (
            daily_recommendation()
        )

        # ----------------------------------------------------
        # 6. AUTHORITATIVE Daily Health Intelligence
        #
        # This is the cache used by:
        #
        # /api/v1/health-intelligence/today
        #
        # force_refresh=True guarantees that an event-driven
        # recalculation produces fresh intelligence.
        # ----------------------------------------------------

        print(
            "[daily-job] 6/6 refreshing current Daily Health Intelligence",
            flush=True,
        )

        intelligence_result = (
            get_daily_health_intelligence(
                force_refresh=True
            )
        )

        if (
            intelligence_result.get(
                "status"
            )
            != "ok"
        ):

            raise RuntimeError(
                "Daily Health Intelligence "
                "did not return status=ok."
            )

        # ----------------------------------------------------
        # Maintain historical compatibility table
        # ----------------------------------------------------

        metric_date = (
            store_intelligence(
                deterministic,
                intelligence_result,
            )
        )

        # ----------------------------------------------------
        # Complete automation audit
        # ----------------------------------------------------

        finish_run(
            run_id,
            "completed",
            metric_date,
            sync_result,
            analytics_result,
            baselines_result,
            deterministic,
            intelligence_result,
            freshness,
        )

        # The complete, current physiology and intelligence state is now
        # durable. Invalidate only at this success boundary so the next
        # Today request cannot cache a plan built from partially refreshed
        # WHOOP data.
        try:

            invalidated_date = (
                invalidate_todays_plan()
            )

            print(
                "TODAYS_PLAN_CACHE "
                "status=invalidated "
                "reason=whoop_daily_pipeline "
                f"date={invalidated_date}",
                flush=True,
            )

        except Exception as exc:

            # Cache maintenance must not rewrite an otherwise successful
            # pipeline audit result or obscure the refreshed source data.
            print(
                "TODAYS_PLAN_CACHE "
                "status=invalidation_failed "
                "reason=whoop_daily_pipeline "
                f"error={type(exc).__name__}",
                flush=True,
            )

        brief = (
            intelligence_result.get(
                "brief"
            )
            or {}
        )

        result = {
            "status":
                "completed",

            "run_id":
                run_id,

            "metric_date":
                metric_date,

            "freshness":
                freshness,

            "sync_new_rows":
                sync_result.get(
                    "new_rows"
                ),

            "training_recommendation":
                deterministic.get(
                    "training_recommendation"
                ),

            "overall_status":
                deterministic.get(
                    "overall_status"
                ),

            "ai_headline":
                brief.get(
                    "headline"
                ),

            "intelligence_cache":
                intelligence_result.get(
                    "cache"
                ),

            "completed_at":
                datetime.now(
                    timezone.utc
                ).isoformat(),
        }

        print(
            json.dumps(
                result,
                default=str,
            ),
            flush=True,
        )

        return result

    except Exception as exc:

        error_text = (
            f"{type(exc).__name__}: {exc}\n"
            f"{traceback.format_exc()}"
        )

        fail_run(
            run_id,
            error_text,
        )

        print(
            "[daily-job] FAILED",
            flush=True,
        )

        print(
            error_text,
            flush=True,
        )

        raise


# ============================================================
# TERMINAL ENTRY POINT
# ============================================================

if __name__ == "__main__":

    run_daily_pipeline()
