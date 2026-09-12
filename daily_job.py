import asyncio
import json
import traceback
from datetime import datetime, timezone

from psycopg.types.json import Jsonb

from db import get_conn, init_db, request_scoped_connection
import time
from whoop_refresh import (coaching_date, mark_started, read_state, raw_source_signature, data_version, complete, mark_failed, observe_source)
from todays_plan import build_todays_plan
from daily_health_intelligence import build_daily_health_ai_payload, generate_daily_health_intelligence
from daily_health_intelligence_store import save_intelligence, _stored_response
from todays_plan_store import load_cached_plan, save_plan
from json_safe import json_safe
from analytics import init_analytics, rebuild_daily_metrics
from baselines import init_baselines, rebuild_baselines
from sync import incremental_sync
from recommendations import daily_recommendation
from freshness import freshness_status

from whoop_webhook_store import pipeline_lock


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
                Jsonb(json_safe(sync_result)),
                Jsonb(json_safe(analytics_result)),
                Jsonb(json_safe(baselines_result)),
                (
                    Jsonb(json_safe(deterministic))
                    if deterministic is not None
                    else None
                ),
                (
                    Jsonb(json_safe(intelligence_result))
                    if intelligence_result is not None
                    else None
                ),
                Jsonb(json_safe(freshness)),
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
                Jsonb(json_safe(deterministic)),
                Jsonb(json_safe(brief)),
                model,
            ))

    return metric_date


# ============================================================
# AUTHORITATIVE DAILY PIPELINE
# ============================================================

def run_daily_pipeline(_lock_held=False):
    if _lock_held:
        return _run_daily_pipeline()
    with pipeline_lock() as acquired:
        if not acquired:
            return {"status": "skipped_pipeline_busy"}
        return _run_daily_pipeline()


def _run_daily_pipeline():

    started = time.monotonic()
    day = coaching_date()
    mark_started("daily_pipeline")
    generation = read_state(day)["generation"]
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

        signature = raw_source_signature()
        prior_state = read_state(day)
        cached = load_cached_plan(day)
        if (cached and prior_state.get("source_signature") == signature
                and not any((sync_result.get("new_rows") or {}).values())
                and data_version((cached.get("plan_payload") or {}).get("source_freshness"))
                    == prior_state.get("last_completed_data_version")
                and prior_state.get("last_completed_data_version")):
            with get_conn() as conn, conn.cursor() as cur:
                complete(cur, day, generation, prior_state["last_completed_data_version"],
                         prior_state.get("latest_seen_source_updated_at"), signature)
            finish_run(run_id, "completed", day, sync_result, {}, {}, None, None, freshness_status())
            print("WHOOP_REFRESH_COALESCE superseded_event_no_new_source_data skip=true "
                  "new_source_data=false rerun_skipped=true today_builds=0 llm_calls=0", flush=True)
            return {"status": "completed", "run_id": run_id, "rebuild_skipped": True,
                    "pipeline_seconds": time.monotonic() - started, "sync_new_rows": sync_result.get("new_rows")}

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

        observe_source(day, freshness.get("source_freshness"))

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
        # Build one authoritative Today plan and use it for synthesis and
        # cache publication. Unchanged-source runs returned before this point.
        # ----------------------------------------------------

        print(
            "[daily-job] 6/6 refreshing current Daily Health Intelligence",
            flush=True,
        )

        plan = build_todays_plan()
        if not isinstance(plan, dict) or plan.get("status") != "ok" or str(plan.get("plan_date")) != day:
            raise RuntimeError("Current Today plan was not built successfully")
        source = {**(freshness.get("source_freshness") or {}), "source_revision": signature}
        plan["source_freshness"] = source
        payload = build_daily_health_ai_payload(plan=plan)
        payload["source_freshness"] = source
        generated = generate_daily_health_intelligence(payload)
        if generated.get("status") != "ok":
            raise RuntimeError("Daily Health Intelligence did not return status=ok")
        # No expensive work in this transaction. Both caches and the completion
        # generation become visible together. Newer accepted events stay pending.
        with request_scoped_connection():
            saved = save_intelligence(payload, generated)
            intelligence_result = _stored_response(saved)
            intelligence_result["cache"]["llm_called"] = generated.get("ai_synthesis_status", "success") == "success"
            save_plan(plan)
            with get_conn() as conn, conn.cursor() as cur:
                complete(cur, day, generation, data_version(source), source.get("source_updated_at"), signature)
        metric_date = store_intelligence(deterministic, intelligence_result)
        print(f"TODAYS_PLAN_CACHE status=replaced data_version={data_version(source)} "
              f"today_builds=1 llm_calls={generated.get('llm_request_count', 0)} "
              f"pipeline_seconds={time.monotonic() - started:.3f}", flush=True)

        # ----------------------------------------------------
        # Complete automation audit (bookkeeping)
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

            "ai_synthesis_status":
                intelligence_result.get(
                    "ai_synthesis_status",
                    "success",
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

        mark_failed(day, exc)
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
