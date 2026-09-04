from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from db import get_conn

EASTERN = ZoneInfo("America/New_York")

def latest_physiology_date():
    source = daily_source_freshness()
    return source.get("metric_date")


def _iso_value(value):
    return value.isoformat() if value is not None else None


def daily_source_freshness():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    metric_date,
                    source_updated_at,
                    generated_at
                FROM whoop_daily_metrics
                WHERE has_recovery = TRUE
                  AND has_sleep = TRUE
                ORDER BY metric_date DESC
                LIMIT 1
            """)
            row = cur.fetchone()

    if not row:
        return {
            "metric_date": None,
            "source_updated_at": None,
            "metrics_generated_at": None,
        }

    return {
        "metric_date": _iso_value(row.get("metric_date")),
        "source_updated_at": _iso_value(row.get("source_updated_at")),
        "metrics_generated_at": _iso_value(row.get("generated_at")),
    }


def weekly_source_freshness():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                WITH latest AS (
                    SELECT MAX(metric_date) AS metric_date
                    FROM whoop_daily_metrics
                )
                SELECT
                    latest.metric_date,
                    MAX(metrics.source_updated_at) AS source_updated_at,
                    MAX(metrics.generated_at) AS metrics_generated_at
                FROM latest
                LEFT JOIN whoop_daily_metrics AS metrics
                  ON metrics.metric_date BETWEEN
                     latest.metric_date - 6
                     AND latest.metric_date
                GROUP BY latest.metric_date
            """)
            row = cur.fetchone()

    if not row:
        return {
            "metric_date": None,
            "source_updated_at": None,
            "metrics_generated_at": None,
        }

    return {
        "metric_date": _iso_value(row.get("metric_date")),
        "source_updated_at": _iso_value(row.get("source_updated_at")),
        "metrics_generated_at": _iso_value(row.get("metrics_generated_at")),
    }

def freshness_status(now_utc=None):
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    local_now = now_utc.astimezone(EASTERN)
    local_today = local_now.date()
    source = daily_source_freshness()
    latest_date_text = source.get("metric_date")
    latest_date = (
        datetime.fromisoformat(latest_date_text).date()
        if latest_date_text
        else None
    )

    if latest_date is None:
        return {
            "status": "no_data",
            "local_now": local_now.isoformat(),
            "local_today": local_today.isoformat(),
            "latest_physiology_date": None,
            "age_days": None,
            "can_generate_current_recommendation": False,
            "source_freshness": source,
            "message": "No complete recovery/sleep physiology is available.",
        }

    age_days = (local_today - latest_date).days

    if age_days == 0:
        status = "fresh"
        can_generate = True
        message = (
            f"Latest complete WHOOP physiology is {latest_date.isoformat()}, "
            "matching today's Eastern coaching date."
        )
    elif age_days == 1:
        status = "pending_today"
        can_generate = False
        message = (
            f"Latest complete WHOOP physiology is {latest_date.isoformat()}. "
            "Today's completed sleep/recovery has not been ingested yet. "
            "Do not treat the prior recommendation as today's recommendation."
        )
    else:
        status = "stale"
        can_generate = False
        message = (
            f"Latest complete WHOOP physiology is {latest_date.isoformat()}, "
            f"{age_days} calendar days behind today. "
            "A new training recommendation should not be generated."
        )

    return {
        "status": status,
        "local_now": local_now.isoformat(),
        "local_today": local_today.isoformat(),
        "latest_physiology_date": latest_date.isoformat(),
        "age_days": age_days,
        "can_generate_current_recommendation": can_generate,
        "source_freshness": source,
        "message": message,
    }
