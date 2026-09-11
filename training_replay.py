"""Point-in-time replay of the accepted B3/B4 training engines.

Recommendation inputs are bounded by ``ReplayContext.as_of``. Outcome queries
use a separate, explicitly post-recommendation window and are never passed to
the recommendation engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from uuid import UUID
from typing import Any
from zoneinfo import ZoneInfo

from integrations.tonal.program_balance import DEFAULT_CALIBRATION

from activity_plan import build_activity_plan, load_activity_context
from db import get_conn, request_scoped_connection
from goals import get_active_goal
from integrations.tonal.workout_prescription import build_daily_workout_prescription


EASTERN = ZoneInfo("America/New_York")
ENGINE_VERSION = "training-b3.2+b4"
PLAN_VERSION = "1.3"
BACKTEST_VERSION = "b3.1-v2-temporal"


@dataclass(frozen=True)
class ReplayContext:
    replay_date: date
    as_of: datetime
    timezone_name: str = "America/New_York"
    mode: str = "replay"

    def __post_init__(self):
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("Replay cutoff must be timezone-aware")
        if self.local_date != self.replay_date:
            raise ValueError("Replay date must match the cutoff's local date")

    @property
    def as_of_utc(self) -> datetime:
        return self.as_of.astimezone(timezone.utc)

    @property
    def local_date(self) -> date:
        return self.as_of.astimezone(ZoneInfo(self.timezone_name)).date()

    @classmethod
    def morning(cls, replay_date: date, cutoff_hour: int = 7) -> "ReplayContext":
        local = datetime.combine(replay_date, time(cutoff_hour), tzinfo=EASTERN)
        return cls(replay_date=replay_date, as_of=local.astimezone(timezone.utc))

    @property
    def local_day_end(self) -> datetime:
        local = datetime.combine(
            self.replay_date + timedelta(days=1), time.min, tzinfo=EASTERN
        )
        return local.astimezone(timezone.utc)


def _json_safe(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return value


def _query_one(sql: str, params: tuple) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return dict(cur.fetchone() or {})


def _query_all(sql: str, params: tuple) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]


def _data_quality(context: ReplayContext) -> dict:
    row = _query_one(
        """
        SELECT
          EXISTS(SELECT 1 FROM whoop_daily_metrics
                 WHERE metric_date = %s AND has_recovery AND has_sleep
                   AND source_updated_at <= %s) AS whoop_current,
          EXISTS(SELECT 1 FROM whoop_daily_metrics
                 WHERE metric_date <= %s AND has_recovery AND has_sleep
                   AND source_updated_at <= %s) AS whoop_history,
          EXISTS(SELECT 1 FROM tonal_workouts WHERE begin_time < %s) AS tonal,
          EXISTS(SELECT 1 FROM apple_health_daily_activity
                 WHERE activity_date < %s) AS apple,
          EXISTS(SELECT 1 FROM apple_health_body_samples
                 WHERE observed_at <= %s
                   AND source_bundle_id = 'com.elink.fittrackhealth') AS body
        """,
        (
            context.replay_date,
            context.as_of,
            context.replay_date,
            context.as_of,
            context.as_of,
            context.replay_date,
            context.as_of,
        ),
    )
    present = {name: bool(row.get(name)) for name in ("whoop_current", "whoop_history", "tonal", "apple", "body")}
    missing = [name for name, available in present.items() if not available]
    if present["whoop_current"] and present["tonal"] and present["apple"]:
        status = "COMPLETE" if present["body"] else "PARTIAL"
    elif present["whoop_history"]:
        status = "PARTIAL"
    else:
        status = "INSUFFICIENT"
    return {"status": status, "sources": present, "missing_sources": missing}


def _actual_outcome(context: ReplayContext) -> dict:
    workouts = _query_all(
        """
        SELECT w.activity_id, w.begin_time, w.end_time, w.workout_title, w.workout_type,
               w.duration_seconds, w.total_reps, w.total_volume, w.set_count,
               ARRAY_REMOVE(ARRAY_AGG(DISTINCT m.name), NULL) AS exercises,
               ARRAY_REMOVE(ARRAY_AGG(DISTINCT muscle), NULL) AS muscles,
               COALESCE(JSONB_AGG(DISTINCT JSONB_BUILD_OBJECT(
                   'movement_id', s.movement_id, 'name', m.name,
                   'set_index', s.set_index, 'reps', s.rep_count,
                   'load_lb', COALESCE(s.avg_weight, s.base_weight),
                   'volume_lb', s.volume
               )) FILTER (WHERE s.movement_id IS NOT NULL), '[]'::jsonb) AS movement_performances
        FROM tonal_workouts w
        LEFT JOIN tonal_sets s USING (activity_id)
        LEFT JOIN tonal_movements m USING (movement_id)
        LEFT JOIN LATERAL jsonb_array_elements_text(COALESCE(m.muscle_groups, '[]'::jsonb)) muscle ON TRUE
        WHERE w.begin_time >= %s AND w.begin_time < %s
        GROUP BY w.activity_id, w.begin_time, w.end_time, w.workout_title, w.workout_type,
                 w.duration_seconds, w.total_reps, w.total_volume, w.set_count
        ORDER BY w.begin_time
        """,
        (context.as_of, context.local_day_end),
    )
    activity = _query_one(
        "SELECT steps, active_energy_kcal, walking_running_distance_km FROM apple_health_daily_activity WHERE activity_date=%s",
        (context.replay_date,),
    )
    whoop = _query_one(
        "SELECT workout_total_strain FROM whoop_daily_metrics WHERE metric_date=%s",
        (context.replay_date,),
    )
    return {
        "workout_performed": bool(workouts),
        "workouts": workouts,
        "actual_steps": activity.get("steps"),
        "active_energy_kcal": activity.get("active_energy_kcal"),
        "walking_running_distance_km": activity.get("walking_running_distance_km"),
        "whoop_workout_strain": whoop.get("workout_total_strain"),
    }


def _comparison(training: dict, activity: dict, outcome: dict) -> dict:
    session = training.get("session") or {}
    recommended_muscles = set(session.get("primary_focus") or []) | set(
        session.get("secondary_focus") or []
    )
    actual_muscles = {
        muscle
        for workout in outcome.get("workouts") or []
        for muscle in workout.get("muscles") or []
    }
    overlap = sorted(recommended_muscles & actual_muscles)
    actual_sets = sum(int(row.get("set_count") or 0) for row in outcome.get("workouts") or [])
    actual_volume = sum(float(row.get("total_volume") or 0) for row in outcome.get("workouts") or [])
    actual_duration = sum(float(row.get("duration_seconds") or 0) for row in outcome.get("workouts") or []) / 60
    diagnostics = session.get("dose_diagnostics") or {}
    median_sets = ((diagnostics.get("historical") or {}).get("median_sets"))
    return {
        "strength_recommended": session.get("session_type") not in (None, "Rest", "Active Recovery"),
        "strength_performed": bool(outcome.get("workout_performed")),
        "recommended_actual_muscle_overlap": overlap,
        "muscle_overlap_ratio": round(len(overlap) / len(recommended_muscles), 3) if recommended_muscles else None,
        "recommended_sets": session.get("total_sets"),
        "recommended_set_ratio_vs_personal_median": (
            round(float(session.get("total_sets")) / float(median_sets), 3)
            if session.get("total_sets") is not None and median_sets else None
        ),
        "actual_sets": actual_sets,
        "recommended_volume_lb": session.get("estimated_total_volume"),
        "actual_volume_lb": round(actual_volume, 1),
        "recommended_duration_min": _duration_target(diagnostics),
        "actual_duration_min": round(actual_duration, 1),
        "step_target": activity.get("step_target"),
        "actual_steps": outcome.get("actual_steps"),
        "step_target_attained": (
            outcome.get("actual_steps") >= activity.get("step_target")
            if outcome.get("actual_steps") is not None and activity.get("step_target")
            else None
        ),
        "progression_follow_up": _progression_follow_up(session, outcome),
    }


def _progression_follow_up(session: dict, outcome: dict) -> list[dict]:
    actual = [item for workout in outcome.get("workouts") or [] for item in workout.get("movement_performances") or []]
    by_movement = {}
    for item in actual:
        by_movement.setdefault(str(item.get("movement_id")), []).append(item)
    results = []
    for exercise in session.get("exercises") or []:
        movement_id = str(exercise.get("movement_id"))
        attempts = by_movement.get(movement_id) or []
        results.append({
            "movement_id": exercise.get("movement_id"),
            "name": exercise.get("name"),
            "progression_state": exercise.get("progression_state"),
            "result": "not_evaluable" if not attempts else "observed_follow_up",
            "actual_sets": len(attempts),
            "note": "Observational only; target achievement requires directly comparable set-mode metadata."
        })
    return results


def replay_day(context: ReplayContext, include_details: bool = True, recommendation_history=None, calibration=DEFAULT_CALIBRATION) -> dict:
    """Run B3/B4 under one read-only logical request to avoid TLS amplification."""
    with request_scoped_connection():
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
        return _replay_day(context, include_details, recommendation_history, calibration)


def _replay_day(context: ReplayContext, include_details: bool = True, recommendation_history=None, calibration=DEFAULT_CALIBRATION) -> dict:
    quality = _data_quality(context)
    goal = get_active_goal(as_of=context.as_of)
    training = build_daily_workout_prescription(
        now=context.as_of,
        recommendation_history=recommendation_history,
        calibration=calibration,
        selection_diagnostics=True,  # Metrics require movement viability even in compact reports.
    )
    strength = _training_activity_shape(training)
    activity_context = load_activity_context(as_of=context.as_of_utc)
    activity = build_activity_plan(
        goal=goal,
        strength=strength,
        context=activity_context,
        as_of=context.as_of_utc,
    )
    outcome = _actual_outcome(context)
    result = {
        "replay_date": context.replay_date.isoformat(),
        "as_of": context.as_of.isoformat(),
        "timezone": context.timezone_name,
        "engine_version": ENGINE_VERSION,
        "plan_version": PLAN_VERSION,
        "backtest_version": BACKTEST_VERSION,
        "calibration": calibration,
        "data_quality": quality,
        "inputs": _input_snapshot(training, activity, goal),
        "recommendation": _recommendation_shape(training, activity),
        "actual_outcome": outcome,
        "comparison": _comparison(training, activity, outcome),
    }
    if include_details:
        result["diagnostics"] = {"training": training, "activity": activity}
    return _json_safe(result)


def _training_activity_shape(training: dict) -> dict:
    readiness = training.get("readiness") or {}
    session = training.get("session") or {}
    return {
        "available": training.get("status") == "ok",
        "recovery_score": readiness.get("recovery_score"),
        "session_type": session.get("session_type"),
        "total_sets": session.get("total_sets"),
        "training_b3": session.get("training_b3") or {},
    }


def _input_snapshot(training: dict, activity: dict, goal: dict | None) -> dict:
    readiness = training.get("readiness") or {}
    session = training.get("session") or {}
    b3 = session.get("training_b3") or {}
    return {
        "whoop": {
            key: readiness.get(key)
            for key in ("metric_date", "recovery_score", "hrv_rmssd_milli", "resting_heart_rate", "sleep_duration_hours", "readiness_band")
        },
        "training": {
            "muscle_readiness": session.get("muscle_readiness"),
            "ranked_muscles": session.get("muscle_priority_diagnostics"),
            "template_scores": session.get("session_template_scores"),
            "selected_template": (training.get("session") or {}).get("session_type"),
        },
        "progression": [
            {
                key: exercise.get(key)
                for key in ("movement_id", "name", "progression_state", "progression_target", "target_weight_lb", "target_reps", "target_rir")
            }
            for exercise in (training.get("session") or {}).get("exercises") or []
        ],
        "body_composition": b3.get("body_composition_strategy"),
        "activity": {
            "baselines": activity.get("baselines"),
            "conditioning_history": activity.get("conditioning_history"),
        },
        "goal": goal,
    }


def _recommendation_shape(training: dict, activity: dict) -> dict:
    readiness = training.get("readiness") or {}
    session = training.get("session") or {}
    dose = session.get("dose_diagnostics") or {}
    return {
        "training_status": readiness.get("training_category") or training.get("status"),
        "primary_focus": session.get("primary_focus") or [],
        "secondary_focus": session.get("secondary_focus") or [],
        "selected_muscles": list(dict.fromkeys((session.get("primary_focus") or []) + (session.get("secondary_focus") or []))),
        "session_type": session.get("session_type"),
        "exercise_count": session.get("exercise_count"),
        "target_sets": session.get("total_sets"),
        "target_volume_lb": session.get("estimated_total_volume"),
        "estimated_duration_min": _duration_target(dose),
        "whoop_capacity_modifier": (dose.get("modifiers") or {}).get("whoop_multiplier"),
        "exercises": session.get("exercises") or [],
        "activity_plan": activity,
    }


def _duration_target(dose: dict):
    target = dose.get("target") or {}
    low = target.get("duration_low_minutes")
    high = target.get("duration_high_minutes")
    return round((float(low) + float(high)) / 2, 1) if low is not None and high is not None else None
