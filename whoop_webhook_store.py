from psycopg.types.json import Jsonb

from db import get_conn


def init_whoop_webhook_tables():

    with get_conn() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                CREATE TABLE IF NOT EXISTS whoop_webhook_events (
                    trace_id TEXT PRIMARY KEY,
                    event_type TEXT,
                    resource_id TEXT,
                    user_id TEXT,
                    payload JSONB,
                    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    pipeline_triggered BOOLEAN NOT NULL DEFAULT FALSE,
                    pipeline_status TEXT,
                    pipeline_completed_at TIMESTAMPTZ,
                    pipeline_error TEXT
                )
            """)


def store_webhook_event(
    trace_id,
    event_type,
    resource_id,
    user_id,
    payload,
):

    if not trace_id:
        raise ValueError(
            "WHOOP webhook event did not contain trace_id."
        )

    with get_conn() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                INSERT INTO whoop_webhook_events (
                    trace_id,
                    event_type,
                    resource_id,
                    user_id,
                    payload
                )
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (trace_id) DO NOTHING
                RETURNING trace_id
            """, (
                trace_id,
                event_type,
                resource_id,
                str(user_id) if user_id is not None else None,
                Jsonb(payload),
            ))

            row = cur.fetchone()

    return row is not None


def mark_pipeline_started(
    trace_id,
):

    with get_conn() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                UPDATE whoop_webhook_events
                SET pipeline_triggered = TRUE,
                    pipeline_status = 'running'
                WHERE trace_id = %s
            """, (
                trace_id,
            ))


def mark_pipeline_completed(
    trace_id,
):

    with get_conn() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                UPDATE whoop_webhook_events
                SET pipeline_status = 'completed',
                    pipeline_completed_at = NOW(),
                    pipeline_error = NULL
                WHERE trace_id = %s
            """, (
                trace_id,
            ))


def mark_pipeline_failed(
    trace_id,
    error_text,
):

    with get_conn() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                UPDATE whoop_webhook_events
                SET pipeline_status = 'failed',
                    pipeline_completed_at = NOW(),
                    pipeline_error = %s
                WHERE trace_id = %s
            """, (
                str(error_text)[:10000],
                trace_id,
            ))