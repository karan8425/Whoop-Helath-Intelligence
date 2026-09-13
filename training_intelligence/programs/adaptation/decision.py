"""Program Intelligence V2 Phase 5-6 - the session decision engine.

Implements the decision hierarchy EXACTLY as an ordered sequence of
gates/tie-breaks, never a single weighted score:

  1. SAFETY / HARD ELIGIBILITY  - no additional hard-safety data source
     exists in this codebase beyond local muscle readiness; this step
     is a documented pass-through, not a fabricated extra gate.
  2. LOCAL MUSCLE READINESS     - HARD gate (feasibility.
     local_readiness_gate). A locally FATIGUED/SUPPRESSED required
     muscle excludes a candidate regardless of systemic recovery.
  3. PROGRAM SEQUENCE VALIDITY  - enforced by CONSTRUCTION: candidates
     are only ever the nominal session plus the immediately-following
     rotation entries (context.py's own bounded outstanding list) -
     an illegal jump is never even offered as a candidate.
  4. PROGRAM NEED                - session_missing_stimulus_score;
     required (nonzero) before a fully-eligible nominal is ever passed
     over for an alternative.
  5. SYSTEMIC CAPACITY           - reused readiness_band; downgrades
     KEEP/SHIFT to REDUCE, or forces RECOVERY/REST, never upgrades a
     hard-ineligible candidate back into eligibility.
  6. RECENCY / MONOTONY          - informational flag surfaced in the
     explanation; does not override eligibility.
  7. TIME FEASIBILITY             - applied AFTER session/dose
     selection (time_budget.py); may trigger a deterministic reduction,
     never a session-family change.
  8. TIE BREAK                    - smallest sequence_cost among
     otherwise-equal alternatives.
"""
from __future__ import annotations

from training_intelligence.programs.adaptation.taxonomy import (
    ACTION_KEEP, ACTION_SHIFT, ACTION_SUBSTITUTE, ACTION_REDUCE,
    ACTION_RECOVERY, ACTION_REST,
)
from training_intelligence.programs.adaptation.feasibility import (
    local_readiness_gate, sequence_cost, recency_flag, program_need_score,
)
from training_intelligence.selection.shadow_selection import REST_FAMILY_NAME

DECISION_ENGINE_VERSION = 1

# PRODUCT POLICY: readiness bands that permit full nominal dose, that
# require a REDUCE downgrade, and that force RECOVERY/REST - reusing
# the EXISTING readiness_band vocabulary (high/good/moderate/low/
# very_low/unknown) verbatim, never a new recovery formula.
FULL_DOSE_BANDS = ("high", "good")
REDUCE_BANDS = ("moderate", "low")
RECOVERY_BANDS = ("very_low",)

# A candidate's program_need_score above this floor counts as
# "genuine" need - not merely nonzero due to rounding. Named,
# versioned, not an arbitrary inline number.
MIN_GENUINE_PROGRAM_NEED = 0.5


def build_candidate_profiles(candidates, muscle_readiness_result, progress, recently_completed_sessions,
                              days_since_map=None):
    """One profile per candidate: local readiness gate, program need,
    recency flag, rotation position (already assigned by the caller -
    0 for nominal, increasing for each further-out rotation entry)."""
    profiles = []
    for position, (session, slots) in enumerate(candidates):
        profiles.append({
            "position": position, "session": session, "slots": slots,
            "local_gate": local_readiness_gate(slots, muscle_readiness_result),
            "program_need": program_need_score(slots, progress),
            "recency_flagged": recency_flag(session, recently_completed_sessions, None, days_since_map),
        })
    return profiles


def _pick_best_alternative(candidates, *, require_fully_ready, min_program_need):
    """Step 4 + step 8: among ELIGIBLE, non-nominal candidates, keep
    only those meeting the program-need floor (and, when required, with
    no recovering-muscle caveat either), then break ties by smallest
    sequence_cost (position, since nominal is always position 0)."""
    pool = [c for c in candidates if c["position"] != 0 and c["local_gate"]["eligible"]]
    if require_fully_ready:
        pool = [c for c in pool if not c["local_gate"]["reduced_only"]]
    pool = [c for c in pool if c["program_need"] >= min_program_need]
    if not pool:
        return None
    return min(pool, key=lambda c: (sequence_cost(c["position"]), c["position"]))


def decide_action(candidate_profiles, readiness):
    """Returns (action, chosen_profile, reason_codes). Pure function -
    no I/O, fully deterministic for identical inputs (this milestone's
    explicit reproducibility requirement)."""
    reason_codes = []
    systemic_band = readiness.get("readiness_band", "unknown")
    nominal = next((c for c in candidate_profiles if c["position"] == 0), None)
    eligible = [c for c in candidate_profiles if c["local_gate"]["eligible"]]

    if nominal is not None:
        reason_codes.append(f"NOMINAL_SESSION_{nominal['session']['session_key'].upper()}")

    if not eligible:
        # No candidate (nominal or otherwise) is locally eligible.
        reason_codes.append("NO_LOCALLY_ELIGIBLE_CANDIDATE")
        recovery_session = next(
            (c for c in candidate_profiles if c["session"]["session_family"] == REST_FAMILY_NAME), None
        )
        if recovery_session is not None:
            reason_codes.append("RECOVERY_SESSION_AVAILABLE_IN_PROGRAM")
            return ACTION_RECOVERY, recovery_session, reason_codes
        reason_codes.append("NO_RECOVERY_SESSION_DEFINED_IN_PROGRAM")
        return ACTION_REST, None, reason_codes

    if systemic_band in RECOVERY_BANDS:
        reason_codes.append("VERY_LOW_SYSTEMIC_CAPACITY")
        recovery_session = next(
            (c for c in candidate_profiles if c["session"]["session_family"] == REST_FAMILY_NAME), None
        )
        if recovery_session is not None:
            return ACTION_RECOVERY, recovery_session, reason_codes
        return ACTION_REST, None, reason_codes

    if nominal is not None and nominal["local_gate"]["eligible"]:
        for m in nominal["local_gate"]["muscle_states"]:
            state = nominal["local_gate"]["muscle_states"][m]
            reason_codes.append(f"{m.upper()}_{state}")

        if not nominal["local_gate"]["reduced_only"]:
            # Fully eligible nominal - KEEP, regardless of how fresh an
            # alternative candidate might be (Case C's explicit rule).
            action, chosen = ACTION_KEEP, nominal
        else:
            # Nominal has a non-blocking RECOVERING required muscle.
            # Only shift if a fully-ready alternative ALSO carries
            # genuine program need; otherwise substitute within the
            # nominal session itself.
            alt = _pick_best_alternative(candidate_profiles, require_fully_ready=True,
                                          min_program_need=MIN_GENUINE_PROGRAM_NEED)
            if alt is not None:
                reason_codes.append(f"{alt['session']['session_key'].upper()}_OUTSTANDING")
                reason_codes.append("SHIFT_MINIMAL_SEQUENCE_COST")
                action, chosen = ACTION_SHIFT, alt
            else:
                reason_codes.append("SUBSTITUTE_RECOVERING_MUSCLE_SLOT")
                action, chosen = ACTION_SUBSTITUTE, nominal
    else:
        # Nominal itself is hard-ineligible - must shift.
        reason_codes.append("NOMINAL_LOCALLY_INELIGIBLE")
        alt = _pick_best_alternative(candidate_profiles, require_fully_ready=False, min_program_need=0)
        if alt is None:
            reason_codes.append("NO_ELIGIBLE_ALTERNATIVE_IN_ROTATION_WINDOW")
            recovery_session = next(
                (c for c in candidate_profiles if c["session"]["session_family"] == REST_FAMILY_NAME), None
            )
            if recovery_session is not None:
                return ACTION_RECOVERY, recovery_session, reason_codes
            return ACTION_REST, None, reason_codes
        reason_codes.append(f"{alt['session']['session_key'].upper()}_OUTSTANDING")
        reason_codes.append("SHIFT_MINIMAL_SEQUENCE_COST")
        action, chosen = ACTION_SHIFT, alt

    # Step 5 (systemic capacity) - downgrade a KEEP/SHIFT to REDUCE;
    # never upgrades an already-hard-ineligible candidate.
    if action in (ACTION_KEEP, ACTION_SHIFT) and systemic_band in REDUCE_BANDS:
        reason_codes.append(f"SYSTEMIC_CAPACITY_{systemic_band.upper()}")
        action = ACTION_REDUCE
    elif systemic_band in FULL_DOSE_BANDS:
        reason_codes.append("HIGH_SYSTEMIC_CAPACITY" if systemic_band == "high" else "GOOD_SYSTEMIC_CAPACITY")

    # Step 6 (recency) - informational only, never changes the action.
    if chosen is not None and chosen.get("recency_flagged"):
        reason_codes.append("RECENT_SAME_FAMILY_EXPOSURE")

    return action, chosen, reason_codes
