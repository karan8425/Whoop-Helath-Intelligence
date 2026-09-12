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

TKI-4.1 addendum (calibration): after C/D scoring produces each eligible
candidate's original v1 `score_total` (scoring.py, UNCHANGED), a separate
soft calibration layer (training_intelligence.selection.monotony /
calibration_policy) computes a bounded `monotony_adjustment` per eligible,
non-Rest candidate - a consecutive-repeat dampener (over the selector's
own optional `recent_selections` history), a rolling real-history
representation adjustment, and a starvation-protection bonus. Ranking and
final selection use `score_total_calibrated = clamp01(score_total +
monotony_adjustment)`; the original `score_total`/`score_components` are
preserved unchanged in the output for full provenance. See
TRAINING_INTELLIGENCE_TKI41_REPORT.md.
"""

from __future__ import annotations

from datetime import datetime

from goals import get_active_goal
from integrations.tonal.muscle_readiness import calculate_muscle_readiness
from integrations.tonal.training_dose import load_session_history, _load_muscle_set_rows
from integrations.tonal.training_priority import SESSION_TEMPLATES
from integrations.tonal.workout_prescription import _latest_readiness
from training_intelligence.dose.goal_policy import GOAL_POLICY_VERSION, resolve_goal_mode
from training_intelligence.selection.calibration_policy import CALIBRATION_POLICY_VERSION
from training_intelligence.selection.candidates import generate_candidates
from training_intelligence.selection.monotony import compute_monotony_adjustment
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

SELECTION_MODEL_VERSION = 2
REST_FAMILY_NAME = "Rest / Active Recovery"


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


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

    rest_score = round(min(1.0, score), 4)
    return {
        "session_family": REST_FAMILY_NAME,
        "primary_muscles": [],
        "eligible": True,
        "exclusion_reasons": [],
        "eligible_muscles": [],
        "excluded_muscles": {},
        "dose": None,
        "score_total": rest_score,
        "score_components": {
            "stimulus_debt": None, "local_readiness": None, "days_since_trained": None,
            "program_balance": None, "systemic_capacity": None, "goal_relevance": None,
            "performance": None, "dose_feasibility": None, "schedule_fit": None,
        },
        "scoring_weights_used": None,
        "rest_bonus_reasons": reasons,
        # Rest is exempt from the TKI-4.1 monotony/rolling-representation/
        # starvation layer (calibration_policy.py's module docstring) -
        # repeating Rest is not the pathology this milestone addresses.
        "monotony": None,
        "score_total_calibrated": rest_score,
    }


def _sort_key(candidate):
    """Section 16 (TKI-4) + TKI-4.1's calibration layer: rank first by the
    CALIBRATED score (original v1 score_total plus the bounded, soft
    monotony/rolling-representation/starvation adjustment), then fall
    back to the original, unchanged tie-break order - larger unresolved
    stimulus debt, then better local readiness, then stronger program-
    balance need, then longer justified days-since-trained, then better
    dose feasibility, then alphabetical as the absolute last resort."""
    components = candidate.get("score_components") or {}
    return (
        -candidate.get("score_total_calibrated", candidate["score_total"]),
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
    monotony = candidate.get("monotony")
    if monotony and monotony["monotony_adjustment_total"] != 0:
        factors.append(
            f"calibration (TKI-4.1): consecutive_repeat_streak={monotony['consecutive_repeat_streak']}, "
            f"repeat_penalty=-{monotony['repeat_penalty']:.3f}, "
            f"rolling_representation_adjustment={monotony['rolling_representation_adjustment']:+.3f}, "
            f"starvation_bonus=+{monotony['starvation_bonus']:.3f} "
            f"-> net {monotony['monotony_adjustment_total']:+.3f} "
            f"(score_total {candidate['score_total']:.3f} -> {candidate['score_total_calibrated']:.3f})"
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
        # TKI-4.1 addendum - the original v1 score_total/score_components
        # above are UNCHANGED; these are the calibrated fields ranking
        # and selection actually use (identical to score_total when no
        # monotony/rolling-representation/starvation adjustment applied,
        # e.g. Rest, or an ineligible candidate).
        "monotony": candidate.get("monotony"),
        "score_total_calibrated": candidate.get("score_total_calibrated", candidate.get("score_total")),
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
    recent_selections=None,
) -> dict:
    """The full TKI-4(.1) shadow selection object as of a given moment.
    Read-only; not reachable from any live request path.

    `recent_selections` (TKI-4.1, optional): an iterable of
    (decision_datetime, session_family) pairs describing what this same
    shadow selector would have selected on preceding decisions, most-
    recent-first or in any order (this function sorts/filters them).
    Defaults to none - a single ad-hoc call (e.g. the admin diagnostic
    endpoint) gets zero consecutive-repeat penalty, which is the correct
    cold-start behavior (no fake certainty about a decision history that
    was never supplied). A caller iterating day-by-day (e.g. a backtest)
    can thread its own growing selection history forward at no extra
    query cost - see the 90-day backtest in
    TRAINING_INTELLIGENCE_TKI41_REPORT.md."""

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

    # Program-balance input (unchanged) + TKI-4.1's wider rolling-
    # representation windows - both from the SAME already-loaded
    # ledger_rows (one query total, no N+1 - section 17).
    family_windows = session_family_windows(ledger_rows, as_of, (7, 14, 30))
    families_14d = family_windows[14]
    family_session_counts_14d = {family: entry["sessions"] for family, entry in families_14d.items()}
    for family in SESSION_TEMPLATES:
        family_session_counts_14d.setdefault(family, 0)

    # ------------------------------------------------------------------
    # C/D: scoring (goal-weighted). scoring.py itself is UNCHANGED by
    # TKI-4.1 - score_total/score_components below are the original v1
    # values, preserved as-is in the final output for provenance.
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
    # TKI-4.1: soft calibration layer - consecutive-repeat monotony
    # dampener, real-history rolling representation, starvation
    # protection. Computed only for eligible, non-Rest candidates; never
    # touches eligibility (candidates.py, unmodified) or the original
    # score_total (scoring.py, unmodified).
    # ------------------------------------------------------------------
    for candidate in eligible:
        monotony = compute_monotony_adjustment(
            candidate, as_of, goal_mode, recent_selections, family_windows, len(eligible),
        )
        candidate["monotony"] = monotony
        candidate["score_total_calibrated"] = round(
            _clamp01(candidate["score_total"] + monotony["monotony_adjustment_total"]), 4
        )

    # ------------------------------------------------------------------
    # E/F: tie-break + final ranking (on the calibrated score).
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
        "calibration_policy_version": CALIBRATION_POLICY_VERSION,
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
            f"Selected '{winner['session_family']}' "
            f"(calibrated score {winner.get('score_total_calibrated', winner['score_total']):.3f}, "
            f"v1 score {winner['score_total']:.3f}) "
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
