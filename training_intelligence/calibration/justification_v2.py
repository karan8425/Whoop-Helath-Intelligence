"""TKI-5.4 (sections 17-21, 28): binding-vs-non-binding justification
with deterministic counterfactuals, strength classification, and a
proportional large-deviation rule.

Root cause (TKI-5.4 baseline, 78 live lifting dates): v2's
`workload_sanity_v2` cited a justification whenever the matching
binding_constraint's own `binding` flag was already True (e.g.
`systemic_readiness.binding = fractions != (1., 1.)`), which is true on
almost every moderate-or-worse WHOOP day regardless of whether that
constraint actually reduced the dose relative to a neutral baseline -
this is why BELOW_PERSONAL_RANGE_JUSTIFIED reached 79.5% of outcomes.
This module replaces that citation rule with an actual counterfactual:
a reason is binding only if REMOVING it, via the SAME pure
capacity.feasible_capacity()/choose_dose() functions that produced the
real decision, changes the computed range or dose (section 19).

PRODUCT POLICY / CALIBRATION PARAMETER throughout. No physiological
claims; nothing here infers causation from WHOOP.
"""
from __future__ import annotations

from training_intelligence.calibration.capacity import feasible_capacity, choose_dose

JUSTIFICATION_POLICY_VERSION = 1

# PRODUCT POLICY: an absolute_workload_ratio below this is a "large"
# deviation requiring proportionally stronger justification (section 21).
LARGE_DEVIATION_RATIO = 0.5

# PRODUCT POLICY: effect-size fractions (relative to demonstrated p75
# capacity) used only for reasons without an inherently STRONG/hard
# qualifier (see _strength). Named so they are auditable, not implicit.
STRONG_EFFECT_FRACTION = 0.35
MODERATE_EFFECT_FRACTION = 0.15

# The neutral goal-posture baseline: general_fitness/maintenance are the
# two modes explicitly documented in goal_policy.py as targeting the
# feasible range's own midpoint with "no specialized emphasis" - the
# only defensible zero-posture reference, not an arbitrary choice.
NEUTRAL_GOAL_MODE = "general_fitness"


def _entry(reason, effect_sets, evidence, strength_kwargs=None):
    binding = effect_sets != 0
    return {
        "reason": reason, "binding": binding,
        "observed_effect": {"set_delta": effect_sets},
        "evidence": evidence,
        "strength": _strength(reason, effect_sets, **(strength_kwargs or {})) if binding else "NONE",
    }


def _strength(reason, effect_sets, capacity=None, readiness_band=None, local_states=None):
    if reason == "detraining_uncertainty":
        return "STRONG"
    if reason == "local_readiness" and local_states and any(
            v in ("FATIGUED", "SUPPRESSED") for v in local_states.values()):
        return "STRONG"
    if reason == "systemic_readiness" and readiness_band in ("low", "very_low"):
        return "STRONG"
    if reason == "systemic_readiness" and readiness_band == "moderate":
        return "MODERATE"
    reference = max(1, (capacity or {}).get("p75_sets") or 1)
    fraction = abs(effect_sets) / reference
    if fraction >= STRONG_EFFECT_FRACTION:
        return "STRONG"
    if fraction >= MODERATE_EFFECT_FRACTION:
        return "MODERATE"
    return "WEAK"


def counterfactual_effects(capacity, sessions, as_of, readiness_band, local_states,
                            structure_cap, goal_mode, actual_feasible, actual_dose):
    """Section 19. Recomputes the SAME pure functions with one named
    factor neutralized at a time and reports the real, computed delta -
    never a heuristic guess at effect size."""
    results = []
    actual_upper = actual_feasible["upper_bound_working_sets"]

    if readiness_band not in (None, "high"):
        without = feasible_capacity(capacity, sessions, as_of, "high", local_states, structure_cap)
        effect = without["upper_bound_working_sets"] - actual_upper
        results.append(_entry("systemic_readiness", effect,
                               {"readiness_band": readiness_band, "counterfactual_band": "high"},
                               {"capacity": capacity, "readiness_band": readiness_band}))

    if local_states and any(v != "READY" for v in local_states.values()):
        neutral_states = {m: "READY" for m in local_states}
        without = feasible_capacity(capacity, sessions, as_of, readiness_band, neutral_states, structure_cap)
        effect = without["upper_bound_working_sets"] - actual_upper
        results.append(_entry("local_readiness", effect, {"local_states": dict(local_states)},
                               {"capacity": capacity, "local_states": local_states}))

    if actual_feasible["recent_exposure"]["state"] == "DETRAINED_UNCERTAIN":
        # feasible_capacity has no direct "absence" override; the 0.7
        # multiplier IS the documented policy effect (capacity.py), so
        # the counterfactual is computed algebraically from that same
        # named constant rather than re-simulating a fake absence.
        implied_without_upper = round(actual_upper / 0.7)
        effect = implied_without_upper - actual_upper
        results.append(_entry("detraining_uncertainty", effect, dict(actual_feasible["recent_exposure"])))

    structure_constraint = next(
        (c for c in actual_feasible["binding_constraints"] if c["constraint"] == "session_structure"), None)
    if structure_constraint and structure_constraint.get("binding"):
        without = feasible_capacity(capacity, sessions, as_of, readiness_band, local_states, None)
        effect = without["upper_bound_working_sets"] - actual_upper
        results.append(_entry("session_structure", effect,
                               {"structure_cap": structure_cap, "unconstrained_upper": without["upper_bound_working_sets"]},
                               {"capacity": capacity}))

    if goal_mode != NEUTRAL_GOAL_MODE:
        neutral_dose = choose_dose(actual_feasible, NEUTRAL_GOAL_MODE)
        effect = actual_dose - neutral_dose
        results.append(_entry("reduced_goal_posture", effect if effect < 0 else 0,
                               {"goal_mode": goal_mode, "neutral_dose": neutral_dose, "actual_dose": actual_dose}))

    return results


def justification_sufficient(justifications, absolute_workload_ratio):
    """Section 20-21: a large deviation requires a STRONG binding
    justification, or at least two independent MODERATE-or-stronger
    ones; a small/moderate deviation requires at least one MODERATE-
    or-stronger binding justification. A single WEAK justification is
    never sufficient on its own, regardless of deviation size."""
    binding = [j for j in justifications if j["binding"]]
    if not binding:
        return False
    qualifying = [j for j in binding if j["strength"] in ("MODERATE", "STRONG")]
    is_large = absolute_workload_ratio is not None and absolute_workload_ratio < LARGE_DEVIATION_RATIO
    if not is_large:
        return len(qualifying) >= 1
    return any(j["strength"] == "STRONG" for j in binding) or len(qualifying) >= 2
