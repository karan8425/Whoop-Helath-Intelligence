"""TKI-4: candidate generation and eligibility - SHADOW MODE ONLY.

Reuses the existing, live SESSION_TEMPLATES taxonomy
(integrations.tonal.training_priority) - no competing taxonomy is
introduced. Eligibility here is deliberately narrow (section 4 of the
assignment): a candidate is excluded ONLY for an explicit, named reason -
insufficient local readiness, an incompatible structural constraint
(Full Body needs both an eligible upper and lower muscle; Core +
Accessories needs Core), insufficient feasible dose (TKI-3), or an
explicit goal-policy recovery-mode restriction. Having LESS stimulus
debt than a peer family is never a reason to exclude - that is scoring's
job (training_intelligence.selection.scoring), not eligibility's.
"""

from __future__ import annotations

from integrations.tonal.training_priority import SESSION_TEMPLATES
from training_intelligence.dose.shadow_dose import build_shadow_dose
from training_intelligence.selection.scoring_policy import (
    MIN_PRODUCTIVE_UPPER_BOUND_WORKING_SETS,
    FAMILY_SYSTEMIC_COST,
)

RECOVERY_MODE_SYSTEMIC_COST_THRESHOLD = 0.6
RECOVERY_MODE_RESTRICTED_BANDS = ("very_low", "low")


def _eligible_muscles(template, readiness_by_name):
    eligible = []
    for muscle in template["muscles"]:
        entry = readiness_by_name.get(muscle)
        state = (entry or {}).get("readiness_state")
        # A muscle with NO readiness entry at all (cold-start: no local
        # readiness data) is neither SUPPRESSED nor FATIGUED - it passes
        # this coarse eligibility gate. It is NOT silently treated as
        # Fresh, though: scoring.py's local_readiness component gives an
        # explicit, lower "unknown" value for it - see section 7/21.
        if state not in ("SUPPRESSED", "FATIGUED"):
            eligible.append(muscle)
    return eligible


def generate_candidates(
    as_of,
    readiness,
    muscle_readiness_result,
    active_goal,
    goal_mode_override=None,
    sessions=None,
    muscle_rows=None,
    ledger_rows=None,
):
    """Returns one candidate dict per SESSION_TEMPLATES family. Each
    candidate carries its own computed TKI-3 shadow dose (None if
    excluded before reaching the dose-feasibility check)."""

    readiness_by_name = {
        entry["muscle"]: entry for entry in muscle_readiness_result.get("muscles", [])
    }
    candidates = []

    for family, template in SESSION_TEMPLATES.items():
        eligible_muscles = _eligible_muscles(template, readiness_by_name)
        excluded_muscles = {
            muscle: (readiness_by_name.get(muscle) or {}).get("readiness_state", "UNKNOWN")
            for muscle in template["muscles"]
            if muscle not in eligible_muscles
        }

        reasons = []
        if len(eligible_muscles) < template["minimum_eligible"]:
            reasons.append(
                f"insufficient_local_readiness: only {len(eligible_muscles)} of "
                f"{template['minimum_eligible']} required muscles are not "
                f"SUPPRESSED/FATIGUED (excluded: {excluded_muscles})"
            )
        if family == "Full Body":
            has_upper = any(m in eligible_muscles for m in ("Chest", "Back", "Shoulders"))
            has_lower = any(m in eligible_muscles for m in ("Glutes", "Hamstrings", "Quads"))
            if not (has_upper and has_lower):
                reasons.append(
                    "incompatible_training_constraint: Full Body requires at least one "
                    "eligible upper-body and one eligible lower-body muscle"
                )
        if family == "Core + Accessories" and "Core" not in eligible_muscles:
            reasons.append(
                "incompatible_training_constraint: Core + Accessories requires Core to be eligible"
            )

        dose = None
        if not reasons:
            dose = build_shadow_dose(
                as_of, family,
                sessions=sessions, muscle_rows=muscle_rows, ledger_rows=ledger_rows,
                goal_mode_override=goal_mode_override,
                readiness=readiness, muscle_readiness_result=muscle_readiness_result,
                active_goal=active_goal,
            )
            upper_bound = dose["feasible_dose_range"]["upper_bound_working_sets"]
            if upper_bound < MIN_PRODUCTIVE_UPPER_BOUND_WORKING_SETS:
                reasons.append(
                    f"insufficient_feasible_dose: TKI-3 upper bound of {upper_bound} "
                    f"working sets is below the minimum productive threshold "
                    f"({MIN_PRODUCTIVE_UPPER_BOUND_WORKING_SETS})"
                )

        resolved_goal_mode = dose["goal_context"]["goal_mode"] if dose else goal_mode_override
        if (
            not reasons
            and resolved_goal_mode == "recovery"
            and FAMILY_SYSTEMIC_COST.get(family, 0.5) > RECOVERY_MODE_SYSTEMIC_COST_THRESHOLD
            and readiness.get("readiness_band") in RECOVERY_MODE_RESTRICTED_BANDS
        ):
            reasons.append(
                "recovery_mode_restriction: a high-systemic-cost family is excluded "
                f"while goal_mode=recovery and systemic readiness_band="
                f"{readiness.get('readiness_band')}"
            )

        candidates.append({
            "session_family": family,
            "primary_muscles": template["muscles"],
            "eligible": not reasons,
            "exclusion_reasons": reasons,
            "eligible_muscles": eligible_muscles,
            "excluded_muscles": excluded_muscles,
            "dose": dose,
        })

    return candidates
