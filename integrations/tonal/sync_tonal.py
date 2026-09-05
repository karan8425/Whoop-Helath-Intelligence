import getpass
import json
from datetime import datetime

import requests

from db import get_conn
from integrations.tonal.client import (
    authenticate as shared_authenticate,
    tonal_get as shared_tonal_get,
)


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

    return shared_authenticate(email, password)


# ============================================================
# TONAL API
# ============================================================

def tonal_get(
    id_token: str,
    path: str,
    *,
    headers=None,
) -> requests.Response:

    return shared_tonal_get(id_token, path, headers=headers)


def get_user_info(
    id_token: str,
) -> dict:

    response = tonal_get(
        id_token,
        "/v6/users/userinfo",
    )

    if response.status_code != 200:
        raise RuntimeError(
            "Could not retrieve Tonal user info. "
            f"HTTP {response.status_code}."
        )

    return response.json()


def get_movements(
    id_token: str,
) -> list:

    response = tonal_get(
        id_token,
        "/v6/movements",
    )

    if response.status_code != 200:
        raise RuntimeError(
            "Could not retrieve Tonal movements. "
            f"HTTP {response.status_code}."
        )

    data = response.json()

    if not isinstance(
        data,
        list,
    ):
        raise RuntimeError(
            "Unexpected Tonal movement response."
        )

    return data


def get_all_workouts(
    id_token: str,
    user_id: str,
) -> list:

    page_size = 100
    offset = 0
    workouts = []

    path = (
        f"/v6/users/"
        f"{user_id}/workout-activities"
    )

    while True:

        response = tonal_get(
            id_token,
            path,
            headers={
                "pg-offset":
                    str(offset),

                "pg-limit":
                    str(page_size),
            },
        )

        if response.status_code != 200:
            raise RuntimeError(
                "Could not retrieve Tonal workouts. "
                f"HTTP {response.status_code}."
            )

        page = response.json()

        if not isinstance(
            page,
            list,
        ):
            raise RuntimeError(
                "Unexpected Tonal workout response."
            )

        workouts.extend(
            page
        )

        print(
            f"Retrieved "
            f"{len(workouts)} workouts..."
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

                if len(
                    workouts
                ) >= total:
                    break

            except ValueError:
                pass

        if not page:
            break

        if len(
            page
        ) < page_size:
            break

        offset += page_size

    workouts.sort(
        key=lambda workout:
            workout.get(
                "beginTime",
                "",
            ),
    )

    return workouts


# ============================================================
# HELPERS
# ============================================================

def json_value(
    value,
) -> str:

    return json.dumps(
        value,
        default=str,
    )


def workout_activity_id(
    workout: dict,
):

    return (
        workout.get("id")
        or workout.get(
            "workoutActivityID"
        )
    )


def workout_duration_seconds(
    workout: dict,
):

    duration = workout.get(
        "duration"
    )

    if duration is not None:

        try:
            return int(
                round(
                    float(
                        duration
                    )
                )
            )

        except (
            TypeError,
            ValueError,
        ):
            pass

    begin = workout.get(
        "beginTime"
    )

    end = workout.get(
        "endTime"
    )

    if (
        not begin
        or not end
    ):
        return None

    try:

        begin_dt = (
            datetime
            .fromisoformat(
                begin.replace(
                    "Z",
                    "+00:00",
                )
            )
        )

        end_dt = (
            datetime
            .fromisoformat(
                end.replace(
                    "Z",
                    "+00:00",
                )
            )
        )

        return int(
            (
                end_dt
                - begin_dt
            ).total_seconds()
        )

    except (
        TypeError,
        ValueError,
    ):
        return None


def movement_ids_for_workout(
    workout: dict,
) -> list:

    movement_ids = []

    for set_data in workout.get(
        "workoutSetActivity",
        [],
    ):

        movement_id = (
            set_data.get(
                "movementId"
            )
        )

        if (
            movement_id
            and movement_id
            not in movement_ids
        ):
            movement_ids.append(
                movement_id
            )

    return movement_ids


# ============================================================
# MOVEMENT SYNC
# ============================================================

def sync_movements(
    movements: list,
) -> int:

    count = 0

    with get_conn() as conn:

        with conn.cursor() as cur:

            for movement in movements:

                movement_id = (
                    movement.get(
                        "id"
                    )
                )

                if not movement_id:
                    continue

                machine_info = (
                    movement.get(
                        "onMachineInfo"
                    )
                    or {}
                )

                cur.execute(
                    """
                    INSERT INTO tonal_movements (
                        movement_id,
                        name,
                        short_name,
                        muscle_groups,
                        accessory,
                        in_free_lift,
                        on_machine,
                        count_reps,
                        hide_reps,
                        is_two_sided,
                        is_bilateral,
                        is_alternating,
                        is_generic,
                        custom_movement,
                        hidden_in_move_picker,
                        skill_level,
                        publish_state,
                        description_how,
                        description_why,
                        thumbnail_media_url,
                        compatibility_status,
                        on_machine_info,
                        feature_group_ids,
                        related_generic_movement_ids,
                        tonal_created_at,
                        tonal_updated_at,
                        raw_data,
                        synced_at
                    )
                    VALUES (
                        %s,
                        %s,
                        %s,
                        %s::jsonb,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s::jsonb,
                        %s::jsonb,
                        %s::jsonb,
                        %s::jsonb,
                        %s,
                        %s,
                        %s::jsonb,
                        NOW()
                    )
                    ON CONFLICT (
                        movement_id
                    )
                    DO UPDATE SET
                        name =
                            EXCLUDED.name,

                        short_name =
                            EXCLUDED.short_name,

                        muscle_groups =
                            EXCLUDED.muscle_groups,

                        accessory =
                            EXCLUDED.accessory,

                        in_free_lift =
                            EXCLUDED.in_free_lift,

                        on_machine =
                            EXCLUDED.on_machine,

                        count_reps =
                            EXCLUDED.count_reps,

                        hide_reps =
                            EXCLUDED.hide_reps,

                        is_two_sided =
                            EXCLUDED.is_two_sided,

                        is_bilateral =
                            EXCLUDED.is_bilateral,

                        is_alternating =
                            EXCLUDED.is_alternating,

                        is_generic =
                            EXCLUDED.is_generic,

                        custom_movement =
                            EXCLUDED.custom_movement,

                        hidden_in_move_picker =
                            EXCLUDED.hidden_in_move_picker,

                        skill_level =
                            EXCLUDED.skill_level,

                        publish_state =
                            EXCLUDED.publish_state,

                        description_how =
                            EXCLUDED.description_how,

                        description_why =
                            EXCLUDED.description_why,

                        thumbnail_media_url =
                            EXCLUDED.thumbnail_media_url,

                        compatibility_status =
                            EXCLUDED.compatibility_status,

                        on_machine_info =
                            EXCLUDED.on_machine_info,

                        feature_group_ids =
                            EXCLUDED.feature_group_ids,

                        related_generic_movement_ids =
                            EXCLUDED.related_generic_movement_ids,

                        tonal_created_at =
                            EXCLUDED.tonal_created_at,

                        tonal_updated_at =
                            EXCLUDED.tonal_updated_at,

                        raw_data =
                            EXCLUDED.raw_data,

                        synced_at =
                            NOW()
                    """,
                    (
                        movement_id,

                        movement.get(
                            "name"
                        ),

                        movement.get(
                            "shortName"
                        ),

                        json_value(
                            movement.get(
                                "muscleGroups"
                            )
                            or []
                        ),

                        machine_info.get(
                            "accessory"
                        ),

                        movement.get(
                            "inFreeLift"
                        ),

                        movement.get(
                            "onMachine"
                        ),

                        movement.get(
                            "countReps"
                        ),

                        movement.get(
                            "hideReps"
                        ),

                        movement.get(
                            "isTwoSided"
                        ),

                        movement.get(
                            "isBilateral"
                        ),

                        movement.get(
                            "isAlternating"
                        ),

                        movement.get(
                            "isGeneric"
                        ),

                        movement.get(
                            "customMovement"
                        ),

                        movement.get(
                            "hiddenInMovePicker"
                        ),

                        movement.get(
                            "skillLevel"
                        ),

                        movement.get(
                            "publishState"
                        ),

                        movement.get(
                            "descriptionHow"
                        ),

                        movement.get(
                            "descriptionWhy"
                        ),

                        movement.get(
                            "thumbnailMediaUrl"
                        ),

                        json_value(
                            movement.get(
                                "compatibilityStatus"
                            )
                        ),

                        json_value(
                            movement.get(
                                "onMachineInfo"
                            )
                        ),

                        json_value(
                            movement.get(
                                "featureGroupIds"
                            )
                        ),

                        json_value(
                            movement.get(
                                "relatedGenericMovementIDs"
                            )
                        ),

                        movement.get(
                            "createdAt"
                        ),

                        movement.get(
                            "updatedAt"
                        ),

                        json_value(
                            movement
                        ),
                    ),
                )

                count += 1

    return count


# ============================================================
# WORKOUT + SET SYNC
# ============================================================

def sync_workouts_and_sets(
    workouts: list,
) -> dict:

    workout_count = 0
    set_count = 0

    with get_conn() as conn:

        with conn.cursor() as cur:

            for index, workout in enumerate(
                workouts,
                start=1,
            ):

                activity_id = (
                    workout_activity_id(
                        workout
                    )
                )

                if not activity_id:
                    continue

                sets = workout.get(
                    "workoutSetActivity",
                    [],
                )

                movement_ids = (
                    movement_ids_for_workout(
                        workout
                    )
                )

                workout_title = (
                    workout.get(
                        "title"
                    )
                    or workout.get(
                        "name"
                    )
                    or workout.get(
                        "workoutTitle"
                    )
                )

                cur.execute(
                    """
                    INSERT INTO tonal_workouts (
                        activity_id,
                        workout_id,
                        workout_title,
                        workout_type,
                        begin_time,
                        end_time,
                        duration_seconds,
                        total_reps,
                        total_volume,
                        set_count,
                        movement_count,
                        movement_ids,
                        raw_data,
                        synced_at
                    )
                    VALUES (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s::jsonb,
                        %s::jsonb,
                        NOW()
                    )
                    ON CONFLICT (
                        activity_id
                    )
                    DO UPDATE SET
                        workout_id =
                            EXCLUDED.workout_id,

                        workout_title =
                            EXCLUDED.workout_title,

                        workout_type =
                            EXCLUDED.workout_type,

                        begin_time =
                            EXCLUDED.begin_time,

                        end_time =
                            EXCLUDED.end_time,

                        duration_seconds =
                            EXCLUDED.duration_seconds,

                        total_reps =
                            EXCLUDED.total_reps,

                        total_volume =
                            EXCLUDED.total_volume,

                        set_count =
                            EXCLUDED.set_count,

                        movement_count =
                            EXCLUDED.movement_count,

                        movement_ids =
                            EXCLUDED.movement_ids,

                        raw_data =
                            EXCLUDED.raw_data,

                        synced_at =
                            NOW()
                    """,
                    (
                        activity_id,

                        workout.get(
                            "workoutId"
                        ),

                        workout_title,

                        workout.get(
                            "workoutType"
                        ),

                        workout.get(
                            "beginTime"
                        ),

                        workout.get(
                            "endTime"
                        ),

                        workout_duration_seconds(
                            workout
                        ),

                        workout.get(
                            "totalReps"
                        ),

                        workout.get(
                            "totalVolume"
                        ),

                        len(
                            sets
                        ),

                        len(
                            movement_ids
                        ),

                        json_value(
                            movement_ids
                        ),

                        json_value(
                            workout
                        ),
                    ),
                )

                workout_count += 1

                for (
                    set_index,
                    set_data,
                ) in enumerate(
                    sets
                ):

                    cur.execute(
                        """
                        INSERT INTO tonal_sets (
                            activity_id,
                            set_index,
                            movement_id,
                            rep_count,
                            base_weight,
                            suggested_weight,
                            avg_weight,
                            max_weight,
                            volume,
                            one_rep_max,
                            struggling_score,
                            inconsistency_score,
                            rom,
                            duration_seconds,
                            spotter,
                            eccentric,
                            chains,
                            flex,
                            progressive,
                            burnout,
                            max_con_power,
                            raw_data,
                            synced_at
                        )
                        VALUES (
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s::jsonb,
                            NOW()
                        )
                        ON CONFLICT (
                            activity_id,
                            set_index
                        )
                        DO UPDATE SET
                            movement_id =
                                EXCLUDED.movement_id,

                            rep_count =
                                EXCLUDED.rep_count,

                            base_weight =
                                EXCLUDED.base_weight,

                            suggested_weight =
                                EXCLUDED.suggested_weight,

                            avg_weight =
                                EXCLUDED.avg_weight,

                            max_weight =
                                EXCLUDED.max_weight,

                            volume =
                                EXCLUDED.volume,

                            one_rep_max =
                                EXCLUDED.one_rep_max,

                            struggling_score =
                                EXCLUDED.struggling_score,

                            inconsistency_score =
                                EXCLUDED.inconsistency_score,

                            rom =
                                EXCLUDED.rom,

                            duration_seconds =
                                EXCLUDED.duration_seconds,

                            spotter =
                                EXCLUDED.spotter,

                            eccentric =
                                EXCLUDED.eccentric,

                            chains =
                                EXCLUDED.chains,

                            flex =
                                EXCLUDED.flex,

                            progressive =
                                EXCLUDED.progressive,

                            burnout =
                                EXCLUDED.burnout,

                            max_con_power =
                                EXCLUDED.max_con_power,

                            raw_data =
                                EXCLUDED.raw_data,

                            synced_at =
                                NOW()
                        """,
                        (
                            activity_id,

                            set_index,

                            set_data.get(
                                "movementId"
                            ),

                            set_data.get(
                                "repCount"
                            ),

                            set_data.get(
                                "baseWeight"
                            ),

                            set_data.get(
                                "suggestedWeight"
                            ),

                            set_data.get(
                                "avgWeight"
                            ),

                            set_data.get(
                                "maxWeight"
                            ),

                            set_data.get(
                                "volume"
                            ),

                            set_data.get(
                                "oneRepMax"
                            ),

                            set_data.get(
                                "strugglingScore"
                            ),

                            set_data.get(
                                "inconsistencyScore"
                            ),

                            set_data.get(
                                "rom"
                            ),

                            set_data.get(
                                "duration"
                            ),

                            set_data.get(
                                "spotter"
                            ),

                            set_data.get(
                                "eccentric"
                            ),

                            set_data.get(
                                "chains"
                            ),

                            set_data.get(
                                "flex"
                            ),

                            set_data.get(
                                "progressive"
                            ),

                            set_data.get(
                                "burnout"
                            ),

                            set_data.get(
                                "maxConPower"
                            ),

                            json_value(
                                set_data
                            ),
                        ),
                    )

                    set_count += 1

                if (
                    index % 50 == 0
                    or index
                    == len(
                        workouts
                    )
                ):

                    print(
                        f"Stored "
                        f"{index}/"
                        f"{len(workouts)} "
                        f"workouts..."
                    )

    return {
        "workouts":
            workout_count,

        "sets":
            set_count,
    }


# ============================================================
# DATABASE VALIDATION
# ============================================================

def database_counts() -> dict:

    with get_conn() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT COUNT(*) AS count
                FROM tonal_movements
                """
            )

            movements = (
                cur.fetchone()[
                    "count"
                ]
            )

            cur.execute(
                """
                SELECT COUNT(*) AS count
                FROM tonal_workouts
                """
            )

            workouts = (
                cur.fetchone()[
                    "count"
                ]
            )

            cur.execute(
                """
                SELECT COUNT(*) AS count
                FROM tonal_sets
                """
            )

            sets = (
                cur.fetchone()[
                    "count"
                ]
            )

            cur.execute(
                """
                SELECT
                    MIN(begin_time)
                        AS oldest,
                    MAX(begin_time)
                        AS newest
                FROM tonal_workouts
                """
            )

            dates = (
                cur.fetchone()
            )

    return {
        "movements":
            movements,

        "workouts":
            workouts,

        "sets":
            sets,

        "oldest_workout":
            (
                dates[
                    "oldest"
                ].isoformat()
                if dates[
                    "oldest"
                ]
                else None
            ),

        "newest_workout":
            (
                dates[
                    "newest"
                ].isoformat()
                if dates[
                    "newest"
                ]
                else None
            ),
    }


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "\nTonal → Health Intelligence Sync"
    )

    print(
        "This imports Tonal movements, "
        "workouts and set-level history "
        "into Supabase."
    )

    print(
        "\nYour Tonal credentials are "
        "entered locally and are not "
        "written to the database or disk.\n"
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

    # --------------------------------------------------------
    # MOVEMENTS
    # --------------------------------------------------------

    print(
        "\nRetrieving Tonal movement catalog..."
    )

    movements = get_movements(
        id_token
    )

    print(
        f"Retrieved "
        f"{len(movements)} movements."
    )

    print(
        "Writing movement catalog "
        "to Supabase..."
    )

    movement_count = (
        sync_movements(
            movements
        )
    )

    print(
        f"Stored "
        f"{movement_count} movements."
    )

    # --------------------------------------------------------
    # WORKOUTS
    # --------------------------------------------------------

    print(
        "\nRetrieving complete "
        "Tonal workout history..."
    )

    workouts = get_all_workouts(
        id_token,
        user_id,
    )

    print(
        f"Retrieved "
        f"{len(workouts)} workouts."
    )

    print(
        "\nWriting workouts and sets "
        "to Supabase..."
    )

    imported = (
        sync_workouts_and_sets(
            workouts
        )
    )

    # Remove secrets from local variables
    # as soon as this run no longer needs them.
    password = None
    tokens = None
    id_token = None

    print(
        "\nSYNC COMPLETE"
    )

    print(
        json.dumps(
            imported,
            indent=2,
        )
    )

    print(
        "\nValidating database..."
    )

    counts = database_counts()

    print(
        json.dumps(
            counts,
            indent=2,
        )
    )

    print(
        "\nNo Tonal password or "
        "authentication token was "
        "stored in Supabase."
    )


if __name__ == "__main__":
    main()
