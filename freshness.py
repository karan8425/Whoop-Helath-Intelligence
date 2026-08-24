from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo

from db import get_conn

EASTERN = ZoneInfo("America/New_York")


def latest_physiology_date():
    """
    Returns the newest date with recovery + sleep physiology in the normalized
    daily metrics layer. This is safer than using calendar max alone.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT MAX(metric_date) AS latest_date
                FROM whoop_daily_metrics
                WHERE has_recovery = TRUE
                  AND has_sleep = TRUE
            """)
            row = cur.fetchone()
    return row["latest_date"] if row else None


def freshness_status(now_utc=None):
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    local_now = now_utc.astimezone(EASTERN)
    local_today = local_now.date()
    latest_date = latest_physiology_date()

    if latest_date is None:
        return {
            "status": "no_data",
            "local_now": local_now.isoformat(),
            "local_today": local_today.isoformat(),
            "latest_physiology_date": None,
            "age_days": None,
            "can_generate_current_recommendation": False,
            "message": "No complete recovery/sleep physiology is available.",
        }

    age_days = (local_today - latest_date).days

    # At 5 AM Eastern, a sleep/recovery cycle commonly belongs to the date on
    # which that overnight cycle started. Therefore yesterday is considered fresh.
    if age_days <= 1:
        status = "fresh"
        can_generate = True
        message = (
            f"Latest complete WHOOP physiology is {latest_date.isoformat()}, "
            "which is current enough for this morning's recommendation."
        )
    elif age_days == 2:
        status = "pending_today"
        can_generate = False
        message = (
            f"Latest complete WHOOP physiology is {latest_date.isoformat()}. "
            "Today's recovery/sleep appears not to have arrived yet. "
            "Do not treat the prior training recommendation as today's recommendation."
        )
    else:
        status = "stale"
        can_generate = False
        message = (
            f"Latest complete WHOOP physiology is {latest_date.isoformat()}, "
            f"{age_days} calendar days behind the current Eastern date. "
            "A new training recommendation should not be generated."
        )

    return {
        "status": status,
        "local_now": local_now.isoformat(),
        "local_today": local_today.isoformat(),
        "latest_physiology_date": latest_date.isoformat(),
        "age_days": age_days,
        "can_generate_current_recommendation": can_generate,
        "message": message,
    }
