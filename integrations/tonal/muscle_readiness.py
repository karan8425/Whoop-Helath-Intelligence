from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median

from db import get_conn


PROGRAMMING_MUSCLES = (
    "Chest", "Back", "Shoulders", "Biceps", "Triceps", "Core",
    "Glutes", "Hamstrings", "Quads",
)
PRIMARY_SET_WEIGHT = 1.0
SECONDARY_SET_WEIGHT = 0.35
SUPPLEMENTAL_WEIGHT = 0.25
PRIMARY_24H_SUPPRESSION_SETS = 3.0
PRIMARY_48H_SUBSTANTIAL_SETS = 6.0
HEAVY_EFFECTIVE_SETS = 10.0
FRESHNESS_HIGH_CONFIDENCE_HOURS = 72.0
FRESHNESS_LOW_CONFIDENCE_HOURS = 120.0
ABBREVIATED_REASON = "atypical abbreviated freestyle session"


def _normalize_muscle(value):
    if value in ("Abs", "Obliques"):
        return "Core"
    return value if value in PROGRAMMING_MUSCLES else None


def _hours(now, then):
    if not then:
        return None
    return max(0.0, (now - then).total_seconds() / 3600.0)


def _load_rows(now, lookback_days=30):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT w.activity_id, w.begin_time, w.total_volume AS workout_volume,
                       COALESCE(o.include_in_training_analysis, TRUE) AS included,
                       o.exclusion_reason, s.volume, m.muscle_groups
                FROM tonal_workouts w
                LEFT JOIN tonal_workout_overrides o USING (activity_id)
                JOIN tonal_sets s USING (activity_id)
                LEFT JOIN tonal_movements m USING (movement_id)
                WHERE w.begin_time <= %s AND w.begin_time >= %s
                ORDER BY w.begin_time DESC, s.set_index
                """,
                (now, now - timedelta(days=lookback_days)),
            )
            rows = cur.fetchall()
            cur.execute(
                "SELECT MAX(begin_time) AS latest FROM tonal_workouts WHERE begin_time <= %s",
                (now,),
            )
            latest = cur.fetchone()["latest"]
    return rows, latest


def calculate_muscle_readiness(now=None, rows=None, latest_workout_at=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if rows is None:
        rows, latest_workout_at = _load_rows(now)

    sessions = defaultdict(lambda: defaultdict(lambda: {
        "primary_sets": 0.0, "secondary_sets": 0.0, "volume": 0.0,
    }))
    unmapped_sets = 0

    for row in rows:
        groups = []
        for raw in row.get("muscle_groups") or []:
            muscle = _normalize_muscle(raw)
            if muscle and muscle not in groups:
                groups.append(muscle)
        if not groups:
            unmapped_sets += 1
            continue

        included = row.get("included") is True
        supplemental = (
            not included
            and " ".join(str(row.get("exclusion_reason") or "").casefold().split())
            == ABBREVIATED_REASON
        )
        if not included and not supplemental:
            continue

        session_weight = SUPPLEMENTAL_WEIGHT if supplemental else 1.0
        key = (row.get("activity_id"), row.get("begin_time"), supplemental)
        volume = float(row.get("volume") or 0.0)
        sessions[key][groups[0]]["primary_sets"] += PRIMARY_SET_WEIGHT * session_weight
        sessions[key][groups[0]]["volume"] += volume * session_weight
        for muscle in groups[1:]:
            sessions[key][muscle]["secondary_sets"] += SECONDARY_SET_WEIGHT * session_weight
            sessions[key][muscle]["volume"] += volume * SECONDARY_SET_WEIGHT * session_weight

    history = defaultdict(list)
    for (_, when, supplemental), muscles in sessions.items():
        for muscle, exposure in muscles.items():
            history[muscle].append({"when": when, "supplemental": supplemental, **exposure})

    latest_hours = _hours(now, latest_workout_at)
    if latest_hours is None or latest_hours > FRESHNESS_LOW_CONFIDENCE_HOURS:
        confidence = "low"
    elif latest_hours > FRESHNESS_HIGH_CONFIDENCE_HOURS:
        confidence = "reduced"
    else:
        confidence = "high"

    output = []
    for muscle in PROGRAMMING_MUSCLES:
        exposures = history[muscle]
        volumes = [item["volume"] for item in exposures if item["volume"] > 0]
        baseline_volume = median(volumes) if volumes else 0.0
        effective_24 = effective_48 = effective_7d = recent_volume = 0.0
        primary_24 = primary_48 = 0.0
        last_primary = None

        for item in exposures:
            hours = _hours(now, item["when"])
            effective = item["primary_sets"] + item["secondary_sets"]
            if hours <= 168:
                effective_7d += effective
                recent_volume += item["volume"]
            if hours <= 48:
                effective_48 += effective
                primary_48 += item["primary_sets"]
            if hours <= 24:
                effective_24 += effective
                primary_24 += item["primary_sets"]
            if item["primary_sets"] > 0 and (last_primary is None or item["when"] > last_primary):
                last_primary = item["when"]

        hours_primary = _hours(now, last_primary)
        volume_ratio = recent_volume / baseline_volume if baseline_volume else 0.0
        suppressed = (
            primary_24 >= PRIMARY_24H_SUPPRESSION_SETS
            or primary_48 >= PRIMARY_48H_SUBSTANTIAL_SETS
        )
        fatigue = min(75.0, effective_24 * 7.0 + max(0, effective_48 - effective_24) * 3.5 + max(0, effective_7d - effective_48) * 0.8)
        if volume_ratio >= 2.0 or effective_48 >= HEAVY_EFFECTIVE_SETS:
            fatigue = min(85.0, fatigue + 10.0)
        score = round(max(0.0, 100.0 - fatigue), 1)

        if suppressed:
            state = "SUPPRESSED"
            reason = "Meaningful recent primary exposure is inside the 24/48-hour suppression window."
        elif effective_48 >= 7:
            state = "FATIGUED"
            reason = "Substantial effective exposure remains inside 48 hours."
        elif effective_7d >= 8 or effective_48 >= 3:
            state = "RECOVERING"
            reason = "Recent effective exposure is still decaying."
        elif hours_primary is None or hours_primary >= 120:
            state = "FRESH" if confidence == "high" else "READY"
            reason = "No recent primary exposure is recorded." if confidence == "high" else "No recent exposure is recorded, but Tonal freshness confidence is limited."
        else:
            state = "READY"
            reason = "Recent exposure is below suppression and fatigue thresholds."

        output.append({
            "muscle": muscle,
            "hours_since_primary_exposure": round(hours_primary, 1) if hours_primary is not None else None,
            "effective_sets_24h": round(effective_24, 2),
            "effective_sets_48h": round(effective_48, 2),
            "effective_sets_7d": round(effective_7d, 2),
            "recent_volume": round(recent_volume, 1),
            "readiness_score": score,
            "readiness_state": state,
            "reason": reason,
        })

    return {
        "as_of": now.isoformat(),
        "latest_tonal_workout_at": latest_workout_at.isoformat() if latest_workout_at else None,
        "latest_workout_age_hours": round(latest_hours, 1) if latest_hours is not None else None,
        "selection_confidence": confidence,
        "unmapped_sets_ignored": unmapped_sets,
        "muscles": output,
    }
