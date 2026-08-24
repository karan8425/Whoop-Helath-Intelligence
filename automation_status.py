from db import get_conn


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


def latest_stored_intelligence():
    init_automation_tables()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT metric_date, deterministic_recommendation, ai_brief,
                       model, generated_at
                FROM whoop_daily_intelligence
                ORDER BY metric_date DESC
                LIMIT 1
            """)
            row = cur.fetchone()

    if not row:
        return None

    return {
        **row,
        "metric_date": row["metric_date"].isoformat(),
        "generated_at": row["generated_at"].isoformat(),
    }


def latest_automation_run():
    init_automation_tables()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, started_at, completed_at, status, metric_date,
                       sync_result, analytics_result, baselines_result,
                       deterministic_recommendation, ai_result, error
                FROM whoop_daily_automation_runs
                ORDER BY id DESC
                LIMIT 1
            """)
            row = cur.fetchone()

    if not row:
        return None

    return {
        **row,
        "started_at": row["started_at"].isoformat() if row["started_at"] else None,
        "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
        "metric_date": row["metric_date"].isoformat() if row["metric_date"] else None,
    }


def automation_summary():
    init_automation_tables()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                  COUNT(*) AS total_runs,
                  COUNT(*) FILTER (WHERE status='completed') AS completed_runs,
                  COUNT(*) FILTER (WHERE status='failed') AS failed_runs,
                  MAX(completed_at) FILTER (WHERE status='completed') AS last_success
                FROM whoop_daily_automation_runs
            """)
            summary = cur.fetchone()

    return {
        "total_runs": summary["total_runs"],
        "completed_runs": summary["completed_runs"],
        "failed_runs": summary["failed_runs"],
        "last_success": (
            summary["last_success"].isoformat()
            if summary["last_success"] else None
        ),
    }
