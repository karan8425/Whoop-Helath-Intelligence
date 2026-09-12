"""TKI-5.1: feasible exercise-count range + dose-absorption waterfall -
SHADOW MODE ONLY.

Root cause this addresses (see TRAINING_INTELLIGENCE_TKI51_REPORT.md
section D for the full decomposition): TKI-5 v1 computed a PREFERRED
exercise count from dose/goal alone, asked integrations.tonal.
workout_prescription._select_movements() to fill that many slots, and
recorded a "fallback" whenever fewer eligible movements existed than
requested - conflating a normal, adaptive, still-valid smaller session
with a genuine data/safety shortfall. It also never noticed that B3's
own integrations.tonal.progressive_overload.prescribe() can reduce an
already-allocated movement's set count below its integrations.tonal.
workout_prescription._set_allocation() share (its REDUCE state, under
DECLINING performance + low/moderate readiness) WITHOUT that reduction
ever being redistributed to another, safely-absorbing movement or even
reported - silently under-delivering the justified TKI-3 dose (the
Sep-12 7-of-9 finding).

Neither _select_movements()/_movement_eligibility()/_candidate_score()
nor progressive_overload.prescribe() is modified here - both are called
exactly as before, verbatim, possibly more than once per movement (they
are pure, cheap, no-DB-access functions - calling prescribe() again with
a different set_count is not "modifying" it, it is USING it, the same
way a caller trying two different set counts would).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from integrations.tonal import progressive_overload as b3_progressive_overload
from integrations.tonal.workout_prescription import _set_allocation, _family_for_movement
from training_intelligence.stimulus.ledger import _is_working_set, _session_weight, _require_aware
from training_intelligence.stimulus.mapping import classify_muscle_groups
from training_intelligence.stimulus.session_family import classify_workout_family

from training_intelligence.prescription.prescription_policy import (
    MIN_EXERCISES,
    MAX_EXERCISES,
    DEFAULT_SETS_PER_EXERCISE,
    EXERCISE_COUNT_DELTA_BY_GOAL_MODE,
    HISTORICAL_STRUCTURE_LOOKBACK_DAYS,
    MIN_COMPARABLE_SESSIONS_FOR_HISTORICAL_STRUCTURE,
    PERSONAL_TOLERATED_SETS_MARGIN,
    PER_MOVEMENT_ABSORPTION_CAP_FALLBACK,
    MAX_REALLOCATION_SHARE_PER_MOVEMENT,
    MIN_ALLOCATED_SETS_TO_BE_REALLOCATION_TARGET,
    NON_ABSORBING_PROGRESSION_STATES,
)


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def _median(values):
    values = sorted(values)
    n = len(values)
    if n == 0:
        return None
    mid = n // 2
    if n % 2 == 1:
        return float(values[mid])
    return (values[mid - 1] + values[mid]) / 2.0


# ----------------------------------------------------------------------
# Section 4/5: personal historical session structure.
# ----------------------------------------------------------------------

def historical_session_structure(ledger_rows, as_of: datetime, family: str,
                                  lookback_days: int = HISTORICAL_STRUCTURE_LOOKBACK_DAYS) -> dict:
    """Per-REAL-SESSION (not aggregated-across-the-window) exercise-count
    distribution for `family`, from the already-loaded `ledger_rows` (no
    new per-candidate query - one dataset, grouped here, section 24).
    Every row with begin_time > as_of or older than the lookback is
    defensively dropped, even though load_rows() already bounds this in
    SQL - the same as_of-safety discipline every other TKI shadow module
    in this package applies to caller-injected data.

    Returns {"sample_count", "median_exercises_per_session",
    "median_sets_per_exercise", "source"} - "source" is either
    "session_family_personal_history" (enough real comparable sessions
    existed) or "insufficient_personal_history" (section 5's fallback
    hierarchy collapses to the goal/dose default from here)."""

    as_of = _require_aware(as_of)
    cutoff = as_of - timedelta(days=lookback_days)

    by_workout = defaultdict(list)
    for row in ledger_rows or []:
        begin_time = row.get("begin_time")
        if begin_time is None or begin_time > as_of or begin_time < cutoff:
            continue
        if not _is_working_set(row):
            continue
        if _session_weight(row) is None:
            continue
        by_workout[row.get("activity_id")].append(row)

    exercises_per_session = []
    sets_per_exercise_samples = []
    for workout_rows in by_workout.values():
        trained_primary = set()
        for row in workout_rows:
            classification = classify_muscle_groups(row.get("muscle_groups"))
            if classification.is_mapped:
                trained_primary.add(classification.primary)
        if classify_workout_family(frozenset(trained_primary)) != family:
            continue
        movements = defaultdict(int)
        for row in workout_rows:
            movements[row.get("movement_id")] += 1
        exercises_per_session.append(len(movements))
        sets_per_exercise_samples.extend(movements.values())

    sample_count = len(exercises_per_session)
    if sample_count < MIN_COMPARABLE_SESSIONS_FOR_HISTORICAL_STRUCTURE:
        return {
            "sample_count": sample_count,
            "median_exercises_per_session": None,
            "median_sets_per_exercise": None,
            "source": "insufficient_personal_history",
        }
    return {
        "sample_count": sample_count,
        "median_exercises_per_session": _median(exercises_per_session),
        "median_sets_per_exercise": _median(sets_per_exercise_samples),
        "source": "session_family_personal_history",
    }


# ----------------------------------------------------------------------
# Section 3/7: feasible exercise-count range - an OUTCOME of the usable
# candidate pool, not a preference computed first.
# ----------------------------------------------------------------------

def feasible_exercise_count(eligible_pool_size: int, dose_working_sets: float, dose_upper_bound: float,
                             goal_mode, historical_structure: dict) -> dict:
    """Returns {"min", "max", "preferred", "confidence", "limiting_factors",
    "preference_source"}. `max` is bounded by the eligible candidate pool
    FIRST - a preference is never allowed to exceed what genuinely
    exists, closing the section-2 root cause (a preference computed
    before the pool was known, then reported as a "fallback" when the
    pool couldn't satisfy it)."""

    limiting_factors = []

    pool_max = min(MAX_EXERCISES, eligible_pool_size)
    feasible_min = min(MIN_EXERCISES, pool_max) if pool_max > 0 else 0

    # A movement can never safely absorb fewer than 2 sets in this
    # architecture (_set_allocation's own floor) - requesting more slots
    # than max_sets // 2 can support is mechanically infeasible.
    if dose_upper_bound:
        sets_bound = max(1, int(dose_upper_bound) // 2)
        pool_max = min(pool_max, sets_bound)
        feasible_min = min(feasible_min, pool_max)

    # Section 4/5: personal history first, else the goal/dose default.
    if historical_structure.get("source") == "session_family_personal_history":
        preference_source = "session_family_personal_history"
        raw_preferred = historical_structure["median_exercises_per_session"]
        preferred = int(round(raw_preferred))
    else:
        preference_source = "goal_policy_dose_default"
        if not dose_working_sets or dose_working_sets <= 0:
            preferred = 0
        else:
            base = round(dose_working_sets / DEFAULT_SETS_PER_EXERCISE)
            preferred = base + EXERCISE_COUNT_DELTA_BY_GOAL_MODE.get(goal_mode, 0)

    preferred = int(_clamp(preferred, MIN_EXERCISES, MAX_EXERCISES)) if preferred else 0
    chosen = min(preferred, pool_max) if pool_max > 0 else 0

    # Only report a factor as LIMITING if it actually reduced the chosen
    # count below the raw preference - never merely because the pool
    # happens to be smaller than the absolute MAX_EXERCISES ceiling.
    if preferred > pool_max:
        pool_only_cap = min(MAX_EXERCISES, eligible_pool_size)
        if pool_only_cap < preferred:
            limiting_factors.append(f"eligible movement pool size ({eligible_pool_size})")
        if dose_upper_bound and max(1, int(dose_upper_bound) // 2) < preferred:
            limiting_factors.append(f"TKI-3 feasible upper bound ({dose_upper_bound} sets)")

    if pool_max <= 0:
        confidence = "LOW"
    elif historical_structure.get("source") == "session_family_personal_history" and \
            historical_structure["sample_count"] >= MIN_COMPARABLE_SESSIONS_FOR_HISTORICAL_STRUCTURE * 2:
        confidence = "HIGH"
    elif historical_structure.get("source") == "session_family_personal_history":
        confidence = "MEDIUM"
    else:
        confidence = "MEDIUM" if pool_max >= MIN_EXERCISES else "LOW"

    return {
        "min": max(0, min(feasible_min, chosen)),
        "max": pool_max,
        "preferred": chosen,
        "confidence": confidence,
        "limiting_factors": limiting_factors,
        "preference_source": preference_source,
    }


# ----------------------------------------------------------------------
# Section 8/9/10/11: dose-absorption waterfall.
# ----------------------------------------------------------------------

def _personal_absorption_cap(profile):
    """Section 10, tier A/D. Tier A: this movement's own historical
    recent_sets_per_session plus a margin, when BACKED BY ENOUGH
    comparable sessions (>= MIN_COMPARABLE_SESSIONS_FOR_HISTORICAL_
    STRUCTURE) to trust it as real personal tolerance, not one lucky
    session. Tier D (labeled PRODUCT POLICY fallback): PER_MOVEMENT_
    ABSORPTION_CAP_FALLBACK, identical to B3's own _set_allocation()
    remainder-loop ceiling - not a new number invented here."""
    performance = profile.get("performance") or {}
    history = profile.get("history") or {}
    recent = performance.get("recent_sets_per_session")
    sessions = history.get("sessions_in_lookback") or 0
    if recent and sessions >= MIN_COMPARABLE_SESSIONS_FOR_HISTORICAL_STRUCTURE:
        return int(round(recent)) + PERSONAL_TOLERATED_SETS_MARGIN
    return PER_MOVEMENT_ABSORPTION_CAP_FALLBACK


def dose_absorption_waterfall(selected, roles, readiness_band, target_sets, max_sets) -> dict:
    """Stage 1: B3's own _set_allocation() (UNMODIFIED) produces an
    initial, compound-biased, dose-summing split, then each movement's
    own personal absorption cap (section 10/22) is applied to that split
    BEFORE anything is prescribed - a sparse pool (e.g. a single eligible
    movement) must not be handed the entire dose merely because
    _set_allocation() has no per-movement ceiling of its own when
    exercise_count is small; an explicit shortfall is preferred over
    unsafe concentration.
    Stage 2: B3's own progressive_overload.prescribe() (UNMODIFIED) is
    consulted per (capped) movement - this is where a REDUCE state can
    additionally give back sets (the Sep-12 root cause).
    Stage 3 (NEW): any resulting shortfall (from either the cap or a
    REDUCE state) is offered to movements whose state is NOT REDUCE/
    REBUILD, preferring 'primary' role movements, bounded by each
    movement's own absorption cap and by MAX_REALLOCATION_SHARE_PER_
    MOVEMENT - never by simply re-querying _set_allocation() with a
    larger target (that would exceed TKI-3's dose). Only if nothing can
    safely absorb the remainder is an explicit, disclosed dose_shortfall
    reported."""

    # Defensive: target_sets should never exceed max_sets given TKI-3's
    # own upstream invariant (goal-adjusted working_sets <= feasible
    # upper bound), but this is enforced here directly rather than
    # merely assumed - a redistribution loop must never be allowed to
    # chase a target above what max_sets permits.
    target_sets = min(target_sets, max_sets) if max_sets else target_sets

    initial = _set_allocation(selected, readiness_band, target_sets=target_sets, max_sets=max_sets)

    per_movement = []
    for profile, role, initial_sets in zip(selected, roles, initial):
        cap = _personal_absorption_cap(profile)
        # Section 22: clip BEFORE prescribing, not after - a sparse pool
        # (few movements sharing _set_allocation()'s even split) must
        # never dump an implausibly large count onto one movement just
        # because it has no other movement to share the dose with.
        capped_sets = min(initial_sets, cap)
        result = b3_progressive_overload.prescribe(profile, readiness_band, capped_sets)
        per_movement.append({
            "profile": profile,
            "role": role,
            "initial_sets": capped_sets,
            "sets": result["sets"],
            "progression_state": result["progression_state"],
            "cap": cap,
            "prescribe_result": result,
        })

    allocated = sum(m["sets"] for m in per_movement)
    shortfall = max(0, target_sets - allocated)
    max_per_movement_reallocation = max(1, round(target_sets * MAX_REALLOCATION_SHARE_PER_MOVEMENT))

    if shortfall > 0:
        # Primary-role, absorbing-state movements first (section 12/13 -
        # preserve session purpose; never prefer an accessory over an
        # under-covered primary muscle).
        candidates = [
            m for m in per_movement
            if m["progression_state"] not in NON_ABSORBING_PROGRESSION_STATES
            and m["sets"] >= MIN_ALLOCATED_SETS_TO_BE_REALLOCATION_TARGET
        ]
        candidates.sort(key=lambda m: (m["role"] != "primary", m["profile"].get("movement_id")))

        cursor = 0
        guard = 0
        while shortfall > 0 and candidates and guard < 200:
            guard += 1
            m = candidates[cursor % len(candidates)]
            reallocated_so_far = m["sets"] - m["initial_sets"]
            at_personal_cap = m["sets"] >= m["cap"]
            at_share_cap = reallocated_so_far >= max_per_movement_reallocation
            if not at_personal_cap and not at_share_cap:
                new_set_count = m["sets"] + 1
                result = b3_progressive_overload.prescribe(m["profile"], readiness_band, new_set_count)
                # A movement whose state is legitimately non-absorbing
                # (e.g. it flips to REDUCE at the new count - unlikely,
                # since prescribe()'s trend/readiness inputs do not
                # depend on set_count, but never assumed) is dropped
                # from further consideration rather than trusted blindly.
                if result["progression_state"] in NON_ABSORBING_PROGRESSION_STATES or result["sets"] <= m["sets"]:
                    candidates.remove(m)
                    continue
                m["sets"] = result["sets"]
                m["prescribe_result"] = result
                shortfall -= 1
            else:
                candidates.remove(m)
                continue
            cursor += 1

    allocated = sum(m["sets"] for m in per_movement)
    shortfall = max(0, target_sets - allocated)
    shortfall_reason = None
    if shortfall > 0:
        reduce_count = sum(1 for m in per_movement if m["progression_state"] == "REDUCE")
        shortfall_reason = (
            f"{shortfall} of {target_sets} justified working sets could not be safely absorbed: "
            f"{reduce_count} selected movement(s) are in a REDUCE state (declining performance + "
            f"reduced readiness) and no remaining movement had personal-tolerance headroom to absorb "
            f"the difference without exceeding its own safe cap."
        )

    return {
        "dose_target": target_sets,
        "dose_allocated": allocated,
        "dose_shortfall": shortfall,
        "shortfall_reason": shortfall_reason,
        "per_movement": per_movement,
    }
