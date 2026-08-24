import json
import traceback
from datetime import datetime, timezone

from psycopg.types.json import Jsonb

from db import get_conn, init_db
from analytics import init_analytics, rebuild_daily_metrics
from baselines import init_baselines, rebuild_baselines
from sync import incremental_sync
from recommendations import daily_recommendation
from ai_intelligence import generate_daily_ai_brief


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
                    error TEXT
                )
            """)


def start_run():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO whoop_daily_automation_runs DEFAULT VALUES
                RETURNING id
            """)
            return cur.fetchone()["id"]


def finish_run(run_id, metric_date, sync_result, analytics_result,
               baselines_result, deterministic, ai_result):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE whoop_daily_automation_runs
                SET completed_at=NOW(),
                    status='completed',
                    metric_date=%s,
                    sync_result=%s,
                    analytics_result=%s,
                    baselines_result=%s,
                    deterministic_recommendation=%s,
                    ai_result=%s
                WHERE id=%s
            """, (
                metric_date,
                Jsonb(sync_result),
                Jsonb(analytics_result),
                Jsonb(baselines_result),
                Jsonb(deterministic),
                Jsonb(ai_result),
                run_id,
            ))


def fail_run(run_id, error_text):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE whoop_daily_automation_runs
                SET completed_at=NOW(),
                    status='failed',
                    error=%s
                WHERE id=%s
            """, (error_text[:10000], run_id))


def store_intelligence(deterministic, ai_result):
    metric_date = deterministic.get("metric_date")
    if not metric_date:
        raise RuntimeError("Deterministic recommendation did not contain metric_date.")

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
                VALUES (%s,%s,%s,%s,NOW())
                ON CONFLICT(metric_date) DO UPDATE SET
                    deterministic_recommendation=EXCLUDED.deterministic_recommendation,
                    ai_brief=EXCLUDED.ai_brief,
                    model=EXCLUDED.model,
                    generated_at=NOW()
            """, (
                metric_date,
                Jsonb(deterministic),
                Jsonb(ai_result.get("brief")),
                ai_result.get("model"),
            ))

    return metric_date


def run_daily_pipeline():
    init_db()
    init_analytics()
    init_baselines()
    init_automation_tables()

    run_id = start_run()
    print(f"[daily-job] started run_id={run_id}", flush=True)

    try:
        print("[daily-job] 1/5 syncing latest WHOOP records", flush=True)
        sync_result = __import__("asyncio").run(incremental_sync())

        print("[daily-job] 2/5 rebuilding daily metrics", flush=True)
        analytics_result = rebuild_daily_metrics()

        print("[daily-job] 3/5 rebuilding personal baselines", flush=True)
        baselines_result = rebuild_baselines()

        print("[daily-job] 4/5 calculating deterministic recommendation", flush=True)
        deterministic = daily_recommendation()

        print("[daily-job] 5/5 generating OpenAI daily briefing", flush=True)
        ai_result = generate_daily_ai_brief()

        # Guardrail: never allow AI to change deterministic training category.
        ai_result["brief"]["training_recommendation"] = deterministic["training_recommendation"]

        metric_date = store_intelligence(deterministic, ai_result)

        finish_run(
            run_id,
            metric_date,
            sync_result,
            analytics_result,
            baselines_result,
            deterministic,
            ai_result,
        )

        result = {
            "status": "completed",
            "run_id": run_id,
            "metric_date": metric_date,
            "sync_new_rows": sync_result.get("new_rows"),
            "daily_metrics": analytics_result,
            "baselines": {
                "baseline_rows": baselines_result.get("baseline_rows"),
                "dates": baselines_result.get("dates"),
            },
            "training_recommendation": deterministic.get("training_recommendation"),
            "overall_status": deterministic.get("overall_status"),
            "ai_headline": ai_result.get("brief", {}).get("headline"),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

        print(json.dumps(result, default=str), flush=True)
        return result

    except Exception as exc:
        error_text = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        fail_run(run_id, error_text)
        print("[daily-job] FAILED", flush=True)
        print(error_text, flush=True)
        raise


if __name__ == "__main__":
    run_daily_pipeline()
