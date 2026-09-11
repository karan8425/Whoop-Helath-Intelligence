import json
from datetime import datetime

from db import get_conn
from integrations.tonal.client import (
    TonalAuthenticationError,
    TonalConfigurationError,
    TonalRequestError,
    authenticate,
    automation_credentials,
)
from integrations.tonal.sync_strength_scores import (
    get_strength_history,
    sync_records,
)
from integrations.tonal.sync_tonal import (
    get_all_workouts,
    get_movements,
    get_user_info,
    sync_movements,
    sync_workouts_and_sets,
    workout_activity_id,
)


SAFE_ERROR_MESSAGES = {
    TonalConfigurationError: "Development Tonal automation credentials are not configured.",
    TonalAuthenticationError: "Tonal authentication was rejected.",
    TonalRequestError: "A Tonal source request failed.",
}


def _safe_error(exc):
    for error_type, message in SAFE_ERROR_MESSAGES.items():
        if isinstance(exc, error_type):
            return type(exc).__name__, message
    return type(exc).__name__, "Tonal synchronization failed during processing."


def _stored_latest_workout():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(begin_time) AS latest FROM tonal_workouts")
            return cur.fetchone()["latest"]


def _start_audit(sync_mode):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tonal_sync_runs (status, sync_mode)
                VALUES ('running', %s)
                RETURNING id
                """,
                (sync_mode,),
            )
            return cur.fetchone()["id"]


def _finish_audit(run_id, status, **fields):
    allowed = {
        "source_latest_workout_at",
        "stored_latest_workout_before",
        "stored_latest_workout_after",
        "workouts_received",
        "workouts_inserted_or_updated",
        "sets_received",
        "sets_inserted_or_updated",
        "movements_received",
        "strength_scores_received",
        "strength_scores_inserted_or_updated",
        "error_class",
        "error_message_sanitized",
    }
    values = {key: value for key, value in fields.items() if key in allowed}
    assignments = ["status = %s", "completed_at = NOW()"]
    params = [status]
    for key, value in values.items():
        assignments.append(f"{key} = %s")
        params.append(value)
    params.append(run_id)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE tonal_sync_runs SET {', '.join(assignments)} WHERE id = %s",
                params,
            )


def _parse_time(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _source_latest(workouts):
    values = [_parse_time(row.get("beginTime")) for row in workouts]
    values = [value for value in values if value is not None]
    return max(values) if values else None


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _material_training_change(movements, workouts, strength_scores):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT movement_id, raw_data FROM tonal_movements")
            stored_movements = {str(row["movement_id"]): _canonical(row["raw_data"]) for row in cur.fetchall()}
            cur.execute("SELECT activity_id, raw_data FROM tonal_workouts")
            stored_workouts = {str(row["activity_id"]): _canonical(row["raw_data"]) for row in cur.fetchall()}
            cur.execute("SELECT source_record_key, raw_data FROM tonal_strength_scores")
            stored_scores = {str(row["source_record_key"]): _canonical(row["raw_data"]) for row in cur.fetchall()}

    source_movements = {str(row.get("id")): _canonical(row) for row in movements if row.get("id")}
    source_workouts = {
        str(workout_activity_id(row)): _canonical(row)
        for row in workouts
        if workout_activity_id(row)
    }
    source_scores = {str(row.get("id")): _canonical(row) for row in strength_scores if row.get("id")}
    return (
        source_movements != stored_movements
        or source_workouts != stored_workouts
        or source_scores != stored_scores
    )


def run_sync(environ=None):
    email, password = automation_credentials(environ)
    run_id = _start_audit("development_cron")
    stored_before = _stored_latest_workout()
    tokens = None
    id_token = None

    try:
        tokens = authenticate(email, password)
        id_token = tokens["id_token"]
        user_info = get_user_info(id_token)
        user_id = user_info.get("id") or user_info.get("userId")
        if not user_id:
            raise ValueError("Tonal user response did not contain a user identifier.")

        movements = get_movements(id_token)
        workouts = get_all_workouts(id_token, user_id)
        strength_scores = get_strength_history(id_token, user_id)

        material_change = _material_training_change(
            movements,
            workouts,
            strength_scores,
        )

        movement_count = sync_movements(movements)
        imported = sync_workouts_and_sets(workouts)
        strength_count = sync_records(strength_scores)

        source_latest = _source_latest(workouts)
        stored_after = _stored_latest_workout()
        if source_latest and (stored_after is None or source_latest > stored_after):
            raise RuntimeError("Tonal source/store freshness boundary mismatch.")

        result = {
            "status": "completed",
            "source_latest_workout_at": source_latest,
            "stored_latest_workout_before": stored_before,
            "stored_latest_workout_after": stored_after,
            "workouts_received": len(workouts),
            "workouts_inserted_or_updated": imported["workouts"],
            "sets_received": sum(len(row.get("workoutSetActivity") or []) for row in workouts),
            "sets_inserted_or_updated": imported["sets"],
            "movements_received": len(movements),
            "strength_scores_received": len(strength_scores),
            "strength_scores_inserted_or_updated": strength_count,
            "material_training_change": material_change,
        }
        audit_result = {
            key: value
            for key, value in result.items()
            if key != "status"
        }
        _finish_audit(run_id, "completed", **audit_result)
        cache_invalidated = False
        if material_change:
            from todays_plan_store import invalidate_todays_plan

            invalidate_todays_plan()
            cache_invalidated = True
        result["today_cache_invalidated"] = cache_invalidated
        return result
    except Exception as exc:
        error_class, safe_message = _safe_error(exc)
        _finish_audit(
            run_id,
            "failed",
            stored_latest_workout_before=stored_before,
            stored_latest_workout_after=_stored_latest_workout(),
            error_class=error_class,
            error_message_sanitized=safe_message,
        )
        raise RuntimeError(safe_message) from exc
    finally:
        password = None
        tokens = None
        id_token = None


def main():
    try:
        result = run_sync()
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        return 1

    printable = {
        key: value.isoformat() if isinstance(value, datetime) else value
        for key, value in result.items()
    }
    print(json.dumps(printable, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
