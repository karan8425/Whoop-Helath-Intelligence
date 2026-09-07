"""Deterministic, personalized daily movement prescription (Training-B4)."""

from datetime import datetime
from math import ceil
from zoneinfo import ZoneInfo

from db import get_conn


EASTERN = ZoneInfo("America/New_York")

CONFIG = {
    "minimum_target": 3000,
    "maximum_target": 15000,
    "maximum_weekly_increase": 700,
    "goal_increase": {"lean_cut": 500, "lose_body_fat": 500, "recomposition": 300},
    "recovery_adjustment": {"very_low": -500, "low": -300, "moderate": 0, "good": 200, "high": 300},
    "steps_per_minute": {"RECOVERY_WALK": 75, "EASY_WALK": 85, "BRISK_WALK": 105,
                         "ZONE2_WALK": 110, "ZONE2_JOG": 145},
    "session_minutes": (10, 45),
}


def _number(value, default=0.0):
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _goal_type(goal):
    return ((goal or {}).get("goal_type") or (goal or {}).get("phase") or "maintenance").lower()


def _recovery_band(strength):
    score = _number((strength or {}).get("recovery_score"), 50)
    if score < 25:
        return "very_low"
    if score < 45:
        return "low"
    if score < 67:
        return "moderate"
    if score < 85:
        return "good"
    return "high"


def load_activity_context(now=None):
    """One round trip for Apple steps, Tonal-day split, WHOOP cardio and HR."""
    now = now or datetime.now(EASTERN)
    today = now.astimezone(EASTERN).date()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                WITH days AS (
                    SELECT a.activity_date, a.steps,
                           EXISTS (SELECT 1 FROM tonal_workouts t
                                   WHERE (t.begin_time AT TIME ZONE 'America/New_York')::date = a.activity_date)
                               AS strength_day
                    FROM apple_health_daily_activity a
                    WHERE a.activity_date >= %s - INTERVAL '90 days'
                      AND a.activity_date <= %s
                ), stats AS (
                    SELECT
                      MAX(steps) FILTER (WHERE activity_date = %s) AS steps_today,
                      MAX(activity_date) FILTER (WHERE activity_date = %s) AS today_row,
                      AVG(steps) FILTER (WHERE activity_date >= %s - INTERVAL '7 days' AND activity_date < %s) AS avg_7,
                      AVG(steps) FILTER (WHERE activity_date >= %s - INTERVAL '14 days' AND activity_date < %s) AS avg_14,
                      AVG(steps) FILTER (WHERE activity_date >= %s - INTERVAL '30 days' AND activity_date < %s) AS avg_30,
                      AVG(steps) FILTER (WHERE activity_date < %s) AS avg_90,
                      COUNT(steps) FILTER (WHERE activity_date >= %s - INTERVAL '7 days' AND activity_date < %s) AS n_7,
                      COUNT(steps) FILTER (WHERE activity_date >= %s - INTERVAL '14 days' AND activity_date < %s) AS n_14,
                      COUNT(steps) FILTER (WHERE activity_date >= %s - INTERVAL '30 days' AND activity_date < %s) AS n_30,
                      COUNT(steps) FILTER (WHERE activity_date < %s) AS n_90,
                      AVG(steps) FILTER (WHERE strength_day AND activity_date < %s) AS strength_avg,
                      AVG(steps) FILTER (WHERE NOT strength_day AND activity_date < %s) AS non_strength_avg
                    FROM days
                ), cardio AS (
                    SELECT COUNT(*) FILTER (WHERE start_time >= %s - INTERVAL '30 days') AS aerobic_30,
                           COUNT(*) FILTER (WHERE start_time >= %s - INTERVAL '30 days'
                             AND LOWER(COALESCE(sport_name,'')) ~ 'run|jog') AS runs_30,
                           AVG(average_heart_rate) FILTER (WHERE start_time >= %s - INTERVAL '90 days') AS aerobic_hr,
                           MAX(max_heart_rate) FILTER (WHERE start_time >= %s - INTERVAL '90 days') AS observed_max_hr
                    FROM whoop_workouts
                    WHERE LOWER(COALESCE(sport_name,'')) ~ 'walk|hike|run|jog|cycl|elliptical|rowing'
                ), phys AS (
                    SELECT (SELECT resting_heart_rate FROM whoop_recoveries
                            WHERE resting_heart_rate IS NOT NULL ORDER BY created_at DESC LIMIT 1) AS resting_hr,
                           (SELECT max_heart_rate FROM whoop_body_measurements WHERE id=1) AS profile_max_hr
                )
                SELECT stats.*, cardio.*, phys.* FROM stats CROSS JOIN cardio CROSS JOIN phys
            """, tuple([today] * 24))
            row = dict(cur.fetchone() or {})
    return row


def _baselines(context):
    return {str(days): {"average_steps": round(_number(context.get(f"avg_{days}"))) or None,
                        "days_available": int(context.get(f"n_{days}") or 0)}
            for days in (7, 14, 30, 90)}


def calculate_step_target(context, goal, recovery_band, body_signal="INSUFFICIENT_DATA"):
    baselines = _baselines(context)
    available = [baselines[str(w)]["average_steps"] for w in (14, 30, 7, 90)
                 if baselines[str(w)]["average_steps"] is not None]
    baseline = available[0] if available else CONFIG["minimum_target"]
    configured = _number((goal or {}).get("daily_step_target"), 0)
    preferred = baseline + CONFIG["goal_increase"].get(_goal_type(goal), 0)
    if configured:
        preferred = min(preferred + CONFIG["maximum_weekly_increase"], configured)
    preferred += CONFIG["recovery_adjustment"][recovery_band]
    if body_signal == "LEAN_MASS_RISK":
        preferred = min(preferred, baseline)
    weekly_ceiling = baseline + CONFIG["maximum_weekly_increase"]
    target = int(round(max(CONFIG["minimum_target"], min(preferred, weekly_ceiling,
                       CONFIG["maximum_target"])) / 100.0) * 100)
    supported_preferred = min(preferred, weekly_ceiling, CONFIG["maximum_target"])
    return {"recommended": target,
            "minimum": int(max(CONFIG["minimum_target"], round(baseline * .9 / 100) * 100)),
            "preferred": int(round(supported_preferred / 100) * 100),
            "upper": int(min(CONFIG["maximum_target"], round(weekly_ceiling / 100) * 100)),
            "baseline_used": baseline, "baselines": baselines}


def _zone2(context):
    resting = context.get("resting_hr")
    maximum = context.get("profile_max_hr") or context.get("observed_max_hr")
    if resting and maximum and maximum > resting + 40:
        reserve = maximum - resting
        return {"minimum": round(resting + reserve * .60), "maximum": round(resting + reserve * .70),
                "methodology": "Karvonen heart-rate reserve (60–70%)",
                "provenance": "WHOOP resting HR and profile/observed max HR"}
    return None


def _project(steps, baseline, hour):
    if hour >= 21:
        return int(steps)
    elapsed_fraction = max(.15, min(1.0, (hour - 7) / 15))
    expected_so_far = baseline * elapsed_fraction
    remaining_pattern = max(0, baseline - expected_so_far)
    return int(round(max(steps, steps + remaining_pattern) / 100) * 100)


def _sessions(gap, hour, recovery, strength, running_supported, zone2, body_signal):
    if gap <= 500:
        return []
    late = hour >= 20
    lower = "lower" in str((strength or {}).get("session_type", "")).lower()
    rest = not (strength or {}).get("available", False)
    if recovery in ("very_low", "low"):
        modality = "RECOVERY_WALK" if lower else "EASY_WALK"
    elif recovery == "moderate" or lower or body_signal == "LEAN_MASS_RISK":
        modality = "BRISK_WALK"
    elif running_supported and not lower and gap >= 2500:
        modality = "ZONE2_JOG"
    else:
        modality = "ZONE2_WALK" if not lower else "BRISK_WALK"
    if late:
        minutes_total = min(20, ceil(gap / CONFIG["steps_per_minute"][modality] / 5) * 5)
        modality = "RECOVERY_WALK" if recovery in ("very_low", "low") else "EASY_WALK"
        blocks = [minutes_total]
    else:
        maximum = 45 if rest and recovery in ("good", "high") else 40
        minutes_total = min(maximum, max(10, ceil(gap / CONFIG["steps_per_minute"][modality] / 5) * 5))
        blocks = [minutes_total] if minutes_total <= 25 else [minutes_total // 2, minutes_total - minutes_total // 2]
    dayparts = ["Midday", "Evening"] if hour < 12 else (["Afternoon", "Evening"] if hour < 17 else ["Evening"])
    guidance = "Brisk but sustainable; you should still be able to speak in short sentences."
    return [{"modality": modality, "duration_minutes": minutes,
             "intensity": "easy" if modality in ("EASY_WALK", "RECOVERY_WALK") else "moderate",
             "target_hr_range": zone2 if modality.startswith("ZONE2") else None,
             "intensity_guidance": guidance if modality.startswith("ZONE2") and not zone2 else None,
             "approximate_steps": int(minutes * CONFIG["steps_per_minute"][modality]),
             "preferred_daypart": dayparts[min(i, len(dayparts)-1)],
             "rationale": "Adds low-fatigue movement without compromising today's strength stimulus."}
            for i, minutes in enumerate(blocks)]


def build_activity_plan(goal=None, strength=None, context=None, now=None):
    now = now or datetime.now(EASTERN)
    context = context or load_activity_context(now)
    strength = strength or {}
    body = ((strength.get("training_b3") or {}).get("body_composition_strategy") or {})
    body_signal = body.get("training_strategy_signal", "INSUFFICIENT_DATA")
    recovery = _recovery_band(strength)
    target = calculate_step_target(context, goal, recovery, body_signal)
    steps = int(round(_number(context.get("steps_today"))))
    remaining = max(0, target["recommended"] - steps)
    projected = _project(steps, target["baseline_used"], now.hour)
    projected_gap = max(0, target["recommended"] - projected)
    runs = int(context.get("runs_30") or 0)
    aerobic = int(context.get("aerobic_30") or 0)
    zone2 = _zone2(context)
    sessions = _sessions(projected_gap, now.hour, recovery, strength, runs >= 2, zone2, body_signal)
    pct = round(min(100.0, steps / target["recommended"] * 100), 1) if target["recommended"] else 0
    if remaining == 0:
        level = "ON_TRACK"
    elif remaining <= 1500:
        level = "SMALL_GAP"
    elif remaining <= 4000:
        level = "MODERATE_GAP"
    else:
        level = "LARGE_GAP"
    if now.hour >= 20 and remaining > sum(s["approximate_steps"] for s in sessions):
        late_note = "Do not chase the full target tonight; complete the safe session and resume normally tomorrow."
    else:
        late_note = None
    strength_name = strength.get("session_type") or "rest day"
    summary = (f"Complete today's {strength_name} plan and " if strength.get("available") else "")
    summary += ("no extra conditioning is needed." if not sessions else
                f"add {len(sessions)} manageable walking session{'s' if len(sessions) != 1 else ''} totaling {sum(s['duration_minutes'] for s in sessions)} minutes.")
    return {"status": "ok" if context.get("today_row") else "partial_data",
            "steps_so_far": steps, "step_target": target["recommended"],
            "steps_remaining": remaining, "percent_complete": pct,
            "projected_steps_without_intervention": projected, "projected_gap": projected_gap,
            "recommendation_level": level, "minimum_reasonable_target": target["minimum"],
            "preferred_target": target["preferred"], "upper_supported_target": target["upper"],
            "baselines": target["baselines"],
            "training_day_average_steps": round(_number(context.get("strength_avg"))) or None,
            "non_training_day_average_steps": round(_number(context.get("non_strength_avg"))) or None,
            "sessions": sessions, "recovery_context": {"band": recovery},
            "strength_context": {"session_type": strength.get("session_type"), "total_sets": strength.get("total_sets")},
            "goal_context": {"goal_type": _goal_type(goal), "daily_step_target": (goal or {}).get("daily_step_target"),
                             "body_composition_signal": body_signal,
                             "nutrition_is_prescription_not_intake": True},
            "conditioning_history": {"aerobic_workouts_30d": aerobic, "running_workouts_30d": runs,
                                      "running_supported": runs >= 2},
            "zone2_methodology": zone2 or {"methodology": "talk test", "provenance": "insufficient reliable HR inputs"},
            "late_day_guidance": late_note, "confidence": "high" if target["baselines"]["30"]["days_available"] >= 21 else "moderate",
            "overall_training_summary": summary,
            "activity_updated_at": now.isoformat(),
            "methodology": "Apple Health steps; personalized historical baseline; goal and WHOOP bounded adjustments; no nutrition-intake assumption; no regional-fat modality input."}
