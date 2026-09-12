"""Assembles the deterministic shadow training-state object.

SHADOW MODE ONLY. This module reads the weekly stimulus ledger, session
families, local muscle readiness, active goal, and WHOOP recovery - and
returns them together for diagnostic/inspection purposes. It does not
rank sessions (see TRAINING_INTELLIGENCE_KNOWLEDGE_BASE_V1.md and the
TKI-1/TKI-2 assignment section 15), and nothing here is imported by any
production recommendation path (today's B3/B4 workout selection,
TrainingDetail, dose, or progression are untouched).

Readiness, goal, and WHOOP reads are strictly consuming existing,
already-shipped calculations:

    - integrations.tonal.muscle_readiness.calculate_muscle_readiness
    - goals.get_active_goal
    - integrations.tonal.workout_prescription._latest_readiness

None of their logic is reimplemented or modified here.
"""

from __future__ import annotations

from datetime import datetime

from goals import get_active_goal
from integrations.tonal.muscle_readiness import calculate_muscle_readiness
from integrations.tonal.workout_prescription import _latest_readiness
from training_intelligence.knowledge.loader import KNOWLEDGE_VERSION
from training_intelligence.stimulus.ledger import build_ledger_windows, load_rows
from training_intelligence.stimulus.mapping import movement_mapping_report
from training_intelligence.stimulus.policy import (
    STIMULUS_POLICY_VERSION,
    EXERCISE_MAPPING_VERSION,
    LEDGER_WINDOWS_DAYS,
    HYPERTROPHY_REFERENCE_SETS_PER_WEEK,
    HYPERTROPHY_REFERENCE_LABEL,
)
from training_intelligence.stimulus.session_family import session_family_windows
from training_intelligence.stimulus.taxonomy import CANONICAL_MUSCLES, to_canonical

# Section 5/12: the only training_objective this milestone actually
# asserts is the one the approved spec states explicitly for a lean cut.
# Other phases are read and surfaced, but their objective is intentionally
# left unset rather than inventing an unstated policy mapping.
_PHASE_TRAINING_OBJECTIVE = {
    "lean_cut": "preserve_or_gain_lean_mass",
}

# Local readiness states -> the simplified fresh/recovering/fatigued/
# unknown vocabulary the assignment's shadow-state contract (section 14)
# asks for. The underlying integrations.tonal.muscle_readiness engine
# itself is untouched; this is a read-only display rollup.
_READINESS_STATE_ROLLUP = {
    "FRESH": "fresh",
    "READY": "fresh",
    "RECOVERING": "recovering",
    "FATIGUED": "fatigued",
    "SUPPRESSED": "fatigued",
}


def _rollup_readiness(readiness_state: str) -> str:
    return _READINESS_STATE_ROLLUP.get(readiness_state, "unknown")


def _goal_context(as_of: datetime) -> dict:
    goal = get_active_goal(as_of=as_of) or {}
    phase = goal.get("phase")
    return {
        "phase": phase,
        "goal_type": goal.get("goal_type"),
        "training_objective": _PHASE_TRAINING_OBJECTIVE.get(phase),
    }


def _whoop_context(as_of: datetime) -> dict:
    readiness = _latest_readiness(now=as_of)
    return {
        "recovery_score": readiness.get("recovery_score"),
        # Reused verbatim from the existing production banding
        # (integrations.tonal.workout_prescription._latest_readiness).
        # NOTE: this differs from the green/yellow/red thresholds stated
        # in TRAINING_INTELLIGENCE_KNOWLEDGE_BASE_V1.md section 8 - see
        # the data-quality note in TRAINING_INTELLIGENCE_TKI12_REPORT.md.
        # Not reconciled here: this milestone must not create a new WHOOP
        # calculation.
        "band": readiness.get("readiness_band"),
        "hrv_rmssd_milli": readiness.get("hrv_rmssd_milli"),
        "resting_heart_rate": readiness.get("resting_heart_rate"),
        "sleep_duration_hours": readiness.get("sleep_duration_hours"),
        "recent_strain": None,  # not sourced by _latest_readiness; no new WHOOP query added.
        "metric_date": readiness.get("metric_date"),
    }


def _local_readiness(as_of: datetime) -> dict:
    result = calculate_muscle_readiness(now=as_of)
    by_existing_muscle = {entry["muscle"]: entry for entry in result.get("muscles", [])}
    canonical = {}
    for existing_muscle, entry in by_existing_muscle.items():
        canonical_muscle = to_canonical(existing_muscle)
        if canonical_muscle is None:
            continue
        canonical[canonical_muscle] = {
            "readiness_state_raw": entry["readiness_state"],
            "readiness": _rollup_readiness(entry["readiness_state"]),
            "readiness_score": entry.get("readiness_score"),
            "hours_since_primary_exposure": entry.get("hours_since_primary_exposure"),
        }
    return {
        "as_of_confidence": result.get("selection_confidence"),
        "latest_tonal_workout_at": result.get("latest_tonal_workout_at"),
        "muscles": canonical,
    }


def build_shadow_training_state(as_of: datetime, rows=None) -> dict:
    """The full TKI-1/TKI-2 shadow object (assignment section 14).
    `rows` may be pre-fetched Tonal set rows for deterministic testing;
    otherwise this issues its own bounded, as-of-safe queries."""

    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be a timezone-aware datetime")

    if rows is None:
        rows = load_rows(as_of, max(LEDGER_WINDOWS_DAYS))

    ledger_windows = build_ledger_windows(as_of, LEDGER_WINDOWS_DAYS, rows=rows)
    family_windows = session_family_windows(rows, as_of, LEDGER_WINDOWS_DAYS)
    local_readiness = _local_readiness(as_of)

    muscles = {}
    for muscle in CANONICAL_MUSCLES:
        muscles[muscle] = {
            "direct_sets_7d": ledger_windows[7]["muscles"][muscle]["direct_sets"],
            "secondary_sets_7d": ledger_windows[7]["muscles"][muscle]["secondary_set_equivalents"],
            "stimulus_sets_7d": ledger_windows[7]["muscles"][muscle]["total_stimulus_sets"],
            "stimulus_sets_14d": ledger_windows[14]["muscles"][muscle]["total_stimulus_sets"],
            "stimulus_sets_30d": ledger_windows[30]["muscles"][muscle]["total_stimulus_sets"],
            "sessions_7d": ledger_windows[7]["muscles"][muscle]["sessions"],
            "days_since_trained": ledger_windows[7]["muscles"][muscle]["days_since_trained"],
            "exercise_count_7d": ledger_windows[7]["muscles"][muscle]["exercise_count"],
            "working_sets_7d": ledger_windows[7]["muscles"][muscle]["working_sets"],
            "recent_volume_lb_7d": ledger_windows[7]["muscles"][muscle]["recent_volume_lb"],
            "readiness": local_readiness["muscles"].get(muscle, {}).get("readiness", "unknown"),
            "readiness_state_raw": local_readiness["muscles"].get(muscle, {}).get("readiness_state_raw"),
        }

    unmapped_working_sets_7d = ledger_windows[7]["data_quality"]["unmapped_working_sets"]
    considered_7d = ledger_windows[7]["data_quality"]["total_working_sets_considered"]
    mapped_pct_7d = (
        round(100.0 * considered_7d / (considered_7d + unmapped_working_sets_7d), 1)
        if (considered_7d + unmapped_working_sets_7d) > 0
        else None
    )

    return {
        "status": "ok",
        "mode": "shadow",
        "knowledge_version": KNOWLEDGE_VERSION,
        "stimulus_policy_version": STIMULUS_POLICY_VERSION,
        "exercise_mapping_version": EXERCISE_MAPPING_VERSION,
        "as_of": as_of.isoformat(),

        "goal": _goal_context(as_of),
        "systemic_readiness": _whoop_context(as_of),
        "local_readiness_context": {
            "as_of_confidence": local_readiness["as_of_confidence"],
            "latest_tonal_workout_at": local_readiness["latest_tonal_workout_at"],
        },

        "muscles": muscles,

        "session_history": {
            "families_7d": family_windows[7],
            "families_14d": family_windows[14],
            "families_30d": family_windows[30],
        },

        "hypertrophy_reference": {
            "sets_per_week": HYPERTROPHY_REFERENCE_SETS_PER_WEEK,
            "label": HYPERTROPHY_REFERENCE_LABEL,
        },

        "data_quality": {
            "working_sets_considered_7d": considered_7d,
            "unmapped_working_sets_7d": unmapped_working_sets_7d,
            "mapped_working_sets_pct_7d": mapped_pct_7d,
        },

        "ranking": None,  # deliberately not computed - see assignment section 15.
    }


def data_quality_report(as_of: datetime, rows=None) -> dict:
    """Movement-level mapping coverage (distinct exercises, not weighted
    by set count) - used by TRAINING_INTELLIGENCE_TKI12_REPORT.md."""

    if rows is None:
        rows = load_rows(as_of, max(LEDGER_WINDOWS_DAYS))
    seen = {}
    for row in rows:
        movement_id = row.get("movement_id")
        if movement_id is not None and movement_id not in seen:
            seen[movement_id] = {
                "movement_id": movement_id,
                "name": row.get("movement_name"),
                "muscle_groups": row.get("muscle_groups"),
            }
    return movement_mapping_report(seen.values())
