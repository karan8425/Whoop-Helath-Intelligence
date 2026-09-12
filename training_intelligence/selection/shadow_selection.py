"""TKI-4: session selection / scoring - SHADOW MODE ONLY.

Answers "what should this user train today" as a deterministic,
explainable shadow ranking. Does not replace, call, or influence
integrations.tonal.training_priority.build_training_priority() (the
live, production session-selection engine feeding B3) in any way -
nothing here writes to the database or is reachable from any mobile/
production request path.

Layers (section 2 of the assignment):

  A. Candidate generation   - training_intelligence.selection.candidates
                               (one candidate per SESSION_TEMPLATES family)
  B. Candidate eligibility  - candidates.py (readiness/structural/dose-
                               feasibility/recovery-mode gates only -
                               never "less debt than a peer")
  C. Candidate scoring      - training_intelligence.selection.scoring
                               (9 bounded [0,1] dimensions)
  D. Goal-policy adjustment - scoring_policy.SCORING_WEIGHTS, applied as
                               a weighted average inside scoring.py
  E. Personalized tie-break - _sort_key below (versioned, explicit order)
  F. Final shadow ranking   - this module's build_shadow_selection()
  G. Explanation/provenance - _explain() below + each candidate's own
                               explanation_factors

Shared, as_of-bound inputs (WHOOP readiness, muscle readiness, active
goal, session history, muscle-set rows, ledger rows) are fetched exactly
ONCE per build_shadow_selection() call and reused across every candidate
family - see section 23's performance requirement and
TRAINING_INTELLIGENCE_TKI4_REPORT.md's performance section for the
before/after query count this avoids.
"""

from __future__ import annotations

from datetime import datetime

from goals import get_active_goal
from integrations.tonal.muscle_readiness import calculate_muscle_readiness
from integrations.tonal.training_dose import load_session_history, _load_muscle_set_rows
from integrations.tonal.training_priority import SESSION_TEMPLATES
from integrations.tonal.workout_prescription import _latest_readiness
from training_intelligence.dose.goal_policy import GOAL_POLICY_VERSION, resolve_goal_mode
from training_intelligence.selection.candidates import generate_candidates
from training_intelligence.selection.scoring import score_candidates
from training_intelligence.selection.scoring_policy import (
    SCORING_POLICY_VERSION,
    MIN_PRODUCTIVE_UPPER_BOUND_WORKING_SETS,
    REST_BASELINE_SCORE,
    REST_BONUS_VERY_LOW_SYSTEMIC,
    REST_BONUS_LOW_SYSTEMIC,
    REST_BONUS_NO_ELIGIBLE_LIFTING_CANDIDATE,
    REST_BONUS_NO_PRODUCTIVE_DOSE_ANYWHERE,
    REST_BONUS_RECOVERY_GOAL_MODE,
)
from training_intelligence.stimulus.ledger import load_rows
from training_intelligence.stimulus.session_family import session_family_windows

SELECTION_MODEL_VERSION = 1
REST_FAMILY_NAME = "Rest / Active Recovery"


def _rest_candidate(readiness, goal_mode, eligible_candidates, any_productive_dose):
    band = readiness.get("readiness_band")
    score = REST_BASELINE_SCORE
    reasons = []

    if band == "very_low":
        score += REST_BONUS_VERY_LOW_SYSTEMIC
        reasons.append(f"systemic readiness_band={band}")
    elif band == "low":
        score += REST_BONUS_LOW_SYSTEMIC
        reasons.append(f"systemic readiness_band={band}")
    if not eligible_candidates:
        score += REST_BONUS_NO_ELIGIBLE_LIFTING_CANDIDATE
        reasons.append("no lifting session family is currently eligible")
    if not any_productive_dose:
        score += REST_BONUS_NO_PRODUCTIVE_DOSE_ANYWHERE
        reasons.append("no eligible lifting family has a productive feasible dose")
    if goal_mode == "recovery":
        score += REST_BONUS_RECOVERY_GOAL_MODE
        reasons.append("goal_mode=recovery")

    if not reasons:
        reasons.append("no condition currently favors rest - included for completeness")

    return {
        "session_family": REST_FAMILY_NAME,
        "primary_muscles": [],
        "eligible": True,
        "exclusion_reasons": [],
        "eligible_muscles": [],
        "excluded_muscles": {},
        "dose": None,
        "score_total": round(min(1.0, score), 4),
        "score_components": {
            "stimulus_debt": None, "local_readiness": None, "days_since_trained": None,
            "program_balance": None, "systemic_capacity": None, "goal_relevance": None,
            "performance": None, "dose_feasibility": None, "schedule_fit": None,
        },
        "scoring_weights_used": None,
        "rest_bonus_reasons": reasons,
    }


def _sort_key(candidate):
    """Section 16: explicit, versioned tie-break order - larger unresolved
    stimulus debt, then better local readiness, then stronger program-
    balance need, then longer justified days-since-trained, then better
    dose feasibility, then alphabetical as the absolute last resort."""
    components = candidate.get("score_components") or {}
    return (
        -candidate["score_total"],
        -(components.get("stimulus_debt") or 0.0),
        -(components.get("local_readiness") or 0.0),
        -(components.get("program_balance") or 0.0),
        -(components.get("days_since_trained") or 0.0),
        -(components.get("dose_feasibility") or 0.0),
        candidate["session_family"],
    )


def _dose_summary(dose):
    if dose is None:
        return None
    return {
        "working_sets": dose["recommended_dose"]["working_sets"],
        "goal_agnostic_reference_working_sets": dose["recommended_dose"]["goal_agnostic_reference_working_sets"],
        "feasible_dose_range": dose["feasible_dose_range"],
        "dose_classification": dose["dose_classification"],
        "working_sets_personal_band": dose["recommended_dose"]["working_sets_personal_band"],
    }


def _explanation_factors(candidate, goal_mode):
    factors = []
    if not candidate["eligible"]:
        factors.extend(candidate["exclusion_reasons"])
        return factors
    if candidate["session_family"] == REST_FAMILY_NAME:
        factors.extend(candidate["rest_bonus_reasons"])
        return factors
    components = candidate["score_components"]
    factors.append(
        f"stimulus_debt={components['stimulus_debt']:.2f}, "
        f"local_readiness={components['local_readiness']:.2f}, "
        f"days_since_trained={components['days_since_trained']:.2f}, "
        f"program_balance={components['program_balance']:.2f}"
    )
    factors.append(
        f"systemic_capacity={components['systemic_capacity']:.2f}, "
        f"goal_relevance={components['goal_relevance']:.2f} (goal_mode={goal_mode}), "
        f"performance={components['performance']:.2f}, "
        f"dose_feasibility={components['dose_feasibility']:.2f}, "
        f"schedule_fit={components['schedule_fit']:.2f}"
    )
    dose = candidate["dose"]
    if dose:
        factors.append(
            f"TKI-3 feasible dose range: {dose['feasible_dose_range']['lower_bound_working_sets']}-"
            f"{dose['feasible_dose_range']['upper_bound_working_sets']} working sets -> "
            f"goal-adjusted {dose['recommended_dose']['working_sets']}."
        )
    return factors


def _finalize_candidate(candidate, goal_mode):
    dose = candidate["dose"]
    return {
        "session_family": candidate["session_family"],
        "eligible": candidate["eligible"],
        "exclusion_reasons": candidate["exclusion_reasons"],
        # Ineligible candidates were never scored (score_candidates()
        # only scores eligible ones, per section 4: ineligibility is
        # never merely "scored lower") - .get() rather than a KeyError.
        "score_total": candidate.get("score_total"),
        "score_components": candidate.get("score_components"),
        "primary_muscles": candidate["primary_muscles"],
        # SESSION_TEMPLATES does not itself distinguish primary/secondary
        # muscles at the family level (only individual exercises do,
        # via training_intelligence.stimulus.mapping) - documented gap,
        # not fabricated data.
        "secondary_muscles": [],
        "goal_mode": goal_mode,
        "goal_policy_version": GOAL_POLICY_VERSION,
        "dose_summary": _dose_summary(dose),
        "confidence": dose["confidence"] if dose else ("LOW" if candidate["session_family"] != REST_FAMILY_NAME else "HIGH"),
        "data_quality": dose["data_quality"] if dose else {},
        "explanation_factors": _explanation_factors(candidate, goal_mode),
    }


def build_shadow_selection(
    as_of: datetime,
    goal_mode_override=None,
    sessions=None,
    muscle_rows=None,
    ledger_rows=None,
) -> dict:
    """The full TKI-4 shadow selection object as of a given moment.
    Read-only; not reachable from any live request path."""

    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be a timezone-aware datetime")

    # ------------------------------------------------------------------
    # Shared inputs, fetched exactly once (section 23).
    # ------------------------------------------------------------------
    readiness = _latest_readiness(now=as_of)
    muscle_readiness_result = calculate_muscle_readiness(now=as_of)
    active_goal = get_active_goal(as_of=as_of) or {}

    if sessions is None:
        sessions = load_session_history(as_of)
    if muscle_rows is None:
        muscle_rows = _load_muscle_set_rows(as_of, 90)
    if ledger_rows is None:
        ledger_rows = load_rows(as_of, 30)

    goal_mode = goal_mode_override or resolve_goal_mode(active_goal)

    # ------------------------------------------------------------------
    # A/B: candidate generation + eligibility.
    # ------------------------------------------------------------------
    candidates = generate_candidates(
        as_of, readiness, muscle_readiness_result, active_goal,
        goal_mode_override=goal_mode_override,
        sessions=sessions, muscle_rows=muscle_rows, ledger_rows=ledger_rows,
    )

    # Program-balance input: TKI-2's own session-family classification of
    # ACTUAL recent Tonal history (not merely past recommendations).
    families_14d = session_family_windows(ledger_rows, as_of, (14,))[14]
    family_session_counts_14d = {family: entry["sessions"] for family, entry in families_14d.items()}
    for family in SESSION_TEMPLATES:
        family_session_counts_14d.setdefault(family, 0)

    # ------------------------------------------------------------------
    # C/D: scoring (goal-weighted).
    # ------------------------------------------------------------------
    candidates = score_candidates(
        candidates, muscle_readiness_result, readiness, goal_mode, family_session_counts_14d
    )

    eligible = [c for c in candidates if c["eligible"]]
    any_productive_dose = any(
        c["dose"] is not None
        and c["dose"]["feasible_dose_range"]["upper_bound_working_sets"] >= MIN_PRODUCTIVE_UPPER_BOUND_WORKING_SETS
        for c in eligible
    )
    rest = _rest_candidate(readiness, goal_mode, eligible, any_productive_dose)

    # ------------------------------------------------------------------
    # E/F: tie-break + final ranking.
    # ------------------------------------------------------------------
    ranked = sorted(eligible + [rest], key=_sort_key)
    winner = ranked[0]

    finalized_candidates = [_finalize_candidate(c, goal_mode) for c in candidates]
    finalized_candidates.append(_finalize_candidate(rest, goal_mode))
    finalized_ranked = [_finalize_candidate(c, goal_mode) for c in ranked]

    return {
        "status": "ok",
        "mode": "shadow",
        "selection_model_version": SELECTION_MODEL_VERSION,
        "scoring_policy_version": SCORING_POLICY_VERSION,
        "goal_policy_version": GOAL_POLICY_VERSION,
        "as_of": as_of.isoformat(),

        "goal_mode": goal_mode,
        "systemic_capacity": {
            "recovery_score": readiness.get("recovery_score"),
            "readiness_band": readiness.get("readiness_band"),
        },

        # G: every candidate (eligible or not), for full provenance.
        "candidates": finalized_candidates,
        "ranked_eligible": finalized_ranked,

        "selected_session_family": winner["session_family"],
        "selection_explanation": (
            f"Selected '{winner['session_family']}' (score {winner['score_total']:.3f}) "
            f"over {len(ranked) - 1} other eligible candidate(s): "
            + "; ".join(_explanation_factors(winner, goal_mode))
        ),

        "data_quality": {
            "selection_confidence": muscle_readiness_result.get("selection_confidence"),
            "tonal_freshness_hours": muscle_readiness_result.get("latest_workout_age_hours"),
            "eligible_candidate_count": len(eligible),
            "ineligible_candidate_count": len(candidates) - len(eligible),
        },
    }
