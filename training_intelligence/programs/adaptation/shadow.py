"""Program Intelligence V2 - the assembled shadow entry point:
build_daily_program_adaptation(). Never imported by any live route
except the one new admin-only diagnostic endpoint. Not connected to
/api/v1/todays-plan; no mobile output changes; no feature flag touched.

Orchestrates, never duplicates:
  - context.py            (Phase 1, V1 repository)
  - progress.py            (Phase 2, stimulus.ledger - TKI-2)
  - feasibility.py/decision.py (Phase 3/5/6)
  - tonal_resolution.py    (Phase 8-10, V1 tonal_mapping + TKI-5.2/5.4)
  - time_budget.py         (Phase 11)
  - workload_v2/justification_v2 (Phase 12, TKI-5.4)
  - explanation.py         (Phase 7)
  - snapshot.py            (Phase 14)
"""
from __future__ import annotations

from datetime import datetime, timezone

from db import get_conn
from integrations.tonal.workout_prescription import _latest_readiness
from integrations.tonal.muscle_readiness import calculate_muscle_readiness
from training_intelligence.stimulus.ledger import build_muscle_stimulus_ledger
from training_intelligence.stimulus.taxonomy import to_canonical
from training_intelligence.calibration.history import load_history
from training_intelligence.calibration.workload_v2 import workload_sanity_v3
from training_intelligence.calibration.shadow import quality_verdict_v3
from training_intelligence.dose.goal_policy import GOAL_MODES

from training_intelligence.programs import repository, service
from training_intelligence.programs.adaptation.context import build_schedule_context, MAX_OUTSTANDING_SESSIONS
from training_intelligence.programs.adaptation.progress import (
    build_program_progress, classify_muscle_status,
)
from training_intelligence.programs.adaptation.decision import build_candidate_profiles, decide_action
from training_intelligence.programs.adaptation.tonal_resolution import (
    resolve_slots, personalize_dose_and_progression,
)
from training_intelligence.programs.adaptation.time_budget import (
    resolve_available_duration, build_time_context,
)
from training_intelligence.programs.adaptation.explanation import build_explanation
from training_intelligence.programs.adaptation.taxonomy import (
    ADAPTATION_TAXONOMY_VERSION, ACTION_KEEP, ACTION_SHIFT, ACTION_SUBSTITUTE, ACTION_REDUCE,
)
from training_intelligence.programs.adaptation.decision import DECISION_ENGINE_VERSION
from training_intelligence.programs.adaptation.feasibility import FEASIBILITY_VERSION
from training_intelligence.programs.adaptation.tonal_resolution import TONAL_RESOLUTION_VERSION
from training_intelligence.programs.adaptation.progress import PROGRAM_PROGRESS_VERSION
from training_intelligence.programs.adaptation.time_budget import TIME_BUDGET_VERSION

ADAPTATION_ENGINE_VERSION = 1

# PRODUCT POLICY: recency window for the stimulus ledger used as ACTUAL
# training in program-progress comparisons - reused verbatim shape from
# stimulus.ledger, just a wider window suited to a program cycle rather
# than TKI-2's own 7-day default.
DEFAULT_LEDGER_WINDOW_DAYS = 21


def _slots_for(structure, session_dict):
    session_id = next(s["id"] for s in structure["sessions"] if s["session_key"] == session_dict["session_key"])
    return next(s["slots"] for s in structure["sessions"] if s["id"] == session_id)


def build_daily_program_adaptation(user_id, as_of=None, *, available_duration_min=None,
                                    goal_mode_override=None, enrollment_override=None,
                                    readiness_override=None, muscle_readiness_override=None, rows_override=None,
                                    persist_snapshot=False, include_candidates=True, include_debug=False):
    """The section-5 primary decision function. Deterministic for
    identical frozen inputs: same as_of, same enrollment_override, and
    - for the synthetic Phase-15 scenario tests - the same explicit
    readiness_override/muscle_readiness_override/rows_override (never
    read live in that case), matching build_calibrated_shadow_
    prescription's own established override-parameter convention."""
    as_of = as_of or datetime.now(timezone.utc)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)

    schedule = build_schedule_context(user_id, as_of, enrollment_override=enrollment_override)
    if schedule is None:
        return {"status": "no_active_program", "as_of": as_of.isoformat()}

    program = repository.get_program(program_id=schedule.program_id)
    # service.get_program_structure() (V1, reused unchanged) - the same
    # dict-shaped hierarchy the admin API returns, loaded in 4 queries
    # total regardless of program size (see its own docstring).
    structure = service.get_program_structure(program.slug)
    sessions_out_by_key = {s["session_key"]: s for s in structure["sessions"]}

    goal_mode = goal_mode_override if goal_mode_override in GOAL_MODES else program.goal_mode

    # ---- candidates: nominal + bounded outstanding rotation window ----
    candidate_sessions = [schedule.nominal_next_session] + list(schedule.outstanding_sessions[:2])
    candidates = [
        (sessions_out_by_key[c["session_key"]], _slots_for(structure, c))
        for c in candidate_sessions if c is not None
    ]

    # ---- readiness (systemic + local) - reused unchanged ----
    readiness = readiness_override if readiness_override is not None else _latest_readiness(now=as_of)
    muscle_readiness = (
        muscle_readiness_override if muscle_readiness_override is not None
        else calculate_muscle_readiness(now=as_of)
    )

    # ---- program progress: PROGRAM INTENT vs ACTUAL TRAINING ----
    with get_conn() as conn:
        ledger = build_muscle_stimulus_ledger(as_of, window_days=DEFAULT_LEDGER_WINDOW_DAYS, conn=conn)
    all_sessions_with_slots = [(sessions_out_by_key[s["session_key"]], _slots_for(structure, s))
                                for s in structure["sessions"]]
    progress = build_program_progress(program, all_sessions_with_slots, ledger)
    expected_phase_fraction = ((schedule.sequence_position or -1) + 1) / max(len(structure["sessions"]), 1)
    progress = classify_muscle_status(progress, expected_phase_fraction)

    # ---- decision ----
    days_since_map = {
        s["session_key"]: schedule.days_since_last_program_session
        for s in schedule.recently_completed_sessions[:1]
    }
    profiles = build_candidate_profiles(
        candidates, muscle_readiness, progress, schedule.recently_completed_sessions, days_since_map,
    )
    action, chosen, reason_codes = decide_action(profiles, readiness)

    result = {
        "status": "ok",
        "decision_version": ADAPTATION_ENGINE_VERSION,
        "adaptation_taxonomy_version": ADAPTATION_TAXONOMY_VERSION,
        "decision_engine_version": DECISION_ENGINE_VERSION,
        "feasibility_version": FEASIBILITY_VERSION,
        "tonal_resolution_version": TONAL_RESOLUTION_VERSION,
        "program_progress_version": PROGRAM_PROGRESS_VERSION,
        "time_budget_version": TIME_BUDGET_VERSION,
        "as_of": as_of.isoformat(),
        "active_program": {"program_id": program.id, "slug": program.slug, "name": program.name,
                            "goal_mode": goal_mode, "is_shadow_context": schedule.is_shadow_context},
        "nominal_session": schedule.nominal_next_session,
        "program_progress": progress,
        "readiness": readiness,
        "systemic_capacity": {"readiness_band": readiness.get("readiness_band")},
    }

    if include_candidates:
        result["candidate_sessions"] = [
            {"session": c["session"]["session_key"], "position": c["position"],
             "local_gate": c["local_gate"], "program_need": c["program_need"],
             "recency_flagged": c["recency_flagged"]}
            for c in profiles
        ]

    if chosen is None:
        result["decision"] = {"action": action, "selected_session": None, "confidence": "n/a",
                               "reason_codes": reason_codes, "resolved_exercises": []}
        result["explanation"] = build_explanation(action, schedule.nominal_next_session, None, reason_codes)
        return result

    # ---- Tonal resolution + personalized dose/progression ----
    if rows_override is not None:
        rows = rows_override
    else:
        with get_conn() as conn:
            rows = load_history(as_of, conn=conn)
    session_dict, slots = chosen["session"], chosen["slots"]
    resolved_slots = resolve_slots(slots, rows, as_of)
    unresolved_required = [s for s in resolved_slots if s["status"] == "UNRESOLVED" and s["required"]]

    local_states = {
        s["primary_muscle"]: chosen["local_gate"]["muscle_states"].get(s["primary_muscle"], "UNKNOWN")
        for s in slots if s.get("required", True)
    }
    personalized = personalize_dose_and_progression(
        resolved_slots, rows, as_of, session_dict["session_family"], readiness.get("readiness_band"),
        local_states, goal_mode,
    )

    # REDUCE applies a proportional trim to the dose envelope BEFORE
    # Tonal/time resolution ever ran choose_dose - already reflected
    # via feasible_capacity/choose_dose's own readiness-band scaling
    # (capacity.py, unchanged). No separate ad-hoc reduction is layered
    # on top here for REDUCE specifically.

    # ---- time feasibility ----
    duration_min, duration_source = resolve_available_duration(
        request_override_min=available_duration_min,
        user_preference_min=None,  # shadow context carries no enrollment row to read a preference from
        program_default_min=program.default_session_duration_min,
    )
    time_ctx = build_time_context(
        [{"sets": e["working_sets"], "rep_min": e["rep_range"]["minimum"], "rep_max": e["rep_range"]["maximum"],
          "rest_seconds_min": e["rest_seconds"]["minimum"], "rest_seconds_max": e["rest_seconds"]["maximum"],
          "is_unilateral": bool(e.get("is_bilateral") is False)}
         for e in personalized["exercises"]],
        available_duration_min=duration_min, source=duration_source,
    )
    exercises = personalized["exercises"]
    if time_ctx["fit_status"] == "EXCEEDS":
        exercises, time_ctx = _apply_time_reduction(exercises, resolved_slots, duration_min, duration_source)

    # ---- workload sanity (Phase 12) - a CHECK, not the target ----
    # `exercises` already carries every field workload_sanity_v3/
    # quality_verdict_v3 (TKI-5.4, reused unchanged) expect: estimated_
    # volume, working_sets, workload, primary_muscles/secondary_muscles,
    # confidence, progression_evidence, movement_name.
    workload_sanity = workload_sanity_v3(
        exercises, personalized["sessions_for_workload"], session_dict["session_family"],
        as_of, personalized["target_sets"], personalized["capacity_reference"], readiness.get("readiness_band"),
        local_states, personalized["structure_cap"], goal_mode, personalized["feasible_range"],
        personalized["target_sets"],
    )
    quality = quality_verdict_v3(
        exercises, personalized["feasible_range"], workload_sanity,
        local_states, personalized["target_sets"],
    )

    estimated_total_volume = round(sum(e["estimated_volume"] or 0 for e in exercises), 1)

    result["decision"] = {
        "action": action,
        "selected_session": {"session_key": session_dict["session_key"], "session_name": session_dict["session_name"],
                              "session_family": session_dict["session_family"]},
        "confidence": personalized["capacity_reference"].get("confidence"),
        "reason_codes": reason_codes,
        "dose": {"target_sets": personalized["target_sets"], "delivered_sets": personalized["delivered_sets"]},
        "feasible_range": personalized["feasible_range"],
        "resolved_exercises": exercises,
        "unresolved_required_slots": unresolved_required,
        "estimated_total_volume": estimated_total_volume,
        "workload_sanity": workload_sanity,
        "quality_verdict": quality,
    }
    result["workload_sanity"] = workload_sanity
    result["time_context"] = time_ctx
    if include_debug:
        result["debug"] = {"resolved_slots": resolved_slots}
    result["explanation"] = build_explanation(
        action, schedule.nominal_next_session, chosen, reason_codes,
        systemic_band=readiness.get("readiness_band"), time_context=time_ctx,
    )

    if persist_snapshot:
        from training_intelligence.programs.adaptation.snapshot import save_adaptation_snapshot
        try:
            snapshot_info = save_adaptation_snapshot(as_of, program.id, result)
        except Exception as exc:
            snapshot_info = {"stored": False, "error": f"{type(exc).__name__}: {exc}"}
        result["snapshot"] = {"decision_id": snapshot_info["snapshot"]["decision_id"], "stored": snapshot_info["stored"]}

    return result


def _apply_time_reduction(exercises, resolved_slots, available_min, source):
    """Phase 11's deterministic reduction order: (1) retain required
    primary/secondary-compound slots' presence always, (2) shrink
    non-required (accessory/isolation-role-optional) slots' sets toward
    a floor of 1 first, (3) remove non-required slots entirely if still
    EXCEEDS, (4) only then shrink required slots toward their own
    set_min (never below it, never to zero). The main training pattern
    (primary_compound required slots) is never removed."""
    from training_intelligence.programs.adaptation.time_budget import (
        estimate_session_duration_minutes, classify_fit,
    )

    required_slot_ids = {s["slot_id"] for s in resolved_slots if s["status"] == "resolved" and s["required"]}
    working = [dict(e) for e in exercises]

    def _fit(execs):
        low, high = estimate_session_duration_minutes([
            {"sets": e["working_sets"], "rep_min": e["rep_range"]["minimum"], "rep_max": e["rep_range"]["maximum"],
             "rest_seconds_min": e["rest_seconds"]["minimum"], "rest_seconds_max": e["rest_seconds"]["maximum"],
             "is_unilateral": bool(e.get("is_bilateral") is False)}
            for e in execs
        ])
        return low, high, classify_fit(low, high, available_min)

    # Step: shrink optional (non-required) slots' sets toward 1.
    for e in working:
        if e["slot_id"] in required_slot_ids:
            continue
        while e["working_sets"] > 1:
            e["working_sets"] -= 1
            low, high, fit = _fit(working)
            if fit != "EXCEEDS":
                return working, build_time_context_from_fit(low, high, available_min, source)

    # Step: remove optional slots entirely.
    non_required = [e for e in working if e["slot_id"] not in required_slot_ids]
    for e in list(non_required):
        working.remove(e)
        low, high, fit = _fit(working)
        if fit != "EXCEEDS":
            return working, build_time_context_from_fit(low, high, available_min, source)

    # Step: shrink required slots toward set_min (never below it - the
    # slot's own set_min is looked up from resolved_slots).
    set_min_by_slot = {s["slot_id"]: s["set_min"] for s in resolved_slots if s["status"] == "resolved"}
    for e in working:
        floor = set_min_by_slot.get(e["slot_id"], 1)
        while e["working_sets"] > floor:
            e["working_sets"] -= 1
            low, high, fit = _fit(working)
            if fit != "EXCEEDS":
                return working, build_time_context_from_fit(low, high, available_min, source)

    low, high, fit = _fit(working)
    return working, build_time_context_from_fit(low, high, available_min, source)


def build_time_context_from_fit(low, high, available_min, source):
    from training_intelligence.programs.adaptation.time_budget import TIME_BUDGET_VERSION as _V, classify_fit
    return {
        "time_budget_version": _V, "estimated_minutes_low": low, "estimated_minutes_high": high,
        "available_duration_min": available_min, "duration_source": source,
        "fit_status": classify_fit(low, high, available_min),
    }
