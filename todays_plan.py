from datetime import datetime
from zoneinfo import ZoneInfo
from time import perf_counter
import logging

from goals import get_active_goal
from goal_progress import goal_progress
from apple_health_trends import apple_health_trends

from nutrition_prescription import build_nutrition_prescription
from sleep_prescription import build_sleep_prescription
from hydration_prescription import build_hydration_prescription

from integrations.tonal.workout_prescription import (
    build_daily_workout_prescription,
)


EASTERN = ZoneInfo("America/New_York")

logger = logging.getLogger(__name__)


# ============================================================
# HELPERS
# ============================================================

def _today():
    return datetime.now(EASTERN).date().isoformat()


def _safe_engine(engine, engine_name):
    started = perf_counter()

    try:
        result = engine()

        elapsed = perf_counter() - started

        logger.info(
            "TODAYS_PLAN_TIMING engine=%s seconds=%.3f",
            engine_name,
            elapsed,
        )

        if not isinstance(result, dict):
            return {
                "status": "error",
                "engine": engine_name,
                "reason": "Engine returned an invalid response.",
            }

        return result

    except Exception as exc:
        elapsed = perf_counter() - started

        logger.exception(
            "TODAYS_PLAN_TIMING engine=%s seconds=%.3f status=error",
            engine_name,
            elapsed,
        )

        return {
            "status": "error",
            "engine": engine_name,
            "reason": str(exc),
        }


# ============================================================
# TRAINING CARD
# ============================================================

def _training_card(workout):
    if workout.get("status") != "ok":
        return {
            "status": workout.get("status", "not_ready"),
            "available": False,
            "reason": workout.get("reason"),
        }

    readiness = workout.get("readiness") or {}
    session = workout.get("session") or {}

    exercises = session.get("exercises") or []
    exercise_details = []

    for exercise in exercises:
        smart_weight = exercise.get("smart_weight") or {}
        hardware_context = exercise.get("hardware_context") or {}
        historical_context = exercise.get("historical_context") or {}

        exercise_details.append(
            {
                "movement_id": exercise.get("movement_id"),
                "name": exercise.get("name"),
                "exercise_family": exercise.get("exercise_family"),
                "muscle_groups": exercise.get("muscle_groups") or [],
                "accessory": exercise.get("accessory"),

                "sets": exercise.get("sets"),
                "reps_per_set": exercise.get("reps_per_set"),
                "target_weight_lb": exercise.get("target_weight_lb"),
                "target_rir": exercise.get("target_rir"),

                "estimated_volume": exercise.get("estimated_volume"),

                "progression_earned": exercise.get("progression_earned"),
                "progression_applied": exercise.get("progression_applied"),
                "overload_method": exercise.get("overload_method"),
                "progression_reason": exercise.get("progression_reason"),
                "next_progression_option": exercise.get(
                    "next_progression_option"
                ),

                "smart_weight": {
                    "mode": smart_weight.get("mode"),
                    "spotter": smart_weight.get("spotter"),
                    "reason": smart_weight.get("reason"),
                },

                "hardware_context": {
                    "tonal_max_per_arm_lb": hardware_context.get(
                        "tonal_max_per_arm_lb"
                    ),
                    "tonal_max_combined_lb": hardware_context.get(
                        "tonal_max_combined_lb"
                    ),
                    "near_hardware_ceiling": hardware_context.get(
                        "near_hardware_ceiling"
                    ),
                    "ceiling_usage_pct": hardware_context.get(
                        "ceiling_usage_pct"
                    ),
                },

                "historical_context": {
                    "recent_working_weight": historical_context.get(
                        "recent_working_weight"
                    ),
                    "recent_sets_per_session": historical_context.get(
                        "recent_sets_per_session"
                    ),
                    "recent_reps_per_session": historical_context.get(
                        "recent_reps_per_session"
                    ),
                    "recent_estimated_1rm": historical_context.get(
                        "recent_estimated_1rm"
                    ),
                    "estimated_1rm_change_pct": historical_context.get(
                        "estimated_1rm_change_pct"
                    ),
                    "recent_struggling_score": historical_context.get(
                        "recent_struggling_score"
                    ),
                    "recent_inconsistency_score": historical_context.get(
                        "recent_inconsistency_score"
                    ),
                },
            }
        )

    return {
        "status": "ok",
        "available": True,

        "category": readiness.get("training_category"),
        "recovery_score": readiness.get("recovery_score"),

        "session_type": session.get("session_type"),
        "primary_focus": session.get("primary_focus") or [],
        "secondary_focus": session.get("secondary_focus") or [],

        "total_sets": session.get("total_sets"),
        "exercise_count": session.get("exercise_count"),
        "estimated_total_volume": session.get("estimated_total_volume"),

        "progressive_overload_exercises": session.get(
            "progressive_overload_exercises"
        ),
        "direct_core_exercises": session.get("direct_core_exercises"),

        "target_set_range": session.get("target_set_range") or {},

        "exercises": exercise_details,

        "progression_policy": workout.get("progression_policy") or {},

        "action": {
            "label": "View Workout",
            "destination": "training",
        },
    }


# ============================================================
# NUTRITION CARD
# ============================================================

def _nutrition_card(nutrition):
    if nutrition.get("status") != "ok":
        return {
            "status": nutrition.get("status", "not_ready"),
            "available": False,
            "reason": nutrition.get("reason"),
        }

    macros = nutrition.get("macros") or {}
    activity = nutrition.get("activity") or {}
    goal_progress_data = nutrition.get("goal_progress") or {}

    return {
        "status": "ok",
        "available": True,

        "phase": nutrition.get("phase"),
        "calories": nutrition.get("calorie_target"),

        "protein_g": macros.get("protein_g"),
        "carbs_g": macros.get("carbs_g"),
        "fat_g": macros.get("fat_g"),

        "macro_calorie_check": nutrition.get("macro_calorie_check"),
        "current_weight_lb": nutrition.get("current_weight_lb"),

        "activity": activity,
        "goal_progress": goal_progress_data,

        "priority": nutrition.get("nutrition_priority"),
        "rationale": nutrition.get("rationale"),

        "action": {
            "label": "View Nutrition",
            "destination": "nutrition",
        },
    }


# ============================================================
# HYDRATION CARD
# ============================================================

def _hydration_card(hydration):
    if hydration.get("status") != "ok":
        return {
            "status": hydration.get("status", "not_ready"),
            "available": False,
            "reason": hydration.get("reason"),
        }

    return {
        "status": "ok",
        "available": True,

        "daily_target_fl_oz": hydration.get("daily_target_fl_oz"),
        "daily_target_display": hydration.get("daily_target_display"),

        "baseline_target_fl_oz": hydration.get(
            "baseline_target_fl_oz"
        ),

        "training_adjustment_fl_oz": hydration.get(
            "training_adjustment_fl_oz"
        ),

        "priority": hydration.get("priority"),
        "rationale": hydration.get("rationale"),

        "action": {
            "label": "View Hydration",
            "destination": "hydration",
        },
    }


# ============================================================
# SLEEP CARD
# ============================================================

def _sleep_card(sleep):
    if sleep.get("status") != "ok":
        return {
            "status": sleep.get("status", "not_ready"),
            "available": False,
            "reason": sleep.get("reason"),
        }

    schedule = sleep.get("recommended_schedule") or {}
    latest = sleep.get("latest_sleep") or {}
    baselines = sleep.get("baselines") or {}
    trend = sleep.get("trend") or {}
    planning_efficiency = sleep.get("planning_efficiency") or {}

    return {
        "status": "ok",
        "available": True,

        "sleep_target_hours": sleep.get("sleep_target_hours"),
        "sleep_target_display": sleep.get("sleep_target_display"),

        "time_in_bed_target_hours": sleep.get(
            "time_in_bed_target_hours"
        ),

        "time_in_bed_target_display": sleep.get(
            "time_in_bed_target_display"
        ),

        "schedule_available": schedule.get("available"),
        "wake_time": schedule.get("wake_time_local"),
        "recommended_bedtime": schedule.get(
            "recommended_bedtime_local"
        ),
        "schedule_reason": schedule.get("reason"),

        "latest_sleep_hours": latest.get("duration_hours"),
        "recovery_score": latest.get("recovery_score"),

        "sleep_performance_percentage": latest.get(
            "sleep_performance_percentage"
        ),

        "sleep_consistency_percentage": latest.get(
            "sleep_consistency_percentage"
        ),

        "sleep_efficiency_percentage": latest.get(
            "sleep_efficiency_percentage"
        ),

        "sleep_trend": trend.get("status"),
        "trend_direction": trend.get("direction"),
        "trend_summary": trend.get("summary"),
        "gap_to_target_hours": trend.get("gap_to_target_hours"),
        "versus_30d_hours": trend.get("versus_30d_hours"),

        "average_sleep_7d_hours": baselines.get(
            "average_sleep_7d_hours"
        ),

        "average_sleep_30d_hours": baselines.get(
            "average_sleep_30d_hours"
        ),

        "planning_efficiency": planning_efficiency,

        "priority": sleep.get("priority"),
        "rationale": sleep.get("rationale"),

        "action": {
            "label": "View Sleep Plan",
            "destination": "sleep",
        },
    }


# ============================================================
# TODAY'S PLAN
# ============================================================

def build_todays_plan():
    total_started = perf_counter()

    logger.info(
        "TODAYS_PLAN_TIMING status=start"
    )

    # --------------------------------------------------------
    # SHARED CONTEXT
    # --------------------------------------------------------

    goal = _safe_engine(
        get_active_goal,
        "goal",
    )

    trends = _safe_engine(
        apple_health_trends,
        "apple_health_trends",
    )

    if (
        isinstance(goal, dict)
        and goal.get("status") == "error"
    ):
        active_goal = None
    else:
        active_goal = goal

    if (
        isinstance(trends, dict)
        and trends.get("status") == "error"
    ):
        shared_trends = None
    else:
        shared_trends = trends

    progress = _safe_engine(
        lambda: goal_progress(
            goal=active_goal,
            trends=shared_trends,
        ),
        "goal_progress",
    )

    # --------------------------------------------------------
    # PRESCRIPTION ENGINES
    # --------------------------------------------------------

    workout = _safe_engine(
        build_daily_workout_prescription,
        "training",
    )

    nutrition = _safe_engine(
        lambda: build_nutrition_prescription(
            goal=active_goal,
            trends=shared_trends,
            progress=progress,
        ),
        "nutrition",
    )

    hydration = _safe_engine(
        lambda: build_hydration_prescription(
            workout=workout,
            trends=shared_trends,
        ),
        "hydration",
    )

    sleep = _safe_engine(
        build_sleep_prescription,
        "sleep",
    )

    # --------------------------------------------------------
    # API CARDS
    # --------------------------------------------------------

    cards_started = perf_counter()

    training_card = _training_card(workout)
    nutrition_card = _nutrition_card(nutrition)
    hydration_card = _hydration_card(hydration)
    sleep_card = _sleep_card(sleep)

    logger.info(
        "TODAYS_PLAN_TIMING engine=card_building seconds=%.3f",
        perf_counter() - cards_started,
    )

    cards = {
        "training": training_card,
        "nutrition": nutrition_card,
        "hydration": hydration_card,
        "sleep": sleep_card,
    }

    available_cards = [
        name
        for name, card in cards.items()
        if card.get("available")
    ]

    total_elapsed = (
        perf_counter()
        - total_started
    )

    logger.info(
        "TODAYS_PLAN_TIMING status=complete total_seconds=%.3f",
        total_elapsed,
    )

    return {
        "status": "ok",
        "version": "1.2",
        "plan_date": _today(),
        "available_sections": available_cards,
        "training": training_card,
        "nutrition": nutrition_card,
        "hydration": hydration_card,
        "sleep": sleep_card,
    }

# ============================================================
# LOCAL VALIDATION
# ============================================================

def main():
    plan = build_todays_plan()

    print()
    print("TODAY'S HEALTH PLAN V1.2")
    print("=" * 78)

    print("Status:", plan.get("status"))
    print("Date:", plan.get("plan_date"))
    print()

    training = plan.get("training") or {}

    print("TRAINING")
    print("Category:", training.get("category"))
    print("Session:", training.get("session_type"))
    print("Exercises:", training.get("exercise_count"))
    print("Sets:", training.get("total_sets"))
    print()

    for index, exercise in enumerate(
        training.get("exercises") or [],
        start=1,
    ):
        print(
            f"{index}. {exercise.get('name')} | "
            f"{exercise.get('sets')} x "
            f"{exercise.get('reps_per_set')} | "
            f"{exercise.get('target_weight_lb')} lb | "
            f"RIR {exercise.get('target_rir')}"
        )

        smart_weight = exercise.get("smart_weight") or {}

        print(
            "   Smart Weight:",
            smart_weight.get("mode"),
            "| Spotter:",
            smart_weight.get("spotter"),
        )

        print(
            "   Progression:",
            exercise.get("overload_method"),
        )

    print()

    nutrition = plan.get("nutrition") or {}

    print("NUTRITION")
    print("Calories:", nutrition.get("calories"))
    print("Protein:", nutrition.get("protein_g"), "g")
    print("Carbs:", nutrition.get("carbs_g"), "g")
    print("Fat:", nutrition.get("fat_g"), "g")
    print()

    hydration = plan.get("hydration") or {}

    print("HYDRATION")
    print(
        "Target:",
        hydration.get("daily_target_display"),
    )
    print(
        "Training adjustment:",
        hydration.get("training_adjustment_fl_oz"),
        "fl oz",
    )
    print()

    sleep = plan.get("sleep") or {}

    print("SLEEP TONIGHT")
    print(
        "Sleep target:",
        sleep.get("sleep_target_display"),
    )
    print(
        "Time in bed:",
        sleep.get("time_in_bed_target_display"),
    )
    print(
        "Bedtime:",
        sleep.get("recommended_bedtime"),
    )
    print(
        "Trend:",
        sleep.get("sleep_trend"),
    )
    print(
        "Trend summary:",
        sleep.get("trend_summary"),
    )
    print()

    print(
        "Available sections:",
        ", ".join(
            plan.get(
                "available_sections",
                [],
            )
        ),
    )

    print("=" * 78)


if __name__ == "__main__":
    main()