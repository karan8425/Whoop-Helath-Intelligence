"""Reusable read model of the active Goal Setting V2 contract.

Future recommendation logic (Today, nutrition, training) should read the
goal through this single accessor rather than re-querying health_goal_profiles
directly. It exposes exactly what a downstream engine needs and nothing that
would let an aggressive target date leak into an aggressive prescription.

IMPORTANT: `target_date` / `expected_weekly_weight_change_lb` here are the
*actionable* plan values - already clamped to the supported guardrails by
goals.activate_goal(). The user's aspirational date (if it was outside the
supported range) is carried separately as `aspirational_target_date` and must
NOT be used to size a deficit.
"""

from goals import get_active_goal
import goal_pace_config as pace_cfg


def get_goal_contract():
    goal = get_active_goal()
    if not goal:
        return {"status": "no_active_goal"}

    return {
        "status": "ok",
        "goal_version": goal.get("goal_version") or 1,
        "goal_type": goal.get("goal_type"),
        "phase": goal.get("phase"),
        "lean_mass_priority": goal.get("lean_mass_priority")
        or pace_cfg.DEFAULT_LEAN_MASS_PRIORITY,
        "starting": {
            "weight_lb": goal.get("phase_start_weight_lb"),
            "body_fat_percentage": goal.get("phase_start_body_fat_percentage"),
            "fat_mass_lb": goal.get("phase_start_fat_mass_lb"),
            "lean_mass_lb": goal.get("phase_start_lean_mass_lb"),
            "phase_start_date": goal.get("phase_start_date"),
        },
        "target": {
            "weight_lb": goal.get("target_weight_lb"),
            "body_fat_percentage": goal.get("target_body_fat_percentage"),
            "fat_mass_lb": goal.get("target_fat_mass_lb"),
            "lean_mass_lb": goal.get("target_lean_mass_lb"),
        },
        "timeline": {
            # actionable, guardrail-clamped
            "target_date": goal.get("target_date"),
            "selected_pace": goal.get("selected_pace"),
            "expected_weekly_weight_change_lb":
                goal.get("expected_weekly_weight_change_lb"),
            "timeline_status": goal.get("timeline_status") or "not_configured",
            # aspirational only - never size a prescription from this
            "aspirational_target_date": goal.get("aspirational_target_date"),
        },
        "legacy_targets": {
            "daily_step_target": goal.get("daily_step_target"),
            "strength_sessions_per_week": goal.get("strength_sessions_per_week"),
            "protein_target_grams": goal.get("protein_target_grams"),
        },
        "prescription_note": (
            "Nutrition/training prescriptions are driven by phase configuration "
            "and current measurements, not by target_date. An aggressive "
            "aspirational date does not increase deficit size."
        ),
    }
