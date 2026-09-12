"""Durable refresh generations; publishing never acknowledges events newer than sync."""
import hashlib
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from db import get_conn


def coaching_date():
    return datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def data_version(source):
    source = source or {}
    if not source.get("metric_date") or not source.get("source_updated_at"):
        return None
    stamp = datetime.fromisoformat(str(source["source_updated_at"]).replace("Z", "+00:00"))
    token = str(source["metric_date"]) + "|" + stamp.astimezone(timezone.utc).isoformat()
    # The fingerprint also covers an existing row revised below a different
    # resource's MAX timestamp. It is stable across repeated ingestion.
    if source.get("source_revision"):
        token += "|" + source["source_revision"]
    return hashlib.sha256(token.encode()).hexdigest()[:24]


def ensure_table():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS whoop_refresh_state (
            coaching_date DATE PRIMARY KEY,
            generation BIGINT NOT NULL DEFAULT 0,
            completed_generation BIGINT NOT NULL DEFAULT 0,
            started_at TIMESTAMPTZ,
            trigger_type TEXT,
            latest_seen_source_updated_at TIMESTAMPTZ,
            last_completed_data_version TEXT,
            completed_at TIMESTAMPTZ,
            source_signature TEXT,
            error_type TEXT
        )""")


def mark_started(trigger_type, cur=None):
    if cur is None:
        ensure_table()
        with get_conn() as conn, conn.cursor() as cursor:
            return mark_started(trigger_type, cursor)
    cur.execute("""INSERT INTO whoop_refresh_state
        (coaching_date, generation, started_at, trigger_type)
        VALUES (%s, 1, NOW(), %s)
        ON CONFLICT (coaching_date) DO UPDATE SET
        generation = whoop_refresh_state.generation + 1,
        started_at = CASE WHEN whoop_refresh_state.generation >
            whoop_refresh_state.completed_generation THEN whoop_refresh_state.started_at ELSE NOW() END,
        trigger_type = EXCLUDED.trigger_type, error_type = NULL
        RETURNING generation""", (coaching_date(), trigger_type))
    generation = cur.fetchone()["generation"]
    print(f"WHOOP_REFRESH_STATE status=started source_event={trigger_type} generation={generation}", flush=True)
    return generation


def read_state(day=None):
    ensure_table()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM whoop_refresh_state WHERE coaching_date=%s", (day or coaching_date(),))
        row = dict(cur.fetchone() or {})
    row["refresh_in_progress"] = row.get("generation", 0) > row.get("completed_generation", 0)
    return row


def complete(cur, day, generation, version, source, signature):
    cur.execute("""UPDATE whoop_refresh_state SET
        completed_generation=GREATEST(completed_generation, %s),
        last_completed_data_version=%s, latest_seen_source_updated_at=%s,
        source_signature=%s, completed_at=NOW(), error_type=NULL
        WHERE coaching_date=%s""", (generation, version, source, signature, day))
    print(f"WHOOP_REFRESH_STATE status=completed new_data_version={version} generation={generation}", flush=True)


def mark_failed(day, error):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("UPDATE whoop_refresh_state SET error_type=%s WHERE coaching_date=%s",
                    (type(error).__name__, day))


def raw_source_signature():
    # Per-record timestamps detect revisions even when row counts and global MAX
    # timestamps do not change. synced_at is deliberately excluded.
    values = []
    with get_conn() as conn, conn.cursor() as cur:
        for table, key in (("whoop_cycles", "id"), ("whoop_recoveries", "sleep_id"),
                           ("whoop_sleeps", "id"), ("whoop_workouts", "id")):
            cur.execute(f"SELECT {key}::text AS id, updated_at FROM {table} ORDER BY {key}")
            values.append([table, [(r["id"], str(r["updated_at"])) for r in cur.fetchall()]])
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def metadata(payload, state):
    result = dict(payload)
    source = result.get("source_freshness") or {}
    result.update(data_version=data_version(source),
                  source_updated_at=source.get("source_updated_at"),
                  metrics_generated_at=source.get("metrics_generated_at"),
                  refresh_in_progress=state.get("refresh_in_progress", False),
                  latest_source_updated_at=str(state.get("latest_seen_source_updated_at") or source.get("source_updated_at") or "") or None,
                  refresh_started_at=str(state.get("started_at") or "") or None,
                  refresh_error=state.get("error_type"))
    result["freshness"] = {**(result.get("freshness") or {}),
        **{key: result[key] for key in ("data_version", "source_updated_at",
            "metrics_generated_at", "refresh_in_progress")}}
    print(f"API_REFRESH_STATE refresh_in_progress={result['refresh_in_progress']} "
          f"served_cache={result.get('status') == 'ok'} data_version={result['data_version']}", flush=True)
    return result


def observe_source(day, source):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("UPDATE whoop_refresh_state SET latest_seen_source_updated_at=%s WHERE coaching_date=%s",
                    ((source or {}).get("source_updated_at"), day))


def same_metric_source(left, right):
    # Latest metrics do not carry the raw-source fingerprint; a webhook
    # publication supplies it. Compare the authoritative metric timestamp here.
    left = {k: v for k, v in (left or {}).items() if k != "source_revision"}
    right = {k: v for k, v in (right or {}).items() if k != "source_revision"}
    return data_version(left) is not None and data_version(left) == data_version(right)
