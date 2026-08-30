from copy import deepcopy

from daily_coaching_summary import build_daily_coaching_summary
from todays_plan import build_todays_plan


VERSION = "1.0"


def _action(label, destination):
    return {
        "label": label,
        "destination": destination,
    }


def _recovery_card(plan):
    sleep = plan.get("sleep") or {}
    training = plan.get("training") or {}
    score = sleep.get("recovery_score")
    if score is None:
        score = training.get("recovery_score")
    band = sleep.get("recovery_band")

    if band == "red":
        headline = "Recovery is low"
        supporting_text = "Keep today's effort restorative and prioritize sleep."
    elif band == "yellow":
        headline = "Recovery is moderate"
        supporting_text = "Follow the prescribed plan without adding unnecessary load."
    elif band == "green":
        headline = "Recovery is strong"
        supporting_text = "Follow the prescribed training plan while protecting normal sleep."
    else:
        headline = "Recovery is unavailable"
        supporting_text = "No reliable recovery classification is available."

    return {
        "recovery_score": score,
        "recovery_band": band,
        "status": band or "unknown",
        "headline": headline,
        "supporting_text": supporting_text,
        "destination": "recovery",
    }


def _training_card(plan, coaching):
    source = plan.get("training") or {}
    actions = coaching.get("top_actions") or []
    instruction = next(
        (
            item for item in actions
            if "session" in item.lower() or "training" in item.lower()
        ),
        None,
    )
    if instruction is None and source.get("category"):
        instruction = f"Follow the prescribed {source['category']} session."

    return {
        "status": source.get("status", "unknown"),
        "category": source.get("category"),
        "session_type": source.get("session_type"),
        "primary_focus": source.get("primary_focus") or [],
        "total_sets": source.get("total_sets"),
        "exercise_count": source.get("exercise_count"),
        "instruction": instruction,
        "action": _action("View Workout", "training"),
    }


def _nutrition_card(plan):
    source = plan.get("nutrition") or {}
    return {
        "status": source.get("status", "unknown"),
        "calories": source.get("calories", source.get("calorie_target")),
        "protein_g": source.get("protein_g", source.get("protein_target_g")),
        "carbs_g": source.get("carbs_g", source.get("carbohydrate_target_g")),
        "fat_g": source.get("fat_g", source.get("fat_target_g")),
        "intake_tracking_status": source.get("intake_tracking_status"),
        "priority": source.get("priority"),
        "action": _action("View Nutrition", "nutrition"),
    }


def _sleep_card(plan):
    source = plan.get("sleep") or {}
    return {
        "status": source.get("status", "unknown"),
        "sleep_target_display": source.get("sleep_target_display"),
        "time_in_bed_target_display": source.get("time_in_bed_target_display"),
        "recommended_bedtime": source.get("recommended_bedtime"),
        "wake_time": source.get("wake_time"),
        "recovery_band": source.get("recovery_band"),
        "trend_summary": source.get("trend_summary") or source.get("sleep_trend"),
        "action": _action("View Sleep Plan", "sleep"),
    }


def _hydration_card(plan):
    source = plan.get("hydration") or {}
    return {
        "status": source.get("status", "unknown"),
        "daily_target_display": source.get("daily_target_display"),
        "priority": source.get("priority"),
        "action": _action("View Hydration", "hydration"),
    }


def _activity_card(coaching):
    source = coaching.get("activity") or {}
    status = source.get("status", "unknown")
    if status == "on_track":
        text = "Activity is on track; no corrective action is needed."
    elif status == "close":
        text = "Activity is close to the configured target."
    elif status == "below_target":
        text = "Activity is meaningfully below the configured target."
    else:
        text = "Activity status is not currently available."

    return {
        "average_steps_7d": source.get("average_steps_7d"),
        "target_steps": source.get("target_steps"),
        "percentage_of_target": source.get("percentage_of_target"),
        "status": status,
        "status_text": text,
    }


def _goal_progress_card(plan, coaching):
    nutrition = plan.get("nutrition") or {}
    source = coaching.get("goal_progress") or {}
    body_fat = source.get("body_fat") or {}
    weight = source.get("weight") or {}
    status = source.get("status") or "insufficient_data"

    if status == "baseline_building":
        summary = "The current phase is baseline-building; progress classification is withheld."
    elif status == "insufficient_data":
        summary = "There is not enough data to classify goal progress."
    else:
        summary = source.get("summary") or "Goal progress follows the validated goal engine."

    return {
        "phase": source.get("phase", nutrition.get("phase")),
        "status": status,
        "phase_age_days": source.get("phase_age_days"),
        "direction": source.get("direction"),
        "body_fat": {
            "start_percentage": body_fat.get("start_percentage"),
            "current_percentage": body_fat.get("current_percentage"),
            "target_percentage": body_fat.get("target_percentage"),
        },
        "weight": {
            "current_lb": weight.get("current_lb", nutrition.get("current_weight_lb")),
        },
        "summary": summary,
    }


def build_today_experience(plan=None, coaching_summary=None):
    """Build the compact deterministic mobile Today contract."""

    if plan is None:
        plan = build_todays_plan()

    if plan.get("status") != "ok":
        return {
            "status": plan.get("status", "not_ready"),
            "version": VERSION,
            "date": plan.get("plan_date"),
            "reason": plan.get("reason"),
        }

    if coaching_summary is None:
        coaching_summary = build_daily_coaching_summary(plan)

    coaching = deepcopy(coaching_summary)

    return {
        "status": "ok",
        "version": VERSION,
        "date": plan.get("plan_date"),
        "overall_state": coaching.get("overall_state"),
        "headline": coaching.get("headline"),
        "summary": coaching.get("summary"),
        "top_actions": coaching.get("top_actions") or [],
        "cards": {
            "recovery": _recovery_card(plan),
            "training": _training_card(plan, coaching),
            "nutrition": _nutrition_card(plan),
            "sleep": _sleep_card(plan),
            "hydration": _hydration_card(plan),
            "activity": _activity_card(coaching),
            "goal_progress": _goal_progress_card(plan, coaching),
        },
    }
