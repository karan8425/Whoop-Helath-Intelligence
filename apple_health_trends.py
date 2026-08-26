from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from db import get_conn


EASTERN = ZoneInfo("America/New_York")

WINDOWS = (7, 14, 30, 90)

HUME_BUNDLE_ID = "com.elink.fittrackhealth"


def _pct_change(current, baseline):
    if current is None or baseline is None or baseline == 0:
        return None

    return ((current - baseline) / baseline) * 100.0


def _round(value, digits=2):
    if value is None:
        return None

    return round(float(value), digits)


def _activity_baselines():
    """
    Calculate activity averages over the preceding
    7 / 14 / 30 / 90 calendar days.

    Today's partial activity is NOT included in the baseline.
    """

    today = datetime.now(timezone.utc).astimezone(EASTERN).date()

    result = {}

    with get_conn() as conn:
        with conn.cursor() as cur:

            # Current day
            cur.execute(
                """
                SELECT
                    activity_date,
                    steps,
                    active_energy_kcal,
                    resting_energy_kcal,
                    walking_running_distance_km
                FROM apple_health_daily_activity
                WHERE activity_date = %s
                """,
                (today,),
            )

            current = cur.fetchone()

            result["current"] = dict(current) if current else None

            # Historical baselines
            baselines = {}

            for window in WINDOWS:

                cur.execute(
                    """
                    SELECT
                        AVG(steps) AS steps,
                        AVG(active_energy_kcal) AS active_energy_kcal,
                        AVG(resting_energy_kcal) AS resting_energy_kcal,
                        AVG(walking_running_distance_km)
                            AS walking_running_distance_km,
                        COUNT(steps) AS step_days
                    FROM apple_health_daily_activity
                    WHERE activity_date >= %s - (%s * INTERVAL '1 day')
                      AND activity_date < %s
                    """,
                    (today, window, today),
                )

                row = cur.fetchone()

                baselines[str(window)] = {
                    "steps": _round(row["steps"], 0),
                    "active_energy_kcal":
                        _round(row["active_energy_kcal"]),
                    "resting_energy_kcal":
                        _round(row["resting_energy_kcal"]),
                    "walking_running_distance_km":
                        _round(row["walking_running_distance_km"]),
                    "days_available":
                        int(row["step_days"] or 0),
                    "coverage_percentage":
                        round(
                            ((row["step_days"] or 0) / window)
                            * 100.0,
                            1,
                        ),
                }

    if result["current"]:

        current_steps = result["current"]["steps"]

        for window in WINDOWS:

            baseline_steps = baselines[str(window)]["steps"]

            baselines[str(window)][
                "steps_pct_vs_baseline"
            ] = _round(
                _pct_change(
                    current_steps,
                    baseline_steps,
                ),
                1,
            )

    result["baselines"] = baselines

    return result


def _latest_hume_sample(metric_name):

    with get_conn() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    value,
                    unit,
                    observed_at,
                    source_name,
                    source_bundle_id
                FROM apple_health_body_samples
                WHERE metric_name = %s
                  AND source_bundle_id = %s
                ORDER BY observed_at DESC
                LIMIT 1
                """,
                (
                    metric_name,
                    HUME_BUNDLE_ID,
                ),
            )

            return cur.fetchone()


def _hume_metric_trend(metric_name):
    """
    Body-composition trends deliberately use Hume only.

    This prevents measurements from different scales/apps
    from being blended into a potentially misleading trend.
    """

    latest = _latest_hume_sample(metric_name)

    if not latest:

        return {
            "available": False,
            "reason":
                "No Hume measurements are available.",
        }

    latest_time = latest["observed_at"]

    result = {
        "available": True,
        "current_value":
            _round(latest["value"]),
        "unit":
            latest["unit"],
        "observed_at":
            latest_time.isoformat(),
        "source_name":
            latest["source_name"],
        "source_bundle_id":
            latest["source_bundle_id"],
        "windows": {},
    }

    with get_conn() as conn:
        with conn.cursor() as cur:

            for window in WINDOWS:

                cur.execute(
                    """
                    SELECT
                        AVG(value) AS baseline,
                        COUNT(*) AS observations,
                        MIN(observed_at) AS oldest,
                        MAX(observed_at) AS newest
                    FROM apple_health_body_samples
                    WHERE metric_name = %s
                      AND source_bundle_id = %s
                      AND observed_at >=
                          %s - (%s * INTERVAL '1 day')
                      AND observed_at < %s
                    """,
                    (
                        metric_name,
                        HUME_BUNDLE_ID,
                        latest_time,
                        window,
                        latest_time,
                    ),
                )

                row = cur.fetchone()

                baseline = row["baseline"]
                observations = int(
                    row["observations"] or 0
                )

                result["windows"][str(window)] = {
                    "baseline":
                        _round(baseline),
                    "observations":
                        observations,
                    "pct_vs_baseline":
                        _round(
                            _pct_change(
                                latest["value"],
                                baseline,
                            ),
                            1,
                        ),
                    "oldest":
                        row["oldest"].isoformat()
                        if row["oldest"]
                        else None,
                    "newest":
                        row["newest"].isoformat()
                        if row["newest"]
                        else None,
                }

    return result


def apple_health_trends():

    activity = _activity_baselines()

    weight = _hume_metric_trend(
        "body_weight"
    )

    body_fat = _hume_metric_trend(
        "body_fat_percentage"
    )

    lean_mass = _hume_metric_trend(
        "lean_body_mass"
    )

    return {
        "status": "ok",

        "methodology": {
            "activity":
                "Apple Health daily activity. "
                "Current partial day excluded from historical baselines.",

            "weight":
                "Hume-only measurements.",

            "body_fat":
                "Hume-only measurements to prevent cross-device bias.",

            "lean_mass":
                "Hume-only. If unavailable, excluded from coaching.",

            "baseline_windows":
                list(WINDOWS),
        },

        "activity":
            activity,

        "body_composition": {
            "weight":
                weight,

            "body_fat_percentage":
                body_fat,

            "lean_body_mass":
                lean_mass,
        },
    }
