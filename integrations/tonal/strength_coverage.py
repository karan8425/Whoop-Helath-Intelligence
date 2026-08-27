import getpass
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import requests


AUTH0_DOMAIN = "tonal.auth0.com"
CLIENT_ID = "ERCyexW-xoVG_Yy3RDe-eV4xsOnRHP6L"
API_BASE = "https://api.tonal.com"


# ============================================================
# STRENGTH GOAL
# ============================================================

TARGET_FREQUENCY_PER_7_DAYS = 2

TARGET_MUSCLES = [
    "Chest",
    "Back",
    "Shoulders",
    "Biceps",
    "Triceps",
    "Abs",
    "Obliques",
    "Glutes",
    "Hamstrings",
    "Quads",
    "Forearms",
]


# ============================================================
# AUTHENTICATION
# ============================================================

def authenticate(
    email: str,
    password: str,
) -> dict:

    response = requests.post(
        f"https://{AUTH0_DOMAIN}/oauth/token",
        json={
            "grant_type": "password",
            "client_id": CLIENT_ID,
            "username": email,
            "password": password,
            "scope":
                "openid profile email offline_access",
        },
        timeout=30,
    )

    if response.status_code != 200:
        raise RuntimeError(
            "Tonal authentication failed with "
            f"HTTP {response.status_code}: "
            f"{response.text[:300]}"
        )

    return response.json()


# ============================================================
# USER INFORMATION
# ============================================================

def get_user_info(
    id_token: str,
) -> dict:

    response = requests.get(
        f"{API_BASE}/v6/users/userinfo",
        headers={
            "Authorization":
                f"Bearer {id_token}"
        },
        timeout=30,
    )

    if response.status_code != 200:
        raise RuntimeError(
            "Could not retrieve Tonal user information. "
            f"HTTP {response.status_code}: "
            f"{response.text[:300]}"
        )

    return response.json()


# ============================================================
# MOVEMENT CATALOG
# ============================================================

def get_movement_catalog(
    id_token: str,
) -> list:

    response = requests.get(
        f"{API_BASE}/v6/movements",
        headers={
            "Authorization":
                f"Bearer {id_token}"
        },
        timeout=30,
    )

    if response.status_code != 200:
        raise RuntimeError(
            "Could not retrieve Tonal movement catalog. "
            f"HTTP {response.status_code}: "
            f"{response.text[:300]}"
        )

    data = response.json()

    if not isinstance(data, list):
        raise RuntimeError(
            "Unexpected movement catalog response."
        )

    return data


def build_movement_map(
    movements: list,
) -> dict:

    movement_map = {}

    for movement in movements:

        movement_id = movement.get(
            "id"
        )

        if not movement_id:
            continue

        machine_info = (
            movement.get(
                "onMachineInfo"
            )
            or {}
        )

        movement_map[movement_id] = {
            "name":
                movement.get(
                    "name"
                ),

            "muscle_groups":
                movement.get(
                    "muscleGroups"
                )
                or [],

            "is_generic":
                movement.get(
                    "isGeneric"
                )
                or False,

            "accessory":
                machine_info.get(
                    "accessory"
                ),
        }

    return movement_map


# ============================================================
# WORKOUT HISTORY
# ============================================================

def get_all_workouts(
    id_token: str,
    user_id: str,
) -> list:

    url = (
        f"{API_BASE}/v6/users/"
        f"{user_id}/workout-activities"
    )

    page_size = 100
    offset = 0
    workouts = []

    while True:

        response = requests.get(
            url,
            headers={
                "Authorization":
                    f"Bearer {id_token}",

                "pg-offset":
                    str(offset),

                "pg-limit":
                    str(page_size),
            },
            timeout=30,
        )

        if response.status_code != 200:
            raise RuntimeError(
                "Could not retrieve Tonal workout history. "
                f"HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )

        page = response.json()

        if not isinstance(page, list):
            raise RuntimeError(
                "Unexpected workout history response."
            )

        workouts.extend(
            page
        )

        total_header = (
            response.headers.get(
                "pg-total"
            )
        )

        if total_header:

            try:
                total = int(
                    total_header
                )

                if len(workouts) >= total:
                    break

            except ValueError:
                pass

        if len(page) < page_size:
            break

        if not page:
            break

        offset += page_size

    workouts.sort(
        key=lambda workout:
            workout.get(
                "beginTime",
                "",
            ),
        reverse=True,
    )

    return workouts


# ============================================================
# DATE HELPERS
# ============================================================

def parse_tonal_date(
    value: str,
):

    if not value:
        return None

    try:
        return datetime.fromisoformat(
            value.replace(
                "Z",
                "+00:00",
            )
        )

    except ValueError:
        return None


# ============================================================
# COVERAGE ENGINE
# ============================================================

def calculate_strength_coverage(
    workouts: list,
    movement_map: dict,
    days: int = 7,
) -> dict:

    now = datetime.now(
        timezone.utc
    )

    window_start = (
        now
        - timedelta(
            days=days
        )
    )

    metrics = {}

    for muscle in TARGET_MUSCLES:

        metrics[muscle] = {
            "sessions_hit": 0,
            "sets": 0,
            "reps": 0,
            "attributed_volume": 0.0,
            "last_trained_at": None,
            "exercises": defaultdict(
                lambda: {
                    "sets": 0,
                    "reps": 0,
                    "volume": 0.0,
                }
            ),
        }

    workouts_in_window = 0
    generic_sets_excluded = 0
    unknown_movement_sets = 0

    for workout in workouts:

        begin_time = parse_tonal_date(
            workout.get(
                "beginTime"
            )
        )

        if not begin_time:
            continue

        if begin_time < window_start:
            continue

        if begin_time > now:
            continue

        workouts_in_window += 1

        muscles_hit_this_workout = set()

        sets = workout.get(
            "workoutSetActivity",
            [],
        )

        for set_data in sets:

            movement_id = set_data.get(
                "movementId"
            )

            if not movement_id:
                continue

            movement = movement_map.get(
                movement_id
            )

            if not movement:
                unknown_movement_sets += 1
                continue

            if movement.get(
                "is_generic"
            ):
                generic_sets_excluded += 1
                continue

            muscle_groups = (
                movement.get(
                    "muscle_groups"
                )
                or []
            )

            exercise_name = (
                movement.get(
                    "name"
                )
                or "Unknown Movement"
            )

            reps = (
                set_data.get(
                    "repCount"
                )
                or 0
            )

            volume = float(
                set_data.get(
                    "volume"
                )
                or 0
            )

            for muscle in muscle_groups:

                if muscle not in metrics:
                    continue

                muscles_hit_this_workout.add(
                    muscle
                )

                metrics[
                    muscle
                ][
                    "sets"
                ] += 1

                metrics[
                    muscle
                ][
                    "reps"
                ] += reps

                metrics[
                    muscle
                ][
                    "attributed_volume"
                ] += volume

                exercise = (
                    metrics[
                        muscle
                    ][
                        "exercises"
                    ][
                        exercise_name
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

                last_trained = (
                    metrics[
                        muscle
                    ][
                        "last_trained_at"
                    ]
                )

                if (
                    last_trained is None
                    or begin_time
                    > last_trained
                ):
                    metrics[
                        muscle
                    ][
                        "last_trained_at"
                    ] = begin_time

        # A muscle only counts once per workout,
        # even if several exercises trained it.
        for muscle in muscles_hit_this_workout:

            metrics[
                muscle
            ][
                "sessions_hit"
            ] += 1

    result = {}

    for muscle in TARGET_MUSCLES:

        data = metrics[
            muscle
        ]

        sessions = data[
            "sessions_hit"
        ]

        if sessions == 0:
            status = "high_priority"

        elif sessions == 1:
            status = "priority"

        elif sessions == 2:
            status = "on_track"

        else:
            status = "well_covered"

        last_trained = data[
            "last_trained_at"
        ]

        if last_trained:

            hours_since = (
                now
                - last_trained
            ).total_seconds() / 3600

            days_since = round(
                hours_since / 24,
                1,
            )

            last_trained_string = (
                last_trained
                .isoformat()
            )

        else:
            days_since = None
            last_trained_string = None

        exercises = {}

        for (
            exercise_name,
            exercise_data,
        ) in data[
            "exercises"
        ].items():

            exercises[
                exercise_name
            ] = {
                "sets":
                    exercise_data[
                        "sets"
                    ],

                "reps":
                    exercise_data[
                        "reps"
                    ],

                "volume":
                    round(
                        exercise_data[
                            "volume"
                        ],
                        1,
                    ),
            }

        result[
            muscle
        ] = {
            "sessions_hit":
                sessions,

            "target_sessions":
                TARGET_FREQUENCY_PER_7_DAYS,

            "status":
                status,

            "sets":
                data[
                    "sets"
                ],

            "reps":
                data[
                    "reps"
                ],

            "attributed_volume":
                round(
                    data[
                        "attributed_volume"
                    ],
                    1,
                ),

            "last_trained_at":
                last_trained_string,

            "days_since_last_trained":
                days_since,

            "exercises":
                exercises,
        }

    priority_order = sorted(
        TARGET_MUSCLES,
        key=lambda muscle: (
            result[
                muscle
            ][
                "sessions_hit"
            ],

            -(
                result[
                    muscle
                ][
                    "days_since_last_trained"
                ]
                or 999
            ),
        ),
    )

    return {
        "status":
            "ok",

        "window_days":
            days,

        "window_start":
            window_start.isoformat(),

        "calculated_at":
            now.isoformat(),

        "target_frequency_per_muscle":
            TARGET_FREQUENCY_PER_7_DAYS,

        "workouts_in_window":
            workouts_in_window,

        "generic_sets_excluded":
            generic_sets_excluded,

        "unknown_movement_sets":
            unknown_movement_sets,

        "priority_order":
            priority_order,

        "muscles":
            result,

        "interpretation_note":
            (
                "Compound-movement volume is attributed "
                "to every Tonal muscle group listed for "
                "that movement, so muscle-level volumes "
                "should not be summed into total workout volume."
            ),
    }


# ============================================================
# COMPACT SUMMARY
# ============================================================

def compact_summary(
    coverage: dict,
):

    print(
        "\nSTRENGTH COVERAGE - LAST 7 DAYS"
    )

    print(
        "=" * 70
    )

    for muscle in coverage[
        "priority_order"
    ]:

        data = (
            coverage[
                "muscles"
            ][
                muscle
            ]
        )

        sessions = (
            data[
                "sessions_hit"
            ]
        )

        target = (
            data[
                "target_sessions"
            ]
        )

        status = (
            data[
                "status"
            ]
        )

        days_since = (
            data[
                "days_since_last_trained"
            ]
        )

        if days_since is None:
            last_text = "not trained in window"
        else:
            last_text = (
                f"{days_since} days ago"
            )

        print(
            f"{muscle:<12} "
            f"{sessions}/{target}   "
            f"{status:<14} "
            f"sets={data['sets']:<3} "
            f"last={last_text}"
        )

    print(
        "=" * 70
    )

    print(
        "\nPriority order:"
    )

    print(
        " → ".join(
            coverage[
                "priority_order"
            ]
        )
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "\nTonal Strength Coverage Engine"
    )

    print(
        "Goal: train each major muscle group "
        "approximately twice per rolling 7 days."
    )

    print(
        "Credentials are entered locally "
        "and are not saved.\n"
    )

    email = input(
        "Tonal email: "
    ).strip()

    password = getpass.getpass(
        "Tonal password: "
    )

    print(
        "\nAuthenticating..."
    )

    tokens = authenticate(
        email,
        password,
    )

    id_token = tokens.get(
        "id_token"
    )

    if not id_token:

        raise RuntimeError(
            "Authentication succeeded "
            "but no id_token was returned."
        )

    print(
        "Authentication successful."
    )

    user_info = get_user_info(
        id_token
    )

    user_id = (
        user_info.get(
            "id"
        )
        or user_info.get(
            "userId"
        )
    )

    if not user_id:

        raise RuntimeError(
            "Could not determine Tonal user ID."
        )

    print(
        "Retrieving movement catalog..."
    )

    movements = (
        get_movement_catalog(
            id_token
        )
    )

    movement_map = (
        build_movement_map(
            movements
        )
    )

    print(
        f"Movement catalog: "
        f"{len(movements)} movements"
    )

    print(
        "Retrieving workout history..."
    )

    workouts = (
        get_all_workouts(
            id_token,
            user_id,
        )
    )

    print(
        f"Workout history: "
        f"{len(workouts)} workouts"
    )

    coverage = (
        calculate_strength_coverage(
            workouts,
            movement_map,
            days=7,
        )
    )

    compact_summary(
        coverage
    )

    print(
        "\nFULL JSON"
    )

    print(
        json.dumps(
            coverage,
            indent=2,
            default=str,
        )
    )

    print(
        "\nNo Tonal password or token "
        "was written to disk."
    )


if __name__ == "__main__":
    main()
