from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import mean, median

from db import get_conn


# ============================================================
# CONFIGURATION
# ============================================================

LOOKBACK_DAYS = 180

MAX_RECENT_SESSIONS = 6

MIN_SESSIONS_FOR_PROGRESSION = 3

TONAL_MAX_PER_ARM_LB = 100.0

TONAL_MAX_COMBINED_LB = 200.0

HARDWARE_CEILING_THRESHOLD = 0.95


# ============================================================
# HELPERS
# ============================================================

def _float(value):
    if value is None:
        return None

    return float(value)


def _int(value):
    if value is None:
        return None

    return int(value)


def _safe_mean(values):
    values = [
        float(value)
        for value in values
        if value is not None
    ]

    if not values:
        return None

    return mean(values)


def _safe_median(values):
    values = [
        float(value)
        for value in values
        if value is not None
    ]

    if not values:
        return None

    return median(values)


def _pct_change(current, baseline):
    if (
        current is None
        or baseline is None
        or baseline == 0
    ):
        return None

    return (
        (current - baseline)
        / baseline
        * 100.0
    )


def _round(value, digits=1):
    if value is None:
        return None

    return round(
        float(value),
        digits,
    )


def _mode_name(row):
    modes = []

    if row.get("eccentric"):
        modes.append("eccentric")

    if row.get("chains"):
        modes.append("chains")

    if row.get("flex"):
        modes.append("flex")

    if row.get("progressive"):
        modes.append("progressive")

    if row.get("burnout"):
        modes.append("burnout")

    if not modes:
        return "standard"

    return "+".join(modes)


# ============================================================
# DATABASE QUERY
# ============================================================

def _load_recent_sets():

    cutoff = (
        datetime.now(timezone.utc)
        - timedelta(days=LOOKBACK_DAYS)
    )

    with get_conn() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    w.activity_id,
                    w.begin_time,
                    w.end_time,
                    w.total_volume AS workout_total_volume,

                    s.set_index,
                    s.movement_id,
                    s.rep_count,
                    s.base_weight,
                    s.suggested_weight,
                    s.avg_weight,
                    s.max_weight,
                    s.volume,
                    s.one_rep_max,
                    s.struggling_score,
                    s.inconsistency_score,
                    s.rom,
                    s.duration_seconds,
                    s.spotter,
                    s.eccentric,
                    s.chains,
                    s.flex,
                    s.progressive,
                    s.burnout,
                    s.max_con_power,

                    m.name,
                    m.short_name,
                    m.muscle_groups,
                    m.accessory,
                    m.is_two_sided,
                    m.is_bilateral,
                    m.is_alternating,
                    m.is_generic,
                    m.custom_movement

                FROM public.tonal_sets s

                JOIN public.tonal_workouts w
                    ON w.activity_id = s.activity_id

                JOIN public.tonal_movements m
                    ON m.movement_id = s.movement_id

                LEFT JOIN public.tonal_workout_overrides o
                    ON o.activity_id = w.activity_id

                WHERE
                    w.begin_time >= %s

                    AND COALESCE(
                        o.include_in_training_analysis,
                        TRUE
                    ) = TRUE

                    AND COALESCE(
                        m.is_generic,
                        FALSE
                    ) = FALSE

                    AND COALESCE(
                        m.custom_movement,
                        FALSE
                    ) = FALSE

                ORDER BY
                    w.begin_time DESC,
                    s.movement_id,
                    s.set_index
                """,
                (cutoff,),
            )

            return cur.fetchall()


# ============================================================
# SESSION AGGREGATION
# ============================================================

def _aggregate_session(rows):

    if not rows:
        return None

    first = rows[0]

    base_weights = [
        _float(row.get("base_weight"))
        for row in rows
        if row.get("base_weight") is not None
    ]

    avg_weights = [
        _float(row.get("avg_weight"))
        for row in rows
        if row.get("avg_weight") is not None
    ]

    one_rms = [
        _float(row.get("one_rep_max"))
        for row in rows
        if row.get("one_rep_max") is not None
    ]

    struggle_scores = [
        _float(row.get("struggling_score"))
        for row in rows
        if row.get("struggling_score") is not None
    ]

    inconsistency_scores = [
        _float(row.get("inconsistency_score"))
        for row in rows
        if row.get("inconsistency_score") is not None
    ]

    rom_values = [
        _float(row.get("rom"))
        for row in rows
        if row.get("rom") is not None
    ]

    power_values = [
        _float(row.get("max_con_power"))
        for row in rows
        if row.get("max_con_power") is not None
    ]

    reps = sum(
        _int(row.get("rep_count")) or 0
        for row in rows
    )

    volume = sum(
        _float(row.get("volume")) or 0
        for row in rows
    )

    mode_counts = defaultdict(int)

    for row in rows:
        mode_counts[
            _mode_name(row)
        ] += 1

    return {
        "activity_id":
            str(
                first.get(
                    "activity_id"
                )
            ),

        "begin_time":
            first.get(
                "begin_time"
            ),

        "set_count":
            len(rows),

        "total_reps":
            reps,

        "total_volume":
            volume,

        "median_base_weight":
            _safe_median(
                base_weights
            ),

        "average_base_weight":
            _safe_mean(
                base_weights
            ),

        "average_actual_weight":
            _safe_mean(
                avg_weights
            ),

        "max_base_weight":
            (
                max(base_weights)
                if base_weights
                else None
            ),

        "best_estimated_1rm":
            (
                max(one_rms)
                if one_rms
                else None
            ),

        "average_struggling_score":
            _safe_mean(
                struggle_scores
            ),

        "average_inconsistency_score":
            _safe_mean(
                inconsistency_scores
            ),

        "average_rom":
            _safe_mean(
                rom_values
            ),

        "max_concentric_power":
            (
                max(power_values)
                if power_values
                else None
            ),

        "spotter_sets":
            sum(
                1
                for row in rows
                if row.get("spotter")
            ),

        "mode_counts":
            dict(
                mode_counts
            ),
    }


# ============================================================
# PROGRESSION ANALYSIS
# ============================================================

def _progression_analysis(
    sessions,
    is_bilateral,
    is_two_sided,
):

    recent = sessions[
        :MAX_RECENT_SESSIONS
    ]

    session_count = len(
        recent
    )

    if not recent:

        return {
            "status":
                "insufficient_data",

            "progression_earned":
                False,

            "recommended_overload_method":
                "hold",
        }

    latest = recent[0]

    recent_working_weight = (
        _safe_median(
            [
                session.get(
                    "median_base_weight"
                )
                for session in recent[:3]
            ]
        )
    )

    recent_reps_per_session = (
        _safe_mean(
            [
                session.get(
                    "total_reps"
                )
                for session in recent[:3]
            ]
        )
    )

    recent_sets_per_session = (
        _safe_mean(
            [
                session.get(
                    "set_count"
                )
                for session in recent[:3]
            ]
        )
    )

    recent_volume = (
        _safe_mean(
            [
                session.get(
                    "total_volume"
                )
                for session in recent[:3]
            ]
        )
    )

    recent_1rm = (
        _safe_mean(
            [
                session.get(
                    "best_estimated_1rm"
                )
                for session in recent[:3]
            ]
        )
    )

    if session_count >= 4:

        older_sessions = recent[3:6]

    elif session_count >= 2:

        older_sessions = recent[1:]

    else:

        older_sessions = []

    previous_working_weight = (
        _safe_median(
            [
                session.get(
                    "median_base_weight"
                )
                for session in older_sessions
            ]
        )
    )

    previous_1rm = (
        _safe_mean(
            [
                session.get(
                    "best_estimated_1rm"
                )
                for session in older_sessions
            ]
        )
    )

    load_change_pct = (
        _pct_change(
            recent_working_weight,
            previous_working_weight,
        )
    )

    one_rm_change_pct = (
        _pct_change(
            recent_1rm,
            previous_1rm,
        )
    )

    recent_struggling = (
        _safe_mean(
            [
                session.get(
                    "average_struggling_score"
                )
                for session in recent[:3]
            ]
        )
    )

    recent_inconsistency = (
        _safe_mean(
            [
                session.get(
                    "average_inconsistency_score"
                )
                for session in recent[:3]
            ]
        )
    )

    recent_power = (
        _safe_mean(
            [
                session.get(
                    "max_concentric_power"
                )
                for session in recent[:3]
            ]
        )
    )

    progression_earned = False

    progression_reasons = []

    if (
        session_count
        >= MIN_SESSIONS_FOR_PROGRESSION
    ):

        one_rm_supports_progression = (
            one_rm_change_pct is not None
            and one_rm_change_pct >= 1.0
        )

        load_supports_progression = (
            load_change_pct is not None
            and load_change_pct >= 0
        )

        effort_is_acceptable = (
            recent_struggling is None
            or recent_struggling <= 0.80
        )

        consistency_is_acceptable = (
            recent_inconsistency is None
            or recent_inconsistency <= 0.60
        )

        progression_earned = (
            (
                one_rm_supports_progression
                or load_supports_progression
            )
            and effort_is_acceptable
            and consistency_is_acceptable
        )

        if one_rm_supports_progression:

            progression_reasons.append(
                "Recent estimated 1RM is improving."
            )

        if load_supports_progression:

            progression_reasons.append(
                "Recent working load is stable or improving."
            )

        if not effort_is_acceptable:

            progression_reasons.append(
                "Recent struggling score is elevated."
            )

        if not consistency_is_acceptable:

            progression_reasons.append(
                "Recent movement inconsistency is elevated."
            )

    else:

        progression_reasons.append(
            "Not enough recent sessions to earn progression."
        )

    per_arm_load = (
        recent_working_weight
    )

    hardware_ceiling_pct = None

    if per_arm_load is not None:

        hardware_ceiling_pct = (
            per_arm_load
            / TONAL_MAX_PER_ARM_LB
            * 100.0
        )

    near_hardware_ceiling = (
        hardware_ceiling_pct is not None
        and hardware_ceiling_pct
        >= (
            HARDWARE_CEILING_THRESHOLD
            * 100.0
        )
    )

    uses_two_arms = bool(
        is_bilateral
        or is_two_sided
    )

    estimated_combined_load = None

    if per_arm_load is not None:

        if uses_two_arms:

            estimated_combined_load = (
                per_arm_load
                * 2
            )

        else:

            estimated_combined_load = (
                per_arm_load
            )

    if near_hardware_ceiling:

        overload_method = "reps"

    elif progression_earned:

        overload_method = "load_or_reps"

    else:

        overload_method = "hold"

    return {
        "status":
            "usable",

        "recent_session_count":
            session_count,

        "recent_working_weight_per_arm_lb":
            _round(
                recent_working_weight,
                1,
            ),

        "recent_reps_per_session":
            _round(
                recent_reps_per_session,
                1,
            ),

        "recent_sets_per_session":
            _round(
                recent_sets_per_session,
                1,
            ),

        "recent_volume":
            _round(
                recent_volume,
                1,
            ),

        "recent_estimated_1rm":
            _round(
                recent_1rm,
                1,
            ),

        "working_load_change_pct":
            _round(
                load_change_pct,
                1,
            ),

        "estimated_1rm_change_pct":
            _round(
                one_rm_change_pct,
                1,
            ),

        "recent_struggling_score":
            _round(
                recent_struggling,
                3,
            ),

        "recent_inconsistency_score":
            _round(
                recent_inconsistency,
                3,
            ),

        "recent_max_concentric_power":
            _round(
                recent_power,
                1,
            ),

        "latest_session":
            {
                "begin_time":
                    latest.get(
                        "begin_time"
                    ),

                "sets":
                    latest.get(
                        "set_count"
                    ),

                "reps":
                    latest.get(
                        "total_reps"
                    ),

                "volume":
                    _round(
                        latest.get(
                            "total_volume"
                        ),
                        1,
                    ),

                "median_base_weight":
                    _round(
                        latest.get(
                            "median_base_weight"
                        ),
                        1,
                    ),

                "best_estimated_1rm":
                    _round(
                        latest.get(
                            "best_estimated_1rm"
                        ),
                        1,
                    ),
            },

        "progression_earned":
            progression_earned,

        "progression_reasons":
            progression_reasons,

        "recommended_overload_method":
            overload_method,

        "tonal_hardware": {
            "max_per_arm_lb":
                TONAL_MAX_PER_ARM_LB,

            "max_combined_lb":
                TONAL_MAX_COMBINED_LB,

            "current_per_arm_load_lb":
                _round(
                    per_arm_load,
                    1,
                ),

            "estimated_combined_load_lb":
                _round(
                    estimated_combined_load,
                    1,
                ),

            "per_arm_ceiling_usage_pct":
                _round(
                    hardware_ceiling_pct,
                    1,
                ),

            "near_hardware_ceiling":
                near_hardware_ceiling,
        },
    }


# ============================================================
# PUBLIC PROFILE ENGINE
# ============================================================

def build_movement_performance_profiles():

    rows = (
        _load_recent_sets()
    )

    movement_sessions = defaultdict(
        lambda: defaultdict(list)
    )

    movement_meta = {}

    for row in rows:

        movement_id = str(
            row.get(
                "movement_id"
            )
        )

        activity_id = str(
            row.get(
                "activity_id"
            )
        )

        movement_sessions[
            movement_id
        ][
            activity_id
        ].append(
            row
        )

        if movement_id not in movement_meta:

            movement_meta[
                movement_id
            ] = {
                "movement_id":
                    movement_id,

                "name":
                    row.get(
                        "name"
                    ),

                "short_name":
                    row.get(
                        "short_name"
                    ),

                "muscle_groups":
                    row.get(
                        "muscle_groups"
                    )
                    or [],

                "accessory":
                    row.get(
                        "accessory"
                    ),

                "is_two_sided":
                    bool(
                        row.get(
                            "is_two_sided"
                        )
                    ),

                "is_bilateral":
                    bool(
                        row.get(
                            "is_bilateral"
                        )
                    ),

                "is_alternating":
                    bool(
                        row.get(
                            "is_alternating"
                        )
                    ),
            }

    profiles = []

    for (
        movement_id,
        sessions_by_activity,
    ) in movement_sessions.items():

        sessions = []

        all_mode_counts = defaultdict(
            int
        )

        total_sets = 0

        total_spotter_sets = 0

        for session_rows in (
            sessions_by_activity.values()
        ):

            session = (
                _aggregate_session(
                    session_rows
                )
            )

            if session is None:
                continue

            sessions.append(
                session
            )

            total_sets += (
                session.get(
                    "set_count"
                )
                or 0
            )

            total_spotter_sets += (
                session.get(
                    "spotter_sets"
                )
                or 0
            )

            for (
                mode,
                count,
            ) in (
                session.get(
                    "mode_counts",
                    {}
                ).items()
            ):

                all_mode_counts[
                    mode
                ] += count

        sessions.sort(
            key=lambda item: (
                item.get(
                    "begin_time"
                )
                or datetime.min.replace(
                    tzinfo=timezone.utc
                )
            ),
            reverse=True,
        )

        meta = (
            movement_meta[
                movement_id
            ]
        )

        progression = (
            _progression_analysis(
                sessions,
                meta.get(
                    "is_bilateral"
                ),
                meta.get(
                    "is_two_sided"
                ),
            )
        )

        smart_weight_usage = {}

        for mode in (
            "standard",
            "eccentric",
            "chains",
            "flex",
            "progressive",
            "burnout",
        ):

            count = (
                all_mode_counts.get(
                    mode,
                    0
                )
            )

            smart_weight_usage[
                mode
            ] = {
                "sets":
                    count,

                "pct_of_sets":
                    (
                        round(
                            count
                            / total_sets
                            * 100.0,
                            1,
                        )
                        if total_sets
                        else 0.0
                    ),
            }

        profile = {
            **meta,

            "history": {
                "sessions_in_lookback":
                    len(
                        sessions
                    ),

                "total_sets":
                    total_sets,

                "last_trained_at":
                    (
                        sessions[0].get(
                            "begin_time"
                        )
                        if sessions
                        else None
                    ),

                "spotter_sets":
                    total_spotter_sets,

                "smart_weight_usage":
                    smart_weight_usage,
            },

            "performance":
                progression,
        }

        profiles.append(
            profile
        )

    profiles.sort(
        key=lambda item: (
            item.get(
                "history",
                {}
            ).get(
                "last_trained_at"
            )
            or datetime.min.replace(
                tzinfo=timezone.utc
            )
        ),
        reverse=True,
    )

    return {
        "status":
            "ok",

        "calculated_at":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "lookback_days":
            LOOKBACK_DAYS,

        "movement_count":
            len(
                profiles
            ),

        "tonal_hardware_limits": {
            "max_per_arm_lb":
                TONAL_MAX_PER_ARM_LB,

            "max_combined_lb":
                TONAL_MAX_COMBINED_LB,
        },

        "profiles":
            profiles,
    }


# ============================================================
# TERMINAL TEST
# ============================================================

def main():

    result = (
        build_movement_performance_profiles()
    )

    print(
        "\nTONAL MOVEMENT PERFORMANCE PROFILES"
    )

    print(
        "=" * 78
    )

    print(
        f"Movements analyzed: "
        f"{result['movement_count']}"
    )

    print(
        f"Lookback: "
        f"{result['lookback_days']} days"
    )

    print(
        "=" * 78
    )

    for profile in (
        result[
            "profiles"
        ][:20]
    ):

        performance = (
            profile[
                "performance"
            ]
        )

        hardware = (
            performance.get(
                "tonal_hardware",
                {}
            )
        )

        print()

        print(
            profile.get(
                "name"
            )
        )

        print(
            "  Muscles:",
            ", ".join(
                profile.get(
                    "muscle_groups",
                    []
                )
            )
            or "Unknown",
        )

        print(
            "  Sessions:",
            profile[
                "history"
            ][
                "sessions_in_lookback"
            ],
        )

        print(
            "  Working load/arm:",
            performance.get(
                "recent_working_weight_per_arm_lb"
            ),
        )

        print(
            "  Recent 1RM:",
            performance.get(
                "recent_estimated_1rm"
            ),
        )

        print(
            "  1RM trend:",
            performance.get(
                "estimated_1rm_change_pct"
            ),
            "%",
        )

        print(
            "  Progression earned:",
            performance.get(
                "progression_earned"
            ),
        )

        print(
            "  Overload method:",
            performance.get(
                "recommended_overload_method"
            ),
        )

        print(
            "  Hardware ceiling:",
            hardware.get(
                "per_arm_ceiling_usage_pct"
            ),
            "%",
        )

    print()

    print(
        "=" * 78
    )


if __name__ == "__main__":
    main()
