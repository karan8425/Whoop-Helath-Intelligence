"""Program Intelligence V2 Phase 2 - program progress / stimulus
requirement.

Reuses training_intelligence.stimulus.ledger's own terminology verbatim
(direct_sets, secondary_set_equivalents, total_stimulus_sets) - never
"scientifically measured effective sets." PROGRAM INTENT (what the
active program's slots call for over one rotation) is kept explicitly
separate from ACTUAL TRAINING (what the stimulus ledger says was
really performed) - the two are never conflated into one number.
"""
from __future__ import annotations

from training_intelligence.stimulus.taxonomy import CANONICAL_MUSCLES, to_canonical

PROGRAM_PROGRESS_VERSION = 1

# PRODUCT POLICY: tolerance band around the "expected phase fraction"
# (how far through one rotation cycle the schedule says we should be)
# before a muscle is called overdue/ahead rather than on_track.
ON_TRACK_TOLERANCE = 0.20


def _planned_sets_per_muscle(sessions_with_slots):
    """sessions_with_slots: [(session_dict, [slot_dict, ...]), ...] for
    ONE full rotation cycle. Planned stimulus per muscle = sum of each
    required slot's midpoint set target (set_min+set_max)/2 for slots
    where that muscle is primary - a deterministic, disclosed estimate,
    not a claim about physiological requirement."""
    planned = {m: 0.0 for m in CANONICAL_MUSCLES}
    for _session, slots in sessions_with_slots:
        for slot in slots:
            if not slot.get("required", True):
                continue
            muscle = slot["primary_muscle"]
            if muscle not in planned:
                continue
            midpoint = (slot["set_min"] + slot["set_max"]) / 2.0
            planned[muscle] += midpoint
    return planned


def build_program_progress(program, sessions_with_slots, muscle_stimulus_ledger):
    """`muscle_stimulus_ledger`: training_intelligence.stimulus.ledger.
    build_muscle_stimulus_ledger()'s own output (ACTUAL training,
    reused unchanged - never recomputed here). `sessions_with_slots`:
    one full rotation cycle's (session, slots) pairs (PROGRAM INTENT).

    cycle_days is the window ACTUAL training is compared against - a
    deterministic estimate (rotation length / days_per_week * 7), not a
    physiological claim, and labeled as such."""
    cycle_days = round(len(sessions_with_slots) / max(program.days_per_week, 1) * 7, 1)
    planned = _planned_sets_per_muscle(sessions_with_slots)
    ledger_muscles = muscle_stimulus_ledger.get("muscles", {})

    muscles_out = {}
    for muscle in CANONICAL_MUSCLES:
        planned_sets = round(planned[muscle], 2)
        actual = ledger_muscles.get(muscle, {})
        completed_sets = actual.get("total_stimulus_sets", 0.0)
        if planned_sets <= 0:
            # This program never targets this muscle - not "overdue",
            # simply not applicable this cycle.
            muscles_out[muscle] = {
                "planned_sets": 0.0, "completed_sets": completed_sets, "remaining_sets": 0.0,
                "completion_ratio": None, "status": "not_applicable",
                "direct_sets": actual.get("direct_sets", 0.0),
                "secondary_set_equivalents": actual.get("secondary_set_equivalents", 0.0),
            }
            continue
        remaining = max(0.0, planned_sets - completed_sets)
        ratio = round(completed_sets / planned_sets, 3) if planned_sets else None
        muscles_out[muscle] = {
            "planned_sets": planned_sets, "completed_sets": completed_sets,
            "remaining_sets": round(remaining, 2), "completion_ratio": ratio,
            "status": None,  # filled in below once we know the expected phase fraction
            "direct_sets": actual.get("direct_sets", 0.0),
            "secondary_set_equivalents": actual.get("secondary_set_equivalents", 0.0),
        }

    return {
        "program_progress_version": PROGRAM_PROGRESS_VERSION,
        "cycle_days": cycle_days,
        "ledger_window_days": muscle_stimulus_ledger.get("window_days"),
        "muscles": muscles_out,
    }


def classify_muscle_status(progress, expected_phase_fraction):
    """Applies the on-track/overdue/ahead label using the schedule's own
    expected_phase_fraction (how far through the rotation the nominal
    sequence position implies we should be) - separated from
    build_program_progress() so the classification's one free
    parameter is explicit and testable on its own."""
    for muscle, entry in progress["muscles"].items():
        if entry["status"] == "not_applicable":
            continue
        ratio = entry["completion_ratio"]
        if ratio is None:
            entry["status"] = "unknown"
            continue
        if ratio < expected_phase_fraction - ON_TRACK_TOLERANCE:
            entry["status"] = "overdue"
        elif ratio > expected_phase_fraction + ON_TRACK_TOLERANCE:
            entry["status"] = "ahead"
        else:
            entry["status"] = "on_track"
    return progress


def session_missing_stimulus_score(session_slots, progress):
    """Phase 3.C (PROGRAM NEED): how much a candidate session would
    advance currently-missing program stimulus - sum of `remaining_sets`
    over the session's own required-slot primary muscles. Bounded,
    deterministic, and reused identically by every candidate so ranking
    is apples-to-apples."""
    muscles = {slot["primary_muscle"] for slot in session_slots if slot.get("required", True)}
    return round(sum(progress["muscles"].get(m, {}).get("remaining_sets", 0.0) for m in muscles), 2)
