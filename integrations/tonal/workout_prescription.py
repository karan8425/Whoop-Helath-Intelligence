from datetime import datetime, timezone

from db import get_conn

from integrations.tonal.movement_performance import (
    build_movement_performance_profiles,
)

from integrations.tonal.training_priority import (
    build_training_priority,
)


# ============================================================
# TONAL HARDWARE LIMITS
# ============================================================

TONAL_MAX_PER_ARM_LB = 100.0
TONAL_MAX_COMBINED_LB = 200.0


# ============================================================
# SESSION VOLUME TARGETS
#
# WHOOP determines today's fatigue budget.
# The movement-performance engine determines whether individual
# exercises have earned progressive overload.
# ============================================================

SESSION_RULES = {

    "high": {
        "min_sets": 14,
        "target_sets": 16,
        "max_sets": 18,
        "allow_progression": True,
        "allow_smart_weight": True,
        "target_rir": "1-2",
    },

    "good": {
        "min_sets": 13,
        "target_sets": 15,
        "max_sets": 17,
        "allow_progression": True,
        "allow_smart_weight": False,
        "target_rir": "2",
    },

    "moderate": {
        "min_sets": 11,
        "target_sets": 13,
        "max_sets": 15,
        "allow_progression": False,
        "allow_smart_weight": False,
        "target_rir": "2-3",
    },

    "low": {
        "min_sets": 6,
        "target_sets": 8,
        "max_sets": 10,
        "allow_progression": False,
        "allow_smart_weight": False,
        "target_rir": "3-4",
    },
}


# ============================================================
# EXERCISE FAMILY RULES
# ============================================================

EXERCISE_FAMILIES = {

    "squat": {
        "Goblet Squat",
        "Barbell Front Squat",
        "Squat with Row",
    },

    "split_squat_lunge": {
        "Split Squat",
        "Barbell Front Racked Split Squat",
        "Racked Reverse Lunge",
    },

    "hinge": {
        "Barbell Deadlift",
        "Neutral Grip Deadlift",
        "Barbell RDL",
        "Single-Leg RDL",
        "Pull Through",
        "Barbell Hip Thrust",
    },

    "core_flexion": {
        "Seated Cable Crunch",
        "V-Up",
        "Resisted Leg Raise",
    },

    "core_rotation": {
        "Single-Leg Chop",
        "Rotational Lift",
        "Lateral Bridge with Row",
    },
}


CORE_MOVEMENTS = (
    EXERCISE_FAMILIES["core_flexion"]
    | EXERCISE_FAMILIES["core_rotation"]
)


# ============================================================
# HELPERS
# ============================================================

def _float(value):

    if value is None:
        return None

    return float(value)


def _clamp(
    value,
    minimum,
    maximum,
):

    return max(
        minimum,
        min(
            maximum,
            value,
        ),
    )


def _round_weight(value):

    if value is None:
        return None

    value = _clamp(
        float(value),
        5.0,
        TONAL_MAX_PER_ARM_LB,
    )

    return round(value)


def _family_for_movement(name):

    for (
        family,
        names,
    ) in EXERCISE_FAMILIES.items():

        if name in names:
            return family

    return "other"


def _is_core_movement(profile):

    return (
        profile.get("name")
        in CORE_MOVEMENTS
    )


# ============================================================
# WHOOP READINESS
# ============================================================

def _latest_readiness(now=None):

    with get_conn() as conn:

        with conn.cursor() as cur:

            date_filter = ""
            params = ()
            if now is not None:
                date_filter = "AND metric_date <= %s"
                params = (now.date(),)
            cur.execute(
                f"""
                SELECT
                    metric_date,
                    recovery_score,
                    hrv_rmssd_milli,
                    resting_heart_rate,
                    sleep_duration_hours
                FROM public.whoop_daily_metrics
                WHERE has_recovery = TRUE
                {date_filter}
                ORDER BY metric_date DESC
                LIMIT 1
                """,
                params,
            )

            row = cur.fetchone()

    if not row:

        return {
            "available": False,
            "readiness_band": "unknown",
            "training_category": None,
        }

    recovery = _float(
        row.get(
            "recovery_score"
        )
    )

    if recovery is None:

        band = "unknown"
        category = None

    elif recovery >= 80:

        band = "high"
        category = "Push"

    elif recovery >= 67:

        band = "good"
        category = "Normal"

    elif recovery >= 45:

        band = "moderate"
        category = "Moderate"

    elif recovery >= 25:

        band = "low"
        category = "Active Recovery"

    else:

        band = "very_low"
        category = "Rest"

    return {
        "available":
            recovery is not None,

        "metric_date":
            (
                row.get(
                    "metric_date"
                ).isoformat()
                if row.get(
                    "metric_date"
                )
                else None
            ),

        "recovery_score":
            recovery,

        "hrv_rmssd_milli":
            _float(
                row.get(
                    "hrv_rmssd_milli"
                )
            ),

        "resting_heart_rate":
            _float(
                row.get(
                    "resting_heart_rate"
                )
            ),

        "sleep_duration_hours":
            _float(
                row.get(
                    "sleep_duration_hours"
                )
            ),

        "readiness_band":
            band,

        "training_category":
            category,
    }


# ============================================================
# CANDIDATE SCORING
# ============================================================

def _candidate_score(
    profile,
    primary_focus,
    secondary_focus,
):

    score = 0

    muscles = set(
        profile.get(
            "muscle_groups"
        )
        or []
    )

    history = (
        profile.get(
            "history"
        )
        or {}
    )

    performance = (
        profile.get(
            "performance"
        )
        or {}
    )

    for muscle in primary_focus:

        if muscle in muscles:
            score += 100

    for muscle in secondary_focus:

        if muscle in muscles:
            score += 30

    sessions = (
        history.get(
            "sessions_in_lookback"
        )
        or 0
    )

    if sessions >= 6:
        score += 30

    elif sessions >= 3:
        score += 20

    elif sessions >= 1:
        score += 10

    if performance.get(
        "progression_earned"
    ):
        score += 10

    struggling = performance.get(
        "recent_struggling_score"
    )

    inconsistency = performance.get(
        "recent_inconsistency_score"
    )

    if (
        struggling is not None
        and struggling >= 0.90
    ):
        score -= 20

    if (
        inconsistency is not None
        and inconsistency >= 0.70
    ):
        score -= 20

    if (
        performance.get(
            "status"
        )
        != "usable"
    ):
        score -= 50

    return score


# ============================================================
# MOVEMENT SELECTION
#
# Avoid redundant exercises while ensuring the requested
# muscles receive direct work.
# ============================================================

def _select_movements(
    profiles,
    primary_focus,
    secondary_focus,
):

    candidates = []

    for profile in profiles:

        score = _candidate_score(
            profile,
            primary_focus,
            secondary_focus,
        )

        if score <= 0:
            continue

        candidates.append(
            {
                "score":
                    score,

                "profile":
                    profile,
            }
        )

    candidates.sort(
        key=lambda item:
            item["score"],
        reverse=True,
    )

    selected = []

    used_families = set()

    # --------------------------------------------------------
    # 1. Squat pattern
    # --------------------------------------------------------

    for candidate in candidates:

        profile = candidate[
            "profile"
        ]

        family = _family_for_movement(
            profile.get(
                "name"
            )
        )

        if family != "squat":
            continue

        selected.append(
            profile
        )

        used_families.add(
            family
        )

        break

    # --------------------------------------------------------
    # 2. Hip-hinge pattern
    # --------------------------------------------------------

    for candidate in candidates:

        profile = candidate[
            "profile"
        ]

        if profile in selected:
            continue

        family = _family_for_movement(
            profile.get(
                "name"
            )
        )

        if family != "hinge":
            continue

        selected.append(
            profile
        )

        used_families.add(
            family
        )

        break

    # --------------------------------------------------------
    # 3. Unilateral / lunge pattern
    # --------------------------------------------------------

    for candidate in candidates:

        profile = candidate[
            "profile"
        ]

        if profile in selected:
            continue

        family = _family_for_movement(
            profile.get(
                "name"
            )
        )

        if family != "split_squat_lunge":
            continue

        selected.append(
            profile
        )

        used_families.add(
            family
        )

        break

    # --------------------------------------------------------
    # 4. Direct core
    # --------------------------------------------------------

    core_candidates = [
        item
        for item in candidates
        if _is_core_movement(
            item["profile"]
        )
    ]

    if core_candidates:

        profile = (
            core_candidates[0][
                "profile"
            ]
        )

        if profile not in selected:

            selected.append(
                profile
            )

            used_families.add(
                _family_for_movement(
                    profile.get(
                        "name"
                    )
                )
            )

    # --------------------------------------------------------
    # 5. Second direct core pattern
    # --------------------------------------------------------

    for candidate in core_candidates:

        profile = candidate[
            "profile"
        ]

        if profile in selected:
            continue

        family = _family_for_movement(
            profile.get(
                "name"
            )
        )

        if family in used_families:
            continue

        selected.append(
            profile
        )

        used_families.add(
            family
        )

        break

    # --------------------------------------------------------
    # 6. Optional fifth movement
    # --------------------------------------------------------

    for candidate in candidates:

        if len(selected) >= 5:
            break

        profile = candidate[
            "profile"
        ]

        if profile in selected:
            continue

        family = _family_for_movement(
            profile.get(
                "name"
            )
        )

        if family in used_families:
            continue

        selected.append(
            profile
        )

        used_families.add(
            family
        )

    return selected[:5]


# ============================================================
# SESSION SET ALLOCATION
# ============================================================

def _set_allocation(
    selected,
    readiness_band,
):

    rules = SESSION_RULES[
        readiness_band
    ]

    target_sets = rules[
        "target_sets"
    ]

    exercise_count = len(
        selected
    )

    if exercise_count == 0:
        return []

    base_sets = max(
        2,
        target_sets
        // exercise_count,
    )

    allocations = [
        base_sets
        for _ in selected
    ]

    remaining = (
        target_sets
        - sum(
            allocations
        )
    )

    compound_indices = []

    for index, profile in enumerate(
        selected
    ):

        family = _family_for_movement(
            profile.get(
                "name"
            )
        )

        if family in (
            "squat",
            "hinge",
            "split_squat_lunge",
        ):

            compound_indices.append(
                index
            )

    if not compound_indices:

        compound_indices = list(
            range(
                exercise_count
            )
        )

    cursor = 0

    while remaining > 0:

        index = compound_indices[
            cursor
            % len(
                compound_indices
            )
        ]

        if allocations[index] < 4:

            allocations[index] += 1
            remaining -= 1

        cursor += 1

        if cursor > 100:
            break

    total = sum(
        allocations
    )

    if total > rules[
        "max_sets"
    ]:

        excess = (
            total
            - rules[
                "max_sets"
            ]
        )

        for index in reversed(
            range(
                len(
                    allocations
                )
            )
        ):

            while (
                excess > 0
                and allocations[index] > 2
            ):

                allocations[index] -= 1
                excess -= 1

    return allocations


# ============================================================
# HISTORICAL REP PROFILE
# ============================================================

def _historical_reps_per_set(
    performance,
):

    sets = performance.get(
        "recent_sets_per_session"
    )

    reps = performance.get(
        "recent_reps_per_session"
    )

    if (
        sets is None
        or sets <= 0
        or reps is None
        or reps <= 0
    ):

        return 10

    return (
        reps
        / sets
    )


def _target_reps(
    profile,
    readiness_band,
):

    performance = (
        profile.get(
            "performance"
        )
        or {}
    )

    historical = (
        _historical_reps_per_set(
            performance
        )
    )

    family = _family_for_movement(
        profile.get(
            "name"
        )
    )

    if family in (
        "squat",
        "hinge",
        "split_squat_lunge",
    ):

        minimum = 6
        maximum = 12

    else:

        minimum = 8
        maximum = 15

    reps = int(
        round(
            historical
        )
    )

    reps = _clamp(
        reps,
        minimum,
        maximum,
    )

    if readiness_band == "low":

        reps -= 2

    return int(
        _clamp(
            reps,
            minimum,
            maximum,
        )
    )


# ============================================================
# MOVEMENT-SPECIFIC LOAD PRESCRIPTION
#
# Moderate readiness should not automatically reduce every
# movement's load. If historical execution is stable, preserve
# the meaningful resistance and reduce fatigue through volume,
# RIR and progression restraint.
# ============================================================

def _target_weight(
    profile,
    readiness_band,
):

    performance = (
        profile.get(
            "performance"
        )
        or {}
    )

    recent_weight = performance.get(
        "recent_working_weight_per_arm_lb"
    )

    if recent_weight is None:

        return (
            None,
            False,
            "hold",
            (
                "No reliable historical working "
                "load is available."
            ),
        )

    recent_weight = float(
        recent_weight
    )

    family = _family_for_movement(
        profile.get(
            "name"
        )
    )

    compound = family in (
        "squat",
        "hinge",
        "split_squat_lunge",
    )

    earned = bool(
        performance.get(
            "progression_earned"
        )
    )

    struggling = performance.get(
        "recent_struggling_score"
    )

    inconsistency = performance.get(
        "recent_inconsistency_score"
    )

    hardware = (
        performance.get(
            "tonal_hardware"
        )
        or {}
    )

    near_ceiling = bool(
        hardware.get(
            "near_hardware_ceiling"
        )
    )

    poor_quality = (
        (
            struggling is not None
            and float(
                struggling
            ) >= 0.90
        )
        or
        (
            inconsistency is not None
            and float(
                inconsistency
            ) >= 0.70
        )
    )

    questionable_quality = (
        (
            struggling is not None
            and float(
                struggling
            ) >= 0.80
        )
        or
        (
            inconsistency is not None
            and float(
                inconsistency
            ) >= 0.60
        )
    )

    # --------------------------------------------------------
    # LOW READINESS
    # --------------------------------------------------------

    if readiness_band == "low":

        multiplier = (
            0.85
            if compound
            else 0.80
        )

        weight = (
            recent_weight
            * multiplier
        )

        return (
            _round_weight(
                weight
            ),
            False,
            "deload",
            (
                "Low readiness: reduce resistance "
                "and working volume rather than "
                "pursue overload."
            ),
        )

    # --------------------------------------------------------
    # MODERATE READINESS
    # --------------------------------------------------------

    if readiness_band == "moderate":

        if poor_quality:

            multiplier = (
                0.92
                if compound
                else 0.90
            )

            reason = (
                "Moderate recovery plus elevated "
                "historical struggling or movement "
                "inconsistency: reduce load."
            )

        elif questionable_quality:

            multiplier = (
                0.97
                if compound
                else 0.95
            )

            reason = (
                "Moderate recovery with some historical "
                "performance-quality concern: use a "
                "small load reduction."
            )

        else:

            multiplier = 1.00

            reason = (
                "Moderate recovery with stable historical "
                "execution: preserve normal working load "
                "and manage fatigue through sets, RIR "
                "and avoiding progressive overload."
            )

        weight = (
            recent_weight
            * multiplier
        )

        return (
            _round_weight(
                weight
            ),
            False,
            "hold",
            reason,
        )

    # --------------------------------------------------------
    # GOOD READINESS
    # --------------------------------------------------------

    if readiness_band == "good":

        weight = recent_weight

        if poor_quality:

            return (
                _round_weight(
                    weight
                ),
                False,
                "hold",
                (
                    "Readiness is good, but historical "
                    "performance quality does not justify "
                    "progression."
                ),
            )

        if earned:

            if near_ceiling:

                return (
                    _round_weight(
                        weight
                    ),
                    True,
                    "reps",
                    (
                        "Progression is earned, but this "
                        "movement is near Tonal's load "
                        "ceiling. Progress through reps."
                    ),
                )

            weight += 1.0

            return (
                _round_weight(
                    weight
                ),
                True,
                "load",
                (
                    "Good readiness and historical "
                    "performance support a conservative "
                    "load progression."
                ),
            )

        return (
            _round_weight(
                weight
            ),
            False,
            "hold",
            (
                "Good readiness, but historical "
                "progression criteria have not "
                "yet been earned."
            ),
        )

    # --------------------------------------------------------
    # HIGH READINESS
    # --------------------------------------------------------

    if readiness_band == "high":

        weight = recent_weight

        if poor_quality:

            return (
                _round_weight(
                    weight
                ),
                False,
                "hold",
                (
                    "WHOOP readiness is high, but "
                    "historical movement quality does "
                    "not support increasing stimulus."
                ),
            )

        if not earned:

            return (
                _round_weight(
                    weight
                ),
                False,
                "hold",
                (
                    "High recovery alone does not justify "
                    "progression. This movement has not "
                    "earned progressive overload yet."
                ),
            )

        if near_ceiling:

            return (
                _round_weight(
                    weight
                ),
                True,
                "reps",
                (
                    "High readiness and progression are "
                    "present, but resistance is near "
                    "Tonal's hardware ceiling. Shift "
                    "overload to repetitions."
                ),
            )

        weight += 2.0

        return (
            _round_weight(
                weight
            ),
            True,
            "load",
            (
                "High readiness plus earned historical "
                "progression support a small resistance "
                "increase."
            ),
        )

    return (
        _round_weight(
            recent_weight
        ),
        False,
        "hold",
        (
            "No progression rule was triggered."
        ),
    )


# ============================================================
# SMART WEIGHT HISTORY
# ============================================================

def _historical_mode_sets(
    profile,
    mode,
):

    history = (
        profile.get(
            "history"
        )
        or {}
    )

    usage = (
        history.get(
            "smart_weight_usage"
        )
        or {}
    )

    mode_data = (
        usage.get(
            mode
        )
        or {}
    )

    return int(
        mode_data.get(
            "sets"
        )
        or 0
    )


# ============================================================
# SMART WEIGHT MODE SELECTION
# ============================================================

def _choose_smart_weight(
    profile,
    readiness_band,
):

    performance = (
        profile.get(
            "performance"
        )
        or {}
    )

    hardware = (
        performance.get(
            "tonal_hardware"
        )
        or {}
    )

    earned = bool(
        performance.get(
            "progression_earned"
        )
    )

    near_ceiling = bool(
        hardware.get(
            "near_hardware_ceiling"
        )
    )

    # Moderate and low readiness:
    # keep resistance predictable.

    if readiness_band in (
        "moderate",
        "low",
    ):

        return {
            "mode":
                "standard",

            "spotter":
                True,

            "reason":
                (
                    "Standard resistance keeps "
                    "today's fatigue predictable."
                ),
        }

    # High readiness near hardware ceiling:
    # Eccentric is preferred only if the movement
    # has historically used it.

    if (
        readiness_band == "high"
        and near_ceiling
        and earned
        and _historical_mode_sets(
            profile,
            "eccentric"
        ) > 0
    ):

        return {
            "mode":
                "eccentric",

            "spotter":
                True,

            "reason":
                (
                    "The movement has earned progression, "
                    "is near Tonal's load ceiling and has "
                    "historical Eccentric usage."
                ),
        }

    # Progressive mode is allowed only if
    # historically used.

    if (
        readiness_band == "high"
        and earned
        and _historical_mode_sets(
            profile,
            "progressive"
        ) > 0
    ):

        return {
            "mode":
                "progressive",

            "spotter":
                True,

            "reason":
                (
                    "High readiness plus earned progression "
                    "and historical Progressive usage support "
                    "this Smart Weight mode."
                ),
        }

    # Chains can become an additional progression tool
    # when historically used.

    if (
        readiness_band == "high"
        and earned
        and _historical_mode_sets(
            profile,
            "chains"
        ) > 0
    ):

        return {
            "mode":
                "chains",

            "spotter":
                True,

            "reason":
                (
                    "High readiness, earned progression "
                    "and historical Chains usage support "
                    "variable resistance."
                ),
        }

    return {
        "mode":
            "standard",

        "spotter":
            True,

        "reason":
            (
                "No stronger Smart Weight progression "
                "rule was satisfied."
            ),
    }


# ============================================================
# EXERCISE PRESCRIPTION
#
# Progression ladder:
#
# 1. Load
# 2. Reps
# 3. Sets
# 4. Smart Weight
#
# Never automatically increase all variables together.
# ============================================================

def _prescribe_exercise(
    profile,
    readiness_band,
    set_count,
):

    performance = (
        profile.get(
            "performance"
        )
        or {}
    )

    (
        weight,
        progression_applied,
        overload_method,
        progression_reason,
    ) = _target_weight(
        profile,
        readiness_band,
    )

    reps = _target_reps(
        profile,
        readiness_band,
    )

    hardware = (
        performance.get(
            "tonal_hardware"
        )
        or {}
    )

    earned = bool(
        performance.get(
            "progression_earned"
        )
    )

    near_ceiling = bool(
        hardware.get(
            "near_hardware_ceiling"
        )
    )

    # --------------------------------------------------------
    # REPS PROGRESSION
    # --------------------------------------------------------

    if (
        readiness_band in (
            "good",
            "high",
        )
        and earned
        and overload_method == "reps"
    ):

        reps += (
            2
            if readiness_band == "high"
            else 1
        )

        progression_applied = True

    reps = int(
        _clamp(
            reps,
            6,
            15,
        )
    )

    # --------------------------------------------------------
    # SMART WEIGHT
    # --------------------------------------------------------

    smart_weight = (
        _choose_smart_weight(
            profile,
            readiness_band,
        )
    )

    # Do not stack a new load increase and
    # an advanced Smart Weight overload simultaneously.

    if (
        overload_method == "load"
        and smart_weight.get(
            "mode"
        )
        != "standard"
    ):

        smart_weight = {
            "mode":
                "standard",

            "spotter":
                True,

            "reason":
                (
                    "Resistance was already increased "
                    "today, so an additional Smart Weight "
                    "overload is withheld."
                ),
        }

    # --------------------------------------------------------
    # NEXT PROGRESSION OPTION
    #
    # At the hardware ceiling we record the next available
    # overload strategy rather than stacking it today.
    # --------------------------------------------------------

    next_progression = None

    if (
        readiness_band == "high"
        and earned
        and near_ceiling
        and overload_method == "reps"
    ):

        if (
            _historical_mode_sets(
                profile,
                "eccentric"
            )
            > 0
        ):

            next_progression = (
                "eccentric"
            )

        elif (
            _historical_mode_sets(
                profile,
                "chains"
            )
            > 0
        ):

            next_progression = (
                "chains"
            )

        elif (
            _historical_mode_sets(
                profile,
                "progressive"
            )
            > 0
        ):

            next_progression = (
                "progressive"
            )

        else:

            next_progression = (
                "additional_set"
            )

    # --------------------------------------------------------
    # ESTIMATED SESSION VOLUME
    #
    # This remains an approximation for planning.
    # Actual Tonal-recorded volume remains authoritative
    # after the workout.
    # --------------------------------------------------------

    estimated_volume = None

    if weight is not None:

        estimated_volume = (
            weight
            * reps
            * set_count
        )

    return {

        "movement_id":
            profile.get(
                "movement_id"
            ),

        "name":
            profile.get(
                "name"
            ),

        "exercise_family":
            _family_for_movement(
                profile.get(
                    "name"
                )
            ),

        "muscle_groups":
            profile.get(
                "muscle_groups"
            )
            or [],

        "accessory":
            profile.get(
                "accessory"
            ),

        "sets":
            set_count,

        "reps_per_set":
            reps,

        "target_weight_lb":
            weight,

        "target_rir":
            SESSION_RULES[
                readiness_band
            ][
                "target_rir"
            ],

        "estimated_volume":
            (
                round(
                    estimated_volume,
                    1,
                )
                if estimated_volume
                is not None
                else None
            ),

        "progression_earned":
            earned,

        "progression_applied":
            progression_applied,

        "overload_method":
            overload_method,

        "progression_reason":
            progression_reason,

        "next_progression_option":
            next_progression,

        "smart_weight":
            smart_weight,

        "hardware_context": {

            "tonal_max_per_arm_lb":
                TONAL_MAX_PER_ARM_LB,

            "tonal_max_combined_lb":
                TONAL_MAX_COMBINED_LB,

            "near_hardware_ceiling":
                near_ceiling,

            "ceiling_usage_pct":
                hardware.get(
                    "per_arm_ceiling_usage_pct"
                ),
        },

        "historical_context": {

            "recent_working_weight":
                performance.get(
                    "recent_working_weight_per_arm_lb"
                ),

            "recent_sets_per_session":
                performance.get(
                    "recent_sets_per_session"
                ),

            "recent_reps_per_session":
                performance.get(
                    "recent_reps_per_session"
                ),

            "recent_estimated_1rm":
                performance.get(
                    "recent_estimated_1rm"
                ),

            "estimated_1rm_change_pct":
                performance.get(
                    "estimated_1rm_change_pct"
                ),

            "recent_struggling_score":
                performance.get(
                    "recent_struggling_score"
                ),

            "recent_inconsistency_score":
                performance.get(
                    "recent_inconsistency_score"
                ),
        },
    }


# ============================================================
# DAILY WORKOUT ENGINE
# ============================================================

def build_daily_workout_prescription(now=None):

    readiness = (
        _latest_readiness(now=now)
    )

    if not readiness.get(
        "available"
    ):

        return {
            "status":
                "not_ready",

            "reason":
                (
                    "Current WHOOP recovery "
                    "is unavailable."
                ),

            "session":
                None,
        }

    readiness_band = (
        readiness.get(
            "readiness_band"
        )
    )

    # --------------------------------------------------------
    # REST DAY
    # --------------------------------------------------------

    if readiness_band == "very_low":

        return {
            "status":
                "ok",

            "generated_at":
                datetime.now(
                    timezone.utc
                ).isoformat(),

            "readiness":
                readiness,

            "session": {

                "session_type":
                    "Rest",

                "primary_focus":
                    [],

                "secondary_focus":
                    [],

                "exercise_count":
                    0,

                "total_sets":
                    0,

                "estimated_total_volume":
                    0,

                "progressive_overload_exercises":
                    0,

                "direct_core_exercises":
                    0,

                "exercises":
                    [],

                "reason":
                    (
                        "WHOOP recovery is too low "
                        "for a productive strength "
                        "prescription today."
                    ),
            },
        }

    priorities = (
        build_training_priority(now=now)
    )

    profiles_result = (
        build_movement_performance_profiles()
    )

    recommended_session = (
        priorities.get(
            "recommended_session"
        )
        or {}
    )

    primary_focus = (
        recommended_session.get(
            "primary_focus"
        )
        or []
    )

    secondary_focus = (
        recommended_session.get(
            "secondary_focus"
        )
        or []
    )

    selected = (
        _select_movements(
            profiles_result.get(
                "profiles",
                []
            ),
            primary_focus,
            secondary_focus,
        )
    )

    allocations = (
        _set_allocation(
            selected,
            readiness_band,
        )
    )

    exercises = []

    for (
        profile,
        set_count,
    ) in zip(
        selected,
        allocations,
    ):

        exercise = (
            _prescribe_exercise(
                profile,
                readiness_band,
                set_count,
            )
        )

        exercises.append(
            exercise
        )

    total_sets = sum(
        exercise.get(
            "sets",
            0
        )
        for exercise in exercises
    )

    total_volume = sum(
        exercise.get(
            "estimated_volume"
        )
        or 0
        for exercise in exercises
    )

    progression_count = sum(
        1
        for exercise in exercises
        if exercise.get(
            "progression_applied"
        )
    )

    direct_core_count = sum(
        1
        for exercise in exercises
        if exercise.get(
            "exercise_family"
        )
        in (
            "core_flexion",
            "core_rotation",
        )
    )

    return {
        "status":
            "ok",

        "generated_at":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "readiness":
            readiness,

        "session": {

            "session_type":
                (
                    recommended_session.get(
                        "session_type"
                    )
                    or priorities.get(
                        "training_focus"
                    )
                ),

            "primary_focus":
                primary_focus,

            "secondary_focus":
                secondary_focus,

            "target_muscles":
                priorities.get("target_muscles", primary_focus + secondary_focus),

            "suppressed_muscles":
                priorities.get("suppressed_muscles", []),

            "recent_training_context":
                priorities.get("recent_training_context", {}),

            "selection_confidence":
                priorities.get("selection_confidence"),

            "session_focus_reason":
                priorities.get("session_focus_reason"),

            "session_template_scores":
                priorities.get("session_template_scores", []),

            "whoop_dosage_effect": (
                f"WHOOP {readiness.get('training_category')} controls session dose; "
                "it does not override locally suppressed muscles."
            ),

            "exercise_count":
                len(
                    exercises
                ),

            "total_sets":
                total_sets,

            "target_set_range": {

                "minimum":
                    SESSION_RULES[
                        readiness_band
                    ][
                        "min_sets"
                    ],

                "target":
                    SESSION_RULES[
                        readiness_band
                    ][
                        "target_sets"
                    ],

                "maximum":
                    SESSION_RULES[
                        readiness_band
                    ][
                        "max_sets"
                    ],
            },

            "estimated_total_volume":
                round(
                    total_volume,
                    1,
                ),

            "progressive_overload_exercises":
                progression_count,

            "direct_core_exercises":
                direct_core_count,

            "exercises":
                exercises,
        },

        "progression_policy": {

            "principle":
                (
                    "Progressive overload is earned "
                    "through historical movement "
                    "performance and then permitted "
                    "or constrained by WHOOP readiness."
                ),

            "high_readiness":
                (
                    "Pursue progressive overload only "
                    "on movements whose historical "
                    "performance has earned progression."
                ),

            "good_readiness":
                (
                    "Use normal productive training "
                    "with conservative progression."
                ),

            "moderate_readiness":
                (
                    "Preserve meaningful resistance "
                    "when execution quality is stable. "
                    "Reduce fatigue primarily through "
                    "session volume, RIR and withholding "
                    "new overload."
                ),

            "low_readiness":
                (
                    "Reduce both load and total working "
                    "volume and do not pursue overload."
                ),

            "tonal_hardware":
                (
                    "Never exceed Tonal's 100 lb "
                    "per-arm or 200 lb combined limits."
                ),

            "progression_ladder":
                [
                    "load",
                    "reps",
                    "sets",
                    "smart_weight",
                ],

            "hardware_ceiling_strategy":
                (
                    "Near Tonal's load ceiling, shift "
                    "progression toward reps, sets or "
                    "historically appropriate Smart "
                    "Weight modes instead of impossible "
                    "load increases."
                ),

            "smart_weight_policy":
                (
                    "Eccentric, Chains and Progressive "
                    "modes are used only when readiness, "
                    "progression status and historical "
                    "movement usage support them."
                ),
        },
    }


# ============================================================
# TERMINAL TEST
# ============================================================

def main():

    result = (
        build_daily_workout_prescription()
    )

    print()

    print(
        "CUSTOM TONAL WORKOUT PRESCRIPTION V3"
    )

    print(
        "=" * 78
    )

    print(
        "Status:",
        result.get(
            "status"
        ),
    )

    if result.get(
        "status"
    ) != "ok":

        print(
            result.get(
                "reason"
            )
        )

        return

    readiness = (
        result.get(
            "readiness"
        )
        or {}
    )

    session = (
        result.get(
            "session"
        )
        or {}
    )

    print(
        "Recovery:",
        readiness.get(
            "recovery_score"
        ),
    )

    print(
        "Readiness:",
        readiness.get(
            "training_category"
        ),
    )

    print(
        "Session:",
        session.get(
            "session_type"
        ),
    )

    print(
        "Target sets:",
        session.get(
            "target_set_range"
        ),
    )

    print(
        "Direct core exercises:",
        session.get(
            "direct_core_exercises"
        ),
    )

    print(
        "=" * 78
    )

    for (
        index,
        exercise,
    ) in enumerate(
        session.get(
            "exercises",
            []
        ),
        start=1,
    ):

        print()

        print(
            f"{index}. "
            f"{exercise.get('name')}"
        )

        print(
            "    Family:",
            exercise.get(
                "exercise_family"
            ),
        )

        print(
            "    Muscles:",
            ", ".join(
                exercise.get(
                    "muscle_groups",
                    []
                )
            ),
        )

        print(
            "    Prescription:",
            exercise.get(
                "sets"
            ),
            "sets ×",
            exercise.get(
                "reps_per_set"
            ),
            "reps",
        )

        print(
            "    Tonal load:",
            exercise.get(
                "target_weight_lb"
            ),
            "lb",
        )

        print(
            "    RIR:",
            exercise.get(
                "target_rir"
            ),
        )

        print(
            "    Smart Weight:",
            exercise.get(
                "smart_weight",
                {}
            ).get(
                "mode"
            ),
        )

        print(
            "    Progression earned:",
            exercise.get(
                "progression_earned"
            ),
        )

        print(
            "    Progression applied:",
            exercise.get(
                "progression_applied"
            ),
        )

        print(
            "    Overload method:",
            exercise.get(
                "overload_method"
            ),
        )

        print(
            "    Reason:",
            exercise.get(
                "progression_reason"
            ),
        )

        if exercise.get(
            "next_progression_option"
        ):

            print(
                "    Next progression option:",
                exercise.get(
                    "next_progression_option"
                ),
            )

        print(
            "    Estimated volume:",
            exercise.get(
                "estimated_volume"
            ),
        )

    print()

    print(
        "=" * 78
    )

    print(
        "Total sets:",
        session.get(
            "total_sets"
        ),
    )

    print(
        "Estimated total volume:",
        session.get(
            "estimated_total_volume"
        ),
    )

    print(
        "Progression exercises:",
        session.get(
            "progressive_overload_exercises"
        ),
    )

    print(
        "=" * 78
    )


if __name__ == "__main__":
    main()
