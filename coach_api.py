import os
import secrets
from fastapi import HTTPException, Request

from db import get_conn
from freshness import freshness_status
from automation_status import latest_stored_intelligence, automation_summary
from baselines import latest_baselines, metric_history


CHATGPT_ACTION_API_KEY = os.getenv("CHATGPT_ACTION_API_KEY", "")

ALLOWED_METRICS = {
    "recovery_score",
    "hrv_rmssd_milli",
    "resting_heart_rate",
    "sleep_duration_hours",
    "sleep_performance_percentage",
    "sleep_consistency_percentage",
    "cycle_strain",
    "workout_count",
}


def require_action_api_key(request: Request):
    if not CHATGPT_ACTION_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="CHATGPT_ACTION_API_KEY is not configured."
        )

    auth = request.headers.get("authorization", "")
    expected = f"Bearer {CHATGPT_ACTION_API_KEY}"

    if not secrets.compare_digest(auth, expected):
        raise HTTPException(status_code=401, detail="Invalid API key.")


def coach_today():
    freshness = freshness_status()
    stored = latest_stored_intelligence()

    result = {
        "freshness": freshness,
        "safe_to_treat_as_current": False,
        "current_intelligence": None,
        "historical_context": None,
    }

    if not stored:
        result["message"] = "No stored daily intelligence is available yet."
        return result

    stored_date = stored.get("metric_date")
    latest_date = freshness.get("latest_physiology_date")

    safe = (
        freshness.get("can_generate_current_recommendation") is True
        and stored_date == latest_date
    )

    result["safe_to_treat_as_current"] = safe

    compact = {
        "metric_date": stored_date,
        "generated_at": stored.get("generated_at"),
        "deterministic_recommendation": stored.get("deterministic_recommendation"),
        "ai_brief": stored.get("ai_brief"),
        "model": stored.get("model"),
    }

    if safe:
        result["current_intelligence"] = compact
        result["message"] = (
            "The stored intelligence matches the latest complete WHOOP physiology "
            "and may be treated as current wearable-based guidance."
        )
    else:
        result["historical_context"] = compact
        result["message"] = (
            "The stored recommendation is historical context only. "
            "Do not present it as today's current training recommendation."
        )

    return result


def coach_status():
    return {
        "freshness": freshness_status(),
        "automation": automation_summary(),
    }


def coach_daily_history(days=30):
    days = max(1, min(int(days), 90))

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    metric_date,
                    recovery_score,
                    resting_heart_rate,
                    hrv_rmssd_milli,
                    sleep_duration_hours,
                    sleep_performance_percentage,
                    sleep_consistency_percentage,
                    cycle_strain,
                    workout_count,
                    workout_total_strain,
                    has_recovery,
                    has_sleep,
                    has_workout
                FROM whoop_daily_metrics
                ORDER BY metric_date DESC
                LIMIT %s
            """, (days,))
            rows = cur.fetchall()

    return {
        "days_requested": days,
        "records": rows,
        "note": (
            "Null physiological values represent missing WHOOP observations and "
            "must not be interpreted as zero."
        ),
    }


def coach_latest_baselines():
    return latest_baselines()


def coach_metric_history(metric, days=30):
    if metric not in ALLOWED_METRICS:
        raise ValueError(
            "Invalid metric. Allowed metrics: " + ", ".join(sorted(ALLOWED_METRICS))
        )
    days = max(1, min(int(days), 365))
    return metric_history(metric, days)
