from contextlib import contextmanager

from psycopg.types.json import Jsonb

from db import get_conn


# ============================================================
# CONFIGURATION
# ============================================================

PIPELINE_LOCK_ID = 8425001


# ============================================================
# DATABASE SETUP / MIGRATION
# ============================================================

def init_whoop_webhook_tables():

    with get_conn() as conn:

        with conn.cursor() as cur:

            # ------------------------------------------------
            # Create the current schema for new installations.
            # ------------------------------------------------

            cur.execute("""
                CREATE TABLE IF NOT EXISTS whoop_webhook_events (
                    id BIGSERIAL PRIMARY KEY,

                    trace_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,

                    resource_id TEXT,
                    user_id TEXT,
                    payload JSONB,

                    received_at TIMESTAMPTZ
                        NOT NULL DEFAULT NOW(),

                    pipeline_triggered BOOLEAN
                        NOT NULL DEFAULT FALSE,

                    pipeline_status TEXT,
                    pipeline_completed_at TIMESTAMPTZ,
                    pipeline_error TEXT
                )
            """)

            # ------------------------------------------------
            # Existing production table migration.
            #
            # The original table used trace_id as its primary
            # key. WHOOP can use the same trace_id for related
            # event types, such as:
            #
            # sleep.updated
            # recovery.updated
            #
            # Therefore trace_id alone cannot be unique.
            # ------------------------------------------------

            cur.execute("""
                ALTER TABLE whoop_webhook_events
                ADD COLUMN IF NOT EXISTS id BIGSERIAL
            """)

            cur.execute("""
                ALTER TABLE whoop_webhook_events
                ADD COLUMN IF NOT EXISTS event_type TEXT
            """)

            cur.execute("""
                ALTER TABLE whoop_webhook_events
                ADD COLUMN IF NOT EXISTS resource_id TEXT
            """)

            cur.execute("""
                ALTER TABLE whoop_webhook_events
                ADD COLUMN IF NOT EXISTS user_id TEXT
            """)

            cur.execute("""
                ALTER TABLE whoop_webhook_events
                ADD COLUMN IF NOT EXISTS payload JSONB
            """)

            cur.execute("""
                ALTER TABLE whoop_webhook_events
                ADD COLUMN IF NOT EXISTS received_at TIMESTAMPTZ
                    NOT NULL DEFAULT NOW()
            """)

            cur.execute("""
                ALTER TABLE whoop_webhook_events
                ADD COLUMN IF NOT EXISTS pipeline_triggered BOOLEAN
                    NOT NULL DEFAULT FALSE
            """)

            cur.execute("""
                ALTER TABLE whoop_webhook_events
                ADD COLUMN IF NOT EXISTS pipeline_status TEXT
            """)

            cur.execute("""
                ALTER TABLE whoop_webhook_events
                ADD COLUMN IF NOT EXISTS pipeline_completed_at
                    TIMESTAMPTZ
            """)

            cur.execute("""
                ALTER TABLE whoop_webhook_events
                ADD COLUMN IF NOT EXISTS pipeline_error TEXT
            """)

            # ------------------------------------------------
            # Find and remove the old trace_id-only primary key.
            # ------------------------------------------------

            cur.execute("""
                SELECT conname
                FROM pg_constraint
                WHERE conrelid =
                    'whoop_webhook_events'::regclass
                  AND contype = 'p'
            """)

            primary_keys = cur.fetchall()

            for row in primary_keys:

                constraint_name = row[
                    "conname"
                ]

                cur.execute("""
                    SELECT a.attname
                    FROM pg_constraint c
                    JOIN unnest(c.conkey)
                        WITH ORDINALITY AS cols(attnum, ord)
                        ON TRUE
                    JOIN pg_attribute a
                        ON a.attrelid = c.conrelid
                       AND a.attnum = cols.attnum
                    WHERE c.conrelid =
                        'whoop_webhook_events'::regclass
                      AND c.conname = %s
                    ORDER BY cols.ord
                """, (
                    constraint_name,
                ))

                columns = [
                    item["attname"]
                    for item in cur.fetchall()
                ]

                if columns != ["id"]:

                    cur.execute(
                        f"""
                        ALTER TABLE whoop_webhook_events
                        DROP CONSTRAINT
                        "{constraint_name}"
                        """
                    )

            # ------------------------------------------------
            # Ensure every historical row has an id.
            # ------------------------------------------------

            cur.execute("""
                UPDATE whoop_webhook_events
                SET id = DEFAULT
                WHERE id IS NULL
            """)

            cur.execute("""
                ALTER TABLE whoop_webhook_events
                ALTER COLUMN id SET NOT NULL
            """)

            # ------------------------------------------------
            # Create id primary key if it does not exist.
            # ------------------------------------------------

            cur.execute("""
                SELECT 1
                FROM pg_constraint c
                JOIN pg_attribute a
                    ON a.attrelid = c.conrelid
                   AND a.attnum = ANY(c.conkey)
                WHERE c.conrelid =
                    'whoop_webhook_events'::regclass
                  AND c.contype = 'p'
                  AND a.attname = 'id'
            """)

            if not cur.fetchone():

                cur.execute("""
                    ALTER TABLE whoop_webhook_events
                    ADD CONSTRAINT
                        whoop_webhook_events_pkey
                    PRIMARY KEY (id)
                """)

            # ------------------------------------------------
            # event_type should exist for every new event.
            #
            # Historical rows should already contain it from
            # the previous implementation. We deliberately do
            # not force NOT NULL during migration in case an
            # old development row predates that field.
            # ------------------------------------------------

            # ------------------------------------------------
            # Remove any old UNIQUE(trace_id) constraint.
            # ------------------------------------------------

            cur.execute("""
                SELECT
                    c.conname,
                    ARRAY_AGG(
                        a.attname
                        ORDER BY cols.ord
                    ) AS columns
                FROM pg_constraint c
                JOIN unnest(c.conkey)
                    WITH ORDINALITY AS cols(attnum, ord)
                    ON TRUE
                JOIN pg_attribute a
                    ON a.attrelid = c.conrelid
                   AND a.attnum = cols.attnum
                WHERE c.conrelid =
                    'whoop_webhook_events'::regclass
                  AND c.contype = 'u'
                GROUP BY c.conname
            """)

            unique_constraints = (
                cur.fetchall()
            )

            for row in unique_constraints:

                if row["columns"] == [
                    "trace_id"
                ]:

                    constraint_name = (
                        row["conname"]
                    )

                    cur.execute(
                        f"""
                        ALTER TABLE whoop_webhook_events
                        DROP CONSTRAINT
                        "{constraint_name}"
                        """
                    )

            # ------------------------------------------------
            # Exact duplicate delivery protection.
            #
            # Related WHOOP events sharing the same trace_id
            # are now allowed as long as event_type differs.
            # ------------------------------------------------

            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS
                    uq_whoop_webhook_trace_event
                ON whoop_webhook_events (
                    trace_id,
                    event_type
                )
            """)

            cur.execute("""
                CREATE INDEX IF NOT EXISTS
                    idx_whoop_webhook_received_at
                ON whoop_webhook_events (
                    received_at DESC
                )
            """)


# ============================================================
# EVENT STORAGE / DEDUPLICATION
# ============================================================

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

    if not event_type:

        raise ValueError(
            "WHOOP webhook event did not contain type."
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
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
                ON CONFLICT (
                    trace_id,
                    event_type
                )
                DO NOTHING
                RETURNING id
            """, (
                trace_id,
                event_type,
                resource_id,
                (
                    str(user_id)
                    if user_id is not None
                    else None
                ),
                Jsonb(payload),
            ))

            row = cur.fetchone()

    if not row:

        return None

    return row["id"]


# ============================================================
# PIPELINE STATUS
# ============================================================

def mark_pipeline_started(
    event_id,
):

    with get_conn() as conn:

        with conn.cursor() as cur:

            cur.execute("""
                UPDATE whoop_webhook_events
                SET pipeline_triggered = TRUE,
                    pipeline_status = 'running',
                    pipeline_completed_at = NULL,
                    pipeline_error = NULL
                WHERE id = %s
            """, (
                event_id,
            ))


def mark_pipeline_completed(
    event_id,
):

    with get_conn() as conn:

        with conn.cursor() as cur:

            cur.execute("""
                UPDATE whoop_webhook_events
                SET pipeline_status = 'completed',
                    pipeline_completed_at = NOW(),
                    pipeline_error = NULL
                WHERE id = %s
            """, (
                event_id,
            ))


def mark_pipeline_skipped(
    event_id,
    reason,
):

    with get_conn() as conn:

        with conn.cursor() as cur:

            cur.execute("""
                UPDATE whoop_webhook_events
                SET pipeline_triggered = TRUE,
                    pipeline_status = %s,
                    pipeline_completed_at = NOW(),
                    pipeline_error = NULL
                WHERE id = %s
            """, (
                reason,
                event_id,
            ))


def mark_pipeline_failed(
    event_id,
    error_text,
):

    with get_conn() as conn:

        with conn.cursor() as cur:

            cur.execute("""
                UPDATE whoop_webhook_events
                SET pipeline_triggered = TRUE,
                    pipeline_status = 'failed',
                    pipeline_completed_at = NOW(),
                    pipeline_error = %s
                WHERE id = %s
            """, (
                str(error_text)[:10000],
                event_id,
            ))


# ============================================================
# DISTRIBUTED PIPELINE LOCK
# ============================================================

@contextmanager
def pipeline_lock():

    connection_context = (
        get_conn()
    )

    conn = (
        connection_context.__enter__()
    )

    acquired = False

    try:

        with conn.cursor() as cur:

            cur.execute("""
                SELECT
                    pg_try_advisory_lock(%s)
                    AS acquired
            """, (
                PIPELINE_LOCK_ID,
            ))

            row = cur.fetchone()

            acquired = bool(
                row["acquired"]
            )

        yield acquired

    finally:

        if acquired:

            try:

                with conn.cursor() as cur:

                    cur.execute("""
                        SELECT
                            pg_advisory_unlock(%s)
                    """, (
                        PIPELINE_LOCK_ID,
                    ))

            except Exception:

                pass

        connection_context.__exit__(
            None,
            None,
            None,
        )