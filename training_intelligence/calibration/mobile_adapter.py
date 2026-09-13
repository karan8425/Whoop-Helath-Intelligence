"""TKI-7 section 9-11: translates build_training_intelligence_
prescription()'s result into the EXACT shape build_daily_workout_
prescription() (B3) returns, so todays_plan.py's existing, UNMODIFIED
`_training_card()` needs no engine-specific branching - mirroring
training_intelligence.prescription.mobile_adapter's (TKI-6, TKI-5.1)
proven pattern exactly, reusing its shared constants/helpers rather
than duplicating them.

Additive-only diagnostics (engine_source/version, workload status,
progression confidence, calibration status, binding justifications)
are namespaced under session["training_intelligence"] - an extra key
_training_card() does not read and never rejects (the same pattern
already proven safe by the TKI-5.1 adapter's own `tki_dose_absorption`
passthrough key).
"""
from __future__ import annotations

from datetime import datetime

from integrations.tonal.workout_prescription import _family_for_movement
from training_intelligence.selection.shadow_selection import REST_FAMILY_NAME
from training_intelligence.prescription.mobile_adapter import PROGRESSION_POLICY, _rir_string
from training_intelligence.calibration.orchestrator import ORCHESTRATOR_VERSION
from training_intelligence.calibration.history import POLICY_VERSION as CALIBRATION_MODEL_VERSION
from training_intelligence.calibration.progression_v2 import PROGRESSION_EVIDENCE_VERSION
from training_intelligence.calibration.workload_v2 import WORKLOAD_V3_POLICY_VERSION
from training_intelligence.calibration.justification_v2 import JUSTIFICATION_POLICY_VERSION
from training_intelligence.calibration.shadow import QUALITY_VERDICT_VERSION

ENGINE_SOURCE_TRAINING_INTELLIGENCE = "training_intelligence"
ENGINE_SOURCE_LEGACY = "legacy"

ENGINE_VERSIONS = {
    "orchestrator_version": ORCHESTRATOR_VERSION,
    "training_intelligence_version": CALIBRATION_MODEL_VERSION,
    "session_selection_version": 4,  # TKI-4, unchanged since introduction
    "dose_version": CALIBRATION_MODEL_VERSION,  # capacity.py shares history.POLICY_VERSION
    "progression_version": PROGRESSION_EVIDENCE_VERSION,
    "workload_sanity_version": WORKLOAD_V3_POLICY_VERSION,
    "justification_version": JUSTIFICATION_POLICY_VERSION,
    "quality_verdict_version": QUALITY_VERDICT_VERSION,
}


def _adapt_exercise(exercise: dict) -> dict:
    """One calibrated-engine exercise -> one B3-shaped raw exercise dict
    (the same target contract training_intelligence.prescription.
    mobile_adapter._adapt_exercise() maps to for TKI-5.1)."""
    name = exercise.get("movement_name")
    resistance = exercise.get("prescribed_resistance_lb")
    reps = exercise.get("target_reps_per_set")
    sets = exercise.get("working_sets")
    primary_muscles = exercise.get("primary_muscles") or []
    secondary_muscles = exercise.get("secondary_muscles") or []
    progression_evidence = exercise.get("progression_evidence") or {}

    return {
        "movement_id": exercise.get("movement_id"),
        "name": name,
        "exercise_family": _family_for_movement(name),
        "muscle_groups": primary_muscles + secondary_muscles,
        "accessory": exercise.get("accessory"),
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
            "resistance_lb": resistance, "sets": sets,
            "rep_range": exercise.get("rep_range") or {},
            "rir_range": exercise.get("target_rir") or {},
        },
        "actual": None,
        # Section 10: the CALIBRATED, cable-aware/normalized estimate,
        # already computed by shadow.py using history.py's learned
        # per-movement workload_multiplier - never recomputed here as a
        # naive resistance*reps*sets, and never a blanket bilateral x2.
        "estimated_volume": exercise.get("estimated_volume"),
        "progression_earned": exercise.get("progression_state") in ("PROGRESS_REPS", "PROGRESS_LOAD"),
        "progression_applied": exercise.get("progression_state") in ("PROGRESS_REPS", "PROGRESS_LOAD"),
        "overload_method": {"PROGRESS_REPS": "reps", "PROGRESS_LOAD": "load"}.get(exercise.get("progression_state")),
        "progression_reason": exercise.get("progression_reason"),
        "smart_weight": {
            "mode": exercise.get("workload_multiplier") and "standard" or "standard",
            "spotter": False,
            "reason": "Calibrated engine prescribes standard-mode destination only (progression_v2.py).",
        },
        "hardware_context": {
            "tonal_max_per_arm_lb": 100.0, "tonal_max_combined_lb": 200.0,
            "near_hardware_ceiling": None,
            "ceiling_usage_pct": round(resistance / 100.0 * 100.0, 1) if resistance is not None else None,
        },
        "historical_context": {
            "recent_working_weight": None, "recent_sets_per_session": None, "recent_reps_per_session": None,
            "recent_estimated_1rm": None, "estimated_1rm_change_pct": None,
            "recent_struggling_score": None, "recent_inconsistency_score": None,
        },
        # Additive-only (section 11): full progression evidence
        # provenance, namespaced so it never collides with a B3 field
        # name and is trivially ignorable by any decoder that doesn't
        # ask for it.
        "training_intelligence_evidence": {
            "evidence_tier": progression_evidence.get("evidence_tier"),
            "exact_mode_sessions": progression_evidence.get("exact_mode_sessions"),
            "compatible_mode_sessions": progression_evidence.get("compatible_mode_sessions"),
            "load_match_quality": progression_evidence.get("load_match_quality"),
            "effort_quality": progression_evidence.get("effort_quality"),
        },
    }


def _workload_summary(result: dict) -> dict:
    v3 = result.get("workload_sanity_v3") or {}
    return {
        "status": v3.get("status"),
        "confidence": v3.get("confidence"),
        "absolute_workload_ratio": v3.get("absolute_workload_ratio"),
        "workload_per_set_ratio": v3.get("workload_per_set_ratio"),
        "gap_classification": v3.get("gap_classification"),
        "justification_sufficient": v3.get("justification_sufficient"),
        "binding_justifications": [
            {"reason": j["reason"], "strength": j["strength"]}
            for j in (v3.get("justifications") or []) if j.get("binding")
        ],
    }


def adapt_calibrated_to_workout_schema(as_of: datetime, result: dict, snapshot_info: dict | None = None) -> dict:
    """The single entry point this module exposes. `result` is the raw,
    unmodified output of orchestrator.build_training_intelligence_
    prescription()'s first element - never mutated, only read. Returns
    a dict shaped exactly like build_daily_workout_prescription()'s own
    return value, plus an additive session["training_intelligence"]
    diagnostics namespace."""
    if result.get("status") != "ok":
        return {"status": result.get("status", "error"), "reason": result.get("reason", "Engine returned a non-ok status.")}

    readiness = result.get("readiness") or {}
    exercises = [_adapt_exercise(e) for e in (result.get("exercises") or [])]
    primary_focus = sorted({m for e in exercises for m in (e.get("primary_muscles") or [])})

    total_sets = sum(e.get("sets") or 0 for e in exercises)
    total_volume = result.get("estimated_total_volume")
    if total_volume is None:
        total_volume = sum(e.get("estimated_volume") or 0 for e in exercises)

    dose = result.get("dose") or {}
    feasible_range = result.get("feasible_range") or {}
    session_type = result.get("selected_session_family")
    quality_v3 = result.get("quality_v3") or {}

    if session_type == REST_FAMILY_NAME:
        session_focus_reason = "Training Intelligence selected Rest / Active Recovery for today."
    elif dose.get("dose_shortfall"):
        session_focus_reason = dose.get("shortfall_reason")
    else:
        session_focus_reason = (quality_v3.get("reasons") or [None])[0]

    return {
        "status": "ok",
        "generated_at": as_of.isoformat(),
        "readiness": readiness,
        "session": {
            "session_type": session_type,
            "primary_focus": primary_focus,
            "secondary_focus": [],
            "target_muscles": primary_focus,
            "suppressed_muscles": [m for m, s in (result.get("local_readiness") or {}).items()
                                   if s in ("FATIGUED", "SUPPRESSED")],
            "recent_training_context": {},
            "selection_confidence": None,
            "session_focus_reason": session_focus_reason,
            "session_template_scores": [],
            "exercise_count": len(exercises),
            "total_sets": total_sets,
            "target_set_range": {
                "minimum": feasible_range.get("lower_bound_working_sets"),
                "target": dose.get("working_sets"),
                "maximum": feasible_range.get("upper_bound_working_sets"),
            },
            "estimated_total_volume": round(total_volume, 1) if total_volume is not None else None,
            "progressive_overload_exercises": sum(1 for e in exercises if e.get("progression_applied")),
            "direct_core_exercises": sum(1 for e in exercises if "Core" in (e.get("muscle_groups") or [])),
            "exercises": exercises,
            "training_b3": {},
            "muscle_priority_diagnostics": [],
            "whoop_dosage_effect": (
                "Training Intelligence's calibrated feasible-dose range controls today's session dose; "
                "it does not override locally suppressed muscles."
            ),
            # Additive-only diagnostics namespace (sections 9, 11, 20-21).
            "training_intelligence": {
                "engine_source": ENGINE_SOURCE_TRAINING_INTELLIGENCE,
                "engine_versions": ENGINE_VERSIONS,
                "goal_mode": result.get("goal_mode"),
                "capacity_reference": {
                    "source": (result.get("personal_capacity_reference") or {}).get("source"),
                    "confidence": (result.get("personal_capacity_reference") or {}).get("confidence"),
                },
                "binding_constraints": [
                    c["constraint"] for c in feasible_range.get("binding_constraints", []) if c.get("binding")
                ],
                "workload": _workload_summary(result),
                "quality_verdict": quality_v3.get("verdict"),
                "quality_reasons": quality_v3.get("reasons"),
                "decision_id": (snapshot_info or {}).get("snapshot", {}).get("decision_id"),
                "snapshot_stored": (snapshot_info or {}).get("stored"),
            },
        },
        "progression_policy": PROGRESSION_POLICY,
    }
