from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")


def _load_workout_eligibility():
    """Load one eligibility record per stored Tonal workout."""

    from db import get_conn

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    w.activity_id,
                    (
                        w.begin_time
                        AT TIME ZONE
                        'America/New_York'
                    )::date AS workout_date,
                    COALESCE(
                        o.include_in_training_analysis,
                        TRUE
                    ) AS included,
                    EXISTS (
                        SELECT 1
                        FROM tonal_sets s
                        WHERE s.activity_id = w.activity_id
                    ) AS has_sets
                FROM tonal_workouts w
                LEFT JOIN tonal_workout_overrides o
                    ON o.activity_id = w.activity_id
                """
            )

            return cur.fetchall()


def _count_qualifying_sessions(
    rows,
    window_start,
    window_end,
):
    """Apply the existing Tonal whole-session eligibility policy."""

    activity_ids = {
        row["activity_id"]
        for row in rows
        if row.get("activity_id") is not None
        and row.get("included") is True
        and row.get("has_sets") is True
        and row.get("workout_date") is not None
        and window_start <= row["workout_date"] <= window_end
    }

    return len(activity_ids)


def strength_adherence(
    target_sessions_per_week,
    now=None,
):
    """Return rolling seven-calendar-day Tonal goal adherence."""

    current = now or datetime.now(timezone.utc)

    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    window_end = current.astimezone(EASTERN).date()
    window_start = window_end - timedelta(days=6)

    base = {
        "sessions_7d": None,
        "target_sessions_per_week": target_sessions_per_week,
        "percentage_of_target": None,
        "remaining_sessions": None,
        "window_start_date": window_start.isoformat(),
        "window_end_date": window_end.isoformat(),
    }

    if target_sessions_per_week is None:
        return {
            "status": "not_configured",
            **base,
        }

    target = int(target_sessions_per_week)
    base["target_sessions_per_week"] = target

    try:
        rows = _load_workout_eligibility()
    except Exception:
        return {
            "status": "not_connected",
            **base,
        }

    # An empty table cannot distinguish a never-synced integration from
    # a successfully synced account with no workouts. Existing history is
    # therefore the minimum evidence used to report a genuine recent zero.
    if not rows:
        return {
            "status": "not_connected",
            **base,
        }

    sessions = _count_qualifying_sessions(
        rows,
        window_start,
        window_end,
    )

    percentage = (
        100.0
        if target == 0
        else round(sessions / target * 100.0, 1)
    )

    return {
        "status": (
            "target_met"
            if sessions >= target
            else "below_target"
        ),
        "sessions_7d": sessions,
        "target_sessions_per_week": target,
        "percentage_of_target": percentage,
        "remaining_sessions": max(0, target - sessions),
        "window_start_date": window_start.isoformat(),
        "window_end_date": window_end.isoformat(),
    }
