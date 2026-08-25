from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from db import get_conn
from freshness import freshness_status
from healthkit_ingest import latest_apple_health


EASTERN = ZoneInfo("America/New_York")


def _lb(kg):
    if kg is None:
        return None
    return kg * 2.2046226218


def _latest_whoop_daily(metric_date):
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
                    sleep_efficiency_percentage,
                    respiratory_rate,
                    cycle_strain,
                    cycle_calories,
                    workout_count,
                    workout_total_strain,
                    workout_total_duration_hours,
                    workout_sports,
                    has_cycle,
                    has_recovery,
                    has_sleep,
                    has_workout
                FROM whoop_daily_metrics
                WHERE metric_date = %s
                LIMIT 1
            """, (metric_date,))
            return cur.fetchone()


def combined_daily_snapshot():
    local_now = datetime.now(timezone.utc).astimezone(EASTERN)
    local_today = local_now.date()

    whoop_freshness = freshness_status()
    whoop = _latest_whoop_daily(local_today)
    apple = latest_apple_health()

    body = apple.get("body") or {}
    activity = apple.get("activity")

    weight = body.get("body_weight")
    body_fat = body.get("body_fat_percentage")
    lean_mass = body.get("lean_body_mass")

    body_summary = {
        "weight": None,
        "body_fat_percentage": None,
        "lean_body_mass": None,
    }

    if weight:
        body_summary["weight"] = {
            "kg": weight.get("value"),
            "lb": _lb(weight.get("value")),
            "source_name": weight.get("source_name"),
            "observed_at": weight.get("observed_at"),
            "classification": weight.get("classification"),
            "coaching_eligible": weight.get("coaching_eligible"),
        }

    if body_fat:
        body_summary["body_fat_percentage"] = {
            "value": body_fat.get("value"),
            "source_name": body_fat.get("source_name"),
            "observed_at": body_fat.get("observed_at"),
            "classification": body_fat.get("classification"),
            "coaching_eligible": body_fat.get("coaching_eligible"),
        }

    if lean_mass:
        body_summary["lean_body_mass"] = {
            "kg": lean_mass.get("value"),
            "lb": _lb(lean_mass.get("value")),
            "source_name": lean_mass.get("source_name"),
            "observed_at": lean_mass.get("observed_at"),
            "classification": lean_mass.get("classification"),
            "coaching_eligible": lean_mass.get("coaching_eligible"),
        }

    whoop_ready = (
        whoop is not None
        and whoop_freshness.get("status") == "fresh"
        and whoop_freshness.get("can_generate_current_recommendation") is True
    )

    activity_ready = bool(
        activity
        and activity.get("classification") == "current"
        and activity.get("coaching_eligible") is True
    )

    body_ready = bool(
        weight
        and weight.get("coaching_eligible") is True
        and body_fat
        and body_fat.get("coaching_eligible") is True
    )

    data_readiness = {
        "whoop_current": whoop_ready,
        "body_composition_current": body_ready,
        "activity_current": activity_ready,
        "lean_mass_current": bool(
            lean_mass and lean_mass.get("coaching_eligible") is True
        ),
        "combined_coaching_ready": bool(
            whoop_ready and body_ready and activity_ready
        ),
    }

    notes = []

    if not body_ready:
        notes.append(
            "Current preferred-source weight/body-fat data is incomplete."
        )

    if lean_mass and not lean_mass.get("coaching_eligible"):
        notes.append(
            "Lean body mass is retained as context but excluded from current coaching because it is stale or from a non-preferred source."
        )

    if not whoop_ready:
        notes.append(
            "WHOOP physiology is not current enough for today's combined coaching."
        )

    if not activity_ready:
        notes.append(
            "Apple Health daily activity is not current enough for today's combined coaching."
        )

    return {
        "status": "ok",
        "coaching_date": local_today.isoformat(),
        "local_now": local_now.isoformat(),
        "data_readiness": data_readiness,
        "whoop_freshness": whoop_freshness,
        "whoop": whoop,
        "body_composition": body_summary,
        "activity": activity,
        "notes": notes,
        "interpretation_note": (
            "This endpoint aligns current WHOOP physiology with Apple Health/Hume "
            "body composition and activity for the same local coaching date. "
            "It does not yet generate combined recommendations."
        ),
    }
