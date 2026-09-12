"""TKI-5: exercise selection + progressive-overload prescription -
SHADOW MODE ONLY.

Answers "given the TKI-4 selected session family and TKI-3 justified
dose, exactly what exercises, sets, reps, resistance, and progression
should this user perform today" as a deterministic, explainable shadow
prescription. Does not replace, call, or influence integrations.tonal.
workout_prescription.build_daily_workout_prescription() (the live,
production exercise-prescription engine feeding B3/B4/iOS) in any way -
nothing here writes to the database or is reachable from any mobile/
production request path.

Architecture (section 2 of the assignment) - and, for every layer, the
already-live, already-validated component it reuses rather than
duplicates:

  A. Candidate generation  - integrations.tonal.movement_performance.
                              build_movement_performance_profiles()
                              (already excludes m.is_generic/
                              m.custom_movement at the SQL level - see
                              TRAINING_INTELLIGENCE_TKI5_REPORT.md's
                              Tonal-semantics audit)
  B. Eligibility            - integrations.tonal.workout_prescription.
                              _select_movements() /
                              _movement_eligibility() (fatigue/
                              suppression/usable-history/target-match
                              gates, unmodified)
  C. Scoring                - integrations.tonal.workout_prescription.
                              _candidate_score() (unmodified), inside
                              _select_movements()
  D. Session composition    - this module's _exercise_count() (new,
                              versioned, dose-driven) +
                              _select_movements() (reused, unmodified)
  E. Set allocation         - integrations.tonal.workout_prescription.
                              _set_allocation(), called with TKI-3's
                              feasible dose instead of B3's own static
                              SESSION_RULES (the function was already
                              built to accept this - see its docstring)
  F. Rep/intensity/          - integrations.tonal.progressive_overload.
     progression                prescribe() (unmodified) - a self-
                              contained, already-versioned per-exercise
                              engine (rep range, target reps, target
                              resistance, RIR, rest, progression state/
                              label/rationale, trajectory, confidence)
  G. Goal posture            - this module's _apply_goal_rep_posture()/
                              _exercise_count() (new): a goal mode may
                              only pick a posture WITHIN what B3/TKI-3
                              already established, never invent capacity
                              or override a fatigue/data-floor state
  H. Confidence/fallback     - this module's per-exercise/session
                              data_quality rollup (new)
  I. Explanation/provenance  - this module's explanation_factors (new)

Nothing above the movement-catalog layer (mode consistency, per-arm
load, bilateral/unilateral, generic-movement exclusion) is reimplemented
here - it lives in, and stays owned by, movement_performance.py /
progressive_overload.py, exactly as B3 already relies on it.
"""

from __future__ import annotations

from datetime import datetime

from integrations.tonal.movement_performance import build_movement_performance_profiles
from integrations.tonal.muscle_readiness import calculate_muscle_readiness
from integrations.tonal.progressive_overload import prescribe as b3_prescribe
from integrations.tonal.training_priority import SESSION_TEMPLATES
from integrations.tonal.workout_prescription import (
    _latest_readiness,
    _select_movements,
    _set_allocation,
    _family_for_movement,
)
from training_intelligence.dose.goal_policy import GOAL_POLICY_VERSION, resolve_goal_mode
from training_intelligence.selection.candidates import _eligible_muscles
from training_intelligence.selection.scoring_policy import MIN_PRODUCTIVE_UPPER_BOUND_WORKING_SETS
from training_intelligence.selection.shadow_selection import (
    build_shadow_selection,
    REST_FAMILY_NAME,
    SELECTION_MODEL_VERSION,
)
from training_intelligence.prescription.prescription_policy import (
    PRESCRIPTION_MODEL_VERSION,
    MIN_EXERCISES,
    MAX_EXERCISES,
    DEFAULT_SETS_PER_EXERCISE,
    EXERCISE_COUNT_DELTA_BY_GOAL_MODE,
    REP_POSITION_FRACTION_BY_GOAL_MODE,
    GOAL_POSTURE_ELIGIBLE_STATES,
    GENERIC_PLACEHOLDER_NAMES,
)

# B3's own SESSION_RULES/CONFIG tables (workout_prescription._set_allocation,
# progressive_overload's per-band RIR table) are keyed by exactly these 4
# WHOOP bands and were never designed to accept "unknown"/None (TKI-4's own
# band value when no WHOOP data exists at all - see muscle_readiness.py /
# scoring.py's own _SYSTEMIC_BAND_MODIFIER_UNKNOWN). Rather than touching
# those reused, unmodified functions, an unrecognized band is normalized to
# "moderate" (the same neutral middle B3 itself effectively assumes via
# progressive_overload.CONFIG["rir"].get(band, CONFIG["rir"]["moderate"]))
# only at THIS module's boundary, right before calling into them.
_B3_KNOWN_READINESS_BANDS = ("high", "good", "moderate", "low")


def _safe_readiness_band(readiness_band):
    return readiness_band if readiness_band in _B3_KNOWN_READINESS_BANDS else "moderate"


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def _exercise_count(target_sets, goal_mode, max_sets=None):
    """Section 7/8: how many exercise slots this session's dose
    deserves, before B3's own _set_allocation distributes the actual
    (dose-bound) sets across them. Never invents extra sets - only
    changes how many movements share the same TKI-3 budget.

    Also enforces a hard invariant (section 34): _set_allocation()
    never gives an exercise fewer than 2 sets, so requesting more
    exercise slots than `max_sets // 2` can support would make it
    mechanically impossible to stay within TKI-3's feasible upper bound.
    Capping here, not inside the reused _set_allocation(), keeps that
    function untouched."""
    if not target_sets or target_sets <= 0:
        return 0
    base = round(target_sets / DEFAULT_SETS_PER_EXERCISE)
    base = _clamp(base, MIN_EXERCISES, MAX_EXERCISES)
    delta = EXERCISE_COUNT_DELTA_BY_GOAL_MODE.get(goal_mode, 0)
    count = int(_clamp(base + delta, MIN_EXERCISES, MAX_EXERCISES))
    if max_sets:
        count = min(count, max(1, max_sets // 2))
    return count


def _filter_generic_placeholders(profiles):
    """Section 21, second defensive layer - see this module's docstring
    and prescription_policy.GENERIC_PLACEHOLDER_NAMES. Returns
    (kept, excluded_names)."""
    kept, excluded = [], []
    for profile in profiles:
        if profile.get("name") in GENERIC_PLACEHOLDER_NAMES:
            excluded.append(profile.get("name"))
            continue
        kept.append(profile)
    return kept, excluded


def _assign_roles(selected, primary_focus):
    """Deterministically re-derives 'primary' vs 'secondary/accessory'
    role labels from the ALREADY-DECIDED final _select_movements()
    ordering - the first exercise that materially covers each distinct
    focus muscle is 'primary' for that muscle; anything selected after
    its muscle was already covered is 'secondary'. Does not re-run or
    duplicate _select_movements()'s own selection algorithm."""
    covered = set()
    roles = []
    for profile in selected:
        muscles = profile.get("muscle_groups") or []
        primary_muscle = muscles[0] if muscles else None
        if primary_muscle in primary_focus and primary_muscle not in covered:
            roles.append("primary")
            covered.add(primary_muscle)
        else:
            roles.append("secondary")
    return roles


def _apply_goal_rep_posture(progression, goal_mode):
    """Section 9/15/16 - see prescription_policy.py's docstring for the
    exact, bounded contract. Returns a new dict; never mutates B3's own
    result in place."""
    if progression["progression_state"] not in GOAL_POSTURE_ELIGIBLE_STATES:
        return dict(progression, goal_posture_applied=None)
    fraction = REP_POSITION_FRACTION_BY_GOAL_MODE.get(goal_mode)
    if fraction is None:
        return dict(progression, goal_posture_applied=None)
    lo = progression["rep_range"]["minimum"]
    hi = progression["rep_range"]["maximum"]
    biased = round(lo + fraction * (hi - lo))
    biased = int(_clamp(biased, lo, hi))
    if progression["progression_state"] == "PROGRESS_REPS":
        # Goal posture may only shift PREFERENCE within the range - it
        # may never walk an already-earned rep increase back down below
        # what B3's own evidence-driven logic already decided.
        biased = max(biased, progression["target_reps_per_set"])
    result = dict(progression)
    result["target_reps_per_set"] = biased
    result["goal_posture_applied"] = fraction
    return result


def _exercise_confidence(profile, progression):
    """Section 31 - never hides a low-confidence prescription; surfaces
    it explicitly instead."""
    performance = profile.get("performance") or {}
    if (performance.get("status") or "") != "usable":
        return "LOW"
    return progression["confidence"]  # HIGH / MEDIUM / LOW / INSUFFICIENT, from B3's own comparable_history()


def _finalize_exercise(profile, set_count, role, goal_mode, readiness_band):
    raw_progression = b3_prescribe(profile, _safe_readiness_band(readiness_band), set_count)
    progression = _apply_goal_rep_posture(raw_progression, goal_mode)
    performance = profile.get("performance") or {}
    return {
        "movement_id": profile.get("movement_id"),
        "movement_name": profile.get("name"),
        "role": role,
        "primary_muscles": (profile.get("muscle_groups") or [])[:1],
        "secondary_muscles": (profile.get("muscle_groups") or [])[1:],
        "working_sets": progression["sets"],
        "rep_range": progression["rep_range"],
        "target_reps_per_set": progression["target_reps_per_set"],
        "prescribed_resistance_lb": progression["target_resistance_lb"],
        "target_rir": progression["target_rir"],
        "rest_seconds": progression["rest_seconds"],
        "progression_action": progression["progression_label"],
        "progression_state": progression["progression_state"],
        "progression_reason": progression["rationale"],
        "goal_posture_applied": progression.get("goal_posture_applied"),
        "comparable_history": progression["comparable_performance"],
        "performance_state": progression["trajectory"],
        "confidence": _exercise_confidence(profile, progression),
        "is_bilateral": bool(profile.get("is_bilateral")),
        "is_two_sided": bool(profile.get("is_two_sided")),
    }


def _rest_prescription(as_of, selection, reason):
    return {
        "status": "ok",
        "mode": "shadow",
        "prescription_model_version": PRESCRIPTION_MODEL_VERSION,
        "selection_model_version": SELECTION_MODEL_VERSION,
        "goal_policy_version": GOAL_POLICY_VERSION,
        "as_of": as_of.isoformat(),
        "selected_session_family": selection["selected_session_family"],
        "goal_mode": selection["goal_mode"],
        "dose": None,
        "exercises": [],
        "session_summary": {
            "total_working_sets": 0,
            "muscle_stimulus": {},
            "goal_mode": selection["goal_mode"],
            "readiness_context": selection["systemic_capacity"],
        },
        "explanation_factors": [reason],
        "fallbacks_used": [],
        "data_quality": selection["data_quality"],
    }


def build_shadow_prescription(as_of: datetime, goal_mode_override=None, selection_result=None) -> dict:
    """The full TKI-5 shadow prescription object as of a given moment.
    Read-only; not reachable from any live request path.

    `selection_result` (optional): a caller-supplied build_shadow_
    selection() result to avoid recomputing TKI-4 when the caller
    already has one for the same as_of/goal_mode (e.g. the Sep-12
    diagnostic and the 90-day backtest below thread this forward at
    zero extra query cost - section 32)."""

    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be a timezone-aware datetime")

    selection = selection_result or build_shadow_selection(as_of, goal_mode_override=goal_mode_override)
    goal_mode = selection["goal_mode"]
    selected_family = selection["selected_session_family"]

    # ------------------------------------------------------------------
    # Section 24: Rest/No-Exercise outcomes - never fabricate a strength
    # workout.
    # ------------------------------------------------------------------
    if selected_family == REST_FAMILY_NAME:
        return _rest_prescription(as_of, selection, "TKI-4 selected Rest / Active Recovery - no strength exercises prescribed.")

    winner = next(c for c in selection["candidates"] if c["session_family"] == selected_family)
    dose = winner["dose_summary"]
    if dose is None or dose["working_sets"] <= 0 or dose["feasible_dose_range"]["upper_bound_working_sets"] < MIN_PRODUCTIVE_UPPER_BOUND_WORKING_SETS:
        return _rest_prescription(
            as_of, selection,
            "TKI-3 dose collapsed to a non-productive range for the selected family - no exercises invented.",
        )

    readiness = _latest_readiness(now=as_of)
    readiness_band = readiness.get("readiness_band")
    muscle_readiness_result = calculate_muscle_readiness(now=as_of)
    readiness_by_name = {entry["muscle"]: entry for entry in muscle_readiness_result.get("muscles", [])}

    template = SESSION_TEMPLATES[selected_family]
    primary_focus = _eligible_muscles(template, readiness_by_name)
    suppressed_muscles = [
        entry["muscle"] for entry in muscle_readiness_result.get("muscles", [])
        if entry.get("readiness_state") in ("SUPPRESSED", "FATIGUED")
    ]

    # ------------------------------------------------------------------
    # A: candidate generation (reused, unmodified) + section 21's
    # redundant defensive filter.
    # ------------------------------------------------------------------
    # build_movement_performance_profiles() returns a wrapper object
    # ({"status", "calculated_at", "movement_count", "profiles": [...]}),
    # not a bare list - unwrapped the same way B3's own
    # build_daily_workout_prescription() does (profiles_result.get(
    # "profiles", [])), not re-derived.
    profiles_result = build_movement_performance_profiles(now=as_of)
    profiles = profiles_result.get("profiles", [])
    profiles, excluded_generic = _filter_generic_placeholders(profiles)

    # ------------------------------------------------------------------
    # D: session composition - how many exercise slots this dose earns.
    # ------------------------------------------------------------------
    max_exercises = _exercise_count(
        dose["working_sets"], goal_mode, max_sets=dose["feasible_dose_range"]["upper_bound_working_sets"],
    )

    # ------------------------------------------------------------------
    # B/C: eligibility + scoring + duplicate-movement-family suppression
    # (all reused, unmodified, from B3's own live engine).
    # ------------------------------------------------------------------
    selected = _select_movements(
        profiles, primary_focus, secondary_focus=[],
        suppressed_muscles=suppressed_muscles, max_exercises=max_exercises,
    )

    fallbacks_used = []
    if not selected:
        return _rest_prescription(
            as_of, selection,
            "No eligible movement had usable history for the selected family's target muscles - "
            "no exercises invented; see data_quality for the underlying cause.",
        )
    if max_exercises and len(selected) < max_exercises:
        fallbacks_used.append(
            f"only {len(selected)} of {max_exercises} desired exercise slots had an eligible movement"
        )

    # ------------------------------------------------------------------
    # E: set allocation - dose-bound (TKI-3), not B3's static SESSION_RULES.
    # ------------------------------------------------------------------
    upper_bound = dose["feasible_dose_range"]["upper_bound_working_sets"]
    set_counts = _set_allocation(
        selected, _safe_readiness_band(readiness_band),
        target_sets=dose["working_sets"],
        max_sets=upper_bound,
    )
    total_allocated = sum(set_counts)
    if total_allocated > upper_bound:
        # Hard invariant (section 34): prescribed total working sets can
        # never exceed TKI-3's feasible dose. _set_allocation() floors
        # every exercise at 2 sets while trimming, so this should be
        # mechanically unreachable given _exercise_count()'s max_sets//2
        # cap above - but this is enforced directly, not merely assumed.
        excess = total_allocated - upper_bound
        for index in reversed(range(len(set_counts))):
            while excess > 0 and set_counts[index] > 1:
                set_counts[index] -= 1
                excess -= 1
            if excess <= 0:
                break
        fallbacks_used.append(
            f"set allocation ({total_allocated}) exceeded the TKI-3 feasible upper bound "
            f"({upper_bound}) and was clipped"
        )

    roles = _assign_roles(selected, primary_focus)

    # ------------------------------------------------------------------
    # F/G: per-exercise rep/load/progression (reused) + goal posture (new).
    # ------------------------------------------------------------------
    exercises = [
        _finalize_exercise(profile, set_count, role, goal_mode, readiness_band)
        for profile, set_count, role in zip(selected, set_counts, roles)
    ]

    muscle_stimulus = {}
    for exercise in exercises:
        for muscle in exercise["primary_muscles"] + exercise["secondary_muscles"]:
            muscle_stimulus[muscle] = muscle_stimulus.get(muscle, 0) + exercise["working_sets"]

    if excluded_generic:
        fallbacks_used.append(f"excluded generic placeholder movement(s): {sorted(set(excluded_generic))}")
    low_confidence = [e["movement_name"] for e in exercises if e["confidence"] in ("LOW", "INSUFFICIENT")]
    if low_confidence:
        fallbacks_used.append(f"low/insufficient-confidence prescriptions for: {low_confidence}")

    explanation_factors = [
        f"Session family '{selected_family}' from TKI-4 (calibrated score {winner['score_total_calibrated']}).",
        f"TKI-3 feasible dose range {dose['feasible_dose_range']['lower_bound_working_sets']}-"
        f"{dose['feasible_dose_range']['upper_bound_working_sets']} working sets -> goal-adjusted {dose['working_sets']}.",
        f"{len(exercises)} exercise slot(s) for goal_mode={goal_mode} (base +/- EXERCISE_COUNT_DELTA_BY_GOAL_MODE).",
        f"Readiness band: {readiness_band}.",
    ]

    return {
        "status": "ok",
        "mode": "shadow",
        "prescription_model_version": PRESCRIPTION_MODEL_VERSION,
        "selection_model_version": SELECTION_MODEL_VERSION,
        "goal_policy_version": GOAL_POLICY_VERSION,
        "as_of": as_of.isoformat(),

        "selected_session_family": selected_family,
        "goal_mode": goal_mode,

        "dose": {
            "working_sets": dose["working_sets"],
            "exercise_count": len(exercises),
            "feasible_range": dose["feasible_dose_range"],
        },

        "exercises": exercises,

        "session_summary": {
            "total_working_sets": sum(e["working_sets"] for e in exercises),
            "muscle_stimulus": muscle_stimulus,
            "goal_mode": goal_mode,
            "readiness_context": selection["systemic_capacity"],
        },

        "explanation_factors": explanation_factors,
        "fallbacks_used": fallbacks_used,
        "data_quality": {
            **selection["data_quality"],
            "exercise_count_requested": max_exercises,
            "exercise_count_fulfilled": len(exercises),
        },
    }
