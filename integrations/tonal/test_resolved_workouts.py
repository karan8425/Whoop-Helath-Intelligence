import getpass
import json

import requests


AUTH0_DOMAIN = "tonal.auth0.com"
CLIENT_ID = "ERCyexW-xoVG_Yy3RDe-eV4xsOnRHP6L"
API_BASE = "https://api.tonal.com"


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
# USER
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
            "Could not retrieve movement catalog. "
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

        movement_map[
            movement_id
        ] = {
            "name":
                movement.get(
                    "name"
                ),

            "short_name":
                movement.get(
                    "shortName"
                ),

            "muscle_groups":
                movement.get(
                    "muscleGroups"
                )
                or [],

            "accessory":
                machine_info.get(
                    "accessory"
                ),

            "is_generic":
                movement.get(
                    "isGeneric"
                ),

            "custom_movement":
                movement.get(
                    "customMovement"
                ),

            "is_bilateral":
                movement.get(
                    "isBilateral"
                ),

            "is_two_sided":
                movement.get(
                    "isTwoSided"
                ),

            "is_alternating":
                movement.get(
                    "isAlternating"
                ),

            "skill_level":
                movement.get(
                    "skillLevel"
                ),

            "description_why":
                movement.get(
                    "descriptionWhy"
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
                "Could not retrieve workout history. "
                f"HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )

        page = response.json()

        if not isinstance(page, list):
            raise RuntimeError(
                "Unexpected workout response."
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
        key=lambda x:
            x.get(
                "beginTime",
                "",
            ),
        reverse=True,
    )

    return workouts


# ============================================================
# RESOLVE WORKOUT
# ============================================================

def resolve_workout(
    workout: dict,
    movement_map: dict,
) -> dict:

    raw_sets = workout.get(
        "workoutSetActivity",
        [],
    )

    exercises = {}

    for set_data in raw_sets:

        movement_id = (
            set_data.get(
                "movementId"
            )
        )

        if not movement_id:
            continue

        movement = (
            movement_map.get(
                movement_id,
                {
                    "name":
                        "Unknown Movement",

                    "muscle_groups":
                        [],

                    "accessory":
                        None,

                    "is_generic":
                        None,
                },
            )
        )

        if movement_id not in exercises:

            exercises[
                movement_id
            ] = {
                "movement_id":
                    movement_id,

                "name":
                    movement.get(
                        "name"
                    ),

                "muscle_groups":
                    movement.get(
                        "muscle_groups",
                        [],
                    ),

                "accessory":
                    movement.get(
                        "accessory"
                    ),

                "is_generic":
                    movement.get(
                        "is_generic"
                    ),

                "sets":
                    0,

                "total_reps":
                    0,

                "total_volume":
                    0.0,

                "working_weights":
                    [],

                "suggested_weights":
                    [],

                "one_rep_max_values":
                    [],

                "rir_values":
                    [],
            }

        exercise = (
            exercises[
                movement_id
            ]
        )

        exercise["sets"] += 1

        reps = (
            set_data.get(
                "repCount"
            )
            or 0
        )

        exercise[
            "total_reps"
        ] += reps

        volume = (
            set_data.get(
                "volume"
            )
            or 0
        )

        exercise[
            "total_volume"
        ] += float(
            volume
        )

        base_weight = (
            set_data.get(
                "baseWeight"
            )
        )

        if base_weight is not None:

            exercise[
                "working_weights"
            ].append(
                base_weight
            )

        suggested_weight = (
            set_data.get(
                "suggestedWeight"
            )
        )

        if suggested_weight is not None:

            exercise[
                "suggested_weights"
            ].append(
                suggested_weight
            )

        one_rep_max = (
            set_data.get(
                "oneRepMax"
            )
        )

        if one_rep_max is not None:

            exercise[
                "one_rep_max_values"
            ].append(
                one_rep_max
            )

        rir = (
            set_data.get(
                "repsInReserve"
            )
        )

        if rir is not None:

            exercise[
                "rir_values"
            ].append(
                rir
            )

    resolved = []

    for exercise in exercises.values():

        weights = (
            exercise.pop(
                "working_weights"
            )
        )

        suggested = (
            exercise.pop(
                "suggested_weights"
            )
        )

        one_rep_max_values = (
            exercise.pop(
                "one_rep_max_values"
            )
        )

        rir_values = (
            exercise.pop(
                "rir_values"
            )
        )

        exercise[
            "total_volume"
        ] = round(
            exercise[
                "total_volume"
            ],
            1,
        )

        exercise[
            "min_weight"
        ] = (
            min(weights)
            if weights
            else None
        )

        exercise[
            "max_weight"
        ] = (
            max(weights)
            if weights
            else None
        )

        exercise[
            "latest_suggested_weight"
        ] = (
            suggested[-1]
            if suggested
            else None
        )

        exercise[
            "best_estimated_1rm"
        ] = (
            round(
                max(
                    one_rep_max_values
                ),
                1,
            )
            if one_rep_max_values
            else None
        )

        exercise[
            "average_rir"
        ] = (
            round(
                sum(
                    rir_values
                )
                / len(
                    rir_values
                ),
                2,
            )
            if rir_values
            else None
        )

        resolved.append(
            exercise
        )

    return {
        "begin_time":
            workout.get(
                "beginTime"
            ),

        "end_time":
            workout.get(
                "endTime"
            ),

        "workout_type":
            workout.get(
                "workoutType"
            ),

        "total_reps":
            workout.get(
                "totalReps"
            ),

        "total_volume":
            workout.get(
                "totalVolume"
            ),

        "exercise_count":
            len(
                resolved
            ),

        "exercises":
            resolved,
    }


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "\nTonal Resolved Workout Test"
    )

    print(
        "This test combines workout history "
        "with the Tonal movement catalog."
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

    id_token = (
        tokens.get(
            "id_token"
        )
    )

    if not id_token:

        raise RuntimeError(
            "Authentication succeeded "
            "but no id_token was returned."
        )

    print(
        "Authentication successful."
    )

    user_info = (
        get_user_info(
            id_token
        )
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

    print(
        f"Movement catalog: "
        f"{len(movements)} movements"
    )

    movement_map = (
        build_movement_map(
            movements
        )
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

    if not workouts:

        print(
            "No workouts found."
        )

        return

    latest_five = (
        workouts[:5]
    )

    resolved = [
        resolve_workout(
            workout,
            movement_map,
        )
        for workout in latest_five
    ]

    print(
        "\nLATEST 5 RESOLVED WORKOUTS"
    )

    print(
        json.dumps(
            resolved,
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
