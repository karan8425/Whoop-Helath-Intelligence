import os
from datetime import datetime, timedelta
from math import isfinite
from statistics import mean
from zoneinfo import ZoneInfo

from db import get_conn


EASTERN = ZoneInfo(
    "America/New_York"
)


# ============================================================
# CONFIGURATION
# ============================================================

MIN_SLEEP_TARGET_HOURS = 7.5
BASE_SLEEP_TARGET_HOURS = 8.0
MAX_SLEEP_TARGET_HOURS = 9.0
MAX_WHOOP_SLEEP_TARGET_HOURS = 10.5

# Raw WHOOP sleep efficiency is useful context, but extremely
# high single-night values should not make our planned sleep
# opportunity unrealistically tight.
MIN_PLANNING_EFFICIENCY = 85.0
MAX_PLANNING_EFFICIENCY = 95.0
DEFAULT_PLANNING_EFFICIENCY = 92.0

LOOKBACK_DAYS = 30

# Used to classify whether recent sleep duration is materially
# different from the longer personal baseline.
TREND_THRESHOLD_HOURS = 0.25

# Even an improving trend remains below target if recent sleep
# is materially below the current prescribed sleep opportunity.
BELOW_TARGET_THRESHOLD_HOURS = 0.50


# ============================================================
# HELPERS
# ============================================================

def _float(value):

    if value is None:
        return None

    try:
        result = float(value)
    except (TypeError, ValueError):
        return None

    return result if isfinite(result) else None


def _round(
    value,
    digits=2,
):

    if value is None:
        return None

    return round(
        float(value),
        digits,
    )


def _clamp(
    value,
    minimum,
    maximum,
):

    return max(
        minimum,
        min(
            maximum,
            value,
        ),
    )


def _hours_to_minutes(
    hours,
):

    return int(
        round(
            float(hours)
            * 60
        )
    )


def _round_to_quarter_hour(hours):
    return round(float(hours) * 4) / 4


def _recovery_band(score):
    if score is None:
        return "unknown"
    if float(score) < 34:
        return "red"
    if float(score) < 67:
        return "yellow"
    return "green"


def _minutes_to_text(
    minutes,
):

    minutes = max(
        0,
        int(
            round(
                minutes
            )
        ),
    )

    hours = (
        minutes
        // 60
    )

    remaining = (
        minutes
        % 60
    )

    return (
        f"{hours}h {remaining:02d}m"
    )


def _parse_wake_time():

    raw = os.getenv(
        "DEFAULT_WAKE_TIME_LOCAL",
        "",
    ).strip()

    if not raw:
        return None

    try:

        parsed = datetime.strptime(
            raw,
            "%H:%M",
        )

    except ValueError:
        return None

    return {
        "hour":
            parsed.hour,

        "minute":
            parsed.minute,

        "display":
            parsed.strftime(
                "%I:%M %p"
            ).lstrip("0"),
    }


# ============================================================
# WHOOP SLEEP HISTORY
# ============================================================

def _load_sleep_history():

    with get_conn() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    m.metric_date,
                    m.recovery_score,
                    m.sleep_duration_hours,
                    m.sleep_performance_percentage,
                    m.sleep_consistency_percentage,
                    m.sleep_efficiency_percentage,
                    m.has_sleep,
                    m.has_recovery,
                    CASE
                        WHEN s.score->'sleep_needed' IS NULL
                        THEN NULL
                        ELSE (
                            COALESCE((s.score->'sleep_needed'->>'baseline_milli')::double precision, 0)
                            + COALESCE((s.score->'sleep_needed'->>'need_from_sleep_debt_milli')::double precision, 0)
                            + COALESCE((s.score->'sleep_needed'->>'need_from_recent_strain_milli')::double precision, 0)
                            + COALESCE((s.score->'sleep_needed'->>'need_from_recent_nap_milli')::double precision, 0)
                        ) / 3600000.0
                    END AS sleep_need_hours
                FROM public.whoop_daily_metrics m
                LEFT JOIN public.whoop_sleeps s
                    ON s.id = m.sleep_id
                WHERE
                    m.metric_date >= CURRENT_DATE - INTERVAL '30 days'
                ORDER BY m.metric_date DESC
                """
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
# BASELINES
# ============================================================

def _average_metric(
    rows,
    metric,
    limit=None,
):

    values = []

    for row in rows:

        value = row.get(
            metric
        )

        if value is None:
            continue

        values.append(
            float(
                value
            )
        )

        if (
            limit is not None
            and len(
                values
            ) >= limit
        ):
            break

    if not values:
        return None

    return mean(
        values
    )


def _latest_complete_sleep(
    rows,
):

    for row in rows:

        if (
            row.get(
                "has_sleep"
            )
            and row.get(
                "sleep_duration_hours"
            )
            is not None
        ):

            return row

    return None


# ============================================================
# TARGET CALCULATION
# ============================================================

def _calculate_sleep_target(
    latest,
    rows,
    training=None,
):

    whoop_sleep_need = _float(
        latest.get("sleep_need_hours")
    )
    target = (
        whoop_sleep_need
        if whoop_sleep_need is not None and whoop_sleep_need > 0
        else BASE_SLEEP_TARGET_HOURS
    )
    sleep_need_source = (
        "whoop_sleep_need"
        if whoop_sleep_need is not None and whoop_sleep_need > 0
        else "personal_history_fallback"
    )

    reasons = []

    if sleep_need_source == "whoop_sleep_need":
        reasons.append(
            f"WHOOP sleep need ({whoop_sleep_need:.2f} h) is the primary anchor."
        )
    else:
        reasons.append(
            "WHOOP sleep need is unavailable, so the target uses recent personal sleep history and recovery."
        )

    recovery = _float(
        latest.get(
            "recovery_score"
        )
    )

    sleep_duration = _float(
        latest.get(
            "sleep_duration_hours"
        )
    )

    sleep_performance = _float(
        latest.get(
            "sleep_performance_percentage"
        )
    )

    sleep_consistency = _float(
        latest.get(
            "sleep_consistency_percentage"
        )
    )

    average_7 = (
        _average_metric(
            rows,
            "sleep_duration_hours",
            limit=7,
        )
    )

    average_30 = (
        _average_metric(
            rows,
            "sleep_duration_hours",
            limit=30,
        )
    )

    if sleep_need_source == "personal_history_fallback":
        personal_anchor = (
            max(BASE_SLEEP_TARGET_HOURS, average_30)
            if average_30 is not None
            else BASE_SLEEP_TARGET_HOURS
        )
        target = personal_anchor
        reasons.append(
            (
                f"The fallback anchor is {personal_anchor:.2f} h "
                "from the recent personal baseline with an "
                "8-hour undersleep floor."
            )
        )

    # --------------------------------------------------------
    # Recovery adjustment
    # --------------------------------------------------------

    if recovery is not None:

        if recovery < 34:

            target = max(target, BASE_SLEEP_TARGET_HOURS + 0.50)

            reasons.append(
                (
                    f"Recovery is {recovery:.0f}%, "
                    "so additional sleep opportunity is prioritized."
                )
            )

        elif recovery < 67:

            target = max(target, BASE_SLEEP_TARGET_HOURS + 0.25)

            reasons.append(
                (
                    f"Recovery is {recovery:.0f}%, "
                    "supporting a modest increase in tonight's sleep target."
                )
            )

        elif recovery >= 80:

            reasons.append(
                (
                    f"Recovery is {recovery:.0f}%; "
                    "there is no need to artificially extend sleep solely "
                    "because readiness is high."
                )
            )

    # --------------------------------------------------------
    # Last-night duration shortfall
    # --------------------------------------------------------

    if sleep_duration is not None:

        if sleep_duration < 6.0:

            target = max(target, BASE_SLEEP_TARGET_HOURS + 0.50)

            reasons.append(
                (
                    f"Last sleep was only {sleep_duration:.2f} hours."
                )
            )

        elif sleep_duration < 7.0:

            target = max(target, BASE_SLEEP_TARGET_HOURS + 0.25)

            reasons.append(
                (
                    f"Last sleep was {sleep_duration:.2f} hours, "
                    "below a full recovery-oriented night."
                )
            )

    # --------------------------------------------------------
    # Sleep performance
    # --------------------------------------------------------

    if (
        sleep_performance is not None
        and sleep_performance < 80
    ):

        target = max(target, BASE_SLEEP_TARGET_HOURS + 0.25)

        reasons.append(
            (
                f"Sleep performance was {sleep_performance:.0f}%, "
                "supporting additional sleep opportunity."
            )
        )

    # --------------------------------------------------------
    # Sleep consistency
    # --------------------------------------------------------

    if (
        sleep_consistency is not None
        and sleep_consistency < 75
    ):

        reasons.append(
            (
                f"Sleep consistency is {sleep_consistency:.0f}%; "
                "bedtime consistency should be prioritized tonight."
            )
        )

    # --------------------------------------------------------
    # Recent sleep deterioration
    # --------------------------------------------------------

    if (
        average_7 is not None
        and average_30 is not None
        and average_7
        < average_30 - 0.40
    ):

        target = max(target, BASE_SLEEP_TARGET_HOURS + 0.25)

        reasons.append(
            (
                f"7-day sleep average ({average_7:.2f} h) "
                f"is below the 30-day average ({average_30:.2f} h)."
            )
        )

    planned_intensity = (
        (training or {}).get("planned_intensity")
        or (training or {}).get("category")
    )
    if (
        sleep_need_source == "personal_history_fallback"
        and str(planned_intensity).lower() in {
            "high", "hard", "high_intensity", "strength",
        }
    ):
        target += 0.25
        reasons.append(
            "A higher-intensity planned training day adds sleep opportunity to the fallback target."
        )

    maximum_target = (
        MAX_WHOOP_SLEEP_TARGET_HOURS
        if sleep_need_source == "whoop_sleep_need"
        else MAX_SLEEP_TARGET_HOURS
    )

    target = _round_to_quarter_hour(_clamp(
        target,
        MIN_SLEEP_TARGET_HOURS,
        maximum_target,
    ))

    return (
        target,
        reasons,
        average_7,
        average_30,
        sleep_need_source,
    )


# ============================================================
# SLEEP TREND
# ============================================================

def _sleep_trend(
    average_7,
    average_30,
    sleep_target,
):

    if (
        average_7 is None
        or average_30 is None
    ):

        return {
            "status":
                "insufficient_data",

            "direction":
                "unknown",

            "versus_30d_hours":
                None,

            "gap_to_target_hours":
                None,

            "summary":
                (
                    "More sleep history is needed "
                    "to evaluate the recent trend."
                ),
        }

    difference = (
        average_7
        - average_30
    )

    gap_to_target = (
        sleep_target
        - average_7
    )

    if (
        difference
        >= TREND_THRESHOLD_HOURS
    ):

        direction = (
            "improving"
        )

    elif (
        difference
        <= -TREND_THRESHOLD_HOURS
    ):

        direction = (
            "deteriorating"
        )

    else:

        direction = (
            "stable"
        )

    below_target = (
        gap_to_target
        >= BELOW_TARGET_THRESHOLD_HOURS
    )

    if (
        direction == "improving"
        and below_target
    ):

        status = (
            "improving_but_below_target"
        )

        summary = (
            f"Recent sleep is improving: the 7-day average "
            f"is {average_7:.2f} hours versus "
            f"{average_30:.2f} hours over 30 days. "
            f"It remains {gap_to_target:.2f} hours below "
            "tonight's sleep target."
        )

    elif direction == "improving":

        status = (
            "improving"
        )

        summary = (
            "Recent sleep duration is improving and is "
            "approaching the current sleep target."
        )

    elif (
        direction == "deteriorating"
        and below_target
    ):

        status = (
            "deteriorating_and_below_target"
        )

        summary = (
            f"Recent sleep is deteriorating: the 7-day average "
            f"is {average_7:.2f} hours versus "
            f"{average_30:.2f} hours over 30 days, and remains "
            f"{gap_to_target:.2f} hours below tonight's target."
        )

    elif direction == "deteriorating":

        status = (
            "deteriorating"
        )

        summary = (
            "Recent sleep duration has declined versus "
            "the longer personal baseline."
        )

    elif below_target:

        status = (
            "stable_but_below_target"
        )

        summary = (
            f"Recent sleep duration is relatively stable, "
            f"but the 7-day average remains "
            f"{gap_to_target:.2f} hours below tonight's target."
        )

    else:

        status = (
            "stable_near_target"
        )

        summary = (
            "Recent sleep duration is stable and reasonably "
            "close to the current sleep target."
        )

    return {
        "status":
            status,

        "direction":
            direction,

        "versus_30d_hours":
            _round(
                difference,
                2,
            ),

        "gap_to_target_hours":
            _round(
                max(
                    0.0,
                    gap_to_target,
                ),
                2,
            ),

        "summary":
            summary,
    }


# ============================================================
# TIME IN BED
# ============================================================

def _planning_efficiency(
    observed_efficiency,
):

    if observed_efficiency is None:

        return (
            DEFAULT_PLANNING_EFFICIENCY,
            "default",
        )

    observed = float(
        observed_efficiency
    )

    if observed < MIN_PLANNING_EFFICIENCY:

        return (
            MIN_PLANNING_EFFICIENCY,
            "bounded_low",
        )

    if observed > MAX_PLANNING_EFFICIENCY:

        return (
            MAX_PLANNING_EFFICIENCY,
            "bounded_high",
        )

    return (
        observed,
        "observed",
    )


def _time_in_bed_target(
    sleep_target_hours,
    efficiency_percentage,
):

    (
        planning_efficiency,
        efficiency_source,
    ) = _planning_efficiency(
        efficiency_percentage
    )

    efficiency_fraction = (
        planning_efficiency
        / 100.0
    )

    time_in_bed = (
        sleep_target_hours
        / efficiency_fraction
    )

    return (
        time_in_bed,
        planning_efficiency,
        efficiency_source,
    )


# ============================================================
# BEDTIME
# ============================================================

def _recommended_bedtime(
    time_in_bed_hours,
):

    wake = (
        _parse_wake_time()
    )

    if not wake:

        return {
            "available":
                False,

            "wake_time_local":
                None,

            "recommended_bedtime_local":
                None,

            "bedtime_date":
                None,

            "reason":
                (
                    "Configure DEFAULT_WAKE_TIME_LOCAL "
                    "before calculating bedtime."
                ),
        }

    now_local = (
        datetime.now(
            EASTERN
        )
    )

    tomorrow = (
        now_local.date()
        + timedelta(
            days=1
        )
    )

    wake_datetime = (
        datetime(
            tomorrow.year,
            tomorrow.month,
            tomorrow.day,
            wake[
                "hour"
            ],
            wake[
                "minute"
            ],
            tzinfo=EASTERN,
        )
    )

    bedtime = (
        wake_datetime
        - timedelta(
            hours=
                time_in_bed_hours
        )
    )

    return {
        "available":
            True,

        "wake_time_local":
            wake[
                "display"
            ],

        "recommended_bedtime_local":
            bedtime.strftime(
                "%I:%M %p"
            ).lstrip(
                "0"
            ),

        "bedtime_date":
            bedtime.date()
            .isoformat(),

        "reason":
            (
                "Bedtime is calculated backward from the "
                "configured wake time using tonight's "
                "recommended time-in-bed target."
            ),
    }


# ============================================================
# PUBLIC ENGINE
# ============================================================

def build_sleep_prescription(rows=None, training=None):

    if rows is None:
        rows = _load_sleep_history()

    latest = (
        _latest_complete_sleep(
            rows
        )
    )

    if not latest:

        return {
            "status":
                "not_ready",

            "reason":
                (
                    "No recent complete WHOOP sleep "
                    "record is available."
                ),
        }

    (
        sleep_target,
        reasons,
        average_7,
        average_30,
        sleep_need_source,
    ) = _calculate_sleep_target(
        latest,
        rows,
        training=training,
    )

    trend = (
        _sleep_trend(
            average_7,
            average_30,
            sleep_target,
        )
    )

    observed_efficiency = _float(
        latest.get(
            "sleep_efficiency_percentage"
        )
    )

    (
        time_in_bed,
        efficiency_used,
        efficiency_source,
    ) = _time_in_bed_target(
        sleep_target,
        observed_efficiency,
    )

    bedtime = (
        _recommended_bedtime(
            time_in_bed
        )
    )

    target_minutes = (
        _hours_to_minutes(
            sleep_target
        )
    )

    bed_minutes = (
        _hours_to_minutes(
            time_in_bed
        )
    )

    priority = (
        "Protect tonight's sleep opportunity and "
        "maintain a consistent bedtime."
    )

    if (
        trend.get(
            "status"
        )
        == "improving_but_below_target"
    ):

        priority = (
            "Keep the recent improvement going, but create "
            "enough sleep opportunity to close the remaining deficit."
        )

    elif (
        trend.get(
            "direction"
        )
        == "deteriorating"
    ):

        priority = (
            "Reverse the recent sleep decline by protecting "
            "tonight's bedtime and full sleep opportunity."
        )

    return {
        "status":
            "ok",

        "version":
            "1.1",

        "metric_date":
            (
                latest.get(
                    "metric_date"
                ).isoformat()
                if latest.get(
                    "metric_date"
                )
                else None
            ),

        "sleep_target_hours":
            _round(
                sleep_target,
                2,
            ),

        "target_sleep_hours": _round(sleep_target, 2),
        "target_sleep_minutes": target_minutes,
        "target_bedtime": (
            bedtime.get("recommended_bedtime_local")
            if bedtime.get("available")
            else None
        ),
        "recovery_score": _round(latest.get("recovery_score"), 0),
        "recovery_band": _recovery_band(latest.get("recovery_score")),
        "sleep_need_source": sleep_need_source,
        "recent_sleep_average": _round(average_7, 2),
        "confidence": (
            "high" if sleep_need_source == "whoop_sleep_need" else "moderate"
        ),

        "sleep_target_minutes":
            target_minutes,

        "sleep_target_display":
            _minutes_to_text(
                target_minutes
            ),

        "time_in_bed_target_hours":
            _round(
                time_in_bed,
                2,
            ),

        "time_in_bed_target_minutes":
            bed_minutes,

        "time_in_bed_target_display":
            _minutes_to_text(
                bed_minutes
            ),

        "recommended_schedule":
            bedtime,

        "latest_sleep": {

            "duration_hours":
                _round(
                    latest.get(
                        "sleep_duration_hours"
                    ),
                    2,
                ),

            "recovery_score":
                _round(
                    latest.get(
                        "recovery_score"
                    ),
                    0,
                ),

            "sleep_performance_percentage":
                _round(
                    latest.get(
                        "sleep_performance_percentage"
                    ),
                    1,
                ),

            "sleep_consistency_percentage":
                _round(
                    latest.get(
                        "sleep_consistency_percentage"
                    ),
                    1,
                ),

            "sleep_efficiency_percentage":
                _round(
                    observed_efficiency,
                    1,
                ),
        },

        "baselines": {

            "average_sleep_7d_hours":
                _round(
                    average_7,
                    2,
                ),

            "average_sleep_30d_hours":
                _round(
                    average_30,
                    2,
                ),
        },

        "trend":
            trend,

        "planning_efficiency": {

            "observed_percentage":
                _round(
                    observed_efficiency,
                    1,
                ),

            "used_percentage":
                _round(
                    efficiency_used,
                    1,
                ),

            "source":
                efficiency_source,

            "maximum_percentage":
                MAX_PLANNING_EFFICIENCY,
        },

        "priority":
            priority,

        "rationale":
            reasons,

        "guardrails": [
            (
                "The sleep target responds to recovery, recent "
                "sleep duration, sleep performance and personal trends."
            ),

            (
                "Fallback sleep targets are constrained to a "
                "practical 7.5-to-9-hour range. Valid WHOOP "
                "sleep need can exceed 9 hours and is bounded "
                "at 10.5 hours only as a data-quality guardrail."
            ),

            (
                "Observed sleep efficiency is bounded between "
                "85% and 95% for planning so extreme single-night "
                "values do not create unrealistic bedtime targets."
            ),

            (
                "The 7-day sleep average is compared with the "
                "30-day personal baseline to distinguish improving, "
                "stable and deteriorating trends."
            ),

            (
                "Bedtime is calculated only when a wake time "
                "has been explicitly configured."
            ),
        ],
    }


# ============================================================
# LOCAL TEST
# ============================================================

def main():

    result = (
        build_sleep_prescription()
    )

    print()
    print(
        "SLEEP PRESCRIPTION V1.1"
    )
    print(
        "=" * 78
    )

    print(
        "Status:",
        result.get(
            "status"
        ),
    )

    if (
        result.get(
            "status"
        )
        != "ok"
    ):

        print(
            "Reason:",
            result.get(
                "reason"
            ),
        )

        return

    print(
        "Sleep target:",
        result.get(
            "sleep_target_display"
        ),
    )

    print(
        "Time in bed target:",
        result.get(
            "time_in_bed_target_display"
        ),
    )

    latest = (
        result.get(
            "latest_sleep"
        )
        or {}
    )

    print(
        "Latest sleep:",
        latest.get(
            "duration_hours"
        ),
        "hours",
    )

    print(
        "Recovery:",
        latest.get(
            "recovery_score"
        ),
    )

    print(
        "Sleep performance:",
        latest.get(
            "sleep_performance_percentage"
        ),
    )

    print(
        "Consistency:",
        latest.get(
            "sleep_consistency_percentage"
        ),
    )

    print(
        "Observed efficiency:",
        latest.get(
            "sleep_efficiency_percentage"
        ),
    )

    planning = (
        result.get(
            "planning_efficiency"
        )
        or {}
    )

    print(
        "Planning efficiency:",
        planning.get(
            "used_percentage"
        ),
    )

    print(
        "Planning efficiency source:",
        planning.get(
            "source"
        ),
    )

    baselines = (
        result.get(
            "baselines"
        )
        or {}
    )

    print(
        "7-day sleep average:",
        baselines.get(
            "average_sleep_7d_hours"
        ),
    )

    print(
        "30-day sleep average:",
        baselines.get(
            "average_sleep_30d_hours"
        ),
    )

    trend = (
        result.get(
            "trend"
        )
        or {}
    )

    print(
        "Sleep trend:",
        trend.get(
            "status"
        ),
    )

    print(
        "Trend summary:",
        trend.get(
            "summary"
        ),
    )

    schedule = (
        result.get(
            "recommended_schedule"
        )
        or {}
    )

    print(
        "Wake time:",
        schedule.get(
            "wake_time_local"
        ),
    )

    print(
        "Recommended bedtime:",
        schedule.get(
            "recommended_bedtime_local"
        ),
    )

    print()
    print(
        "Priority:",
        result.get(
            "priority"
        ),
    )

    print()
    print(
        "Rationale:"
    )

    for reason in (
        result.get(
            "rationale"
        )
        or []
    ):

        print(
            f"  • {reason}"
        )

    print(
        "=" * 78
    )


if __name__ == "__main__":
    main()
