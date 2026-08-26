import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from db import get_conn
from freshness import freshness_status
from healthkit_ingest import latest_apple_health
from automation_status import latest_automation_run
from apple_health_trends import apple_health_trends
from goals import get_active_goal


EASTERN = ZoneInfo("America/New_York")

DEFAULT_DAILY_STEP_TARGET = int(
    os.getenv("DAILY_STEP_TARGET", "7000")
)

BODY_TREND_MIN_OBSERVATIONS = {
    7: 4,
    14: 7,
    30: 15,
    90: 30,
}


def _lb(kg):
    if kg is None:
        return None

    return kg * 2.2046226218


def _latest_whoop_daily(metric_date):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    metric_date,
                    recovery_score,
                    resting_heart_rate,
                    hrv_rmssd_milli,
                    sleep_duration_hours,
                    sleep_performance_percentage,
                    sleep_consistency_percentage,
                    sleep_efficiency_percentage,
                    respiratory_rate,
                    cycle_strain,
                    cycle_calories,
                    workout_count,
                    workout_total_strain,
                    workout_total_duration_hours,
                    workout_sports,
                    has_cycle,
                    has_recovery,
                    has_sleep,
                    has_workout
                FROM whoop_daily_metrics
                WHERE metric_date = %s
                LIMIT 1
                """,
                (metric_date,),
            )

            return cur.fetchone()


def _today_baselines(metric_date):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    metric_name,
                    current_value,
                    baseline_7,
                    n_7,
                    pct_vs_7,
                    baseline_30,
                    n_30,
                    pct_vs_30,
                    baseline_90,
                    n_90,
                    pct_vs_90
                FROM whoop_daily_baselines
                WHERE metric_date = %s
                ORDER BY metric_name
                """,
                (metric_date,),
            )

            rows = cur.fetchall()

    return {
        row["metric_name"]: dict(row)
        for row in rows
    }


def combined_daily_snapshot():
    local_now = (
        datetime.now(timezone.utc)
        .astimezone(EASTERN)
    )

    local_today = local_now.date()

    whoop_freshness = freshness_status()

    whoop = _latest_whoop_daily(
        local_today
    )

    apple = latest_apple_health()

    body = apple.get("body") or {}
    activity = apple.get("activity")

    weight = body.get(
        "body_weight"
    )

    body_fat = body.get(
        "body_fat_percentage"
    )

    lean_mass = body.get(
        "lean_body_mass"
    )

    body_summary = {
        "weight": None,
        "body_fat_percentage": None,
        "lean_body_mass": None,
    }

    if weight:
        body_summary["weight"] = {
            "kg": weight.get("value"),
            "lb": _lb(
                weight.get("value")
            ),
            "source_name": weight.get(
                "source_name"
            ),
            "observed_at": weight.get(
                "observed_at"
            ),
            "classification": weight.get(
                "classification"
            ),
            "coaching_eligible": weight.get(
                "coaching_eligible"
            ),
        }

    if body_fat:
        body_summary[
            "body_fat_percentage"
        ] = {
            "value": body_fat.get("value"),
            "source_name": body_fat.get(
                "source_name"
            ),
            "observed_at": body_fat.get(
                "observed_at"
            ),
            "classification": body_fat.get(
                "classification"
            ),
            "coaching_eligible": body_fat.get(
                "coaching_eligible"
            ),
        }

    if lean_mass:
        body_summary[
            "lean_body_mass"
        ] = {
            "kg": lean_mass.get("value"),
            "lb": _lb(
                lean_mass.get("value")
            ),
            "source_name": lean_mass.get(
                "source_name"
            ),
            "observed_at": lean_mass.get(
                "observed_at"
            ),
            "classification": lean_mass.get(
                "classification"
            ),
            "coaching_eligible": lean_mass.get(
                "coaching_eligible"
            ),
        }

    whoop_ready = bool(
        whoop
        and whoop_freshness.get(
            "status"
        ) == "fresh"
        and whoop_freshness.get(
            "can_generate_current_recommendation"
        ) is True
    )

    activity_current = bool(
        activity
        and activity.get(
            "classification"
        ) == "current"
    )

    current_body = bool(
        weight
        and weight.get(
            "coaching_eligible"
        ) is True
        and body_fat
        and body_fat.get(
            "coaching_eligible"
        ) is True
    )

    readiness = {
        "whoop_current":
            whoop_ready,

        "body_composition_current":
            current_body,

        "activity_current":
            activity_current,

        "lean_mass_current":
            bool(
                lean_mass
                and lean_mass.get(
                    "coaching_eligible"
                ) is True
            ),

        "combined_coaching_ready":
            whoop_ready,

        "body_composition_required_for_training":
            False,

        "activity_required_for_training":
            False,
    }

    notes = []

    if not current_body:
        notes.append(
            "Today's preferred-source Hume weight/body-fat "
            "measurement is not yet current. Historical Hume "
            "data may still be used for trend context."
        )

    if (
        lean_mass
        and not lean_mass.get(
            "coaching_eligible"
        )
    ):
        notes.append(
            "Lean body mass is retained as context "
            "but excluded from current coaching."
        )

    if not whoop_ready:
        notes.append(
            "WHOOP physiology is not current enough "
            "for today's training recommendation."
        )

    return {
        "status": "ok",
        "coaching_date":
            local_today.isoformat(),
        "local_now":
            local_now.isoformat(),
        "data_readiness":
            readiness,
        "whoop_freshness":
            whoop_freshness,
        "whoop":
            whoop,
        "body_composition":
            body_summary,
        "activity":
            activity,
        "notes":
            notes,
    }


def _current_whoop_recommendation(
    metric_date
):
    run = latest_automation_run()

    if not run:
        return None

    if (
        str(
            run.get("metric_date")
        )
        != metric_date.isoformat()
    ):
        return None

    return run.get(
        "deterministic_recommendation"
    )


def _metric_reason(
    metric_name,
    baselines
):
    row = baselines.get(
        metric_name
    )

    if not row:
        return None

    current = row.get(
        "current_value"
    )

    baseline_30 = row.get(
        "baseline_30"
    )

    pct = row.get(
        "pct_vs_30"
    )

    if (
        current is None
        or baseline_30 is None
        or pct is None
    ):
        return None

    if metric_name == "recovery_score":
        return (
            f"Recovery is {current:.0f}, "
            f"{pct:+.1f}% versus the "
            "30-day personal baseline."
        )

    if metric_name == "hrv_rmssd_milli":
        return (
            f"HRV is {current:.1f} ms, "
            f"{pct:+.1f}% versus the "
            "30-day personal baseline."
        )

    if metric_name == "resting_heart_rate":
        return (
            f"Resting heart rate is "
            f"{current:.0f} bpm, "
            f"{pct:+.1f}% versus the "
            "30-day personal baseline."
        )

    if metric_name == "sleep_duration_hours":
        return (
            f"Sleep duration is "
            f"{current:.2f} hours, "
            f"{pct:+.1f}% versus the "
            "30-day personal baseline."
        )

    return None


def _body_metric_trend(
    metric_name,
    metric_payload
):
    if (
        not metric_payload
        or not metric_payload.get(
            "available"
        )
    ):
        return {
            "metric":
                metric_name,

            "status":
                "unavailable",

            "usable_windows":
                [],

            "reason":
                (
                    metric_payload.get(
                        "reason"
                    )
                    if metric_payload
                    else "No data available."
                ),
        }

    usable = []

    for window in (
        7,
        14,
        30,
        90,
    ):
        window_data = (
            metric_payload
            .get(
                "windows",
                {}
            )
            .get(
                str(window),
                {}
            )
        )

        observations = int(
            window_data.get(
                "observations"
            )
            or 0
        )

        minimum = (
            BODY_TREND_MIN_OBSERVATIONS[
                window
            ]
        )

        if observations >= minimum:
            usable.append(
                {
                    "window_days":
                        window,

                    "observations":
                        observations,

                    "minimum_required":
                        minimum,

                    "baseline":
                        window_data.get(
                            "baseline"
                        ),

                    "pct_vs_baseline":
                        window_data.get(
                            "pct_vs_baseline"
                        ),
                }
            )

    return {
        "metric":
            metric_name,

        "status":
            (
                "usable"
                if usable
                else "insufficient_history"
            ),

        "current_value":
            metric_payload.get(
                "current_value"
            ),

        "unit":
            metric_payload.get(
                "unit"
            ),

        "observed_at":
            metric_payload.get(
                "observed_at"
            ),

        "usable_windows":
            usable,

        "reason":
            (
                None
                if usable
                else
                "Hume history does not yet "
                "meet the minimum observation "
                "threshold for trend interpretation."
            ),
    }


def _activity_trend_summary(
    activity_trends
):
    baselines = (
        activity_trends.get(
            "baselines"
        )
        or {}
    )

    b7 = baselines.get(
        "7"
    ) or {}

    b30 = baselines.get(
        "30"
    ) or {}

    b90 = baselines.get(
        "90"
    ) or {}

    steps_7 = b7.get(
        "steps"
    )

    steps_30 = b30.get(
        "steps"
    )

    steps_90 = b90.get(
        "steps"
    )

    def pct(a, b):
        if (
            a is None
            or b in (
                None,
                0,
            )
        ):
            return None

        return (
            (a - b)
            / b
        ) * 100.0

    vs_30 = pct(
        steps_7,
        steps_30
    )

    vs_90 = pct(
        steps_7,
        steps_90
    )

    return {
        "average_steps_7d":
            steps_7,

        "average_steps_30d":
            steps_30,

        "average_steps_90d":
            steps_90,

        "seven_day_vs_30_day_pct":
            (
                round(
                    vs_30,
                    1
                )
                if vs_30 is not None
                else None
            ),

        "seven_day_vs_90_day_pct":
            (
                round(
                    vs_90,
                    1
                )
                if vs_90 is not None
                else None
            ),

        "coverage_7_percentage":
            b7.get(
                "coverage_percentage"
            ),

        "coverage_30_percentage":
            b30.get(
                "coverage_percentage"
            ),

        "coverage_90_percentage":
            b90.get(
                "coverage_percentage"
            ),
    }


def _goal_context(
    goal,
    body_trends,
    activity_trend
):
    if not goal:
        return {
            "active": False,
            "phase": None,
            "guidance": [
                "No active goal profile is configured."
            ],
        }

    phase = goal.get(
        "phase"
    )

    target_body_fat = goal.get(
        "target_body_fat_percentage"
    )

    target_weight_lb = goal.get(
        "target_weight_lb"
    )

    target_steps = goal.get(
        "daily_step_target"
    )

    target_strength = goal.get(
        "strength_sessions_per_week"
    )

    target_protein = goal.get(
        "protein_target_grams"
    )

    current_body_fat = (
        body_trends
        .get(
            "body_fat_percentage",
            {}
        )
        .get(
            "current_value"
        )
    )

    current_weight_kg = (
        body_trends
        .get(
            "weight",
            {}
        )
        .get(
            "current_value"
        )
    )

    current_weight_lb = (
        _lb(
            current_weight_kg
        )
        if current_weight_kg is not None
        else None
    )

    body_fat_gap = None

    if (
        current_body_fat is not None
        and target_body_fat is not None
    ):
        body_fat_gap = (
            current_body_fat
            - target_body_fat
        )

    guidance = []

    if phase == "lean_cut":
        guidance.append(
            "Lean Cut priority: preserve strength-training "
            "quality while reducing body fat gradually."
        )

        if target_steps:
            guidance.append(
                f"Maintain the configured movement target of "
                f"{int(target_steps):,} steps per day unless "
                "recovery or symptoms justify reducing activity."
            )

        if target_strength:
            guidance.append(
                f"Aim for approximately "
                f"{int(target_strength)} strength sessions "
                "per week when recovery supports training."
            )

        if body_fat_gap is not None:
            if body_fat_gap > 0:
                guidance.append(
                    f"Current Hume body fat is about "
                    f"{body_fat_gap:.1f} percentage points "
                    f"above the {target_body_fat:.1f}% target."
                )
            else:
                guidance.append(
                    "The current Hume body-fat reading is "
                    "at or below the configured target."
                )

        if target_protein:
            guidance.append(
                f"Configured protein target is "
                f"{int(target_protein)} g/day; intake adherence "
                "will be evaluated once nutrition data is integrated."
            )

    elif phase == "maintenance":
        guidance.append(
            "Maintenance priority: keep body composition broadly "
            "stable while sustaining strength, recovery, and activity consistency."
        )

        if target_steps:
            guidance.append(
                f"Maintain approximately "
                f"{int(target_steps):,} steps per day "
                "as the configured activity target."
            )

        if target_strength:
            guidance.append(
                f"Maintain approximately "
                f"{int(target_strength)} strength sessions "
                "per week when recovery supports training."
            )

    elif phase == "lean_bulk":
        guidance.append(
            "Lean Bulk priority: support progressive strength "
            "training and controlled weight gain while limiting "
            "unnecessary fat gain."
        )

        if target_strength:
            guidance.append(
                f"Prioritize approximately "
                f"{int(target_strength)} strength sessions "
                "per week when recovery supports training."
            )

        if target_steps:
            guidance.append(
                f"Use {int(target_steps):,} daily steps as an "
                "activity consistency target rather than maximizing "
                "calorie expenditure."
            )

        if target_protein:
            guidance.append(
                f"Configured protein target is "
                f"{int(target_protein)} g/day; intake adherence "
                "will be evaluated once nutrition data is integrated."
            )

    return {
        "active":
            True,

        "phase":
            phase,

        "phase_start_date":
            goal.get(
                "phase_start_date"
            ),

        "target_body_fat_percentage":
            target_body_fat,

        "target_weight_lb":
            target_weight_lb,

        "daily_step_target":
            target_steps,

        "strength_sessions_per_week":
            target_strength,

        "protein_target_grams":
            target_protein,

        "current_hume_body_fat_percentage":
            current_body_fat,

        "body_fat_percentage_points_to_target":
            (
                round(
                    body_fat_gap,
                    1
                )
                if body_fat_gap is not None
                else None
            ),

        "current_hume_weight_lb":
            (
                round(
                    current_weight_lb,
                    1
                )
                if current_weight_lb is not None
                else None
            ),

        "activity_trend":
            activity_trend,

        "guidance":
            guidance,
    }


def combined_deterministic_coaching():

    snapshot = (
        combined_daily_snapshot()
    )

    coaching_date = (
        datetime.fromisoformat(
            snapshot[
                "coaching_date"
            ]
        ).date()
    )

    if not snapshot[
        "data_readiness"
    ][
        "combined_coaching_ready"
    ]:

        return {
            "status":
                "not_ready",

            "coaching_date":
                snapshot[
                    "coaching_date"
                ],

            "data_readiness":
                snapshot[
                    "data_readiness"
                ],

            "message":
                "Current WHOOP physiology "
                "is not ready for coaching.",

            "notes":
                snapshot.get(
                    "notes",
                    []
                ),
        }

    baselines = _today_baselines(
        coaching_date
    )

    whoop_rec = (
        _current_whoop_recommendation(
            coaching_date
        )
    )

    if whoop_rec:

        training = (
            whoop_rec.get(
                "training_recommendation",
                "Normal"
            )
        )

        overall = (
            whoop_rec.get(
                "overall_status",
                "Current WHOOP readiness available"
            )
        )

        confidence = (
            whoop_rec.get(
                "confidence",
                "moderate"
            )
        )

    else:

        training = "Normal"

        overall = (
            "WHOOP recommendation "
            "not yet regenerated today"
        )

        confidence = "low"

    physiology_reasons = []

    for metric in (
        "recovery_score",
        "hrv_rmssd_milli",
        "resting_heart_rate",
        "sleep_duration_hours",
    ):

        reason = _metric_reason(
            metric,
            baselines
        )

        if reason:
            physiology_reasons.append(
                reason
            )

    trends = (
        apple_health_trends()
    )

    activity_trends = (
        trends.get(
            "activity"
        )
        or {}
    )

    body_trends = (
        trends.get(
            "body_composition"
        )
        or {}
    )

    activity_trend = (
        _activity_trend_summary(
            activity_trends
        )
    )

    weight_trend = (
        _body_metric_trend(
            "weight",
            body_trends.get(
                "weight"
            ),
        )
    )

    body_fat_trend = (
        _body_metric_trend(
            "body_fat_percentage",
            body_trends.get(
                "body_fat_percentage"
            ),
        )
    )

    lean_mass_trend = (
        _body_metric_trend(
            "lean_body_mass",
            body_trends.get(
                "lean_body_mass"
            ),
        )
    )

    active_goal = get_active_goal()

    goal_context = _goal_context(
        active_goal,
        body_trends,
        activity_trend,
    )

    configured_step_target = (
        goal_context.get(
            "daily_step_target"
        )
        if goal_context.get(
            "active"
        )
        else None
    )

    daily_step_target = int(
        configured_step_target
        or DEFAULT_DAILY_STEP_TARGET
    )

    activity = (
        snapshot.get(
            "activity"
        )
        or {}
    )

    steps_value = activity.get(
        "steps"
    )

    steps = int(
        steps_value or 0
    )

    local_now = (
        datetime.fromisoformat(
            snapshot[
                "local_now"
            ]
        )
    )

    hour = local_now.hour

    if steps_value is None:

        activity_status = (
            "not_yet_available"
        )

        remaining = (
            daily_step_target
        )

        progress = None

        activity_action = (
            "Today's activity has not populated yet. "
            "Use the historical activity trend rather than "
            "interpreting the current partial day."
        )

    else:

        remaining = max(
            0,
            daily_step_target
            - steps
        )

        progress = (
            (
                steps
                / daily_step_target
            )
            * 100.0
            if daily_step_target > 0
            else None
        )

        if remaining == 0:

            activity_status = (
                "target_met"
            )

            activity_action = (
                "Daily step target is already met; "
                "additional movement can be based on "
                "goal phase, preference, and recovery."
            )

        elif hour < 12:

            activity_status = (
                "in_progress"
            )

            activity_action = (
                f"Build movement through the day; "
                f"about {remaining:,} steps remain "
                "to the configured daily target."
            )

        elif hour < 18:

            activity_status = (
                "below_target_so_far"
            )

            activity_action = (
                f"Add purposeful walking this afternoon; "
                f"about {remaining:,} steps remain "
                "to the configured daily target."
            )

        else:

            activity_status = (
                "below_target_late_day"
            )

            activity_action = (
                f"Activity is below the configured "
                f"daily target; about {remaining:,} "
                "steps remain. Use an easy walk if "
                "it fits recovery and schedule."
            )

    body = (
        snapshot.get(
            "body_composition"
        )
        or {}
    )

    body_context = []

    weight = body.get(
        "weight"
    )

    body_fat = body.get(
        "body_fat_percentage"
    )

    lean_mass = body.get(
        "lean_body_mass"
    )

    if weight:
        body_context.append(
            f"Most recent Hume weight "
            f"is {weight['lb']:.1f} lb."
        )

    if body_fat:
        body_context.append(
            f"Most recent Hume body fat "
            f"is {body_fat['value']:.1f}%."
        )

    if (
        lean_mass
        and not lean_mass.get(
            "coaching_eligible"
        )
    ):
        body_context.append(
            "Lean body mass is excluded from current coaching "
            "because the latest measurement is not from the "
            "preferred current source."
        )

    trend_observations = []

    activity_change = (
        activity_trend.get(
            "seven_day_vs_30_day_pct"
        )
    )

    if activity_change is not None:
        trend_observations.append(
            f"7-day average steps are "
            f"{activity_change:+.1f}% versus "
            "the 30-day activity baseline."
        )

    if (
        weight_trend[
            "status"
        ]
        == "usable"
    ):

        best = (
            weight_trend[
                "usable_windows"
            ][0]
        )

        trend_observations.append(
            f"Hume weight has sufficient "
            f"{best['window_days']}-day observation coverage; "
            f"current value is "
            f"{best['pct_vs_baseline']:+.1f}% versus that "
            "source-consistent baseline."
        )

    else:

        trend_observations.append(
            "Hume weight history is not yet deep enough "
            "for a robust longer-term trend."
        )

    if (
        body_fat_trend[
            "status"
        ]
        == "usable"
    ):

        best = (
            body_fat_trend[
                "usable_windows"
            ][0]
        )

        trend_observations.append(
            f"Hume body fat has sufficient "
            f"{best['window_days']}-day observation coverage; "
            f"current value is "
            f"{best['pct_vs_baseline']:+.1f}% versus that "
            "source-consistent baseline."
        )

    else:

        trend_observations.append(
            "Hume body-fat history is not yet deep enough "
            "for a robust longer-term trend."
        )

    actions = []

    if whoop_rec:

        existing = (
            whoop_rec.get(
                "highest_impact_actions"
            )
            or []
        )

        if existing:
            actions.append(
                existing[0]
            )

    if goal_context.get(
        "active"
    ):

        phase = goal_context.get(
            "phase"
        )

        if (
            phase == "lean_cut"
            and training in (
                "Push",
                "Normal",
            )
        ):

            actions.append(
                "Prioritize a high-quality strength session "
                "to support lean-mass retention during the cut."
            )

        elif (
            phase == "lean_bulk"
            and training in (
                "Push",
                "Normal",
            )
        ):

            actions.append(
                "Use today's readiness to prioritize "
                "progressive strength training."
            )

        elif (
            phase == "maintenance"
            and training in (
                "Push",
                "Normal",
            )
        ):

            actions.append(
                "Train normally and prioritize consistency "
                "rather than adding unnecessary volume."
            )

    actions.append(
        activity_action
    )

    return {
        "status":
            "ok",

        "coaching_date":
            snapshot[
                "coaching_date"
            ],

        "overall_status":
            overall,

        "training_recommendation":
            training,

        "confidence":
            confidence,

        "active_goal":
            goal_context,

        "physiology_reasons":
            physiology_reasons[:4],

        "activity_guidance": {
            "steps_so_far":
                steps_value,

            "configured_step_target":
                daily_step_target,

            "step_progress_percentage":
                (
                    round(
                        progress,
                        1
                    )
                    if progress is not None
                    else None
                ),

            "steps_remaining":
                remaining,

            "status":
                activity_status,

            "active_energy_kcal":
                activity.get(
                    "active_energy_kcal"
                ),

            "resting_energy_kcal":
                activity.get(
                    "resting_energy_kcal"
                ),

            "walking_running_distance_km":
                activity.get(
                    "walking_running_distance_km"
                ),

            "action":
                activity_action,
        },

        "trend_context": {
            "activity":
                activity_trend,

            "weight":
                weight_trend,

            "body_fat_percentage":
                body_fat_trend,

            "lean_body_mass":
                lean_mass_trend,

            "observations":
                trend_observations,

            "body_trend_minimum_observations":
                BODY_TREND_MIN_OBSERVATIONS,
        },

        "body_composition_context":
            body_context,

        "highest_impact_actions":
            actions[:3],

        "data_readiness":
            snapshot[
                "data_readiness"
            ],

        "safety_note":
            (
                "Training guidance is based on personal "
                "wearable trends and is not a medical diagnosis. "
                "Symptoms, illness, injury, medication changes, "
                "or clinician advice should override wearable-based "
                "recommendations."
            ),

        "interpretation_note":
            (
                "WHOOP remains authoritative for readiness and training. "
                "The active goal profile changes how activity and "
                "body-composition context are prioritized, but it does "
                "not override recovery-based safety or training-readiness signals."
            ),
    }
