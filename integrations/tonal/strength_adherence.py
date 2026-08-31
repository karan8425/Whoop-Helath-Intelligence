from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
ABBREVIATED_FREESTYLE_REASON = (
    "atypical abbreviated freestyle session"
)


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
                    o.exclusion_reason,
                    o.notes,
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


def _is_supplemental_strength_activity(row):
    """Recognize only the known abbreviated-but-real override case."""

    reason = row.get("exclusion_reason")

    if not isinstance(reason, str):
        return False

    normalized_reason = " ".join(
        reason.casefold().split()
    )

    return normalized_reason == ABBREVIATED_FREESTYLE_REASON


def _classify_sessions(
    rows,
    window_start,
    window_end,
):
    """Return deduplicated qualifying and supplemental activity IDs."""

    qualifying = set()
    supplemental = set()

    for row in rows:
        activity_id = row.get("activity_id")
        workout_date = row.get("workout_date")

        if (
            activity_id is None
            or row.get("has_sets") is not True
            or workout_date is None
            or not window_start <= workout_date <= window_end
        ):
            continue

        if row.get("included") is True:
            qualifying.add(activity_id)
            supplemental.discard(activity_id)
        elif (
            activity_id not in qualifying
            and _is_supplemental_strength_activity(row)
        ):
            supplemental.add(activity_id)

    supplemental.difference_update(qualifying)

    return qualifying, supplemental


def _count_qualifying_sessions(
    rows,
    window_start,
    window_end,
):
    """Apply the existing Tonal whole-session eligibility policy."""

    qualifying, _ = _classify_sessions(
        rows,
        window_start,
        window_end,
    )

    return len(qualifying)


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
        "qualifying_sessions_7d": None,
        "supplemental_sessions_7d": None,
        "total_strength_activities_7d": None,
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

    qualifying_ids, supplemental_ids = _classify_sessions(
        rows,
        window_start,
        window_end,
    )
    sessions = len(qualifying_ids)
    supplemental_sessions = len(supplemental_ids)

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
        "qualifying_sessions_7d": sessions,
        "supplemental_sessions_7d": supplemental_sessions,
        "total_strength_activities_7d": (
            sessions + supplemental_sessions
        ),
        "target_sessions_per_week": target,
        "percentage_of_target": percentage,
        "remaining_sessions": max(0, target - sessions),
        "window_start_date": window_start.isoformat(),
        "window_end_date": window_end.isoformat(),
    }
