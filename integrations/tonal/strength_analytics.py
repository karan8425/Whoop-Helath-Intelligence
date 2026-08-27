import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from db import get_conn


PROGRAMMING_MUSCLES = [
    "Chest",
    "Back",
    "Shoulders",
    "Biceps",
    "Triceps",
    "Core",
    "Glutes",
    "Hamstrings",
    "Quads",
]

BASELINE_WINDOWS = [
    7,
    14,
    30,
    90,
]


def _window_start(
    days: int,
) -> datetime:

    return (
        datetime.now(
            timezone.utc
        )
        - timedelta(
            days=days
        )
    )


def _normalize_muscle(
    muscle: str,
) -> str | None:

    if not muscle:
        return None

    if muscle in (
        "Abs",
        "Obliques",
    ):
        return "Core"

    if muscle in PROGRAMMING_MUSCLES:
        return muscle

    return None


def _load_training_rows(
    days: int,
) -> list:

    start = _window_start(
        days
    )

    with get_conn() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    w.activity_id,
                    w.begin_time,
                    w.workout_type,

                    s.set_index,
                    s.rep_count,
                    s.volume,
                    s.movement_id,

                    m.name AS movement_name,
                    m.muscle_groups,
                    m.is_generic

                FROM tonal_workouts w

                LEFT JOIN tonal_workout_overrides o
                    ON o.activity_id = w.activity_id

                JOIN tonal_sets s
                    ON s.activity_id = w.activity_id

                LEFT JOIN tonal_movements m
                    ON m.movement_id = s.movement_id

                WHERE w.begin_time >= %s

                  AND COALESCE(
                        o.include_in_training_analysis,
                        TRUE
                      ) = TRUE

                ORDER BY
                    w.begin_time ASC,
                    s.set_index ASC
                """,
                (
                    start,
                ),
            )

            return cur.fetchall()


def _load_strength_history(
    days: int,
) -> list:

    start = _window_start(
        days
    )

    with get_conn() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    observed_at,
                    overall_score,
                    upper_score,
                    lower_score,
                    core_score
                FROM tonal_strength_scores
                WHERE observed_at >= %s
                ORDER BY observed_at ASC
                """,
                (
                    start,
                ),
            )

            return cur.fetchall()


def _strength_summary(
    rows: list,
) -> dict:

    if not rows:

        return {
            "available":
                False
        }

    first = rows[0]
    latest = rows[-1]

    def score_change(
        field: str,
    ):

        start_value = (
            first.get(
                field
            )
        )

        latest_value = (
            latest.get(
                field
            )
        )

        if (
            start_value is None
            or latest_value is None
        ):
            return None

        return round(
            float(
                latest_value
            )
            - float(
                start_value
            ),
            1,
        )

    return {
        "available":
            True,

        "observations":
            len(
                rows
            ),

        "oldest":
            first[
                "observed_at"
            ].isoformat(),

        "newest":
            latest[
                "observed_at"
            ].isoformat(),

        "latest": {
            "overall":
                float(
                    latest[
                        "overall_score"
                    ]
                ),

            "upper":
                float(
                    latest[
                        "upper_score"
                    ]
                ),

            "lower":
                float(
                    latest[
                        "lower_score"
                    ]
                ),

            "core":
                float(
                    latest[
                        "core_score"
                    ]
                ),
        },

        "change": {
            "overall":
                score_change(
                    "overall_score"
                ),

            "upper":
                score_change(
                    "upper_score"
                ),

            "lower":
                score_change(
                    "lower_score"
                ),

            "core":
                score_change(
                    "core_score"
                ),
        },
    }


def calculate_window(
    days: int,
) -> dict:

    rows = _load_training_rows(
        days
    )

    muscle_data = {}

    for muscle in PROGRAMMING_MUSCLES:

        muscle_data[
            muscle
        ] = {
            "primary_sessions":
                set(),

            "secondary_sessions":
                set(),

            "primary_sets":
                0,

            "secondary_sets":
                0,

            "primary_reps":
                0,

            "secondary_reps":
                0,

            "primary_volume":
                0.0,

            "secondary_volume":
                0.0,

            "last_primary_trained_at":
                None,

            "last_secondary_trained_at":
                None,

            "primary_exercises":
                defaultdict(
                    lambda: {
                        "sets": 0,
                        "reps": 0,
                        "volume": 0.0,
                    }
                ),

            "secondary_exercises":
                defaultdict(
                    lambda: {
                        "sets": 0,
                        "reps": 0,
                        "volume": 0.0,
                    }
                ),
        }

    generic_sets_excluded = 0
    unmapped_sets_excluded = 0

    for row in rows:

        movement_id = (
            row.get(
                "movement_id"
            )
        )

        movement_name = (
            row.get(
                "movement_name"
            )
            or "Unknown Movement"
        )

        raw_muscle_groups = (
            row.get(
                "muscle_groups"
            )
            or []
        )

        is_generic = (
            row.get(
                "is_generic"
            )
        )

        if not movement_id:
            unmapped_sets_excluded += 1
            continue

        if is_generic:
            generic_sets_excluded += 1
            continue

        if not raw_muscle_groups:
            unmapped_sets_excluded += 1
            continue

        normalized_groups = []

        for raw_muscle in raw_muscle_groups:

            normalized = (
                _normalize_muscle(
                    raw_muscle
                )
            )

            if (
                normalized
                and normalized
                not in normalized_groups
            ):
                normalized_groups.append(
                    normalized
                )

        if not normalized_groups:
            continue

        primary_muscle = (
            normalized_groups[0]
        )

        secondary_muscles = (
            normalized_groups[1:]
        )

        activity_id = (
            row[
                "activity_id"
            ]
        )

        begin_time = (
            row[
                "begin_time"
            ]
        )

        reps = (
            row.get(
                "rep_count"
            )
            or 0
        )

        volume = float(
            row.get(
                "volume"
            )
            or 0
        )

        # ====================================================
        # PRIMARY MUSCLE
        # ====================================================

        primary = (
            muscle_data[
                primary_muscle
            ]
        )

        primary[
            "primary_sessions"
        ].add(
            activity_id
        )

        primary[
            "primary_sets"
        ] += 1

        primary[
            "primary_reps"
        ] += reps

        primary[
            "primary_volume"
        ] += volume

        if (
            primary[
                "last_primary_trained_at"
            ] is None
            or begin_time
            > primary[
                "last_primary_trained_at"
            ]
        ):
            primary[
                "last_primary_trained_at"
            ] = begin_time

        exercise = (
            primary[
                "primary_exercises"
            ][
                movement_name
            ]
        )

        exercise[
            "sets"
        ] += 1

        exercise[
            "reps"
        ] += reps

        exercise[
            "volume"
        ] += volume

        # ====================================================
        # SECONDARY MUSCLES
        # ====================================================

        for muscle in secondary_muscles:

            secondary = (
                muscle_data[
                    muscle
                ]
            )

            secondary[
                "secondary_sessions"
            ].add(
                activity_id
            )

            secondary[
                "secondary_sets"
            ] += 1

            secondary[
                "secondary_reps"
            ] += reps

            secondary[
                "secondary_volume"
            ] += volume

            if (
                secondary[
                    "last_secondary_trained_at"
                ] is None
                or begin_time
                > secondary[
                    "last_secondary_trained_at"
                ]
            ):
                secondary[
                    "last_secondary_trained_at"
                ] = begin_time

            exercise = (
                secondary[
                    "secondary_exercises"
                ][
                    movement_name
                ]
            )

            exercise[
                "sets"
            ] += 1

            exercise[
                "reps"
            ] += reps

            exercise[
                "volume"
            ] += volume

    now = datetime.now(
        timezone.utc
    )

    muscles = {}

    for muscle in PROGRAMMING_MUSCLES:

        data = (
            muscle_data[
                muscle
            ]
        )

        last_primary = (
            data[
                "last_primary_trained_at"
            ]
        )

        last_secondary = (
            data[
                "last_secondary_trained_at"
            ]
        )

        if last_primary:

            primary_days_since = round(
                (
                    now
                    - last_primary
                ).total_seconds()
                / 86400,
                1,
            )

        else:
            primary_days_since = None

        if last_secondary:

            secondary_days_since = round(
                (
                    now
                    - last_secondary
                ).total_seconds()
                / 86400,
                1,
            )

        else:
            secondary_days_since = None

        primary_exercises = {}

        for (
            exercise_name,
            values,
        ) in data[
            "primary_exercises"
        ].items():

            primary_exercises[
                exercise_name
            ] = {
                "sets":
                    values[
                        "sets"
                    ],

                "reps":
                    values[
                        "reps"
                    ],

                "volume":
                    round(
                        values[
                            "volume"
                        ],
                        1,
                    ),
            }

        secondary_exercises = {}

        for (
            exercise_name,
            values,
        ) in data[
            "secondary_exercises"
        ].items():

            secondary_exercises[
                exercise_name
            ] = {
                "sets":
                    values[
                        "sets"
                    ],

                "reps":
                    values[
                        "reps"
                    ],

                "volume":
                    round(
                        values[
                            "volume"
                        ],
                        1,
                    ),
            }

        muscles[
            muscle
        ] = {
            "primary_sessions":
                len(
                    data[
                        "primary_sessions"
                    ]
                ),

            "secondary_sessions":
                len(
                    data[
                        "secondary_sessions"
                    ]
                ),

            "primary_sets":
                data[
                    "primary_sets"
                ],

            "secondary_sets":
                data[
                    "secondary_sets"
                ],

            "primary_reps":
                data[
                    "primary_reps"
                ],

            "secondary_reps":
                data[
                    "secondary_reps"
                ],

            "primary_volume":
                round(
                    data[
                        "primary_volume"
                    ],
                    1,
                ),

            "secondary_volume":
                round(
                    data[
                        "secondary_volume"
                    ],
                    1,
                ),

            "last_primary_trained_at":
                (
                    last_primary.isoformat()
                    if last_primary
                    else None
                ),

            "days_since_primary_training":
                primary_days_since,

            "last_secondary_trained_at":
                (
                    last_secondary.isoformat()
                    if last_secondary
                    else None
                ),

            "days_since_secondary_training":
                secondary_days_since,

            "primary_exercises":
                primary_exercises,

            "secondary_exercises":
                secondary_exercises,
        }

    strength_rows = (
        _load_strength_history(
            days
        )
    )

    return {
        "days":
            days,

        "training_sets":
            len(
                rows
            ),

        "generic_sets_excluded":
            generic_sets_excluded,

        "unmapped_sets_excluded":
            unmapped_sets_excluded,

        "muscles":
            muscles,

        "strength_scores":
            _strength_summary(
                strength_rows
            ),

        "methodology_note":
            (
                "The first Tonal muscle group listed "
                "for a movement is treated as the "
                "primary training target. Remaining "
                "muscle groups are tracked as secondary "
                "supporting workload and do not satisfy "
                "the direct weekly frequency target."
            ),
    }


def strength_analytics() -> dict:

    windows = {}

    for days in BASELINE_WINDOWS:

        windows[
            str(
                days
            )
        ] = calculate_window(
            days
        )

    latest = (
        windows[
            "7"
        ][
            "strength_scores"
        ]
    )

    return {
        "status":
            "ok",

        "methodology": {
            "training_source":
                (
                    "Tonal workouts and sets "
                    "stored in Supabase."
                ),

            "primary_frequency":
                (
                    "Only the first Tonal muscle "
                    "group listed for a movement "
                    "counts toward the direct "
                    "2x/week frequency goal."
                ),

            "secondary_workload":
                (
                    "Secondary Tonal muscle groups "
                    "are retained as supporting "
                    "workload but do not satisfy "
                    "direct frequency."
                ),

            "core_mapping":
                (
                    "Tonal Abs and Obliques are "
                    "combined into the Core "
                    "programming category."
                ),

            "excluded_workouts":
                (
                    "Any workout explicitly marked "
                    "include_in_training_analysis = false "
                    "is excluded."
                ),

            "generic_movements":
                (
                    "Generic Tonal movements without "
                    "reliable classification are excluded."
                ),

            "windows":
                BASELINE_WINDOWS,
        },

        "latest_strength_scores":
            (
                latest.get(
                    "latest"
                )
                if latest.get(
                    "available"
                )
                else None
            ),

        "windows":
            windows,
    }


def compact_summary():

    result = (
        strength_analytics()
    )

    print(
        "\nTONAL PRIMARY / SECONDARY ANALYTICS"
    )

    print(
        "=" * 78
    )

    latest = (
        result.get(
            "latest_strength_scores"
        )
    )

    if latest:

        print(
            f"Overall: {latest['overall']:.0f} | "
            f"Upper: {latest['upper']:.0f} | "
            f"Lower: {latest['lower']:.0f} | "
            f"Core: {latest['core']:.0f}"
        )

    print(
        "\n7-DAY MUSCLE COVERAGE"
    )

    muscles = (
        result[
            "windows"
        ][
            "7"
        ][
            "muscles"
        ]
    )

    for muscle in PROGRAMMING_MUSCLES:

        data = (
            muscles[
                muscle
            ]
        )

        print(
            f"{muscle:<12} "
            f"primary={data['primary_sessions']:<2} "
            f"secondary={data['secondary_sessions']:<2} "
            f"primary_sets={data['primary_sets']:<3} "
            f"last_primary="
            f"{data['days_since_primary_training']}"
        )

    print(
        "=" * 78
    )

    print(
        "\nFULL JSON"
    )

    print(
        json.dumps(
            result,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":

    compact_summary()
