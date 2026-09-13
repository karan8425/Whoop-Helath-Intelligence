"""TKI-5.2 explicit shadow entry point. Never imported by API routing.

The existing TKI-4 family selection is reused unchanged; only the selected
family's dose and prescription are calibrated. No cache, DB or config writes.
"""
from copy import deepcopy
from collections import defaultdict
from statistics import median

from integrations.tonal.movement_performance import build_movement_performance_profiles
from integrations.tonal.muscle_readiness import calculate_muscle_readiness
from integrations.tonal.training_priority import SESSION_TEMPLATES
from integrations.tonal.workout_prescription import _latest_readiness, _movement_eligibility, _candidate_score
from training_intelligence.selection.shadow_selection import build_shadow_selection, REST_FAMILY_NAME
from training_intelligence.dose.goal_policy import GOAL_POLICY_VERSION, get_goal_policy
from training_intelligence.prescription.shadow_prescription import _apply_goal_rep_posture
from training_intelligence.calibration.history import (
    POLICY_VERSION, load_history, valid_rows, multipliers, sessions_from_rows,
    comparable_sessions, envelope, pattern, COMPOUNDS, distribution,
)
from training_intelligence.calibration.capacity import capacity_reference, feasible_capacity, choose_dose
from training_intelligence.calibration.progression import matched_history, movement_capacity
from training_intelligence.calibration.progression_v2 import prescribe_v2 as prescribe
from training_intelligence.calibration.workload_v2 import (
    workload_sanity_v2, ACCEPTED_JUSTIFICATIONS, workload_sanity_v3,
)
from training_intelligence.calibration.justification_v2 import JUSTIFICATION_POLICY_VERSION

MAX_EXERCISES = 6  # Versioned product ceiling, not a personal constant.
# TKI-5.4 section 37: quality_verdict/_v2/_v3 coexist for continuity;
# this names which verdict function shape is "current" for snapshot
# provenance without forcing a schema rewrite of prior snapshots.
QUALITY_VERDICT_VERSION = 3
MIN_EXERCISE_SETS = 2
COMPOUND_SCORE_BONUS = 15


def select_pool(profiles, targets, states, rows, as_of):
    blocked = {m for m, state in states.items() if state in ("FATIGUED", "SUPPRESSED")}
    candidates = []
    for profile in profiles:
        if profile.get("is_generic") or profile.get("custom_movement"):
            continue
        if not _movement_eligibility(profile, targets, blocked)[0]:
            continue
        if not matched_history(rows, profile["movement_id"], as_of):
            continue
        p = deepcopy(profile)
        p["movement_pattern"] = pattern(p)
        p["capacity_sets"] = movement_capacity(rows, p["movement_id"], as_of)
        p["calibration_score"] = _candidate_score(p, targets, []) + (COMPOUND_SCORE_BONUS if pattern(p) in COMPOUNDS else 0)
        candidates.append(p)
    candidates.sort(key=lambda p: (-p["calibration_score"], str(p["movement_id"])))
    selected, used = [], set()
    # Preserve direct target coverage; compound preference is soft and local.
    for target in targets:
        match = next((p for p in candidates if p["muscle_groups"][0] == target and p["movement_id"] not in used), None)
        if match:
            selected.append(match)
            used.add(match["movement_id"])
    # Fill distinct patterns first, then allow personally supported variants.
    patterns = {p["movement_pattern"] for p in selected}
    for p in candidates:
        if p["movement_id"] not in used and p["movement_pattern"] not in patterns:
            selected.append(p)
            used.add(p["movement_id"])
            patterns.add(p["movement_pattern"])
    selected.extend(p for p in candidates if p["movement_id"] not in used)
    return selected


def workload_sanity(exercises, reference, reasons):
    total = sum(e["estimated_volume"] or 0 for e in exercises)
    selected = next((reference[str(w)] for w in (90, 365, 30)
                     if reference[str(w)]["normalized_workload"]["sample_count"] >= 3), None)
    confidence = "LOW" if any(e["workload"]["confidence"] == "LOW" for e in exercises) else "MEDIUM"
    if not selected or confidence == "LOW" or not selected["normalized_workload"]["median"]:
        return {"status": "INSUFFICIENT_DATA", "ratio_to_median": None, "ratio_to_p25": None,
                "explanation": "Comparable normalized workload or cable semantics are insufficient.",
                "accepted_reason_for_deviation": None, "confidence": "LOW",
                "estimated_workload": total, "reference": selected}
    stats = selected["normalized_workload"]
    status = "BELOW_PERSONAL_RANGE" if total < stats["p25"] else ("ABOVE_PERSONAL_RANGE" if total > stats["p75"] else "WITHIN_PERSONAL_RANGE")
    return {"status": status, "ratio_to_median": round(total / stats["median"], 4),
            "ratio_to_p25": round(total / stats["p25"], 4) if stats["p25"] else None,
            "explanation": "Standard-equivalent base-load workload is a secondary sanity signal; no tonnage target is imposed.",
            "accepted_reason_for_deviation": reasons if status != "WITHIN_PERSONAL_RANGE" and reasons else None,
            "confidence": confidence, "estimated_workload": total, "reference": selected}


def quality_verdict(exercises, feasible, sanity, states, target):
    issues = []
    total = sum(e["working_sets"] for e in exercises)
    if total > feasible["upper_bound_working_sets"]:
        return {"verdict": "CONTRADICTED", "reasons": ["Dose exceeds the hard feasible upper bound."]}
    blocked = {m for m, s in states.items() if s in ("FATIGUED", "SUPPRESSED")}
    if any(blocked & set(e["primary_muscles"] + e["secondary_muscles"]) for e in exercises):
        return {"verdict": "CONTRADICTED", "reasons": ["Prescription loads a fatigued/suppressed muscle."]}
    if total < target:
        issues.append("Selected movements could not safely absorb the chosen dose.")
    if sanity["status"] == "INSUFFICIENT_DATA":
        issues.append("Workload evidence is insufficient.")
    if sanity["status"] != "WITHIN_PERSONAL_RANGE" and not sanity["accepted_reason_for_deviation"]:
        issues.append("Workload deviates from the personal envelope without an accepted justification.")
    if any(e["confidence"] == "LOW" for e in exercises):
        issues.append("At least one movement has weak paired progression evidence.")
    return {"verdict": "QUESTIONABLE" if issues else "SUPPORTED", "reasons": issues or ["Readiness, dose and available workload/progression evidence are consistent."]}


def quality_verdict_v2(exercises, feasible, sanity_v2, states, target):
    """TKI-5.3 section 28: SUPPORTED requires (a) no hard readiness/dose
    violation, (b) composition actually absorbed the target dose, (c)
    progression evidence is acceptable OR honestly conservative (a
    REBUILD/LOW-confidence movement alone is not disqualifying - it is
    an honest "insufficient data" state, not a contradiction), and (d)
    workload is WITHIN range OR deviates with an explicit, named,
    binding justification (never a bare goal-mode mention).
    QUESTIONABLE: no hard contradiction, but an unexplained calibration
    gap remains (unjustified deviation, or workload confidence itself
    too low to judge). CONTRADICTED: a hard safety/dose invariant is
    violated."""
    total = sum(e["working_sets"] for e in exercises)
    if total > feasible["upper_bound_working_sets"]:
        return {"verdict": "CONTRADICTED", "reasons": ["Dose exceeds the hard feasible upper bound."]}
    blocked = {m for m, s in states.items() if s in ("FATIGUED", "SUPPRESSED")}
    if any(blocked & set(e["primary_muscles"] + e["secondary_muscles"]) for e in exercises):
        return {"verdict": "CONTRADICTED", "reasons": ["Prescription loads a fatigued/suppressed muscle."]}

    issues = []
    if total < target:
        issues.append("Selected movements could not safely absorb the chosen dose.")
    status = sanity_v2["status"]
    if status == "INSUFFICIENT_DATA":
        issues.append(
            f"Comparable workload evidence is insufficient (comparable_count={sanity_v2['comparable_count']}, "
            f"known_workload_fraction={sanity_v2['known_workload_fraction']})."
        )
    elif status.endswith("_UNEXPLAINED"):
        issues.append(
            f"Workload is {status.replace('_', ' ').lower()} with no accepted justification "
            f"(ratio_to_median={sanity_v2['absolute_workload_ratio']})."
        )
    # A low-confidence/REBUILD movement is honest uncertainty, not a
    # contradiction - only flagged as a QUESTIONABLE-worthy issue when
    # it is NOT already covered by the workload-evidence issue above,
    # to avoid double-counting the same underlying data gap.
    low_confidence = [e["movement_name"] for e in exercises if e["confidence"] == "LOW"]
    if low_confidence and status != "INSUFFICIENT_DATA":
        issues.append(f"Weak paired progression evidence for: {low_confidence}.")
    return {
        "verdict": "QUESTIONABLE" if issues else "SUPPORTED",
        "reasons": issues or ["Readiness, dose, workload envelope, and progression evidence are all consistent."],
    }


def quality_verdict_v3(exercises, feasible, sanity_v3, states, target):
    """TKI-5.4 section 29: identical hard-safety gates to v2.
    QUESTIONABLE now distinguishes an unjustified/insufficiently-
    justified deviation (sanity_v3.justification_sufficient is False)
    from a genuinely justified one - a reason string existing is never
    enough on its own (justification_v2.justification_sufficient
    already applied the counterfactual + strength + large-deviation
    test before sanity_v3['status'] was assigned)."""
    total = sum(e["working_sets"] for e in exercises)
    if total > feasible["upper_bound_working_sets"]:
        return {"verdict": "CONTRADICTED", "reasons": ["Dose exceeds the hard feasible upper bound."]}
    blocked = {m for m, s in states.items() if s in ("FATIGUED", "SUPPRESSED")}
    if any(blocked & set(e["primary_muscles"] + e["secondary_muscles"]) for e in exercises):
        return {"verdict": "CONTRADICTED", "reasons": ["Prescription loads a fatigued/suppressed muscle."]}

    issues = []
    if total < target:
        issues.append("Selected movements could not safely absorb the chosen dose.")
    status = sanity_v3["status"]
    if status == "INSUFFICIENT_DATA":
        issues.append(
            f"Comparable workload evidence is insufficient (comparable_count={sanity_v3['comparable_count']}, "
            f"known_workload_fraction={sanity_v3['known_workload_fraction']})."
        )
    elif status.endswith("_UNEXPLAINED"):
        binding_count = sum(1 for j in sanity_v3["justifications"] if j["binding"])
        issues.append(
            f"Workload is {status.replace('_', ' ').lower()} - {binding_count} binding factor(s) found, "
            f"none sufficient for a {sanity_v3['gap_classification']} deviation of this magnitude "
            f"(ratio_to_median={sanity_v3['absolute_workload_ratio']})."
        )
    progression_evidence = [e.get("progression_evidence") for e in exercises if e.get("progression_evidence")]
    low_confidence = [e["movement_name"] for e in exercises if e["confidence"] == "LOW"]
    genuinely_sparse = [pe for pe in progression_evidence if pe["evidence_tier"] == 5]
    if low_confidence and status != "INSUFFICIENT_DATA" and len(genuinely_sparse) < len(low_confidence):
        # Some LOW-confidence movements have real (tier 2-4) evidence
        # that simply wasn't strong enough for HIGH/MEDIUM - worth
        # flagging distinctly from a movement with truly zero history.
        issues.append(f"Weak paired progression evidence for: {low_confidence}.")
    elif low_confidence and status != "INSUFFICIENT_DATA" and genuinely_sparse:
        issues.append(f"Genuinely insufficient progression history for: {low_confidence}.")
    return {
        "verdict": "QUESTIONABLE" if issues else "SUPPORTED",
        "reasons": issues or ["Readiness, dose, workload envelope, and progression evidence are all consistent."],
    }


def build_calibrated_shadow_prescription(as_of, goal_mode_override=None, *, rows=None,
                                         profiles=None, selection=None, readiness=None,
                                         muscle_readiness=None):
    rows = load_history(as_of) if rows is None else valid_rows(rows, as_of)
    selection = selection if selection is not None else build_shadow_selection(as_of, goal_mode_override=goal_mode_override)
    family = selection["selected_session_family"]
    goal = get_goal_policy(goal_mode_override or selection["goal_mode"])
    goal_mode = goal.get("goal_mode") or goal_mode_override or selection["goal_mode"]
    readiness = readiness if readiness is not None else _latest_readiness(now=as_of)
    muscle_readiness = muscle_readiness if muscle_readiness is not None else calculate_muscle_readiness(now=as_of)
    states = {r["muscle"]: r.get("readiness_state", "UNKNOWN") for r in muscle_readiness.get("muscles", [])}
    base = {
        "status": "ok", "mode": "shadow", "calibration_model_version": POLICY_VERSION,
        "prescription_model_version": "5.2", "goal_policy_version": GOAL_POLICY_VERSION,
        "as_of": as_of.isoformat(), "goal_mode": goal_mode, "selected_session_family": family,
        "readiness": readiness, "local_readiness": states,
        "selection_provenance": "Unchanged TKI-4 selection; TKI-5.2 calibrates the selected family's dose.",
    }
    if family == REST_FAMILY_NAME:
        rest_quality = {"verdict": "SUPPORTED", "reasons": ["TKI-4 selected deliberate recovery."]}
        return dict(base, exercises=[], dose={"working_sets": 0, "delivered_sets": 0, "dose_shortfall": 0, "shortfall_reason": None},
                    estimated_total_volume=0, quality=rest_quality, quality_v2=rest_quality, quality_v3=rest_quality,
                    workload_sanity_v2={"status": "WITHIN_PERSONAL_RANGE", "estimated_workload": 0,
                                        "justification": ["deliberate_recovery_session"], "confidence": "HIGH"},
                    workload_sanity_v3={"status": "WITHIN_PERSONAL_RANGE", "estimated_workload": 0,
                                        "justifications": [{"reason": "deliberate_recovery_session", "binding": True,
                                                             "strength": "STRONG", "observed_effect": None, "evidence": None}],
                                        "justification_sufficient": True, "confidence": "HIGH",
                                        "gap_classification": "NONE"})
    relationships = multipliers(rows, as_of)
    sessions = sessions_from_rows(rows, as_of, relationships)
    capacity = capacity_reference(sessions, family, as_of)
    targets = SESSION_TEMPLATES[family]["muscles"]
    local = {m: states.get(m, "UNKNOWN") for m in targets}
    if profiles is None:
        profiles = build_movement_performance_profiles(now=as_of)["profiles"]
    pool = select_pool(profiles, targets, states, rows, as_of)
    preliminary = feasible_capacity(capacity, sessions, as_of, readiness.get("readiness_band"), local)
    comparable, structure_source = comparable_sessions(sessions, family, as_of)
    counts = [s["exercise_count"] for s in comparable]
    # Personal structure before goal/product defaults. No sets / 3 fallback.
    preferred = round(median(counts)) if counts else 3
    count_cap = min(MAX_EXERCISES, len(pool), preliminary["upper_bound_working_sets"] // MIN_EXERCISE_SETS)
    count = min(preferred, count_cap)
    selected = pool[:count]
    structure_cap = sum(p["capacity_sets"] for p in selected)
    feasible = feasible_capacity(capacity, sessions, as_of, readiness.get("readiness_band"), local, structure_cap)
    target = choose_dose(feasible, goal_mode)
    # Never allocate minimum sets beyond the goal-adjusted target.
    selected = selected[:target // MIN_EXERCISE_SETS]
    allocations = [MIN_EXERCISE_SETS] * len(selected)
    remaining = target - sum(allocations)
    while remaining > 0:
        choices = [i for i, p in enumerate(selected) if allocations[i] < p["capacity_sets"]]
        if not choices:
            break
        # Balance relative to personal set structure; compound breaks ties.
        i = min(choices, key=lambda i: (allocations[i] / selected[i]["capacity_sets"],
                                       pattern(selected[i]) not in COMPOUNDS, str(selected[i]["movement_id"])))
        allocations[i] += 1
        remaining -= 1
    # Stage 2/3 waterfall: prescribe() can itself REDUCE an already-
    # allocated count below what the capacity-aware split above gave it
    # (evidence-driven, e.g. a declining trend under reduced readiness -
    # this is the exact TKI-5.1 root cause: an allocator's output is not
    # the final delivered dose once the paired-progression comparator is
    # consulted). Any resulting gap is offered to non-REDUCE movements
    # with capacity headroom before being reported as a real shortfall -
    # never silently absorbed, never silently redistributed without a
    # cap, and never re-queried past a movement's own capacity_sets.
    prescribed = [prescribe(p, rows, as_of, readiness.get("readiness_band"), a) for p, a in zip(selected, allocations)]
    delivered = [p["sets"] for p in prescribed]
    shortfall = target - sum(delivered)
    guard = 0
    while shortfall > 0 and guard < 50:
        guard += 1
        choices = [i for i in range(len(selected))
                   if prescribed[i]["progression_state"] != "REDUCE" and delivered[i] < selected[i]["capacity_sets"]]
        if not choices:
            break
        i = min(choices, key=lambda i: (delivered[i] / selected[i]["capacity_sets"],
                                        pattern(selected[i]) not in COMPOUNDS, str(selected[i]["movement_id"])))
        retry = prescribe(selected[i], rows, as_of, readiness.get("readiness_band"), delivered[i] + 1)
        if retry["progression_state"] == "REDUCE" or retry["sets"] <= delivered[i]:
            # This movement cannot safely absorb more - remove it from
            # further consideration rather than looping on it.
            selected[i] = dict(selected[i], capacity_sets=delivered[i])
            continue
        prescribed[i], delivered[i] = retry, retry["sets"]
        shortfall -= 1
    dose_shortfall = max(0, target - sum(delivered))

    exercises = []
    for profile, p in zip(selected, prescribed):
        p = _apply_goal_rep_posture(p, goal_mode)
        workload = relationships[str(profile["movement_id"])]
        muscles = profile["muscle_groups"]
        resistance = p["target_resistance_lb"]
        volume = (round(p["sets"] * p["target_reps_per_set"] * resistance * workload["workload_multiplier"], 1)
                  if resistance is not None else None)
        exercises.append({
            "movement_id": str(profile["movement_id"]), "movement_name": profile["name"],
            "movement_pattern": pattern(profile),
            "role": "primary_compound" if pattern(profile) in COMPOUNDS else "secondary_accessory",
            "primary_muscles": muscles[:1], "secondary_muscles": muscles[1:],
            "accessory": profile.get("accessory"), "is_bilateral": bool(profile.get("is_bilateral")),
            "is_two_sided": bool(profile.get("is_two_sided")), "is_alternating": bool(profile.get("is_alternating")),
            "working_sets": p["sets"], "target_reps_per_set": p["target_reps_per_set"],
            "prescribed_resistance_lb": resistance, "rep_range": p["rep_range"],
            "target_rir": p["target_rir"], "rest_seconds": p["rest_seconds"],
            "progression_state": p["progression_state"], "progression_action": p["progression_label"],
            "progression_reason": p["rationale"], "confidence": p["confidence"],
            "performance_state": p["trajectory"], "comparable_history": p["comparable_performance"],
            "progression_evidence": p.get("progression_evidence"),
            "smart_weight": {"mode": "standard", "spotter": False, "cross_mode_fallback": False},
            "workload": workload, "workload_multiplier": workload["workload_multiplier"],
            "multiplier_source": workload["multiplier_source"],
            "estimated_volume": volume,
        })
    goal_reduced = goal.get("range_position_fraction", .5) is not None and goal.get("range_position_fraction", .5) <= .2
    reasons = []
    for constraint in feasible["binding_constraints"]:
        if constraint["binding"] and constraint["constraint"] in ("systemic_readiness", "local_readiness", "detraining_uncertainty", "session_structure"):
            reasons.append(constraint)
    # Merely naming lean_cut is not an excuse: only explicitly reduced policy posture.
    if goal_reduced:
        reasons.append({"constraint": "reduced_goal_posture", "goal_mode": goal_mode})
    reference = envelope(sessions, family, as_of)
    sanity = workload_sanity(exercises, reference, reasons)
    quality = quality_verdict(exercises, feasible, sanity, states, target)
    sanity_v2 = workload_sanity_v2(exercises, sessions, family, as_of, target,
                                    feasible["binding_constraints"], goal_reduced)
    quality_v2 = quality_verdict_v2(exercises, feasible, sanity_v2, states, target)
    sanity_v3 = workload_sanity_v3(exercises, sessions, family, as_of, target, capacity,
                                    readiness.get("readiness_band"), local, structure_cap, goal_mode,
                                    feasible, target)
    quality_v3 = quality_verdict_v3(exercises, feasible, sanity_v3, states, target)
    return dict(base, personal_capacity_reference=capacity,
                feasible_range=feasible,
                dose={"working_sets": target, "delivered_sets": sum(e["working_sets"] for e in exercises),
                      "dose_shortfall": dose_shortfall,
                      "shortfall_reason": (
                          f"{dose_shortfall} of {target} justified working sets could not be safely "
                          "absorbed: remaining selected movements are in a REDUCE state or already at "
                          "their personal-tolerance capacity_sets ceiling."
                      ) if dose_shortfall > 0 else None},
                composition={"preferred_exercises": preferred, "selected_exercises": len(selected),
                             "eligible_pool_size": len(pool), "source": structure_source,
                             "historical_exercise_distribution": distribution(counts)},
                exercises=exercises, estimated_total_volume=sanity["estimated_workload"],
                workload_reference=reference, workload_sanity=sanity, quality=quality,
                # TKI-5.3 additive fields - the v1 fields above are kept
                # unchanged for continuity/comparison; workload_sanity_v2
                # and quality_v2 are the primary shadow diagnostic now.
                workload_sanity_v2=sanity_v2, quality_v2=quality_v2,
                # TKI-5.4 additive fields - v2 above is kept unchanged for
                # continuity/comparison; workload_sanity_v3/quality_v3 are
                # the primary shadow diagnostic now (real counterfactual
                # justification provenance replaces v2's naive
                # binding-flag citation).
                workload_sanity_v3=sanity_v3, quality_v3=quality_v3)
