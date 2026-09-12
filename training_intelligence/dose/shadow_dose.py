"""TKI-3: personalized training dose - SHADOW MODE ONLY.

This module does NOT compute a dose from scratch. Training-B2
(integrations.tonal.training_dose.compute_dose_target) is already the
live, production personalized-dose engine feeding B3
(integrations.tonal.workout_prescription, called at its line ~1899) -
it already derives personal historical baselines, a WHOOP systemic-
capacity multiplier, a recent-load modifier, and a hard per-muscle
readiness budget (never resurrecting a B1-suppressed/fatigued muscle),
with a tiered comparable-session fallback hierarchy and a confidence
score. Reimplementing any of that here would be exactly the "parallel
definition" this milestone is told not to create.

integrations.tonal.progressive_overload.trajectory() already classifies
recent comparable-session performance as IMPROVING/STABLE/DECLINING/
INSUFFICIENT_DATA from volume trend - also reused as-is.

What TKI-3 actually adds, additively, in shadow mode only:

  1. A descriptive PERSONAL TOLERANCE BAND for today's goal-agnostic
     reference working-set target (tolerance_bands.classify_band),
     something B2 itself does not label.
  2. A DOSE_CLASSIFICATION (reduced/normal/upper_normal) derived
     directly from B2's own already-computed combined multiplier -
     a threshold label, not a new number.
  3. A cross-check against the TKI-2 canonical (10-muscle, Calves-
     aware) stimulus ledger for the same target muscles, alongside
     (not instead of) B2's own 9-muscle baselines.
  4. A GOAL-AGNOSTIC FEASIBLE DOSE RANGE (`feasible_dose_range`) -
     [lower_bound, upper_bound] working sets, built ONLY from B2's own
     already-computed values: the comparable-session baseline, B2's own
     WHOOP_CAPACITY_RANGES band ENDPOINTS (not the single interpolated
     point B2 uses for its own reference value), B2's own recent-load
     multiplier, hard-capped by the same total per-muscle readiness
     budget B2 already enforces. This range is identical across every
     goal mode - see test_goal_mode_never_exceeds_feasible_range.
  5. GOAL POLICY (training_intelligence.dose.goal_policy) selects a
     POSITION within that already-established, goal-agnostic range
     (`range_position_fraction`, one per goal mode) - it never invents
     capacity, never widens the range, and is applied strictly after
     the range's own hard fatigue/history caps, so it can never exceed
     what local readiness and personal history already justify. This is
     the one place goal mode is allowed to change a number:
     `recommended_dose.working_sets` (the goal-adjusted final
     recommendation) may differ across goal modes for the identical
     history/readiness state; `feasible_dose_range` and every other
     numeric field (local_readiness, systemic_capacity,
     historical_dose_reference, performance_state) never do - see
     test_training_intelligence_goal_policy.py.
  6. One unified, provenance-rich shadow object tying all of the above
     together for inspection.

Nothing here is called by workout_prescription.py's live B2/B3 call
site. `session_family` must be supplied by the caller (e.g. for the
Sep-12 diagnostic, "Upper Pull") - TKI-3 evaluates HOW MUCH for a given
session family; it does not select WHICH session family (that is TKI-4,
explicitly out of scope here).
"""

from __future__ import annotations

from datetime import datetime

from goals import get_active_goal
from integrations.tonal.muscle_readiness import calculate_muscle_readiness
from integrations.tonal.progressive_overload import trajectory as performance_trajectory
from integrations.tonal.training_priority import SESSION_TEMPLATES
from integrations.tonal.training_dose import (
    compute_dose_target,
    load_session_history,
    select_comparable_sessions,
    _percentile,
    WHOOP_CAPACITY_RANGES,
    CONSERVATIVE_MUSCLE_SET_BASELINE,
)
from integrations.tonal.workout_prescription import _latest_readiness
from training_intelligence.dose.goal_policy import (
    GOAL_POLICY_VERSION,
    get_goal_policy,
    resolve_goal_mode,
)
from training_intelligence.dose.tolerance_bands import classify_band
from training_intelligence.knowledge.loader import KNOWLEDGE_VERSION
from training_intelligence.stimulus.ledger import build_ledger_windows, load_rows
from training_intelligence.stimulus.policy import STIMULUS_POLICY_VERSION
from training_intelligence.stimulus.taxonomy import to_canonical

# v2: goal_context gained the versioned goal_policy layer (goal_mode,
# goal_mode_source, policy, goal_policy_version) replacing the single-
# phase _PHASE_TRAINING_OBJECTIVE lookup. training_objective is kept at
# the top level for backward compatibility with v1 consumers, now
# populated for every goal mode instead of only lean_cut.
DOSE_MODEL_VERSION = 2

# Product-policy thresholds on B2's own combined multiplier
# (whoop_capacity x recent_load). Not a new dose computation - purely a
# descriptive label over a value B2 already produces. Goal-agnostic:
# these thresholds are identical for every goal mode.
DOSE_CLASSIFICATION_REDUCED_CEILING = 0.85
DOSE_CLASSIFICATION_UPPER_NORMAL_FLOOR = 1.05


def _classify_dose(combined_multiplier: float) -> str:
    if combined_multiplier is None:
        return "normal"
    if combined_multiplier < DOSE_CLASSIFICATION_REDUCED_CEILING:
        return "reduced"
    if combined_multiplier > DOSE_CLASSIFICATION_UPPER_NORMAL_FLOOR:
        return "upper_normal"
    return "normal"


def _feasible_dose_range(dose: dict, target_muscles, readiness_band: str) -> dict:
    """The goal-agnostic feasible working-set range for today - built
    ONLY from values B2 already computed, never a new capacity estimate:

      - the same comparable-session baseline B2 used for its own single-
        point reference (dose["baseline"]["median_sets"]), falling back
        to B2's own CONSERVATIVE_MUSCLE_SET_BASELINE exactly as B2 itself
        does when there is no comparable history at all;
      - B2's own WHOOP_CAPACITY_RANGES band ENDPOINTS (lo, hi) for
        today's readiness_band - not the single interpolated multiplier
        B2 uses for its reference value, so this genuinely represents
        the full width of what today's systemic capacity band justifies;
      - B2's own recent-load multiplier (data-driven, not goal-driven -
        unaffected by which goal mode is active);
      - hard-capped by the SAME total per-muscle readiness budget B2
        already enforces (dose["muscle_budgets"]), so the range can
        never exceed what local muscle readiness allows, and collapses
        to exactly zero when B2's own fatigue invariant would force it
        to zero (all target muscles SUPPRESSED/FATIGUED).

    Goal policy may only select a POSITION inside this range - it never
    widens it, and this function itself never depends on goal mode."""

    baseline_sets = dose["baseline"]["median_sets"]
    if baseline_sets is None:
        baseline_sets = CONSERVATIVE_MUSCLE_SET_BASELINE * max(len(target_muscles), 1)

    recent_load_multiplier = dose["modifiers"]["recent_load"] or 1.0
    lo_whoop, hi_whoop = WHOOP_CAPACITY_RANGES.get(readiness_band, (1.0, 1.0))

    raw_low = baseline_sets * lo_whoop * recent_load_multiplier
    raw_high = baseline_sets * hi_whoop * recent_load_multiplier
    raw_low, raw_high = min(raw_low, raw_high), max(raw_low, raw_high)

    total_muscle_budget = sum(
        budget["budget_effective_sets"] for budget in dose["muscle_budgets"].values()
    )
    budget_capped = False
    if target_muscles:
        if raw_high > total_muscle_budget:
            raw_high = total_muscle_budget
            budget_capped = True
        if raw_low > total_muscle_budget:
            raw_low = total_muscle_budget

    low = max(0.0, raw_low)
    high = max(0.0, raw_high)

    return {
        "lower_bound_working_sets": round(low),
        "upper_bound_working_sets": round(high),
        "capped_by_muscle_readiness_budget": budget_capped,
    }


def _goal_adjusted_working_sets(feasible_range: dict, range_position_fraction) -> int:
    """Selects a point inside the already-established feasible range.
    No resolved goal mode -> the range's own midpoint (a neutral default,
    not a goal decision). Otherwise linearly interpolates by fraction and
    clips defensively (belt-and-suspenders on top of the construction
    guarantee) - goal policy can never push the result outside
    [lower_bound, upper_bound]."""

    low = feasible_range["lower_bound_working_sets"]
    high = feasible_range["upper_bound_working_sets"]
    if range_position_fraction is None:
        return round((low + high) / 2.0)
    fraction = max(0.0, min(1.0, range_position_fraction))
    return max(low, min(high, round(low + fraction * (high - low))))


def _goal_context(as_of, goal_mode_override=None):
    goal = get_active_goal(as_of=as_of) or {}
    goal_mode = goal_mode_override or resolve_goal_mode(goal)
    policy = get_goal_policy(goal_mode)
    return {
        "phase": goal.get("phase"),
        "goal_type": goal.get("goal_type"),
        "goal_mode": goal_mode,
        "goal_mode_source": "override" if goal_mode_override else "resolved_from_active_goal",
        "goal_policy_version": GOAL_POLICY_VERSION,
        "policy": policy,
        # Backward-compatible top-level field (was the only thing
        # exposed before the versioned goal_policy layer existed).
        "training_objective": policy.get("training_objective"),
        # Section 8: a single body-composition reading must not change
        # today's dose. Nothing in this pipeline (B2 or this module)
        # reads a point-in-time body-composition value at all, so this
        # invariant is trivially satisfied by construction, not by a
        # runtime check - documented here for auditability.
        "body_composition_single_reading_influence": "none (not consumed by this pipeline)",
    }


def build_shadow_dose(
    as_of: datetime,
    session_family: str,
    sessions=None,
    muscle_rows=None,
    ledger_rows=None,
    goal_mode_override=None,
) -> dict:
    """The full TKI-3 shadow dose object for one session family, as of a
    given moment. Read-only; calls nothing that writes to the database
    and is not on any live request path.

    `goal_mode_override`: bypasses inference from the active goal profile
    and applies a specific goal_policy.GOAL_MODES entry directly. This is
    the only way to exercise "strength" or "recovery" today, since the
    live health_goal_profiles schema has no goal_type for either yet -
    see training_intelligence/dose/goal_policy.py. Passing it never
    changes any numeric dose output (working_sets, muscle budgets, WHOOP
    multiplier, etc.) - only the goal_context/policy framing - by
    construction, since it is applied strictly after `dose` is computed."""

    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be a timezone-aware datetime")
    if session_family not in SESSION_TEMPLATES:
        raise ValueError(f"Unknown session_family: {session_family!r}")

    # Defensive as_of bound, independent of B2's own assumptions: B2's
    # load_session_history/_load_muscle_set_rows already bound
    # begin_time <= now in SQL on the live path, but its in-memory
    # window functions (_session_window_stats, _muscle_exposures/_hours)
    # only enforce a LOWER window bound once rows are in hand - _hours()
    # even clamps a future timestamp to 0 (max(0.0, negative)), which
    # would make a future row look like it "just happened" if it were
    # ever fed in directly. Rather than touch training_dose.py (a live
    # B3-serving file, out of TKI-3's scope), this shadow-only layer
    # filters any explicitly-injected sessions/rows to <= as_of itself,
    # so it is temporally safe by construction regardless of caller
    # input or B2's internal assumptions.
    if sessions is not None:
        sessions = [s for s in sessions if s.get("begin_time") and s["begin_time"] <= as_of]
    if muscle_rows is not None:
        muscle_rows = [r for r in muscle_rows if r.get("begin_time") and r["begin_time"] <= as_of]

    target_muscles = SESSION_TEMPLATES[session_family]["muscles"]

    readiness = _latest_readiness(now=as_of)
    readiness_band = readiness.get("readiness_band")
    recovery_score = readiness.get("recovery_score")

    muscle_readiness_result = calculate_muscle_readiness(now=as_of)
    muscle_readiness_by_name = {
        entry["muscle"]: entry for entry in muscle_readiness_result.get("muscles", [])
    }
    tonal_freshness_hours = muscle_readiness_result.get("latest_workout_age_hours")

    if sessions is None:
        sessions = load_session_history(as_of)

    # B2's real, unmodified dose computation - the actual recommended dose.
    dose = compute_dose_target(
        as_of,
        readiness_band,
        recovery_score,
        target_muscles,
        session_family,
        muscle_readiness_by_name,
        tonal_freshness_hours,
        sessions=sessions,
        muscle_rows=muscle_rows,
    )

    # The same comparable-session set B2 selected internally, retrieved
    # again through its own public selector (not recomputed) so we can
    # additionally classify a working-set BAND and a performance
    # TRAJECTORY - two descriptive views B2 itself doesn't produce.
    comparable_sessions, comparable_source, comparable_confidence = select_comparable_sessions(
        sessions, target_muscles, session_family, as_of
    )
    comparable_set_counts = [float(s.get("set_count") or 0) for s in comparable_sessions]
    band_p25 = _percentile(comparable_set_counts, 25)
    band_p50 = _percentile(comparable_set_counts, 50)
    band_p75 = _percentile(comparable_set_counts, 75)
    working_sets_band = classify_band(
        dose["target"]["working_sets"], len(comparable_sessions), band_p25, band_p50, band_p75
    )

    trend = performance_trajectory(comparable_sessions)

    # Goal-agnostic feasible range (identical across every goal mode),
    # then goal policy selects a position inside it. See
    # _feasible_dose_range's docstring for why this can never exceed
    # what B2's own history/readiness caps already justify.
    feasible_range = _feasible_dose_range(dose, target_muscles, readiness_band)
    goal_context = _goal_context(as_of, goal_mode_override)
    range_position_fraction = goal_context["policy"].get("range_position_fraction")
    goal_adjusted_working_sets = _goal_adjusted_working_sets(feasible_range, range_position_fraction)

    dose_classification = _classify_dose(dose["modifiers"]["combined"])

    # Cross-check against the TKI-2 canonical (Calves-aware) ledger for
    # the same target muscles - additive provenance, not a replacement
    # for B2's own muscle_baselines (which remain the authoritative input
    # to B2's actual muscle budgets above).
    if ledger_rows is None:
        ledger_rows = load_rows(as_of, 30)
    ledger_windows = build_ledger_windows(as_of, (7, 14, 30), rows=ledger_rows)
    canonical_targets = [to_canonical(m) for m in target_muscles]
    muscle_stimulus_ranges = {
        muscle: {
            "stimulus_sets_7d": ledger_windows[7]["muscles"][canonical]["total_stimulus_sets"],
            "stimulus_sets_14d": ledger_windows[14]["muscles"][canonical]["total_stimulus_sets"],
            "stimulus_sets_30d": ledger_windows[30]["muscles"][canonical]["total_stimulus_sets"],
        }
        for muscle, canonical in zip(target_muscles, canonical_targets)
        if canonical is not None
    }

    explanation_factors = [
        f"WHOOP recovery {recovery_score} ({readiness_band}) -> systemic capacity multiplier {dose['modifiers']['whoop_capacity']}",
        dose["modifiers"]["recent_load_reason"],
        f"Comparable-session baseline source: {dose['baseline']['source']} (similarity confidence: {dose['baseline']['similarity_confidence']})",
        f"Performance trend over {len(comparable_sessions)} comparable session(s): {trend}",
    ]
    for muscle, budget in dose["muscle_budgets"].items():
        explanation_factors.append(f"{muscle}: {budget['reason']}")
    if dose["dose_limited_by"]:
        explanation_factors.append(f"Dose capped by: {dose['dose_limited_by']}")
    explanation_factors.append(
        f"Feasible range today: {feasible_range['lower_bound_working_sets']}-"
        f"{feasible_range['upper_bound_working_sets']} working sets "
        f"(goal-agnostic; capped by muscle readiness budget: {feasible_range['capped_by_muscle_readiness_budget']})"
    )
    if range_position_fraction is not None:
        explanation_factors.append(
            f"Goal mode '{goal_context['goal_mode']}' targets the "
            f"{range_position_fraction:.0%} position in that range -> {goal_adjusted_working_sets} working sets."
        )
    else:
        explanation_factors.append(
            "No goal mode resolved - using the feasible range's midpoint "
            f"({goal_adjusted_working_sets} working sets)."
        )

    fallbacks_used = []
    if dose["baseline"]["source"] != "comparable_sessions_30_90d":
        fallbacks_used.append(f"session_baseline_fallback:{dose['baseline']['source']}")
    for muscle, entry in dose["muscle_baselines"]["muscles"].items():
        if muscle in target_muscles:
            has_history = bool((entry.get("windows", {}).get(14, {}) or {}).get("effective_sets"))
            if not has_history:
                fallbacks_used.append(f"muscle_baseline_fallback:{muscle}:conservative_configured_baseline")
    if working_sets_band == "insufficient_evidence":
        fallbacks_used.append("tolerance_band:insufficient_evidence")

    return {
        "status": "ok",
        "mode": "shadow",
        "dose_model_version": DOSE_MODEL_VERSION,
        "knowledge_version": KNOWLEDGE_VERSION,
        "stimulus_policy_version": STIMULUS_POLICY_VERSION,
        "as_of": as_of.isoformat(),

        "session_family": session_family,
        "target_muscles": target_muscles,

        "systemic_capacity": {
            "recovery_score": recovery_score,
            "readiness_band": readiness_band,
            "whoop_capacity_multiplier": dose["modifiers"]["whoop_capacity"],
        },

        "local_readiness": {
            muscle: {
                "readiness_state": (muscle_readiness_by_name.get(muscle) or {}).get("readiness_state", "UNKNOWN"),
                "budget_effective_sets": dose["muscle_budgets"].get(muscle, {}).get("budget_effective_sets"),
            }
            for muscle in target_muscles
        },

        "historical_dose_reference": {
            "source": dose["baseline"]["source"],
            "similarity_confidence": dose["baseline"]["similarity_confidence"],
            "comparable_session_count": len(comparable_sessions),
            "median_working_sets": band_p50,
            "p25_working_sets": band_p25,
            "p75_working_sets": band_p75,
            "median_volume": dose["baseline"]["median_volume"],
            "median_duration_minutes": dose["baseline"]["median_duration_minutes"],
        },

        "goal_context": goal_context,

        # Goal-agnostic by construction (section 4 of this module's
        # docstring) - identical across every goal_mode_override for the
        # same as_of/history/readiness state. See
        # test_goal_mode_never_exceeds_feasible_range and
        # test_feasible_range_identical_across_goal_modes.
        "feasible_dose_range": feasible_range,

        "performance_state": {
            "trajectory": trend,
            "comparable_session_count": len(comparable_sessions),
        },

        "recommended_dose": {
            # Goal-adjusted final recommendation: a position inside
            # feasible_dose_range selected by the active goal mode's
            # range_position_fraction (goal-agnostic default: the
            # range's own midpoint). MAY differ across goal modes for
            # the identical history/readiness state - see
            # goal_agnostic_reference_working_sets below for B2's own,
            # never-goal-adjusted single-point value.
            "working_sets": goal_adjusted_working_sets,
            # B2's own, unmodified, goal-agnostic single-point reference
            # (kept for audit/backward-compatibility with v1 consumers -
            # this is what "working_sets" always equaled before goal
            # policy learned to select a posture within the range).
            "goal_agnostic_reference_working_sets": dose["target"]["working_sets"],
            "exercise_count": dose["target"]["exercise_count"],
            "muscle_stimulus_ranges": muscle_stimulus_ranges,
            "target_rir_guidance": "See B3/progressive_overload per-exercise RIR - not recomputed here.",
            "progression_posture": trend,
            "working_sets_personal_band": working_sets_band,
        },

        "dose_classification": dose_classification,

        "explanation_factors": explanation_factors,
        "fallbacks_used": fallbacks_used,
        "confidence": dose["dose_confidence"],
        "data_quality": {
            "excluded_session_count": dose["session_baselines"]["excluded_session_count"],
            "unmapped_sets_ignored": dose["muscle_baselines"]["unmapped_sets_ignored"],
            "tonal_freshness_hours": tonal_freshness_hours,
        },

        # Preserves the TKI-2 distinction explicitly (section 11): this
        # object answers HOW MUCH for the given session_family. It says
        # nothing about whether session_family was the right choice -
        # that remains TKI-2's own QUESTIONABLE/SUPPORTED/CONTRADICTED
        # verdict (see TRAINING_INTELLIGENCE_TKI12_REPORT.md), not
        # reproduced or overridden here.
        "session_selection_note": (
            "This object does not evaluate whether "
            f"'{session_family}' was the correct session to train - "
            "see TKI-2's session-selection verdict for that question."
        ),
    }
