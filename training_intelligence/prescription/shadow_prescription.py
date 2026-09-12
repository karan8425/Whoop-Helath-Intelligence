"""TKI-5(.1): exercise selection + progressive-overload prescription -
SHADOW MODE ONLY.

Answers "given the TKI-4 selected session family and TKI-3 justified
dose, exactly what exercises, sets, reps, resistance, and progression
should this user perform today" as a deterministic, explainable shadow
prescription. Does not replace, call, or influence integrations.tonal.
workout_prescription.build_daily_workout_prescription() (the live,
production exercise-prescription engine feeding B3/B4/iOS) in any way -
nothing here writes to the database or is reachable from any mobile/
production request path.

Architecture, and for every layer the already-live, already-validated
component it reuses rather than duplicates:

  A. Candidate generation  - integrations.tonal.movement_performance.
                              build_movement_performance_profiles()
                              (already excludes m.is_generic/
                              m.custom_movement at the SQL level)
  B. Eligibility            - integrations.tonal.workout_prescription.
                              _select_movements() / _movement_
                              eligibility() (unmodified)
  C. Scoring                - integrations.tonal.workout_prescription.
                              _candidate_score() (unmodified), inside
                              _select_movements()
  D. Session composition    - training_intelligence.prescription.
                              composition.feasible_exercise_count() (new
                              in TKI-5.1: the feasible range is derived
                              from the REAL eligible pool + personal
                              history FIRST, then a preference is chosen
                              inside it - never the reverse)
  E. Set allocation          - training_intelligence.prescription.
     / absorption               composition.dose_absorption_waterfall()
                              (new in TKI-5.1): _set_allocation() and
                              progressive_overload.prescribe() are both
                              called exactly as before, UNMODIFIED - the
                              new logic only redistributes a shortfall
                              one of them legitimately creates (e.g. a
                              REDUCE state) to another movement that can
                              safely absorb it, or discloses an explicit
                              shortfall when none can
  F. Rep/intensity/          - integrations.tonal.progressive_overload.
     progression                prescribe() (unmodified)
  G. Goal posture            - this module's _apply_goal_rep_posture()
                              (unchanged from TKI-5): a goal mode may
                              only pick a posture WITHIN what B3/TKI-3
                              already established
  H. Confidence/fallback     - this module's per-exercise/session
                              data_quality rollup + TKI-5.1's adaptation-
                              vs-fallback split (see _classify_adaptations)
  I. Explanation/provenance  - this module's explanation_factors

Nothing above the movement-catalog layer (mode consistency, per-arm
load, bilateral/unilateral, generic-movement exclusion) is reimplemented
here - it lives in, and stays owned by, movement_performance.py /
progressive_overload.py, exactly as B3 already relies on it. See
TRAINING_INTELLIGENCE_TKI51_REPORT.md for the full root-cause analysis
and calibration methodology.
"""

from __future__ import annotations

from datetime import datetime

from integrations.tonal.movement_performance import build_movement_performance_profiles
from integrations.tonal.muscle_readiness import calculate_muscle_readiness
from integrations.tonal.training_priority import SESSION_TEMPLATES
from integrations.tonal.workout_prescription import _latest_readiness, _select_movements
from training_intelligence.dose.goal_policy import GOAL_POLICY_VERSION, resolve_goal_mode
from training_intelligence.selection.candidates import _eligible_muscles
from training_intelligence.selection.scoring_policy import MIN_PRODUCTIVE_UPPER_BOUND_WORKING_SETS
from training_intelligence.selection.shadow_selection import (
    build_shadow_selection,
    REST_FAMILY_NAME,
    SELECTION_MODEL_VERSION,
)
from training_intelligence.stimulus.ledger import load_rows
from training_intelligence.prescription.prescription_policy import (
    PRESCRIPTION_MODEL_VERSION,
    COMPOSITION_MODEL_VERSION,
    MAX_EXERCISES,
    HISTORICAL_STRUCTURE_LOOKBACK_DAYS,
    REP_POSITION_FRACTION_BY_GOAL_MODE,
    GOAL_POSTURE_ELIGIBLE_STATES,
    GENERIC_PLACEHOLDER_NAMES,
)
from training_intelligence.prescription.composition import (
    historical_session_structure,
    feasible_exercise_count,
    dose_absorption_waterfall,
)

# B3's own SESSION_RULES/CONFIG tables (workout_prescription._set_allocation,
# progressive_overload's per-band RIR table) are keyed by exactly these 4
# WHOOP bands and were never designed to accept "unknown"/None (TKI-4's own
# band value when no WHOOP data exists at all). Rather than touching those
# reused, unmodified functions, an unrecognized band is normalized to
# "moderate" only at THIS module's boundary, right before calling into them.
_B3_KNOWN_READINESS_BANDS = ("high", "good", "moderate", "low")


def _safe_readiness_band(readiness_band):
    return readiness_band if readiness_band in _B3_KNOWN_READINESS_BANDS else "moderate"


def _filter_generic_placeholders(profiles):
    """Second, redundant, defensive layer - see prescription_policy.
    GENERIC_PLACEHOLDER_NAMES's docstring. Returns (kept, excluded_names)."""
    kept, excluded = [], []
    for profile in profiles:
        if profile.get("name") in GENERIC_PLACEHOLDER_NAMES:
            excluded.append(profile.get("name"))
            continue
        kept.append(profile)
    return kept, excluded


def _assign_roles(selected, primary_focus):
    """Deterministically re-derives 'primary' vs 'secondary/accessory'
    role labels from the ALREADY-DECIDED _select_movements() ordering -
    the first exercise that materially covers each distinct focus muscle
    is 'primary' for that muscle; anything selected after its muscle was
    already covered is 'secondary'."""
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
    """A goal mode may only shift PREFERENCE within B3's own already-
    computed rep_range - never widen it, never touch REDUCE/REBUILD, and
    never walk an already-earned PROGRESS_REPS increase back down."""
    if progression["progression_state"] not in GOAL_POSTURE_ELIGIBLE_STATES:
        return dict(progression, goal_posture_applied=None)
    fraction = REP_POSITION_FRACTION_BY_GOAL_MODE.get(goal_mode)
    if fraction is None:
        return dict(progression, goal_posture_applied=None)
    lo = progression["rep_range"]["minimum"]
    hi = progression["rep_range"]["maximum"]
    biased = round(lo + fraction * (hi - lo))
    biased = int(max(lo, min(hi, biased)))
    if progression["progression_state"] == "PROGRESS_REPS":
        biased = max(biased, progression["target_reps_per_set"])
    result = dict(progression)
    result["target_reps_per_set"] = biased
    result["goal_posture_applied"] = fraction
    return result


def _exercise_confidence(profile, progression):
    """Never hides a low-confidence prescription; surfaces it explicitly."""
    performance = profile.get("performance") or {}
    if (performance.get("status") or "") != "usable":
        return "LOW"
    return progression["confidence"]


def _finalize_exercise(entry, goal_mode):
    profile = entry["profile"]
    progression = _apply_goal_rep_posture(entry["prescribe_result"], goal_mode)
    return {
        "movement_id": profile.get("movement_id"),
        "movement_name": profile.get("name"),
        "role": entry["role"],
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
        "absorption_cap": entry["cap"],
        "initial_allocation": entry["initial_sets"],
    }


def _rest_prescription(as_of, selection, reason):
    return {
        "status": "ok",
        "mode": "shadow",
        "prescription_model_version": PRESCRIPTION_MODEL_VERSION,
        "composition_model_version": COMPOSITION_MODEL_VERSION,
        "selection_model_version": SELECTION_MODEL_VERSION,
        "goal_policy_version": GOAL_POLICY_VERSION,
        "as_of": as_of.isoformat(),
        "selected_session_family": selection["selected_session_family"],
        "goal_mode": selection["goal_mode"],
        "dose": None,
        "feasible_exercise_count": None,
        "dose_absorption": None,
        "exercises": [],
        "session_summary": {
            "total_working_sets": 0,
            "muscle_stimulus": {},
            "goal_mode": selection["goal_mode"],
            "readiness_context": selection["systemic_capacity"],
        },
        "explanation_factors": [reason],
        "adaptations_used": [],
        "fallbacks_used": [],
        "data_quality": selection["data_quality"],
    }


def build_shadow_prescription(as_of: datetime, goal_mode_override=None, selection_result=None,
                               ledger_rows=None) -> dict:
    """The full TKI-5(.1) shadow prescription object as of a given
    moment. Read-only; not reachable from any live request path.

    `selection_result` (optional): a caller-supplied build_shadow_
    selection() result, to avoid recomputing TKI-4 when the caller
    already has one for the same as_of/goal_mode.
    `ledger_rows` (optional): a caller-supplied set of already-loaded
    ledger rows (training_intelligence.stimulus.ledger.load_rows), to
    avoid a redundant query when the caller already has one covering at
    least HISTORICAL_STRUCTURE_LOOKBACK_DAYS for the same as_of (e.g. a
    backtest iterating day-by-day)."""

    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be a timezone-aware datetime")

    selection = selection_result or build_shadow_selection(as_of, goal_mode_override=goal_mode_override)
    goal_mode = selection["goal_mode"]
    selected_family = selection["selected_session_family"]

    # ------------------------------------------------------------------
    # Rest/No-Exercise outcomes - never fabricate a strength workout.
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
    safe_band = _safe_readiness_band(readiness_band)
    muscle_readiness_result = calculate_muscle_readiness(now=as_of)
    readiness_by_name = {entry["muscle"]: entry for entry in muscle_readiness_result.get("muscles", [])}

    template = SESSION_TEMPLATES[selected_family]
    primary_focus = _eligible_muscles(template, readiness_by_name)
    suppressed_muscles = [
        entry["muscle"] for entry in muscle_readiness_result.get("muscles", [])
        if entry.get("readiness_state") in ("SUPPRESSED", "FATIGUED")
    ]

    # ------------------------------------------------------------------
    # A: candidate generation (reused, unmodified) + defensive
    # generic-placeholder filter.
    # ------------------------------------------------------------------
    profiles_result = build_movement_performance_profiles(now=as_of)
    profiles = profiles_result.get("profiles", [])
    profiles, excluded_generic = _filter_generic_placeholders(profiles)

    if ledger_rows is None:
        ledger_rows = load_rows(as_of, HISTORICAL_STRUCTURE_LOOKBACK_DAYS)

    # ------------------------------------------------------------------
    # B/C: the FULL ranked eligible pool (reused, unmodified) - called
    # ONCE with the absolute ceiling so the real pool size is known
    # BEFORE any exercise-count preference is chosen (section 3's root-
    # cause fix). _select_movements() with a smaller max_exercises is
    # equivalent to truncating this same ranked list (its own internal
    # loops break early at max_exercises, deterministically, given the
    # same inputs) - so this is never called twice.
    # ------------------------------------------------------------------
    full_pool = _select_movements(
        profiles, primary_focus, secondary_focus=[],
        suppressed_muscles=suppressed_muscles, max_exercises=MAX_EXERCISES,
    )

    adaptations_used = []
    fallbacks_used = []

    if not full_pool:
        return _rest_prescription(
            as_of, selection,
            "No eligible movement had usable history for the selected family's target muscles - "
            "no exercises invented; see data_quality for the underlying cause.",
        )

    # ------------------------------------------------------------------
    # D: feasible exercise-count range, from the REAL pool + personal
    # history - never a preference computed first and hoped to fit.
    # ------------------------------------------------------------------
    structure = historical_session_structure(ledger_rows, as_of, selected_family)
    upper_bound = dose["feasible_dose_range"]["upper_bound_working_sets"]
    feasible = feasible_exercise_count(
        eligible_pool_size=len(full_pool),
        dose_working_sets=dose["working_sets"],
        dose_upper_bound=upper_bound,
        goal_mode=goal_mode,
        historical_structure=structure,
    )
    selected = full_pool[:feasible["preferred"]]
    roles = _assign_roles(selected, primary_focus)

    if feasible["limiting_factors"] and len(selected) < MAX_EXERCISES:
        # A smaller-than-the-absolute-ceiling session that is still the
        # BEST FEASIBLE composition given real constraints is a normal
        # adaptation, not a fallback (section 16) - it is only reported
        # as a fallback below if the pool itself was inadequate (see
        # dose_absorption's own shortfall, which is the genuine signal).
        adaptations_used.append(
            f"used {len(selected)} exercise(s) (feasible range {feasible['min']}-{feasible['max']}, "
            f"preference source: {feasible['preference_source']}) - limited by: "
            f"{'; '.join(feasible['limiting_factors'])}"
        )

    # ------------------------------------------------------------------
    # E: dose-absorption waterfall - _set_allocation()/progressive_
    # overload.prescribe() both reused, unmodified; only the shortfall
    # redistribution logic is new (section 8/9).
    # ------------------------------------------------------------------
    waterfall = dose_absorption_waterfall(
        selected, roles, safe_band, target_sets=dose["working_sets"], max_sets=upper_bound,
    )
    if waterfall["dose_shortfall"] > 0:
        fallbacks_used.append(waterfall["shortfall_reason"])

    exercises = [_finalize_exercise(entry, goal_mode) for entry in waterfall["per_movement"]]

    total_allocated = sum(e["working_sets"] for e in exercises)
    if total_allocated > upper_bound:
        # Hard invariant - should be mechanically unreachable given
        # dose_absorption_waterfall()'s own bounds, but never merely
        # assumed.
        raise AssertionError(
            f"TKI-5.1 invariant violation: allocated {total_allocated} sets exceeds "
            f"TKI-3 feasible upper bound {upper_bound}"
        )

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
        f"Feasible exercise count {feasible['min']}-{feasible['max']} (preferred {feasible['preferred']}, "
        f"source: {feasible['preference_source']}, confidence {feasible['confidence']}).",
        f"Dose absorption: {waterfall['dose_allocated']} of {waterfall['dose_target']} targeted sets delivered.",
        f"Readiness band: {readiness_band}.",
    ]

    return {
        "status": "ok",
        "mode": "shadow",
        "prescription_model_version": PRESCRIPTION_MODEL_VERSION,
        "composition_model_version": COMPOSITION_MODEL_VERSION,
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

        "feasible_exercise_count": feasible,
        "dose_absorption": {
            "dose_target": waterfall["dose_target"],
            "dose_allocated": waterfall["dose_allocated"],
            "dose_shortfall": waterfall["dose_shortfall"],
            "shortfall_reason": waterfall["shortfall_reason"],
        },

        "exercises": exercises,

        "session_summary": {
            "total_working_sets": total_allocated,
            "muscle_stimulus": muscle_stimulus,
            "goal_mode": goal_mode,
            "readiness_context": selection["systemic_capacity"],
        },

        "explanation_factors": explanation_factors,
        "adaptations_used": adaptations_used,
        "fallbacks_used": fallbacks_used,
        "data_quality": {
            **selection["data_quality"],
            "eligible_pool_size": len(full_pool),
            "exercise_count_fulfilled": len(exercises),
            "historical_structure_source": structure["source"],
            "historical_structure_sample_count": structure["sample_count"],
        },
    }
