"""Program Intelligence V2 Phase 3 - per-candidate feasibility model.

Each function below is ONE gate or ONE input to the decision hierarchy
in decision.py - never combined into a single opaque weighted score.
Reuses, never recomputes: integrations.tonal.muscle_readiness (local),
integrations.tonal.workout_prescription._latest_readiness (systemic
band), training_intelligence.stimulus.taxonomy.to_canonical.
"""
from __future__ import annotations

from training_intelligence.stimulus.taxonomy import to_canonical
from training_intelligence.programs.adaptation.taxonomy import (
    READY_STATES, RECOVERING_STATES, INELIGIBLE_PRIMARY_STATES,
)

FEASIBILITY_VERSION = 1

# PRODUCT POLICY: a session family repeated within this many days is
# flagged for recency/monotony (Phase 3.E) - not a physiological claim.
RECENCY_FLAG_WINDOW_DAYS = 3


def _muscle_state_map(muscle_readiness_result):
    """integrations.tonal.muscle_readiness.calculate_muscle_readiness()'s
    own Title-Case muscle names, converted once to the lowercase
    canonical taxonomy every slot/program table already uses."""
    states = {}
    for entry in muscle_readiness_result.get("muscles", []):
        canonical = to_canonical(entry["muscle"])
        if canonical:
            states[canonical] = entry["readiness_state"]
    return states


def local_readiness_gate(session_slots, muscle_readiness_result):
    """Section: Phase 3.A / Phase 6 step 2 - a HARD eligibility gate,
    never a weight. A locally FATIGUED/SUPPRESSED required-slot primary
    muscle makes the session hard-ineligible regardless of systemic
    WHOOP recovery (the milestone's own explicit hard rule)."""
    states = _muscle_state_map(muscle_readiness_result)
    required_muscles = sorted({s["primary_muscle"] for s in session_slots if s.get("required", True)})
    muscle_states = {m: states.get(m, "UNKNOWN") for m in required_muscles}
    blocking = [m for m, state in muscle_states.items() if state in INELIGIBLE_PRIMARY_STATES]
    recovering = [m for m, state in muscle_states.items() if state in RECOVERING_STATES]
    return {
        "eligible": not blocking,
        "muscle_states": muscle_states,
        "blocking_muscles": blocking,
        "recovering_muscles": recovering,
        "reduced_only": bool(recovering) and not blocking,
    }


def systemic_capacity_band(readiness):
    """Section: Phase 3.B / Phase 6 step 5 - reuses the EXISTING
    readiness_band classification (integrations.tonal.workout_
    prescription._latest_readiness's own output) verbatim; no new
    recovery formula is invented here."""
    return readiness.get("readiness_band", "unknown")


def sequence_cost(rotation_position_of_candidate, nominal_rotation_position=0):
    """Section: Phase 3.D / Phase 6 step 3 - rotation_position_of_
    candidate is the candidate's own distance-from-nominal in the
    program's rotation order (0 = nominal itself, 1 = next in line,
    ...). A program-sequence-illegal jump (e.g. skipping ahead to a
    session that has not naturally come up in rotation) is never even
    offered as a candidate - see decision.py's candidate bounding -
    so every value this function ever receives is, by construction, a
    LEGAL shift; this only ranks how disruptive it is."""
    return abs(rotation_position_of_candidate - nominal_rotation_position)


def recency_flag(candidate_session, recently_completed_sessions, as_of, days_since_map=None):
    """Section: Phase 3.E - True if this session's OWN family was just
    completed within RECENCY_FLAG_WINDOW_DAYS (duplicate-exposure
    guard). days_since_map (optional): {session_key: days_since} for a
    more precise check than mere list membership."""
    family = candidate_session["session_family"]
    for completed in recently_completed_sessions:
        if completed["session_family"] != family:
            continue
        if days_since_map is not None:
            days = days_since_map.get(completed["session_key"])
            if days is not None and days <= RECENCY_FLAG_WINDOW_DAYS:
                return True
        else:
            return True
    return False


def program_need_score(candidate_slots, progress):
    from training_intelligence.programs.adaptation.progress import session_missing_stimulus_score
    return session_missing_stimulus_score(candidate_slots, progress)
