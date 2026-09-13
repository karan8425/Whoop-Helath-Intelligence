"""TKI-6: schema adapter - translates a TKI-5.1 build_shadow_prescription()
result into the EXACT shape integrations.tonal.workout_prescription.
build_daily_workout_prescription() returns (`{"status", "generated_at",
"readiness", "session", "progression_policy"}`), so that todays_plan.py's
existing, UNMODIFIED `_training_card()` can process either engine's
output through the identical code path.

This module owns 100% of the TKI -> mobile-schema translation.
training_intelligence.prescription.shadow_prescription is never touched
for formatting, and B3 (integrations.tonal.workout_prescription) is
never touched at all - this adapter only reads from both, and writes
to neither.

See TRAINING_INTELLIGENCE_TKI6_REPORT.md section F for the full field
compatibility map and section G for the estimated_total_volume parity
analysis this module implements.
"""

from __future__ import annotations

from datetime import datetime

from integrations.tonal.workout_prescription import _latest_readiness, _family_for_movement
from training_intelligence.selection.shadow_selection import REST_FAMILY_NAME

# Identical, static text to B3's own progression_policy block
# (integrations.tonal.workout_prescription.build_daily_workout_
# prescription's return statement). This describes a readiness-based
# progression PHILOSOPHY, not engine-specific behavior, and B3 does not
# expose it as an importable constant - it is intentionally duplicated
# verbatim here for schema parity, not reimplemented or reinterpreted.
PROGRESSION_POLICY = {
    "principle": (
        "Progressive overload is earned "
        "through historical movement "
        "performance and then permitted "
        "or constrained by WHOOP readiness."
    ),
    "high_readiness": (
        "Pursue progressive overload only "
        "on movements whose historical "
        "performance has earned progression."
    ),
    "good_readiness": (
        "Use normal productive training "
        "with conservative progression."
    ),
    "moderate_readiness": (
        "Preserve meaningful resistance "
        "when execution quality is stable. "
        "Reduce fatigue primarily through "
        "session volume, RIR and withholding "
        "new overload."
    ),
    "low_readiness": (
        "Reduce total working volume and accessories, use "
        "more conservative RIR, and permit only progression "
        "already earned by movement-specific history."
    ),
    "tonal_hardware": (
        "Never exceed Tonal's 100 lb "
        "per-arm or 200 lb combined limits."
    ),
    "progression_ladder": ["load", "reps", "sets", "smart_weight"],
    "hardware_ceiling_strategy": (
        "Near Tonal's load ceiling, shift "
        "progression toward reps, sets or "
        "historically appropriate Smart "
        "Weight modes instead of impossible "
        "load increases."
    ),
    "smart_weight_policy": (
        "Eccentric, Chains and Progressive "
        "modes are used only when readiness, "
        "progression status and historical "
        "movement usage support them."
    ),
}


def _rir_string(target_rir):
    """Matches B3's own target_rir string formatting exactly
    (integrations.tonal.workout_prescription._prescribe_exercise)."""
    if not target_rir:
        return None
    minimum = target_rir.get("minimum")
    maximum = target_rir.get("maximum")
    if minimum is None or maximum is None:
        return None
    return f"{minimum}-{maximum}" if minimum != maximum else str(minimum)


def _exercise_volume(resistance_lb, reps, sets):
    """Section G: the SAME formula B3 uses (resistance * reps * sets,
    rounded to 1 decimal, None when no resistance history exists) -
    applied to TKI-5.1's own already-computed, goal-posture-adjusted
    values. Both engines source resistance/reps/sets from the identical
    integrations.tonal.progressive_overload.prescribe() function, so
    this is the same formula over the same-provenance data, not a new
    metric - see the report for the one disclosed difference (TKI's
    optional goal-posture rep adjustment)."""
    if resistance_lb is None or reps is None or sets is None:
        return None
    return round(resistance_lb * reps * sets, 1)


def _adapt_exercise(exercise: dict) -> dict:
    """One TKI-5.1 finalized exercise -> one B3-shaped raw exercise dict
    (the shape integrations.tonal.workout_prescription._prescribe_
    exercise() returns, i.e. _training_card()'s INPUT contract, not its
    output contract - _training_card() itself does the rest of the
    mapping, unmodified, for both engines)."""
    name = exercise.get("movement_name")
    resistance = exercise.get("prescribed_resistance_lb")
    reps = exercise.get("target_reps_per_set")
    sets = exercise.get("working_sets")
    primary_muscles = exercise.get("primary_muscles") or []
    secondary_muscles = exercise.get("secondary_muscles") or []

    return {
        "movement_id": exercise.get("movement_id"),
        "name": name,
        "exercise_family": _family_for_movement(name),
        "muscle_groups": primary_muscles + secondary_muscles,
        # TKI-5.1's finalized exercise does not carry the raw movement
        # profile's own "accessory" catalog field forward (a documented
        # gap, consistent with its own "secondary_muscles" disclosure
        # precedent) - the iOS field is Optional, so None is safe, not
        # a fabricated value.
        "accessory": None,
        "sets": sets,
        "reps_per_set": reps,
        "target_weight_lb": resistance,
        "target_rir": _rir_string(exercise.get("target_rir")),
        "primary_muscles": primary_muscles,
        "secondary_muscles": secondary_muscles,
        "rep_range": exercise.get("rep_range") or {},
        "rir_range": exercise.get("target_rir") or {},
        "rest_seconds": exercise.get("rest_seconds") or {},
        "progression_state": exercise.get("progression_state"),
        "progression_label": exercise.get("progression_action"),
        "progression_target": exercise.get("progression_reason"),
        "performance_trajectory": exercise.get("performance_state"),
        "progression_confidence": exercise.get("confidence"),
        "comparable_performance": exercise.get("comparable_history") or {},
        "b3_rationale": exercise.get("progression_reason"),
        "prescribed": {
            "resistance_lb": resistance,
            "sets": sets,
            "rep_range": exercise.get("rep_range") or {},
            "rir_range": exercise.get("target_rir") or {},
        },
        "actual": None,
        "estimated_volume": _exercise_volume(resistance, reps, sets),
        # TKI-5.1 does not track "earned" as a separate boolean (B3's
        # own progression_earned) - PROGRESS_* states are the closest
        # equivalent; disclosed, not guessed at finer granularity.
        "progression_earned": exercise.get("progression_state") in ("PROGRESS_REPS", "PROGRESS_LOAD"),
        "progression_applied": exercise.get("progression_state") in ("PROGRESS_REPS", "PROGRESS_LOAD"),
        "overload_method": {
            "PROGRESS_REPS": "reps", "PROGRESS_LOAD": "load",
        }.get(exercise.get("progression_state")),
        "progression_reason": exercise.get("progression_reason"),
        # TKI-5.1 has no Smart Weight mode selection (out of scope for
        # TKI-5.1, unchanged here) - "standard" mode, explicit reason,
        # never fabricated as an active advanced mode.
        "smart_weight": {
            "mode": "standard",
            "spotter": False,
            "reason": "TKI-5.1 does not currently select Smart Weight modes.",
        },
        "hardware_context": {
            "tonal_max_per_arm_lb": 100.0,
            "tonal_max_combined_lb": 200.0,
            "near_hardware_ceiling": None,
            "ceiling_usage_pct": (
                round(resistance / 100.0 * 100.0, 1) if resistance is not None else None
            ),
        },
        "historical_context": {
            "recent_working_weight": (exercise.get("comparable_history") or {}).get("latest_resistance_lb"),
            "recent_sets_per_session": (exercise.get("comparable_history") or {}).get("latest_sets"),
            "recent_reps_per_session": (exercise.get("comparable_history") or {}).get("latest_total_reps"),
            "recent_estimated_1rm": None,
            "estimated_1rm_change_pct": None,
            "recent_struggling_score": None,
            "recent_inconsistency_score": None,
        },
    }


def adapt_to_workout_schema(as_of: datetime, tki_result: dict) -> dict:
    """The single entry point this module exposes. `tki_result` is the
    raw, unmodified output of training_intelligence.prescription.
    shadow_prescription.build_shadow_prescription() - never mutated,
    only read. Returns a dict shaped exactly like build_daily_workout_
    prescription()'s own return value."""

    readiness = _latest_readiness(now=as_of)

    exercises = [_adapt_exercise(e) for e in (tki_result.get("exercises") or [])]
    primary_focus = sorted({m for e in exercises for m in (e.get("primary_muscles") or [])})

    total_sets = sum(e.get("sets") or 0 for e in exercises)
    total_volume = sum(e.get("estimated_volume") or 0 for e in exercises)

    dose = tki_result.get("dose") or {}
    feasible_range = dose.get("feasible_range") or {}
    dose_absorption = tki_result.get("dose_absorption") or {}

    session_type = tki_result.get("selected_session_family")
    if session_type == REST_FAMILY_NAME:
        session_focus_reason = "TKI-5.1 selected Rest / Active Recovery for today."
    elif dose_absorption.get("dose_shortfall"):
        session_focus_reason = dose_absorption.get("shortfall_reason")
    else:
        session_focus_reason = (
            (tki_result.get("explanation_factors") or [None])[0]
        )

    return {
        "status": "ok",
        "generated_at": as_of.isoformat(),
        "readiness": readiness,
        "session": {
            "session_type": session_type,
            "primary_focus": primary_focus,
            "secondary_focus": [],
            "target_muscles": primary_focus,
            "suppressed_muscles": [],
            "recent_training_context": {},
            "selection_confidence": (tki_result.get("data_quality") or {}).get("selection_confidence"),
            "session_focus_reason": session_focus_reason,
            "session_template_scores": [],
            "exercise_count": len(exercises),
            "total_sets": total_sets,
            "target_set_range": {
                "minimum": feasible_range.get("lower_bound_working_sets"),
                "target": dose.get("working_sets"),
                "maximum": feasible_range.get("upper_bound_working_sets"),
            },
            "estimated_total_volume": round(total_volume, 1),
            "progressive_overload_exercises": sum(
                1 for e in exercises if e.get("progression_applied")
            ),
            "direct_core_exercises": sum(
                1 for e in exercises if "Core" in (e.get("muscle_groups") or [])
            ),
            "exercises": exercises,
            "training_b3": {},
            "muscle_priority_diagnostics": [],
            "whoop_dosage_effect": (
                "TKI-5.1 dose absorption controls today's session dose; "
                "it does not override locally suppressed muscles."
            ),
            # TKI-6 provenance - not part of B3's own schema, additive
            # only; _training_card() does not read this key, so it is
            # harmless to include and useful for anyone inspecting the
            # raw cached payload server-side.
            "tki_dose_absorption": dose_absorption,
        },
        "progression_policy": PROGRESSION_POLICY,
    }
