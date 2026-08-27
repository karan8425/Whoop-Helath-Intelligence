import hashlib
import json
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from db import get_conn


EASTERN = ZoneInfo(
    "America/New_York"
)


# ============================================================
# BASIC HELPERS
# ============================================================

def _today_eastern():

    return (
        datetime.now(
            EASTERN
        ).date()
    )


def _json_default(value):

    if isinstance(
        value,
        (
            datetime,
            date,
        ),
    ):
        return value.isoformat()

    return str(value)


def _canonical_json(payload):

    return json.dumps(
        payload,
        sort_keys=True,
        separators=(
            ",",
            ":",
        ),
        default=_json_default,
    )


def build_fingerprint(payload):

    canonical = (
        _canonical_json(
            payload
        )
    )

    return hashlib.sha256(
        canonical.encode(
            "utf-8"
        )
    ).hexdigest()


# ============================================================
# DAILY WORKOUT PRESCRIPTION STORAGE
# ============================================================

def load_workout_prescription(
    prescription_date=None,
):

    target_date = (
        prescription_date
        or _today_eastern()
    )

    with get_conn() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    id,
                    prescription_date,
                    status,
                    readiness_category,
                    recovery_score,
                    session_type,
                    primary_focus,
                    secondary_focus,
                    exercise_count,
                    total_sets,
                    estimated_total_volume,
                    prescription_payload,
                    input_fingerprint,
                    prescription_version,
                    generated_at,
                    updated_at
                FROM public.daily_workout_prescriptions
                WHERE prescription_date = %s
                LIMIT 1
                """,
                (
                    target_date,
                ),
            )

            row = (
                cur.fetchone()
            )

    if not row:
        return None

    return dict(
        row
    )


def save_workout_prescription(
    prescription_payload,
    prescription_date=None,
    force=False,
):

    target_date = (
        prescription_date
        or _today_eastern()
    )

    status = (
        prescription_payload.get(
            "status",
            "unknown",
        )
    )

    readiness = (
        prescription_payload.get(
            "readiness"
        )
        or {}
    )

    session = (
        prescription_payload.get(
            "session"
        )
        or {}
    )

    fingerprint_payload = {
        "date":
            target_date.isoformat(),

        "status":
            status,

        "readiness":
            readiness,

        "session":
            session,

        "progression_policy":
            prescription_payload.get(
                "progression_policy"
            ),
    }

    fingerprint = (
        build_fingerprint(
            fingerprint_payload
        )
    )

    existing = (
        load_workout_prescription(
            target_date
        )
    )

    if (
        existing
        and not force
        and existing.get(
            "input_fingerprint"
        ) == fingerprint
    ):

        return {
            "status":
                "cached",

            "changed":
                False,

            "prescription_date":
                target_date.isoformat(),

            "input_fingerprint":
                fingerprint,

            "record":
                existing,
        }

    existing_version = (
        int(
            existing.get(
                "prescription_version"
            )
        )
        if existing
        and existing.get(
            "prescription_version"
        )
        is not None
        else 0
    )

    new_version = (
        existing_version + 1
    )

    with get_conn() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                INSERT INTO public.daily_workout_prescriptions (
                    prescription_date,
                    status,
                    readiness_category,
                    recovery_score,
                    session_type,
                    primary_focus,
                    secondary_focus,
                    exercise_count,
                    total_sets,
                    estimated_total_volume,
                    prescription_payload,
                    input_fingerprint,
                    prescription_version,
                    generated_at,
                    updated_at
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
                    NOW(),
                    NOW()
                )
                ON CONFLICT (
                    prescription_date
                )
                DO UPDATE SET
                    status =
                        EXCLUDED.status,

                    readiness_category =
                        EXCLUDED.readiness_category,

                    recovery_score =
                        EXCLUDED.recovery_score,

                    session_type =
                        EXCLUDED.session_type,

                    primary_focus =
                        EXCLUDED.primary_focus,

                    secondary_focus =
                        EXCLUDED.secondary_focus,

                    exercise_count =
                        EXCLUDED.exercise_count,

                    total_sets =
                        EXCLUDED.total_sets,

                    estimated_total_volume =
                        EXCLUDED.estimated_total_volume,

                    prescription_payload =
                        EXCLUDED.prescription_payload,

                    input_fingerprint =
                        EXCLUDED.input_fingerprint,

                    prescription_version =
                        EXCLUDED.prescription_version,

                    generated_at =
                        EXCLUDED.generated_at,

                    updated_at =
                        NOW()

                RETURNING
                    id,
                    prescription_date,
                    status,
                    readiness_category,
                    recovery_score,
                    session_type,
                    primary_focus,
                    secondary_focus,
                    exercise_count,
                    total_sets,
                    estimated_total_volume,
                    prescription_payload,
                    input_fingerprint,
                    prescription_version,
                    generated_at,
                    updated_at
                """,
                (
                    target_date,

                    status,

                    readiness.get(
                        "training_category"
                    ),

                    readiness.get(
                        "recovery_score"
                    ),

                    session.get(
                        "session_type"
                    ),

                    Jsonb(
                        session.get(
                            "primary_focus",
                            [],
                        )
                    ),

                    Jsonb(
                        session.get(
                            "secondary_focus",
                            [],
                        )
                    ),

                    session.get(
                        "exercise_count",
                        0,
                    ),

                    session.get(
                        "total_sets",
                        0,
                    ),

                    session.get(
                        "estimated_total_volume"
                    ),

                    Jsonb(
                        prescription_payload
                    ),

                    fingerprint,

                    new_version,
                ),
            )

            row = (
                cur.fetchone()
            )

    log_intelligence_event(
        event_date=target_date,
        event_type=(
            "workout_prescription_saved"
        ),
        source="tonal_prescription_engine",
        event_fingerprint=fingerprint,
        details={
            "prescription_version":
                new_version,

            "readiness_category":
                readiness.get(
                    "training_category"
                ),

            "recovery_score":
                readiness.get(
                    "recovery_score"
                ),

            "session_type":
                session.get(
                    "session_type"
                ),

            "exercise_count":
                session.get(
                    "exercise_count",
                    0,
                ),

            "total_sets":
                session.get(
                    "total_sets",
                    0,
                ),

            "forced":
                bool(
                    force
                ),
        },
    )

    return {
        "status":
            "saved",

        "changed":
            True,

        "prescription_date":
            target_date.isoformat(),

        "input_fingerprint":
            fingerprint,

        "record":
            dict(
                row
            ),
    }


# ============================================================
# DAILY COACHING SNAPSHOT STORAGE
# ============================================================

def load_coaching_snapshot(
    coaching_date=None,
):

    target_date = (
        coaching_date
        or _today_eastern()
    )

    with get_conn() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    id,
                    coaching_date,
                    status,
                    whoop_ready,
                    training_recommendation,
                    tonal_training_focus,
                    active_phase,
                    goal_progress_direction,
                    input_fingerprint,
                    deterministic_payload,
                    ai_brief,
                    ai_model,
                    generated_at,
                    updated_at
                FROM public.daily_coaching_snapshots
                WHERE coaching_date = %s
                LIMIT 1
                """,
                (
                    target_date,
                ),
            )

            row = (
                cur.fetchone()
            )

    if not row:
        return None

    return dict(
        row
    )


def save_coaching_snapshot(
    deterministic_payload,
    ai_brief=None,
    ai_model=None,
    coaching_date=None,
    force=False,
):

    target_date = (
        coaching_date
        or _today_eastern()
    )

    data_readiness = (
        deterministic_payload.get(
            "data_readiness"
        )
        or {}
    )

    tonal_training = (
        deterministic_payload.get(
            "tonal_training"
        )
        or {}
    )

    active_goal = (
        deterministic_payload.get(
            "active_goal"
        )
        or {}
    )

    fingerprint_payload = {
        "date":
            target_date.isoformat(),

        "training_recommendation":
            deterministic_payload.get(
                "training_recommendation"
            ),

        "overall_status":
            deterministic_payload.get(
                "overall_status"
            ),

        "confidence":
            deterministic_payload.get(
                "confidence"
            ),

        "physiology_reasons":
            deterministic_payload.get(
                "physiology_reasons",
                [],
            ),

        "tonal_training":
            tonal_training,

        "active_goal":
            active_goal,

        "body_composition_context":
            deterministic_payload.get(
                "body_composition_context",
                [],
            ),

        "data_readiness":
            data_readiness,
    }

    fingerprint = (
        build_fingerprint(
            fingerprint_payload
        )
    )

    existing = (
        load_coaching_snapshot(
            target_date
        )
    )

    if (
        existing
        and not force
        and existing.get(
            "input_fingerprint"
        ) == fingerprint
        and existing.get(
            "ai_brief"
        )
        is not None
    ):

        return {
            "status":
                "cached",

            "changed":
                False,

            "coaching_date":
                target_date.isoformat(),

            "input_fingerprint":
                fingerprint,

            "record":
                existing,
        }

    status = (
        deterministic_payload.get(
            "status",
            "unknown",
        )
    )

    whoop_ready = bool(
        data_readiness.get(
            "whoop_current"
        )
    )

    training_recommendation = (
        deterministic_payload.get(
            "training_recommendation"
        )
    )

    tonal_training_focus = (
        tonal_training.get(
            "training_focus"
        )
    )

    active_phase = (
        active_goal.get(
            "phase"
        )
    )

    goal_progress_direction = (
        deterministic_payload.get(
            "goal_progress_direction"
        )
        or active_goal.get(
            "goal_progress_direction"
        )
    )

    with get_conn() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                INSERT INTO public.daily_coaching_snapshots (
                    coaching_date,
                    status,
                    whoop_ready,
                    training_recommendation,
                    tonal_training_focus,
                    active_phase,
                    goal_progress_direction,
                    input_fingerprint,
                    deterministic_payload,
                    ai_brief,
                    ai_model,
                    generated_at,
                    updated_at
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
                    NOW(),
                    NOW()
                )
                ON CONFLICT (
                    coaching_date
                )
                DO UPDATE SET
                    status =
                        EXCLUDED.status,

                    whoop_ready =
                        EXCLUDED.whoop_ready,

                    training_recommendation =
                        EXCLUDED.training_recommendation,

                    tonal_training_focus =
                        EXCLUDED.tonal_training_focus,

                    active_phase =
                        EXCLUDED.active_phase,

                    goal_progress_direction =
                        EXCLUDED.goal_progress_direction,

                    input_fingerprint =
                        EXCLUDED.input_fingerprint,

                    deterministic_payload =
                        EXCLUDED.deterministic_payload,

                    ai_brief =
                        EXCLUDED.ai_brief,

                    ai_model =
                        EXCLUDED.ai_model,

                    generated_at =
                        EXCLUDED.generated_at,

                    updated_at =
                        NOW()

                RETURNING
                    id,
                    coaching_date,
                    status,
                    whoop_ready,
                    training_recommendation,
                    tonal_training_focus,
                    active_phase,
                    goal_progress_direction,
                    input_fingerprint,
                    deterministic_payload,
                    ai_brief,
                    ai_model,
                    generated_at,
                    updated_at
                """,
                (
                    target_date,

                    status,

                    whoop_ready,

                    training_recommendation,

                    tonal_training_focus,

                    active_phase,

                    goal_progress_direction,

                    fingerprint,

                    Jsonb(
                        deterministic_payload
                    ),

                    (
                        Jsonb(
                            ai_brief
                        )
                        if ai_brief
                        is not None
                        else None
                    ),

                    ai_model,
                ),
            )

            row = (
                cur.fetchone()
            )

    log_intelligence_event(
        event_date=target_date,
        event_type=(
            "coaching_snapshot_saved"
        ),
        source="daily_intelligence_store",
        event_fingerprint=fingerprint,
        details={
            "status":
                status,

            "whoop_ready":
                whoop_ready,

            "training_recommendation":
                training_recommendation,

            "tonal_training_focus":
                tonal_training_focus,

            "active_phase":
                active_phase,

            "has_ai_brief":
                ai_brief is not None,

            "ai_model":
                ai_model,

            "forced":
                bool(
                    force
                ),
        },
    )

    return {
        "status":
            "saved",

        "changed":
            True,

        "coaching_date":
            target_date.isoformat(),

        "input_fingerprint":
            fingerprint,

        "record":
            dict(
                row
            ),
    }


# ============================================================
# EVENT LOG
# ============================================================

def log_intelligence_event(
    event_type,
    source=None,
    details=None,
    event_fingerprint=None,
    event_date=None,
):

    target_date = (
        event_date
        or _today_eastern()
    )

    payload = (
        details
        or {}
    )

    fingerprint = (
        event_fingerprint
        or build_fingerprint(
            {
                "event_date":
                    target_date.isoformat(),

                "event_type":
                    event_type,

                "source":
                    source,

                "details":
                    payload,
            }
        )
    )

    with get_conn() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                INSERT INTO public.daily_intelligence_events (
                    event_date,
                    event_type,
                    source,
                    event_fingerprint,
                    details,
                    created_at
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    NOW()
                )
                RETURNING
                    id,
                    event_date,
                    event_type,
                    source,
                    event_fingerprint,
                    details,
                    created_at
                """,
                (
                    target_date,

                    event_type,

                    source,

                    fingerprint,

                    Jsonb(
                        payload
                    ),
                ),
            )

            row = (
                cur.fetchone()
            )

    return dict(
        row
    )


def recent_intelligence_events(
    limit=20,
):

    limit = max(
        1,
        min(
            int(
                limit
            ),
            100,
        ),
    )

    with get_conn() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    id,
                    event_date,
                    event_type,
                    source,
                    event_fingerprint,
                    details,
                    created_at
                FROM public.daily_intelligence_events
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (
                    limit,
                ),
            )

            rows = (
                cur.fetchall()
            )

    return [
        dict(
            row
        )
        for row in rows
    ]


# ============================================================
# CACHE DECISION HELPERS
# ============================================================

def coaching_cache_status(
    deterministic_payload,
    coaching_date=None,
):

    target_date = (
        coaching_date
        or _today_eastern()
    )

    existing = (
        load_coaching_snapshot(
            target_date
        )
    )

    if not existing:

        return {
            "cache_hit":
                False,

            "reason":
                "no_snapshot",

            "record":
                None,
        }

    data_readiness = (
        deterministic_payload.get(
            "data_readiness"
        )
        or {}
    )

    tonal_training = (
        deterministic_payload.get(
            "tonal_training"
        )
        or {}
    )

    active_goal = (
        deterministic_payload.get(
            "active_goal"
        )
        or {}
    )

    fingerprint_payload = {
        "date":
            target_date.isoformat(),

        "training_recommendation":
            deterministic_payload.get(
                "training_recommendation"
            ),

        "overall_status":
            deterministic_payload.get(
                "overall_status"
            ),

        "confidence":
            deterministic_payload.get(
                "confidence"
            ),

        "physiology_reasons":
            deterministic_payload.get(
                "physiology_reasons",
                [],
            ),

        "tonal_training":
            tonal_training,

        "active_goal":
            active_goal,

        "body_composition_context":
            deterministic_payload.get(
                "body_composition_context",
                [],
            ),

        "data_readiness":
            data_readiness,
    }

    current_fingerprint = (
        build_fingerprint(
            fingerprint_payload
        )
    )

    stored_fingerprint = (
        existing.get(
            "input_fingerprint"
        )
    )

    if (
        current_fingerprint
        == stored_fingerprint
        and existing.get(
            "ai_brief"
        )
        is not None
    ):

        return {
            "cache_hit":
                True,

            "reason":
                "inputs_unchanged",

            "input_fingerprint":
                current_fingerprint,

            "record":
                existing,
        }

    if (
        current_fingerprint
        == stored_fingerprint
        and existing.get(
            "ai_brief"
        )
        is None
    ):

        return {
            "cache_hit":
                False,

            "reason":
                "ai_brief_missing",

            "input_fingerprint":
                current_fingerprint,

            "record":
                existing,
        }

    return {
        "cache_hit":
            False,

        "reason":
            "material_inputs_changed",

        "input_fingerprint":
            current_fingerprint,

        "stored_fingerprint":
            stored_fingerprint,

        "record":
            existing,
    }


# ============================================================
# INDEPENDENT VALIDATION
#
# This test persists the CURRENT deterministic Tonal workout
# prescription only.
#
# It does NOT call OpenAI.
# It does NOT modify coaching logic.
# ============================================================

def validate_store():

    from integrations.tonal.workout_prescription import (
        build_daily_workout_prescription,
    )

    prescription = (
        build_daily_workout_prescription()
    )

    first_save = (
        save_workout_prescription(
            prescription
        )
    )

    second_save = (
        save_workout_prescription(
            prescription
        )
    )

    loaded = (
        load_workout_prescription()
    )

    events = (
        recent_intelligence_events(
            limit=5
        )
    )

    checks = {

        "prescription_generated":
            prescription.get(
                "status"
            )
            == "ok",

        "first_save_present":
            first_save.get(
                "status"
            )
            in (
                "saved",
                "cached",
            ),

        "second_save_cached":
            second_save.get(
                "status"
            )
            == "cached",

        "loaded_from_database":
            loaded is not None,

        "fingerprint_present":
            bool(
                loaded.get(
                    "input_fingerprint"
                )
                if loaded
                else None
            ),

        "payload_present":
            isinstance(
                loaded.get(
                    "prescription_payload"
                )
                if loaded
                else None,
                dict,
            ),

        "events_available":
            isinstance(
                events,
                list,
            ),
    }

    return {

        "status":
            (
                "ok"
                if all(
                    checks.values()
                )
                else "check_failed"
            ),

        "checks":
            checks,

        "today":
            _today_eastern()
            .isoformat(),

        "first_save_status":
            first_save.get(
                "status"
            ),

        "second_save_status":
            second_save.get(
                "status"
            ),

        "prescription_version":
            (
                loaded.get(
                    "prescription_version"
                )
                if loaded
                else None
            ),

        "session_type":
            (
                loaded.get(
                    "session_type"
                )
                if loaded
                else None
            ),

        "readiness_category":
            (
                loaded.get(
                    "readiness_category"
                )
                if loaded
                else None
            ),

        "recovery_score":
            (
                float(
                    loaded.get(
                        "recovery_score"
                    )
                )
                if loaded
                and loaded.get(
                    "recovery_score"
                )
                is not None
                else None
            ),

        "exercise_count":
            (
                loaded.get(
                    "exercise_count"
                )
                if loaded
                else None
            ),

        "total_sets":
            (
                loaded.get(
                    "total_sets"
                )
                if loaded
                else None
            ),
    }


# ============================================================
# TERMINAL TEST
# ============================================================

def main():

    result = (
        validate_store()
    )

    print()

    print(
        "DAILY INTELLIGENCE STORE VALIDATION"
    )

    print(
        "=" * 78
    )

    print(
        json.dumps(
            result,
            indent=2,
            default=_json_default,
        )
    )

    print(
        "=" * 78
    )


if __name__ == "__main__":
    main()