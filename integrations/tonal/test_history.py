import getpass
import json
from datetime import datetime

import requests


# ============================================================
# TONAL CONFIGURATION
# ============================================================

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

    if response.status_code == 401:
        raise RuntimeError(
            "Tonal rejected the email or password."
        )

    if response.status_code == 403:
        raise RuntimeError(
            "Tonal denied access. "
            "The account may require verification."
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
# STRENGTH SCORE HISTORY
# ============================================================

def get_strength_score_history(
    id_token: str,
    user_id: str,
) -> list:

    today = datetime.now().strftime(
        "%Y-%m-%d"
    )

    response = requests.get(
        (
            f"{API_BASE}/v6/users/"
            f"{user_id}/strength-scores/history"
        ),
        headers={
            "Authorization":
                f"Bearer {id_token}"
        },
        params={
            "limit": 5000,
            "endDate": today,
        },
        timeout=30,
    )

    if response.status_code != 200:
        raise RuntimeError(
            "Could not retrieve Strength Score history. "
            f"HTTP {response.status_code}: "
            f"{response.text[:300]}"
        )

    data = response.json()

    if not isinstance(data, list):
        raise RuntimeError(
            "Unexpected Strength Score history response."
        )

    return data


# ============================================================
# COMPLETE WORKOUT HISTORY
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
                "Unexpected workout history response."
            )

        workouts.extend(
            page
        )

        print(
            f"Retrieved {len(workouts)} workouts..."
        )

        # Tonal may expose the total record count
        # through the pg-total response header.
        total_header = response.headers.get(
            "pg-total"
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

        # Fallback termination condition.
        if len(page) < page_size:
            break

        if len(page) == 0:
            break

        offset += page_size

    # Tonal does not necessarily return
    # workout activities newest-first.
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
# WORKOUT TEMPLATE
# ============================================================

def get_workout_template(
    id_token: str,
    workout_id: str,
) -> dict | None:

    if not workout_id:
        return None

    # Custom workouts commonly use this placeholder ID.
    if (
        workout_id
        == "00000000-0000-0000-0000-000000000001"
    ):
        return None

    response = requests.get(
        (
            f"{API_BASE}/v6/workouts/"
            f"{workout_id}"
        ),
        headers={
            "Authorization":
                f"Bearer {id_token}"
        },
        timeout=30,
    )

    if response.status_code != 200:
        return None

    try:
        return response.json()

    except ValueError:
        return None


# ============================================================
# SET SUMMARY
# ============================================================

def summarize_set(
    set_data: dict,
) -> dict:

    important_fields = [
        "movementId",
        "movementName",
        "prescribedReps",
        "repCount",
        "baseWeight",
        "suggestedWeight",
        "avgWeight",
        "maxWeight",
        "volume",
        "oneRepMax",
        "repsInReserve",
        "strugglingScore",
        "rom",
        "duration",
        "inconsistencyScore",
        "spotter",
        "eccentric",
        "chains",
        "flex",
        "progressive",
        "burnout",
        "maxConPower",
    ]

    result = {}

    for field in important_fields:

        if field in set_data:

            result[field] = (
                set_data.get(
                    field
                )
            )

    return result


# ============================================================
# WORKOUT SUMMARY
# ============================================================

def summarize_workout(
    workout: dict,
    template: dict | None,
) -> dict:

    title = None

    if template:

        title = (
            template.get(
                "title"
            )
            or template.get(
                "name"
            )
            or template.get(
                "displayName"
            )
            or template.get(
                "workoutTitle"
            )
        )

    sets = workout.get(
        "workoutSetActivity",
        [],
    )

    begin_time = workout.get(
        "beginTime"
    )

    end_time = workout.get(
        "endTime"
    )

    return {
        "activity_id":
            workout.get(
                "id"
            )
            or workout.get(
                "workoutActivityID"
            ),

        "workout_id":
            workout.get(
                "workoutId"
            ),

        "workout_title":
            title,

        "workout_type":
            workout.get(
                "workoutType"
            ),

        "begin_time":
            begin_time,

        "end_time":
            end_time,

        "total_reps":
            workout.get(
                "totalReps"
            ),

        "total_volume":
            workout.get(
                "totalVolume"
            ),

        "set_count":
            len(
                sets
            ),

        "movement_ids":
            sorted(
                list(
                    {
                        x.get(
                            "movementId"
                        )
                        for x in sets
                        if x.get(
                            "movementId"
                        )
                    }
                )
            ),

        "sets": [
            summarize_set(
                x
            )
            for x in sets
        ],
    }


# ============================================================
# COMPACT WORKOUT SUMMARY
# ============================================================

def compact_workout_summary(
    workout: dict,
) -> dict:

    sets = workout.get(
        "workoutSetActivity",
        [],
    )

    movement_ids = sorted(
        list(
            {
                x.get(
                    "movementId"
                )
                for x in sets
                if x.get(
                    "movementId"
                )
            }
        )
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

        "workout_id":
            workout.get(
                "workoutId"
            ),

        "total_reps":
            workout.get(
                "totalReps"
            ),

        "total_volume":
            workout.get(
                "totalVolume"
            ),

        "set_count":
            len(
                sets
            ),

        "movement_count":
            len(
                movement_ids
            ),

        "movement_ids":
            movement_ids,
    }


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "\nTonal History Test"
    )

    print(
        "This test retrieves your Strength Score "
        "history and complete Tonal workout history."
    )

    print(
        "Your credentials are entered locally "
        "and are not saved.\n"
    )

    email = input(
        "Tonal email: "
    ).strip()

    password = getpass.getpass(
        "Tonal password: "
    )

    print(
        "\nAuthenticating with Tonal..."
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
            "Authentication succeeded, "
            "but no id_token was returned."
        )

    print(
        "Authentication successful."
    )

    # --------------------------------------------------------
    # USER
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # STRENGTH SCORE HISTORY
    # --------------------------------------------------------

    print(
        "\nRetrieving Strength Score history..."
    )

    strength_history = (
        get_strength_score_history(
            id_token,
            user_id,
        )
    )

    print(
        "Strength Score entries: "
        f"{len(strength_history)}"
    )

    if strength_history:

        print(
            "First Strength Score record date:"
        )

        print(
            strength_history[0].get(
                "date"
            )
            or strength_history[0].get(
                "createdAt"
            )
            or strength_history[0].get(
                "updatedAt"
            )
            or "Date field not identified"
        )

    # --------------------------------------------------------
    # WORKOUT HISTORY
    # --------------------------------------------------------

    print(
        "\nRetrieving complete workout history..."
    )

    all_workouts = (
        get_all_workouts(
            id_token,
            user_id,
        )
    )

    print(
        "\nWORKOUT HISTORY RESULT"
    )

    print(
        f"Total workouts retrieved: "
        f"{len(all_workouts)}"
    )

    if not all_workouts:

        print(
            "No Tonal workouts were returned."
        )

        return

    newest_workout = (
        all_workouts[0]
    )

    oldest_workout = (
        all_workouts[-1]
    )

    print(
        "Newest workout date: "
        f"{newest_workout.get('beginTime')}"
    )

    print(
        "Oldest workout date: "
        f"{oldest_workout.get('beginTime')}"
    )

    # --------------------------------------------------------
    # LATEST FIVE
    # --------------------------------------------------------

    latest_five = (
        all_workouts[:5]
    )

    print(
        "\nLATEST 5 WORKOUTS"
    )

    compact = [
        compact_workout_summary(
            workout
        )
        for workout in latest_five
    ]

    print(
        json.dumps(
            compact,
            indent=2,
            default=str,
        )
    )

    # --------------------------------------------------------
    # DETAILED NEWEST WORKOUT
    # --------------------------------------------------------

    print(
        "\nNEWEST WORKOUT DETAIL"
    )

    newest_workout_id = (
        newest_workout.get(
            "workoutId"
        )
    )

    template = (
        get_workout_template(
            id_token,
            newest_workout_id,
        )
        if newest_workout_id
        else None
    )

    newest_summary = (
        summarize_workout(
            newest_workout,
            template,
        )
    )

    print(
        json.dumps(
            newest_summary,
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
