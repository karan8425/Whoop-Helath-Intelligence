"""Goal Progress V2 - longitudinal goal intelligence.

V1 (`goal_progress()`) answers only "how is the current phase going" for
body composition + a couple of behaviours. V2 keeps every V1 key intact
(backward compatible) and layers structured sections on top:

    phase                - the active goal phase (unchanged concept)
    summary              - overall status + progress toward targets + timeline
    outcomes             - weight / body fat / fat mass / lean mass
    drivers              - steps / strength / sleep / hydration / calories / protein
    physiology           - HRV / resting HR / recovery / VO2 max
    historical_context   - per-metric windows 7D..1Y, NOT truncated at phase start
    intelligence         - deterministic synthesis (no LLM)
    goal_timeline        - required vs observed rate, projection (or not_configured)

Every metric is expressed with the normalized contract in `_metric()` and a
typed availability status (`ok` / `insufficient_history` / `not_connected`
/ `unavailable` / `stale`).

Data sources are composed from the existing providers only - no new
integrations, no migration:

    body_composition_progress()   - Hume current + Hume/Fitdays historical
    apple_health_trends()         - Apple steps, Apple/Hume body windows
    strength_adherence()          - Tonal qualifying sessions (rules reused verbatim)
    whoop_daily_metrics           - HRV / RHR / recovery / sleep windows
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from db import get_conn
from goals import get_active_goal
from goal_progress import goal_progress
from apple_health_trends import apple_health_trends
from body_composition_progress import body_composition_progress
from integrations.tonal.strength_adherence import strength_adherence


EASTERN = ZoneInfo("America/New_York")
KG_TO_LB = 2.2046226218
MIN_PHASE_AGE_DAYS = 7

# Historical-context windows. These control HISTORICAL CONTEXT ONLY and are
# never clipped at phase start.
HISTORY_WINDOWS = {
    "7D": 7,
    "14D": 14,
    "30D": 30,
    "90D": 90,
    "6M": 180,
    "1Y": 365,
}

# Personal-baseline windows for the normalized metric contract.
BASELINE_WINDOWS = (7, 14, 30, 90)

# Deterministic trend thresholds, expressed as a fraction of the reference
# average. Anything inside the band is "flat" (daily variability, not a
# sustained trend). Documented per metric in METHODOLOGY below.
TREND_BAND = {
    "weight": 0.005,               # 0.5 %
    "body_fat_percentage": 0.01,   # 1 % relative
    "fat_mass": 0.01,
    "lean_mass": 0.005,
    "steps": 0.05,                 # 5 %
    "hrv": 0.04,                   # 4 %
    "resting_heart_rate": 0.02,    # 2 %
    "recovery": 0.05,
    "sleep_duration": 0.04,
}

# Which movement direction is favourable for each metric. Interpretation
# (favorable / unfavorable / neutral / context_dependent) is derived from
# this - NOT from the raw mathematical direction. `context_dependent` means
# the phase/goal trajectory decides (weight, and fat in a lean bulk).
FAVORABLE_DIRECTION = {
    "hrv": "increasing",
    "recovery": "increasing",
    "resting_heart_rate": "decreasing",
    "sleep_duration": "increasing",
    "steps": "increasing",
    "lean_mass": "increasing",
    "body_fat": "context_dependent",
    "fat_mass": "context_dependent",
    "weight": "context_dependent",
}

# For body composition, the favourable direction depends on the phase.
PHASE_FAT_FAVORS_DECREASE = {"lean_cut", "maintenance"}


def _semantic(metric_key, direction, *, phase=None, goal_direction=None):
    """Map a mathematical direction to a health interpretation.

    Returns (interpretation, reason). interpretation is one of
    favorable / unfavorable / neutral / context_dependent / insufficient_data.
    Colour in the UI is driven by interpretation, never by direction.
    """

    if direction in (None, "insufficient_data"):
        return "insufficient_data", "Not enough data to interpret."
    if direction == "flat":
        return "neutral", "Change is within normal daily variability."

    favors = FAVORABLE_DIRECTION.get(metric_key, "increasing")

    if metric_key in ("body_fat", "fat_mass"):
        if phase in PHASE_FAT_FAVORS_DECREASE:
            favors = "decreasing"
        elif phase == "lean_bulk":
            return (
                "context_dependent",
                "In a lean bulk some fat gain is expected; judge against "
                "the plan.",
            )
        else:
            favors = "decreasing"

    if metric_key == "weight":
        if goal_direction in ("decrease", "increase"):
            favors = "decreasing" if goal_direction == "decrease" else "increasing"
            good = direction == favors
            return (
                ("favorable" if good else "unfavorable"),
                (
                    "Weight is moving toward the goal target."
                    if good else
                    "Weight is moving away from the goal target."
                ),
            )
        return (
            "context_dependent",
            "Weight direction is only meaningful against the goal trajectory.",
        )

    good = direction == favors
    return (
        ("favorable" if good else "unfavorable"),
        (
            f"{metric_key.replace('_', ' ').title()} {direction} is "
            f"{'favourable' if good else 'unfavourable'} for this goal."
        ),
    )


METHODOLOGY = {
    "phase_vs_history": (
        "Phase progress is scored only from measurements on or after "
        "the active goal's phase_start_date. Historical context uses "
        "every available measurement and is never truncated at phase "
        "start."
    ),
    "trend": (
        "A metric is only called improving/declining when the current "
        "7-day average differs from the reference 7-day average by more "
        "than a metric-specific band (weight 0.5%, HRV 4%, RHR 2%, "
        "recovery 5%, steps 5%). Smaller moves are reported as flat "
        "(daily variability), not a sustained trend."
    ),
    "direction_vs_interpretation": (
        "`direction` (increasing / decreasing / flat) is the raw movement. "
        "`interpretation` (favorable / unfavorable / neutral / "
        "context_dependent) applies the goal context - e.g. a declining "
        "resting heart rate is favorable, a declining HRV is unfavorable, "
        "and weight is judged only against the goal trajectory."
    ),
    "sources": (
        "Current goal scoring uses the preferred source only (Hume for "
        "body composition, WHOOP for physiology/sleep, Apple Health for "
        "steps, Tonal for strength). Historical fallback sources are "
        "labelled and never merged into one unlabelled series."
    ),
    "baselines": (
        "Personal 7/14/30/90-day rolling averages. No population norms."
    ),
    "comparison": (
        "Outcome cards compare the current 7-day average with the "
        "immediately preceding 7-day window (period_start..period_end). "
        "Phase change is a separate comparison against the phase-start value."
    ),
}


# ============================================================
# SMALL HELPERS
# ============================================================

def _round(value, digits=1):
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _today_eastern():
    return datetime.now(timezone.utc).astimezone(EASTERN).date()


def _mean(values):
    values = [float(v) for v in values if v is not None]
    if not values:
        return None
    return sum(values) / len(values)


def _iso(value):
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _trend_from_delta(metric_key, current_avg, reference_avg, lower_is_better):
    """Deterministic three-state trend. Returns (trend, delta, delta_pct)."""

    if current_avg is None or reference_avg is None or reference_avg == 0:
        return "insufficient_data", None, None

    delta = current_avg - reference_avg
    delta_pct = delta / abs(reference_avg) * 100.0
    band = TREND_BAND.get(metric_key, 0.02)

    if abs(delta) <= abs(reference_avg) * band:
        return "flat", _round(delta, 3), _round(delta_pct, 2)

    improving = (delta < 0) if lower_is_better else (delta > 0)
    return (
        ("improving" if improving else "declining"),
        _round(delta, 3),
        _round(delta_pct, 2),
    )


def _direction_word(delta, band_ok):
    if delta is None:
        return "insufficient_data"
    if band_ok:
        return "flat"
    return "increasing" if delta > 0 else "decreasing"


def _metric(
    *,
    name,
    display_name,
    unit,
    preferred_source,
    status,
    source=None,
    source_label=None,
    source_short=None,
    history_source=None,
    current_value=None,
    current_period_average=None,
    previous_period_average=None,
    trend=None,
    direction=None,
    interpretation=None,
    interpretation_reason=None,
    delta=None,
    delta_pct=None,
    comparison=None,
    record_count=None,
    earliest_date=None,
    latest_date=None,
    phase_start_value=None,
    phase_change=None,
    baselines=None,
    series_available=False,
    lower_is_better=None,
    note=None,
):
    """Normalized metric representation shared by every section."""

    return {
        "metric": name,
        "display_name": display_name,
        "unit": unit,
        "status": status,
        "preferred_source": preferred_source,
        "source": source,
        # Full provenance is retained here...
        "source_label": source_label or source,
        # ...and a concise pair for the card.
        "source_short": source_short or source,
        "history_source": history_source,
        "current_value": _round(current_value, 2),
        "current_period_average": _round(current_period_average, 2),
        "previous_period_average": _round(previous_period_average, 2),
        "delta": delta,
        "delta_pct": delta_pct,
        # Explicit "where was I -> where am I" comparison for the card.
        "comparison": comparison,
        # Raw movement...
        "direction": direction
        or ("insufficient_data" if status not in ("ok", "stale") else "flat"),
        # ...vs goal-aware interpretation (drives colour).
        "interpretation": interpretation
        or ("insufficient_data" if status not in ("ok", "stale") else "neutral"),
        "interpretation_reason": interpretation_reason,
        # trend kept for backward compatibility (improving/declining/flat).
        "trend": trend or ("insufficient_data" if status != "ok" else "flat"),
        "record_count": record_count,
        "earliest_date": _iso(earliest_date),
        "latest_date": _iso(latest_date),
        "phase_start_value": _round(phase_start_value, 2),
        "phase_change": _round(phase_change, 2),
        "baselines": baselines or {},
        "series_available": bool(series_available),
        "lower_is_better": lower_is_better,
        "note": note,
    }


def _build_comparison(series, end_date, *, label="vs previous 7 days"):
    """current 7-day window vs the immediately preceding 7-day window,
    with explicit dates and start -> current values."""

    cur = _window_slice(series, 7, end_date)
    prev_end = end_date - timedelta(days=7)
    prev = _window_slice(series, 7, prev_end)
    cur_avg = _mean([p["value"] for p in cur])
    prev_avg = _mean([p["value"] for p in prev])
    if cur_avg is None or prev_avg is None:
        return None
    abs_change = cur_avg - prev_avg
    pct_change = (abs_change / abs(prev_avg) * 100.0) if prev_avg else None
    return {
        "label": label,
        "period_start": (prev_end - timedelta(days=6)).isoformat(),
        "period_end": prev_end.isoformat(),
        "current_period_start": (end_date - timedelta(days=6)).isoformat(),
        "current_period_end": end_date.isoformat(),
        "start_value": _round(prev_avg, 2),
        "current_value": _round(cur_avg, 2),
        "absolute_change": _round(abs_change, 3),
        "percent_change": _round(pct_change, 2),
        "current_measurement_days": len(cur),
        "reference_measurement_days": len(prev),
    }


# ============================================================
# WHOOP DAILY WINDOW READER  (physiology + sleep)
# ============================================================

_WHOOP_COLUMNS = {
    "hrv": "hrv_rmssd_milli",
    "resting_heart_rate": "resting_heart_rate",
    "recovery": "recovery_score",
    "sleep_duration": "sleep_duration_hours",
    "sleep_performance": "sleep_performance_percentage",
    "sleep_consistency": "sleep_consistency_percentage",
}


def _load_whoop_daily(days=400):
    """Recent whoop_daily_metrics rows, oldest first."""

    cutoff = _today_eastern() - timedelta(days=days)
    cols = ", ".join(sorted(set(_WHOOP_COLUMNS.values())))
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT metric_date, {cols}
                FROM whoop_daily_metrics
                WHERE metric_date >= %s
                ORDER BY metric_date ASC
                """,
                (cutoff,),
            )
            return cur.fetchall()


def _whoop_series(rows, column):
    out = []
    for r in rows:
        v = r.get(column)
        if v is not None:
            out.append({"date": r["metric_date"], "value": float(v)})
    return out


def _load_apple_steps(days=400):
    """Full daily-step series from apple_health_daily_activity, oldest first.

    Used directly so the historical context and driver classification are not
    limited to the 7/14/30/90-day baselines that apple_health_trends computes.
    """

    cutoff = _today_eastern() - timedelta(days=days)
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT activity_date, steps
                    FROM apple_health_daily_activity
                    WHERE activity_date >= %s
                      AND steps IS NOT NULL
                      AND steps > 0
                    ORDER BY activity_date ASC
                    """,
                    (cutoff,),
                )
                rows = cur.fetchall()
    except Exception:
        return []
    return [
        {"date": r["activity_date"], "value": float(r["steps"])}
        for r in rows
    ]


# ============================================================
# WINDOW / SERIES ANALYSIS
# ============================================================

def _window_slice(series, days, end_date):
    start = end_date - timedelta(days=days)
    return [p for p in series if start <= p["date"] <= end_date]


def _period_averages(series, end_date):
    """Current 7-day mean vs the immediately preceding 7-day mean."""

    current = _window_slice(series, 7, end_date)
    prev_end = end_date - timedelta(days=7)
    previous = _window_slice(series, 7, prev_end)
    return (
        _mean([p["value"] for p in current]),
        _mean([p["value"] for p in previous]),
        len(current),
        len(previous),
    )


def _sustained_reference(series, end_date, prev_avg):
    """Reference average for sustained-trend classification.

    The mean of the ~23 days that precede the current 7-day window (i.e. the
    30-day window ending one week ago). This filters daily variability and
    short-term change so only a sustained move crosses the trend band. Falls
    back to a 14-day equivalent, then the immediately preceding 7-day mean.
    """

    for span in (30, 14):
        start = end_date - timedelta(days=span)
        stop = end_date - timedelta(days=7)
        pts = [p for p in series if start <= p["date"] <= stop]
        if len(pts) >= 4:
            return _mean([p["value"] for p in pts])
    return prev_avg


def _personal_baselines(series, end_date):
    out = {}
    for w in BASELINE_WINDOWS:
        pts = _window_slice(series, w, end_date)
        out[str(w)] = {
            "average": _round(_mean([p["value"] for p in pts]), 2),
            "days": len(pts),
        }
    return out


def _historical_windows(series, end_date):
    """7D..1Y comparison windows for the HISTORICAL CONTEXT section.

    Never clipped at phase start - `series` is the full history.
    """

    out = {}
    for label, days in HISTORY_WINDOWS.items():
        current = _window_slice(series, 7, end_date)
        ref_center = end_date - timedelta(days=days)
        reference = [
            p for p in series
            if ref_center - timedelta(days=3) <= p["date"]
            <= ref_center + timedelta(days=3)
        ]
        cur_avg = _mean([p["value"] for p in current])
        ref_avg = _mean([p["value"] for p in reference])
        sufficient = cur_avg is not None and ref_avg is not None
        out[label] = {
            "label": label,
            "days": days,
            "sufficient_data": sufficient,
            "current_average": _round(cur_avg, 2),
            "reference_average": _round(ref_avg, 2),
            "change": _round(
                (cur_avg - ref_avg) if sufficient else None, 3
            ),
            "current_measurement_days": len(current),
            "reference_measurement_days": len(reference),
        }
    return out


def _series_bounds(series):
    if not series:
        return None, None, 0
    return series[0]["date"], series[-1]["date"], len(series)


# ============================================================
# OUTCOMES  (body composition)
# ============================================================

def _phase_series(series, phase_start_date):
    if not phase_start_date:
        return series
    return [p for p in series if p["date"] >= phase_start_date]


def _outcome_metric(
    *, name, display_name, unit, bcp_metric, hist_hume, hist_fitdays,
    phase_start_date, lower_is_better, phase=None,
):
    """One OUTCOMES card. Preferred source Hume; Fitdays history labelled."""

    hume = [
        {"date": _parse_date(p["date"]), "value": p["value"]}
        for p in (hist_hume or [])
        if p.get("value") is not None
    ]
    fitdays = [
        {"date": _parse_date(p["date"]), "value": p["value"]}
        for p in (hist_fitdays or [])
        if p.get("value") is not None
    ]
    goal_direction = (bcp_metric or {}).get("goal_direction")

    if not hume:
        # No Hume history at all - historical context can still show Fitdays.
        status = "insufficient_history" if fitdays else "unavailable"
        earliest, latest, count = _series_bounds(fitdays)
        return _metric(
            name=name, display_name=display_name, unit=unit,
            preferred_source="Hume", status=status,
            source=("Fitdays" if fitdays else None),
            source_label=("Fitdays (historical only)" if fitdays else None),
            source_short=("Fitdays" if fitdays else None),
            history_source=("Fitdays" if fitdays else None),
            record_count=count, earliest_date=earliest, latest_date=latest,
            series_available=bool(fitdays), lower_is_better=lower_is_better,
            note="No Hume measurements; Fitdays shown for historical context only."
            if fitdays else "No body-composition measurements available.",
        )

    end_date = hume[-1]["date"]
    cur_avg, prev_avg, cur_n, prev_n = _period_averages(hume, end_date)
    reference = _sustained_reference(hume, end_date, prev_avg)
    trend, delta, delta_pct = _trend_from_delta(
        _trend_key(name), cur_avg, reference, lower_is_better
    )
    band_ok = trend == "flat"
    sustained_delta = (
        (cur_avg - reference)
        if (cur_avg is not None and reference is not None) else None
    )
    direction = _direction_word(sustained_delta, band_ok)
    interpretation, reason = _semantic(
        name, direction, phase=phase, goal_direction=goal_direction
    )
    comparison = _build_comparison(hume, end_date)

    phase_pts = _phase_series(hume, phase_start_date)
    phase_start_value = phase_pts[0]["value"] if phase_pts else None
    current_value = cur_avg if cur_avg is not None else hume[-1]["value"]
    phase_change = (
        (current_value - phase_start_value)
        if (phase_start_value is not None and current_value is not None)
        else None
    )

    earliest, latest, count = _series_bounds(hume)
    status = "ok" if cur_n >= 3 else "insufficient_history"

    has_older_fitdays = bool(fitdays and fitdays[0]["date"] < hume[0]["date"])
    source_label = "Hume"
    if has_older_fitdays:
        source_label = (
            f"Hume (current) - Fitdays before {hume[0]['date'].isoformat()}"
        )

    m = _metric(
        name=name, display_name=display_name, unit=unit,
        preferred_source="Hume", status=status, source="Hume",
        source_label=source_label,
        source_short="Hume",
        history_source=("Fitdays" if has_older_fitdays else None),
        current_value=current_value,
        current_period_average=cur_avg, previous_period_average=prev_avg,
        trend=trend, direction=direction, interpretation=interpretation,
        interpretation_reason=reason, delta=delta, delta_pct=delta_pct,
        comparison=comparison,
        record_count=count, earliest_date=earliest, latest_date=latest,
        phase_start_value=phase_start_value, phase_change=phase_change,
        baselines=_personal_baselines(hume, end_date),
        series_available=True, lower_is_better=lower_is_better,
    )
    # Carry the existing V1 goal horizons through untouched (Goal scoring).
    if bcp_metric:
        m["goal"] = {
            "goal_current_value": bcp_metric.get("goal_current_value"),
            "phase_start_value": bcp_metric.get("phase_start_value"),
            "target_value": bcp_metric.get("target_value"),
            "distance_to_target": bcp_metric.get("distance_to_target"),
            "goal_direction": bcp_metric.get("goal_direction"),
            "progress": bcp_metric.get("progress"),
        }
    return m


def _trend_key(name):
    return {
        "weight": "weight",
        "body_fat": "body_fat_percentage",
        "fat_mass": "fat_mass",
        "lean_mass": "lean_mass",
    }.get(name, name)


def _parse_date(value):
    if hasattr(value, "isoformat") and not isinstance(value, str):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


# ============================================================
# GOAL TIMELINE
# ============================================================

def _goal_timeline(goal, weight_metric):
    """Rate / trajectory / projection.

    A Goal Setting V2 goal persists an explicit `target_date` (plus
    `selected_pace`, `expected_weekly_weight_change_lb`, `timeline_status`).
    That is used when present. A legacy goal with only `phase_end_date`
    still works. A goal with neither remains `not_configured` - no deadline
    is invented.
    """

    target_weight = goal.get("target_weight_lb")
    start_weight = goal.get("phase_start_weight_lb")
    phase_start = goal.get("phase_start_date")

    # V2 explicit contract first, then legacy phase_end_date.
    deadline = goal.get("target_date") or goal.get("phase_end_date")
    persisted_status = goal.get("timeline_status")

    if not deadline:
        return {
            "timeline_status": persisted_status or "not_configured",
            "note": (
                "No goal deadline is configured. Set a target date in Goal "
                "Setting to enable trajectory projection."
            ),
        }

    try:
        end_date = _parse_date(deadline)
        start_date = _parse_date(phase_start)
    except Exception:
        return {"timeline_status": "not_configured"}

    today = _today_eastern()
    weeks_total = max((end_date - start_date).days / 7.0, 0.1)
    weeks_elapsed = max((today - start_date).days / 7.0, 0.0)
    weeks_remaining = max((end_date - today).days / 7.0, 0.0)

    required_total = None
    required_weekly = None
    if start_weight is not None and target_weight is not None:
        required_total = float(target_weight) - float(start_weight)
        required_weekly = required_total / weeks_total
    # Prefer the pace the user actually selected at activation.
    persisted_expected = goal.get("expected_weekly_weight_change_lb")
    if persisted_expected is not None:
        required_weekly = float(persisted_expected)

    observed_weekly = None
    current_weight = weight_metric.get("current_value")
    if (
        start_weight is not None
        and current_weight is not None
        and weeks_elapsed >= 1.0
    ):
        observed_weekly = (float(current_weight) - float(start_weight)) / weeks_elapsed

    projected_completion = None
    variance = None
    on_track = None
    if (
        observed_weekly is not None
        and required_total is not None
        and abs(observed_weekly) > 1e-6
    ):
        remaining = float(target_weight) - float(current_weight)
        weeks_to_go = remaining / observed_weekly if observed_weekly else None
        if weeks_to_go is not None and weeks_to_go >= 0:
            projected_completion = (
                today + timedelta(weeks=weeks_to_go)
            ).isoformat()
        if required_weekly not in (None, 0):
            variance = observed_weekly - required_weekly
            on_track = abs(observed_weekly) >= abs(required_weekly) * 0.7 and (
                (observed_weekly < 0) == (required_weekly < 0)
            )

    return {
        "timeline_status": (
            persisted_status
            if persisted_status in ("configured", "outside_supported_range")
            else "configured"
        ),
        "goal_deadline": end_date.isoformat(),
        "aspirational_target_date": goal.get("aspirational_target_date"),
        "selected_pace": goal.get("selected_pace"),
        "weeks_total": _round(weeks_total, 1),
        "weeks_elapsed": _round(weeks_elapsed, 1),
        "weeks_remaining": _round(weeks_remaining, 1),
        "required_weekly_weight_change_lb": _round(required_weekly, 3),
        "observed_weekly_weight_change_lb": _round(observed_weekly, 3),
        "variance_to_trajectory_lb_per_week": _round(variance, 3),
        "projected_completion_date": projected_completion,
        "on_track": on_track,
    }


# ============================================================
# DETERMINISTIC GOAL INTELLIGENCE
# ============================================================

def _intelligence(*, phase_age_days, outcomes, drivers, physiology, timeline):
    """Rule-based synthesis. Observations are facts from the data; anything
    causal is flagged as a hypothesis, never asserted."""

    positive = []
    constraints = []
    watch = []
    observations = []
    hypotheses = []

    bf = outcomes["body_fat"]
    fatm = outcomes["fat_mass"]
    lean = outcomes["lean_mass"]
    weight = outcomes["weight"]
    strength = drivers["strength"]
    sleep = drivers["sleep"]
    hrv = physiology["hrv"]
    rhr = physiology["resting_heart_rate"]
    recovery = physiology["recovery"]

    # Consume the goal-aware INTERPRETATION, not the raw direction.
    def interp(m):
        return m.get("interpretation")

    def direction(m):
        return m.get("direction")

    def usable(m):
        return m.get("status") in ("ok", "stale")

    bf_fav = usable(bf) and interp(bf) == "favorable"
    fat_fav = usable(fatm) and interp(fatm) == "favorable"
    lean_unfav = usable(lean) and interp(lean) == "unfavorable"
    lean_neutral_or_fav = usable(lean) and interp(lean) in ("neutral", "favorable")
    weight_toward = usable(weight) and interp(weight) == "favorable"
    weight_away = usable(weight) and interp(weight) == "unfavorable"
    weight_flat = usable(weight) and direction(weight) == "flat"
    weight_decreasing = usable(weight) and direction(weight) == "decreasing"

    strength_low = strength.get("adherence_status") == "below_target"
    strength_ok = (not strength_low) and strength.get("status") in ("ok", "stale")

    hrv_unfav = usable(hrv) and interp(hrv) == "unfavorable"
    hrv_fav_or_neutral = usable(hrv) and interp(hrv) in ("favorable", "neutral")
    rhr_unfav = usable(rhr) and interp(rhr) == "unfavorable"
    rhr_fav = usable(rhr) and interp(rhr) == "favorable"
    recovery_unfav = usable(recovery) and interp(recovery) == "unfavorable"
    recovery_fav = usable(recovery) and interp(recovery) == "favorable"
    sleep_unfav = usable(sleep) and interp(sleep) == "unfavorable"
    sleep_ok_or_up = usable(sleep) and interp(sleep) in ("favorable", "neutral")

    # --- Observations (direct readings; no causation) ---
    for label, m in (("Body fat", bf), ("Weight", weight), ("Lean mass", lean),
                     ("HRV", hrv), ("Resting HR", rhr)):
        if usable(m) and direction(m) not in (None, "insufficient_data"):
            observations.append(
                f"{label} is {direction(m)} vs the prior week "
                f"({m.get('delta_pct')}%) - {interp(m)}."
            )

    # --- Positive signals ---
    if bf_fav and lean_neutral_or_fav and strength_ok:
        positive.append(
            "Body fat is down while lean mass holds and strength is maintained "
            "- a favourable recomposition signal."
        )
    if weight_flat and bf_fav and interp(lean) == "favorable":
        positive.append(
            "Scale weight is flat but body fat is down and lean mass is up - "
            "recomposition despite a plateau on the scale."
        )
    if fat_fav and lean_neutral_or_fav:
        positive.append("Fat mass is down with lean mass protected - favourable lean-cut progress.")
    if strength_ok and fat_fav:
        positive.append("Strength is maintained while fat mass falls - a favourable signal.")
    if hrv_fav_or_neutral and rhr_fav:
        positive.append("HRV is stable-to-up and resting heart rate is down - a favourable physiological response.")
    if recovery_fav:
        positive.append("Recovery is trending up.")
    if sleep_ok_or_up and interp(sleep) == "favorable":
        positive.append("Sleep duration is trending up.")

    # --- Constraints ---
    if strength_low:
        constraints.append(
            f"Strength sessions below target "
            f"({strength.get('sessions_7d')}/{strength.get('target_sessions_per_week')} this week)."
        )
    if hrv_unfav and rhr_unfav:
        constraints.append("HRV down and resting heart rate up - recovery physiology is trending adverse.")

    # --- Hypotheses (plausible mechanism, NOT proven causation) ---
    if weight_decreasing and recovery_unfav and (strength_low or lean_unfav):
        hypotheses.append(
            "Weight is dropping while recovery is trending down and strength/"
            "lean mass is slipping. This pattern is consistent with an overly "
            "aggressive deficit; consider a smaller deficit or a diet break."
        )
        constraints.append("Recovery is trending down alongside weight loss.")
    if weight_decreasing and lean_unfav:
        hypotheses.append(
            "Weight is falling and lean mass shows a meaningful decline - this "
            "may indicate excessive lean-tissue loss; protein and strength "
            "volume are the usual levers."
        )
    if hrv_unfav and rhr_unfav:
        hypotheses.append(
            "HRV down and resting heart rate up together often reflect "
            "accumulated fatigue or under-recovery."
        )
    if strength_ok and sleep_unfav:
        hypotheses.append(
            "Training consistency is adequate but sleep is trending down; "
            "sleep is the likely limiting factor for recovery and adaptation."
        )
        watch.append("Sleep duration is trending down.")

    aggressive_deficit = any("aggressive deficit" in h for h in hypotheses)

    # Overall status
    if phase_age_days is None or phase_age_days < MIN_PHASE_AGE_DAYS:
        overall = "insufficient_data"
    elif bf.get("status") != "ok":
        overall = "insufficient_data"
    elif aggressive_deficit:
        # The over-deficit / under-recovery pattern is off-track regardless
        # of a minor concurrent positive (e.g. sleep merely holding steady).
        overall = "off_track"
    elif hypotheses and not positive:
        overall = "off_track"
    elif positive and not constraints:
        overall = "on_track"
    elif positive and constraints:
        overall = "mixed"
    elif constraints:
        overall = "off_track"
    else:
        overall = "mixed"

    if timeline.get("timeline_status") == "configured":
        if timeline.get("on_track") is False and overall == "on_track":
            overall = "mixed"
            watch.append(
                "Observed weight-change rate is behind the configured timeline."
            )
        elif timeline.get("on_track") is True:
            positive.append("Weight-change rate is tracking the configured timeline.")

    # Highest-priority action
    if overall == "insufficient_data":
        action = (
            "Keep logging Hume body-composition measurements; direction "
            "cannot be assessed yet."
        )
    elif any("aggressive deficit" in h for h in hypotheses):
        action = (
            "Ease the calorie deficit (or take a short diet break) and "
            "protect sleep until recovery and strength stabilise."
        )
    elif strength_low:
        action = (
            "Restore strength training to the weekly target - it is the "
            "biggest lever for retaining lean mass in a cut."
        )
    elif sleep_unfav:
        action = "Prioritise sleep duration; it is the current limiter."
    elif overall == "on_track":
        action = "Hold the current plan; the trend is favourable."
    else:
        action = (
            "Tighten the most off-target behaviour (steps or strength) and "
            "re-check in a week."
        )

    return {
        "overall_status": overall,
        "positive_signals": positive,
        "constraints": constraints,
        "watch_items": watch,
        "observations": observations,
        "hypotheses": hypotheses,
        "highest_priority_action": action,
        "disclaimer": (
            "Observations are direct readings from your data. Hypotheses "
            "describe plausible mechanisms and are not proven causation."
        ),
    }


# ============================================================
# DRIVERS
# ============================================================

def _steps_driver(steps_series, goal, end_date):
    """Apple Health steps. Classifies on the best available recent window so a
    sparse last-7-days sync does not falsely report insufficient history when
    weeks of step data exist."""

    target = goal.get("daily_step_target")
    earliest, latest, count = _series_bounds(steps_series)

    if not steps_series:
        return _metric(
            name="steps", display_name="Steps", unit="steps/day",
            preferred_source="Apple Health", status="unavailable",
            source="Apple Health", source_label="Apple Health",
            source_short="Apple Health", record_count=0,
            lower_is_better=False,
            note="No Apple Health step history is available.",
        )

    bl = _personal_baselines(steps_series, end_date)
    w7 = _window_slice(steps_series, 7, end_date)
    w14 = _window_slice(steps_series, 14, end_date)
    w30 = _window_slice(steps_series, 30, end_date)

    # Choose the shortest window with enough days for a stable current average.
    if len(w7) >= 4:
        cur_pts, cur_label, cur_days = w7, "last 7 days", 7
    elif len(w14) >= 5:
        cur_pts, cur_label, cur_days = w14, "last 14 days", 14
    elif len(w30) >= 7:
        cur_pts, cur_label, cur_days = w30, "last 30 days", 30
    else:
        cur_pts, cur_label, cur_days = [], None, 0

    if not cur_pts:
        return _metric(
            name="steps", display_name="Steps", unit="steps/day",
            preferred_source="Apple Health", status="insufficient_history",
            source="Apple Health", source_label="Apple Health",
            source_short="Apple Health", record_count=count,
            earliest_date=earliest, latest_date=latest,
            baselines=bl, series_available=True, lower_is_better=False,
            note="Fewer than 4 recent days of step data.",
        )

    cur_avg = _mean([p["value"] for p in cur_pts])
    # Reference: the ~4 weeks before the current window.
    ref_start = end_date - timedelta(days=cur_days + 28)
    ref_stop = end_date - timedelta(days=cur_days)
    ref_pts = [p for p in steps_series if ref_start <= p["date"] <= ref_stop]
    ref_avg = _mean([p["value"] for p in ref_pts])

    trend, delta, delta_pct = _trend_from_delta(
        "steps", cur_avg, ref_avg, lower_is_better=False
    )
    band_ok = trend == "flat"
    sdelta = (cur_avg - ref_avg) if (cur_avg is not None and ref_avg is not None) else None
    direction = _direction_word(sdelta, band_ok)
    interpretation, reason = _semantic("steps", direction)

    comparison = None
    if ref_avg is not None:
        comparison = {
            "label": f"{cur_label} vs prior 4 weeks",
            "period_start": ref_start.isoformat(),
            "period_end": ref_stop.isoformat(),
            "current_period_start": (end_date - timedelta(days=cur_days - 1)).isoformat(),
            "current_period_end": end_date.isoformat(),
            "start_value": _round(ref_avg, 0),
            "current_value": _round(cur_avg, 0),
            "absolute_change": _round(cur_avg - ref_avg, 0),
            "percent_change": _round(delta_pct, 1),
            "current_measurement_days": len(cur_pts),
            "reference_measurement_days": len(ref_pts),
        }

    m = _metric(
        name="steps", display_name="Steps", unit="steps/day",
        preferred_source="Apple Health", status="ok",
        source="Apple Health", source_label="Apple Health",
        source_short="Apple Health",
        current_value=cur_avg, current_period_average=cur_avg,
        previous_period_average=ref_avg, trend=trend, direction=direction,
        interpretation=interpretation, interpretation_reason=reason,
        delta=delta, delta_pct=delta_pct, comparison=comparison,
        record_count=count, earliest_date=earliest, latest_date=latest,
        baselines=bl, series_available=True, lower_is_better=False,
        note=(None if cur_label == "last 7 days"
              else f"Recent daily step sync is sparse; showing the {cur_label} average."),
    )
    m["target"] = target
    m["target_basis"] = cur_label
    # A percentage is only meaningful when we have a usable current average.
    m["percentage_of_target"] = (
        _round(cur_avg / target * 100.0, 0) if target else None
    )
    return m


def _strength_driver(goal):
    s = strength_adherence(goal.get("strength_sessions_per_week"))
    status_map = {
        "target_met": "ok",
        "below_target": "ok",
        "not_connected": "not_connected",
        "not_configured": "unavailable",
    }
    return {
        "metric": "strength",
        "display_name": "Strength",
        "unit": "sessions/week",
        "status": status_map.get(s.get("status"), "unavailable"),
        "preferred_source": "Tonal",
        "source": "Tonal",
        "source_label": "Tonal",
        "adherence_status": s.get("status"),
        "sessions_7d": s.get("sessions_7d"),
        "qualifying_sessions_7d": s.get("qualifying_sessions_7d"),
        "supplemental_sessions_7d": s.get("supplemental_sessions_7d"),
        "target_sessions_per_week": s.get("target_sessions_per_week"),
        "percentage_of_target": s.get("percentage_of_target"),
        "remaining_sessions": s.get("remaining_sessions"),
        "window_start_date": s.get("window_start_date"),
        "window_end_date": s.get("window_end_date"),
        "trend": "flat",
        "note": "Qualifying Tonal session rules are unchanged from strength adherence.",
    }


def _whoop_metric_driver_or_physio(
    *, name, display_name, unit, column, rows, lower_is_better, end_date,
    min_days=5,
):
    series = _whoop_series(rows, column)
    earliest, latest, count = _series_bounds(series)
    if count < min_days:
        return _metric(
            name=name, display_name=display_name, unit=unit,
            preferred_source="WHOOP", status="insufficient_history",
            source="WHOOP", source_label="WHOOP",
            record_count=count, earliest_date=earliest, latest_date=latest,
            series_available=bool(series), lower_is_better=lower_is_better,
        )
    cur_avg, prev_avg, cur_n, prev_n = _period_averages(series, end_date)
    reference = _sustained_reference(series, end_date, prev_avg)
    key = {
        "hrv": "hrv", "resting_heart_rate": "resting_heart_rate",
        "recovery": "recovery", "sleep_duration": "sleep_duration",
    }.get(name, name)
    trend, delta, delta_pct = _trend_from_delta(
        key, cur_avg, reference, lower_is_better
    )
    band_ok = trend == "flat"
    sdelta = (cur_avg - reference) if (cur_avg is not None and reference is not None) else None
    direction = _direction_word(sdelta, band_ok)
    semantic_key = {"sleep_duration": "sleep_duration"}.get(name, name)
    interpretation, reason = _semantic(semantic_key, direction)
    return _metric(
        name=name, display_name=display_name, unit=unit,
        preferred_source="WHOOP", status="ok", source="WHOOP",
        source_label="WHOOP", source_short="WHOOP",
        current_value=(cur_avg if cur_avg is not None else series[-1]["value"]),
        current_period_average=cur_avg, previous_period_average=prev_avg,
        trend=trend, direction=direction, interpretation=interpretation,
        interpretation_reason=reason,
        delta=delta, delta_pct=delta_pct,
        comparison=_build_comparison(series, end_date),
        record_count=count, earliest_date=earliest, latest_date=latest,
        baselines=_personal_baselines(series, end_date),
        series_available=True, lower_is_better=lower_is_better,
    )


# ============================================================
# PUBLIC ENTRY POINT
# ============================================================

def goal_progress_v2(
    goal=None,
    trends=None,
    body_progress=None,
    whoop_rows=None,
    steps_rows=None,
    v1=None,
):
    if goal is None:
        goal = get_active_goal()

    if not goal:
        return {
            "status": "no_active_goal",
            "version": 2,
            "message": "Create an active goal before calculating progress.",
        }

    if v1 is None:
        v1 = goal_progress(goal=goal, trends=trends, body_progress=body_progress)
    if trends is None:
        trends = apple_health_trends()
    if body_progress is None:
        body_progress = body_composition_progress()
    if whoop_rows is None:
        try:
            whoop_rows = _load_whoop_daily()
        except Exception:
            whoop_rows = []
    if steps_rows is None:
        steps_rows = _load_apple_steps()
    steps_series = [
        {"date": _parse_date(p["date"]), "value": p["value"]}
        for p in (steps_rows or [])
        if p.get("value") is not None
    ]
    phase_name = goal.get("phase")

    phase_start_date = None
    if goal.get("phase_start_date"):
        phase_start_date = _parse_date(goal["phase_start_date"])

    today = _today_eastern()
    phase_age_days = (
        max(0, (today - phase_start_date).days) if phase_start_date else None
    )

    bcp_metrics = (body_progress or {}).get("metrics", {}) or {}
    hist = (body_progress or {}).get("historical_context", {}) or {}
    hume_hist = hist.get("hume", {}) or {}
    fitdays_hist = hist.get("fitdays", {}) or {}

    outcomes = {
        "weight": _outcome_metric(
            name="weight", display_name="Weight", unit="lb",
            bcp_metric=bcp_metrics.get("weight"),
            hist_hume=hume_hist.get("weight"),
            hist_fitdays=fitdays_hist.get("weight"),
            phase_start_date=phase_start_date, lower_is_better=True,
            phase=phase_name,
        ),
        "body_fat": _outcome_metric(
            name="body_fat", display_name="Body Fat", unit="percent",
            bcp_metric=bcp_metrics.get("body_fat_percentage"),
            hist_hume=hume_hist.get("body_fat_percentage"),
            hist_fitdays=fitdays_hist.get("body_fat_percentage"),
            phase_start_date=phase_start_date, lower_is_better=True,
            phase=phase_name,
        ),
        "fat_mass": _outcome_metric(
            name="fat_mass", display_name="Fat Mass", unit="lb",
            bcp_metric=bcp_metrics.get("fat_mass"),
            hist_hume=hume_hist.get("fat_mass"),
            hist_fitdays=fitdays_hist.get("fat_mass"),
            phase_start_date=phase_start_date, lower_is_better=True,
            phase=phase_name,
        ),
        "lean_mass": _outcome_metric(
            name="lean_mass", display_name="Lean Body Mass", unit="lb",
            bcp_metric=bcp_metrics.get("lean_mass"),
            hist_hume=hume_hist.get("lean_mass"),
            hist_fitdays=fitdays_hist.get("lean_mass"),
            phase_start_date=phase_start_date, lower_is_better=False,
            phase=phase_name,
        ),
    }

    whoop_end = _today_eastern()
    drivers = {
        "steps": _steps_driver(steps_series, goal, whoop_end),
        "strength": _strength_driver(goal),
        "sleep": _whoop_metric_driver_or_physio(
            name="sleep_duration", display_name="Sleep Duration", unit="hours",
            column="sleep_duration_hours", rows=whoop_rows,
            lower_is_better=False, end_date=whoop_end,
        ),
        "hydration": {
            "metric": "hydration", "display_name": "Hydration",
            "unit": "L/day", "status": "unavailable",
            "preferred_source": "Apple Health / manual",
            "source": None, "source_label": None, "source_short": None,
            "note": (
                "No reliable actual hydration intake is connected. Prescribed "
                "hydration targets are not shown as consumption."
            ),
            "trend": "insufficient_data",
            "direction": "insufficient_data",
            "interpretation": "insufficient_data",
        },
        "calories": {
            "metric": "calories", "display_name": "Calories",
            "unit": "kcal/day", "status": "not_connected",
            "preferred_source": "Nutrition integration (future)",
            "source": None, "source_label": None, "source_short": None,
            "note": "Actual calorie intake is not connected.",
            "trend": "insufficient_data",
            "direction": "insufficient_data",
            "interpretation": "insufficient_data",
        },
        "protein": {
            "metric": "protein", "display_name": "Protein",
            "unit": "g/day", "status": "not_connected",
            "preferred_source": "Nutrition integration (future)",
            "source": None, "source_label": None, "source_short": None,
            "target_grams_per_day": goal.get("protein_target_grams"),
            "note": "Actual protein intake is not connected.",
            "trend": "insufficient_data",
            "direction": "insufficient_data",
            "interpretation": "insufficient_data",
        },
    }
    # sleep-performance is a supporting reading on the sleep card
    sleep_perf = _whoop_metric_driver_or_physio(
        name="sleep_performance", display_name="Sleep Performance",
        unit="percent", column="sleep_performance_percentage",
        rows=whoop_rows, lower_is_better=False, end_date=whoop_end,
    )
    drivers["sleep"]["sleep_performance"] = {
        "status": sleep_perf["status"],
        "current_period_average": sleep_perf["current_period_average"],
        "trend": sleep_perf["trend"],
    }

    physiology = {
        "hrv": _whoop_metric_driver_or_physio(
            name="hrv", display_name="HRV (RMSSD)", unit="ms",
            column="hrv_rmssd_milli", rows=whoop_rows,
            lower_is_better=False, end_date=whoop_end,
        ),
        "resting_heart_rate": _whoop_metric_driver_or_physio(
            name="resting_heart_rate", display_name="Resting Heart Rate",
            unit="bpm", column="resting_heart_rate", rows=whoop_rows,
            lower_is_better=True, end_date=whoop_end,
        ),
        "recovery": _whoop_metric_driver_or_physio(
            name="recovery", display_name="Recovery", unit="percent",
            column="recovery_score", rows=whoop_rows,
            lower_is_better=False, end_date=whoop_end,
        ),
        "vo2_max": {
            "metric": "vo2_max", "display_name": "VO2 Max",
            "unit": "ml/kg/min", "status": "unavailable",
            "preferred_source": "Apple Health",
            "source": None, "source_label": None, "source_short": None,
            "note": (
                "VO2 max is not collected by the current Apple Health sync "
                "(the app reads body mass, body fat, lean mass, steps and "
                "active energy). WHOOP does not expose VO2 max via its public "
                "API, so no fallback is used."
            ),
            "trend": "insufficient_data",
            "direction": "insufficient_data",
            "interpretation": "insufficient_data",
        },
    }

    # -------- historical context (NOT truncated at phase start) --------
    historical_context = {}
    for key, hume_pts in (
        ("weight", hume_hist.get("weight")),
        ("body_fat", hume_hist.get("body_fat_percentage")),
        ("fat_mass", hume_hist.get("fat_mass")),
        ("lean_mass", hume_hist.get("lean_mass")),
    ):
        series = [
            {"date": _parse_date(p["date"]), "value": p["value"]}
            for p in (hume_pts or [])
            if p.get("value") is not None
        ]
        e, l, c = _series_bounds(series)
        fd = fitdays_hist.get(
            "body_fat_percentage" if key == "body_fat" else key
        ) or []
        historical_context[key] = {
            "preferred_source": "Hume",
            "windows": _historical_windows(series, series[-1]["date"]) if series else {},
            "hume_series": [
                {"date": p["date"].isoformat(), "value": _round(p["value"], 2)}
                for p in series
            ],
            "fitdays_series": [
                {"date": _iso(p["date"]), "value": _round(p["value"], 2)}
                for p in fd
            ],
            "hume_record_count": c,
            "hume_earliest_date": _iso(e),
            "hume_latest_date": _iso(l),
            "fitdays_record_count": len(fd),
        }
    for key, column in (
        ("hrv", "hrv_rmssd_milli"),
        ("resting_heart_rate", "resting_heart_rate"),
        ("recovery", "recovery_score"),
        ("sleep_duration", "sleep_duration_hours"),
    ):
        series = _whoop_series(whoop_rows, column)
        e, l, c = _series_bounds(series)
        historical_context[key] = {
            "preferred_source": "WHOOP",
            "windows": _historical_windows(series, series[-1]["date"]) if series else {},
            "series": [
                {"date": p["date"].isoformat(), "value": _round(p["value"], 2)}
                for p in series
            ],
            "record_count": c,
            "earliest_date": _iso(e),
            "latest_date": _iso(l),
        }

    # Steps history is its own source (Apple Health), never phase-truncated.
    se, sl, sc = _series_bounds(steps_series)
    historical_context["steps"] = {
        "preferred_source": "Apple Health",
        "windows": (
            _historical_windows(steps_series, steps_series[-1]["date"])
            if steps_series else {}
        ),
        "series": [
            {"date": p["date"].isoformat(), "value": _round(p["value"], 0)}
            for p in steps_series
        ],
        "record_count": sc,
        "earliest_date": _iso(se),
        "latest_date": _iso(sl),
    }

    timeline = _goal_timeline(goal, outcomes["weight"])

    intelligence = _intelligence(
        phase_age_days=phase_age_days,
        outcomes=outcomes,
        drivers=drivers,
        physiology=physiology,
        timeline=timeline,
    )

    summary = {
        "phase_name": v1.get("phase"),
        "overall_status": intelligence["overall_status"],
        "headline": v1.get("summary"),
        "progress_toward_weight_target": (v1.get("weight") or {}).get(
            "progress_percentage"
        ),
        "progress_toward_body_fat_target": (v1.get("body_fat") or {}).get(
            "progress_percentage"
        ),
        "timeline_status": timeline.get("timeline_status"),
    }

    phase = {
        "phase": goal.get("phase"),
        "phase_start_date": goal.get("phase_start_date"),
        "phase_end_date": goal.get("phase_end_date"),
        "phase_age_days": phase_age_days,
        "phase_day": (phase_age_days + 1) if phase_age_days is not None else None,
        "minimum_status_age_days": MIN_PHASE_AGE_DAYS,
        "is_baseline_building": (
            phase_age_days is not None and phase_age_days < MIN_PHASE_AGE_DAYS
        ),
    }

    # Backward compatible by construction: every V1 key (including `phase` as
    # a bare string and `summary` as a sentence) is preserved untouched, and
    # the V2 sections are added under NON-colliding keys. An old app build
    # keeps decoding GoalProgressResponse; the new build reads the sections.
    result = dict(v1)
    result.update(
        {
            "status": "ok",
            "version": 2,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "phase_detail": phase,
            "summary_detail": summary,
            "outcomes": outcomes,
            "drivers": drivers,
            "physiology": physiology,
            "historical_context": historical_context,
            "historical_windows": list(HISTORY_WINDOWS.keys()),
            "goal_timeline": timeline,
            "intelligence": intelligence,
            "methodology": METHODOLOGY,
        }
    )
    return result
