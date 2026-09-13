"""Program Intelligence V2 Phases 8-10 - Tonal slot resolution,
personalized dose, and progression.

Reuses, never duplicates:
  - training_intelligence.programs.repository/tonal_mapping (V1): slot
    -> ranked candidate Tonal movements.
  - training_intelligence.calibration.history: multipliers/
    sessions_from_rows/capacity_reference inputs (cable-aware workload,
    comparable-session capacity) - TKI-5.2.
  - training_intelligence.calibration.capacity: feasible_capacity/
    choose_dose - TKI-5.2.
  - training_intelligence.calibration.progression.movement_capacity -
    per-movement structure cap (TKI-5.1/5.2).
  - training_intelligence.calibration.progression_v2.prescribe_v2 -
    tiered progression evidence (TKI-5.4).

Architectural rule (Phase 9): RECENT stimulus tells us what is
MISSING (progress.py); HISTORICAL successful training tells us what
the user CAN handle (capacity_reference, built from the full lookback
window, never narrowed to "recent"); LOCAL + SYSTEMIC readiness tell
us how much of that demonstrated capacity is appropriate TODAY; the
PROGRAM supplies the slot's [set_min, set_max] envelope; GOAL only
repositions the dose within whatever range survives all of the above.
Sparse recent training must never shrink demonstrated capacity itself
- capacity_reference already draws from up to 365 days, not "recent".
"""
from __future__ import annotations

from db import get_conn
from training_intelligence.programs import repository
from training_intelligence.programs.tonal_mapping import rank_candidates
from training_intelligence.calibration.history import multipliers, sessions_from_rows
from training_intelligence.calibration.capacity import (
    capacity_reference as build_capacity_reference, feasible_capacity, choose_dose,
)
from training_intelligence.calibration.progression import movement_capacity
from training_intelligence.calibration.progression_v2 import prescribe_v2

TONAL_RESOLUTION_VERSION = 1

UNRESOLVED = "UNRESOLVED"


def _load_movement_profiles(movement_ids):
    """One batched query for every candidate movement this resolution
    might need - never one query per slot/candidate (Phase 20's
    anti-N+1 requirement)."""
    if not movement_ids:
        return {}
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT movement_id, name, muscle_groups, is_bilateral, is_two_sided, is_alternating, accessory "
                "FROM public.tonal_movements WHERE movement_id = ANY(%s)",
                (list(movement_ids),),
            )
            return {str(r["movement_id"]): r for r in cur.fetchall()}


def resolve_slots(slots, rows, as_of):
    """Phase 8: for every slot, rank Tonal candidates (V1's own
    deterministic engine, unchanged) and pick the top-ranked one whose
    id is also a batch-loaded real movement. Personal-history
    preference is applied ONLY as tonal_mapping.rank_candidates' own
    documented tie-break (personal_session_counts) - never overriding
    mapping_quality. Required slots that resolve to zero candidates are
    reported explicitly (status=UNRESOLVED), never silently dropped."""
    all_candidates = {}
    for slot in slots:
        mapping_rows = repository.get_candidate_tonal_movements(
            slot["movement_pattern"], slot["primary_muscle"], slot["exercise_role"],
        )
        all_candidates[slot["id"]] = mapping_rows

    # Personal usage tie-break input: how many past sessions used each
    # candidate movement - computed once from the already-loaded rows,
    # never a new query per slot.
    session_counts = {}
    for row in rows:
        mid = str(row["movement_id"])
        session_counts.setdefault(mid, set()).add(str(row["activity_id"]))
    personal_counts = {mid: len(s) for mid, s in session_counts.items()}

    movement_ids_needed = set()
    ranked_by_slot = {}
    for slot in slots:
        ranked = rank_candidates(all_candidates[slot["id"]], personal_session_counts=personal_counts)
        ranked_by_slot[slot["id"]] = ranked
        if ranked:
            movement_ids_needed.add(ranked[0]["movement_id"])

    profiles = _load_movement_profiles(movement_ids_needed)

    resolved = []
    for slot in slots:
        ranked = ranked_by_slot[slot["id"]]
        if not ranked:
            resolved.append({
                "slot_id": slot["id"], "required": slot.get("required", True),
                "movement_pattern": slot["movement_pattern"], "primary_muscle": slot["primary_muscle"],
                "exercise_role": slot["exercise_role"], "status": UNRESOLVED,
                "chosen_movement": None, "alternates": [], "rationale": "No eligible Tonal candidate found.",
            })
            continue
        chosen = ranked[0]
        profile_row = profiles.get(chosen["movement_id"])
        resolved.append({
            "slot_id": slot["id"], "required": slot.get("required", True),
            "movement_pattern": slot["movement_pattern"], "primary_muscle": slot["primary_muscle"],
            "exercise_role": slot["exercise_role"], "status": "resolved",
            "chosen_movement": {
                "movement_id": chosen["movement_id"], "movement_name": chosen["movement_name"],
                "mapping_quality": chosen["mapping_quality"], "score": chosen["score"],
                "muscle_groups": (profile_row.get("muscle_groups") if profile_row else None) or [],
                "is_bilateral": bool(profile_row.get("is_bilateral")) if profile_row else None,
                "accessory": profile_row.get("accessory") if profile_row else None,
            },
            "alternates": ranked[1:4],
            "rationale": chosen["reasons"],
            "set_min": slot["set_min"], "set_max": slot["set_max"],
            "rep_min": slot["rep_min"], "rep_max": slot["rep_max"],
            "rir_min": slot["rir_min"], "rir_max": slot["rir_max"],
            "rest_seconds_min": slot["rest_seconds_min"], "rest_seconds_max": slot["rest_seconds_max"],
        })
    return resolved


def personalize_dose_and_progression(resolved_slots, rows, as_of, session_family, readiness_band,
                                      local_states, goal_mode):
    """Phase 9-10. Builds ONE capacity_reference/feasible_capacity for
    the whole session (not per-slot), then a per-movement progression
    call (progression_v2.prescribe_v2, TKI-5.4 - tiered evidence,
    mode-compatibility-aware, never a disconnected median-load-times-
    average-reps calculation). Returns (exercises, capacity, feasible).
    """
    relationships = multipliers(rows, as_of)
    sessions = sessions_from_rows(rows, as_of, relationships)
    capacity = build_capacity_reference(sessions, session_family, as_of)

    resolvable = [s for s in resolved_slots if s["status"] == "resolved"]
    structure_cap = sum(
        movement_capacity(rows, s["chosen_movement"]["movement_id"], as_of) for s in resolvable
    ) if resolvable else None
    feasible = feasible_capacity(capacity, sessions, as_of, readiness_band, local_states, structure_cap)
    target_sets = choose_dose(feasible, goal_mode)

    # Waterfall allocation (mirrors calibration/shadow.py's own proven
    # pattern): start every resolvable slot at its floor (1 set - never
    # its own program set_min, since the SUM of every required slot's
    # set_min can legitimately exceed a low-capacity day's feasible
    # upper bound; forcing set_min per slot regardless of the total
    # budget is exactly the TKI-5.1 root-cause bug this codebase already
    # fixed once), then greedily grow toward each slot's own set_max,
    # highest-priority role first, until target_sets is delivered or no
    # slot has remaining headroom. Never exceeds feasible's own upper
    # bound (target_sets is already <= that bound by construction of
    # choose_dose/feasible_capacity) - total delivered can only ever be
    # <= target_sets, so quality_verdict_v3's hard dose-range gate can
    # never fire from this allocation.
    _ROLE_PRIORITY = {"primary_compound": 0, "secondary_compound": 1, "unilateral": 2,
                       "core": 3, "isolation": 4, "accessory": 5, "conditioning": 6}
    ordered = sorted(resolvable, key=lambda s: (_ROLE_PRIORITY.get(s["exercise_role"], 9), s["slot_id"]))
    allocations = {s["slot_id"]: min(1, s["set_max"]) for s in ordered}
    remaining = target_sets - sum(allocations.values())
    below_program_min = {s["slot_id"]: True for s in ordered}  # updated as slots reach their own set_min
    guard = 0
    while remaining > 0 and guard < 200:
        guard += 1
        choices = [s for s in ordered if allocations[s["slot_id"]] < s["set_max"]]
        if not choices:
            break
        # Prefer slots still below their OWN program set_min first (so
        # a slot only exceeds its floor once every slot has reached it),
        # then by role priority, then by smallest current allocation.
        target_slot = min(choices, key=lambda s: (
            allocations[s["slot_id"]] >= s["set_min"],
            _ROLE_PRIORITY.get(s["exercise_role"], 9),
            allocations[s["slot_id"]],
        ))
        allocations[target_slot["slot_id"]] += 1
        if allocations[target_slot["slot_id"]] >= target_slot["set_min"]:
            below_program_min[target_slot["slot_id"]] = False
        remaining -= 1
    dose_shortfall = max(0, remaining)

    exercises = []
    allocated_total = 0
    for s in resolvable:
        allocation = allocations[s["slot_id"]]
        allocated_total += allocation

        profile = {
            "movement_id": s["chosen_movement"]["movement_id"], "name": s["chosen_movement"]["movement_name"],
            "muscle_groups": s["chosen_movement"]["muscle_groups"],
        }
        progression = prescribe_v2(profile, rows, as_of, readiness_band, allocation)
        movement_id = s["chosen_movement"]["movement_id"]
        workload = relationships.get(movement_id, {
            "workload_multiplier": 1, "multiplier_source": "product_policy_fallback", "confidence": "LOW",
        })
        resistance = progression["target_resistance_lb"]
        reps = progression["target_reps_per_set"]
        sets = progression["sets"]
        estimated_volume = (
            round(sets * reps * resistance * workload["workload_multiplier"], 1)
            if resistance is not None else None
        )
        exercises.append({
            "slot_id": s["slot_id"], "movement_id": movement_id,
            "movement_name": s["chosen_movement"]["movement_name"],
            "primary_muscles": [s["primary_muscle"]], "secondary_muscles": [],
            "working_sets": sets, "target_reps_per_set": reps,
            "target_resistance_lb": resistance, "rep_range": progression["rep_range"],
            "target_rir": progression["target_rir"], "rest_seconds": progression["rest_seconds"],
            "progression_state": progression["progression_state"], "progression_action": progression["progression_label"],
            "progression_reason": progression["rationale"], "confidence": progression["confidence"],
            "progression_evidence": progression["progression_evidence"],
            "is_bilateral": s["chosen_movement"]["is_bilateral"],
            "workload": workload, "workload_multiplier": workload["workload_multiplier"],
            "estimated_volume": estimated_volume,
            "below_program_set_min": below_program_min[s["slot_id"]],
            "program_set_min": s["set_min"], "program_set_max": s["set_max"],
        })

    return {
        "exercises": exercises, "capacity_reference": capacity, "feasible_range": feasible,
        "target_sets": target_sets, "delivered_sets": allocated_total, "structure_cap": structure_cap,
        "sessions_for_workload": sessions, "dose_shortfall": dose_shortfall,
    }
