"""TKI-5.2 shadow-only historical evidence and workload accounting.

Policy v1, not a physiological model. A caller supplies one user's rows or
a connection already scoped to that user (the current schema is single-user).
No persistence, global learned state, or changes to the stimulus ledger.
"""
from collections import Counter, defaultdict
from datetime import timedelta
from math import isfinite
from statistics import median

from db import get_conn
from training_intelligence.stimulus.ledger import _require_aware
from training_intelligence.stimulus.mapping import classify_muscle_groups
from training_intelligence.stimulus.session_family import classify_workout_family
from training_intelligence.stimulus.taxonomy import to_canonical
from integrations.tonal.training_priority import SESSION_TEMPLATES

POLICY_VERSION = 1
LOOKBACK_DAYS = 365
MIN_SESSIONS = 3
MIN_MULTIPLIER_SETS = 6
MIN_MULTIPLIER_SESSIONS = 2
RATIO_TOLERANCE = 0.12
MIN_RATIO_AGREEMENT = 0.8
MODE_FLAGS = ("eccentric", "chains", "progressive", "burnout", "flex")


def number(value):
    try:
        value = float(value)
        return value if isfinite(value) else None
    except (TypeError, ValueError):
        return None


def quantile(values, fraction):
    values = sorted(values)
    if not values:
        return None
    point = (len(values) - 1) * fraction
    lower = int(point)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (point - lower)


def distribution(values):
    return {"sample_count": len(values), "median": quantile(values, .5),
            "p25": quantile(values, .25), "p75": quantile(values, .75)}


def mode(row):
    active = [flag for flag in MODE_FLAGS if row.get(flag)]
    return "+".join(active) if active else "standard"


def unassisted(row):
    """Spotter enabled is not proof of assisted reps; inspect actual resistance.

    Rows with >5% mean resistance loss cannot anchor unassisted progression.
    """
    avg = number(row.get("avg_weight"))
    base = number(row.get("base_weight"))
    return avg is not None and base is not None and .95 <= avg / base <= 1.05 if base else False


def valid_rows(rows, as_of):
    _require_aware(as_of)
    tenants = {r.get("user_id") for r in rows if r.get("user_id") is not None}
    if len(tenants) > 1:
        raise ValueError("Calibration requires a single user's history")
    result = {}
    for row in rows:
        begin, end = row.get("begin_time"), row.get("end_time")
        if not begin or not end or begin > as_of or end > as_of or end < begin:
            continue
        if begin < as_of - timedelta(days=LOOKBACK_DAYS):
            continue
        if row.get("included") is not True or row.get("is_generic") or row.get("custom_movement"):
            continue
        if not row.get("movement_id") or not classify_muscle_groups(row.get("muscle_groups")).is_mapped:
            continue
        raw = row.get("raw_data") or {}
        if raw.get("warmUp") in (True, "true", 1):
            continue
        reps, load, volume = (number(row.get(k)) for k in ("rep_count", "base_weight", "volume"))
        if reps is None or load is None or volume is None or not (0 < reps <= 200 and 0 < load <= 100 and volume > 0):
            continue
        result[(str(row.get("activity_id")), row.get("set_index"))] = dict(
            row, rep_count=reps, base_weight=load, volume=volume)
    return sorted(result.values(), key=lambda r: (r["begin_time"], str(r["activity_id"]), r["set_index"]), reverse=True)


def load_history(as_of, conn=None):
    """One bounded query; the supplied connection must be user-scoped."""
    _require_aware(as_of)
    query = """
        SELECT w.activity_id, w.begin_time, w.end_time, w.duration_seconds,
               s.set_index, s.movement_id, s.rep_count, s.base_weight,
               s.avg_weight, s.volume, s.raw_data, s.eccentric, s.chains,
               s.progressive, s.burnout, s.flex, s.spotter,
               s.struggling_score, s.inconsistency_score,
               m.name, m.muscle_groups, m.accessory, m.is_bilateral,
               m.is_two_sided, m.is_alternating, m.is_generic, m.custom_movement,
               COALESCE(o.include_in_training_analysis, TRUE) AS included
        FROM tonal_sets s JOIN tonal_workouts w USING(activity_id)
        JOIN tonal_movements m USING(movement_id)
        LEFT JOIN tonal_workout_overrides o USING(activity_id)
        WHERE w.begin_time <= %s AND w.end_time <= %s AND w.begin_time >= %s
          AND COALESCE(o.include_in_training_analysis, TRUE)
          AND NOT COALESCE(m.is_generic, FALSE)
          AND NOT COALESCE(m.custom_movement, FALSE)
        ORDER BY w.begin_time DESC, w.activity_id, s.set_index
    """
    def read(connection):
        with connection.cursor() as cur:
            cur.execute(query, (as_of, as_of, as_of - timedelta(days=LOOKBACK_DAYS)))
            return valid_rows(cur.fetchall(), as_of)
    if conn is not None:
        return read(conn)
    with get_conn() as connection:
        return read(connection)


def multipliers(rows, as_of):
    """Learn only standard-mode one/two cable relationships, never flag-based x2.

    A ratio must have >=6 valid sets, >=2 workouts and >=80% agreement
    within 12% of one or two. Otherwise explicit low-confidence fallback.
    """
    groups = defaultdict(list)
    for row in valid_rows(rows, as_of):
        groups[str(row["movement_id"])].append(row)
    result = {}
    for movement, history in groups.items():
        standard = [r for r in history if mode(r) == "standard"]
        ratios = [r["volume"] / (r["base_weight"] * r["rep_count"]) for r in standard]
        ratios = [r for r in ratios if .5 <= r <= 2.5]
        observed = median(ratios) if ratios else None
        nearest = min((1, 2), key=lambda n: abs(n - observed)) if observed is not None else 1
        agreement = sum(abs(r - nearest) <= RATIO_TOLERANCE * nearest for r in ratios) / len(ratios) if ratios else 0
        sessions = len({str(r["activity_id"]) for r in standard})
        confident = (len(ratios) >= MIN_MULTIPLIER_SETS and sessions >= MIN_MULTIPLIER_SESSIONS
                     and agreement >= MIN_RATIO_AGREEMENT)
        result[movement] = {
            "workload_model_version": POLICY_VERSION,
            "workload_multiplier": nearest if confident else 1,
            "multiplier_source": "historical_standard_mode_ratio" if confident else "product_policy_fallback",
            "confidence": ("HIGH" if len(ratios) >= 20 else "MEDIUM") if confident else "LOW",
            "sample_count": len(ratios), "session_count": sessions,
            "observed_median_ratio": observed, "agreement_fraction": round(agreement, 4),
            "warning": None if confident else "Unknown cable semantics; factor 1 is an uncertain fallback, not a measured relationship.",
        }
    return result


def pattern(row):
    """Soft, versioned role classification; never changes muscle mappings."""
    name = (row.get("name") or row.get("movement_name") or "").casefold()
    if any(t in name for t in ("stretch", "rotation")):
        return "accessory"
    if any(t in name for t in ("fly", "curl", "extension", "raise", "kickback")):
        return "isolation"
    if "press" in name:
        return "vertical_press" if any(t in name for t in ("overhead", "shoulder")) else "horizontal_press"
    if "row" in name:
        return "horizontal_pull"
    if "pulldown" in name or "pull-up" in name:
        return "vertical_pull"
    if any(t in name for t in ("deadlift", "rdl")):
        return "hinge"
    if any(t in name for t in ("squat", "lunge")):
        return "squat_lunge"
    return "unknown"


COMPOUNDS = frozenset(("vertical_press", "horizontal_press", "horizontal_pull", "vertical_pull", "hinge", "squat_lunge"))


def sessions_from_rows(rows, as_of, relationships=None):
    rows = valid_rows(rows, as_of)
    relationships = relationships if relationships is not None else multipliers(rows, as_of)
    groups = defaultdict(list)
    for row in rows:
        groups[str(row["activity_id"])].append(row)
    sessions = []
    for activity, rr in groups.items():
        counts = Counter(classify_muscle_groups(r["muscle_groups"]).primary for r in rr)
        movements = {str(r["movement_id"]) for r in rr}
        known = all(relationships[str(r["movement_id"])]["confidence"] != "LOW" for r in rr)
        normalized = sum(r["base_weight"] * r["rep_count"] *
                         relationships[str(r["movement_id"])]["workload_multiplier"] for r in rr)
        sessions.append({
            "activity_id": activity, "begin_time": rr[0]["begin_time"],
            "family": classify_workout_family(frozenset(counts)),
            "primary_counts": dict(counts), "set_count": len(rr),
            "exercise_count": len(movements), "movement_ids": sorted(movements),
            "primary_movement_count": len({str(r["movement_id"]) for r in rr if pattern(r) in COMPOUNDS}),
            "sets_per_movement": dict(Counter(str(r["movement_id"]) for r in rr)),
            "normalized_workload": round(normalized, 1), "workload_confident": known,
            "recorded_volume": sum(r["volume"] for r in rr),
        })
    return sorted(sessions, key=lambda s: (s["begin_time"], s["activity_id"]), reverse=True)


def comparable_sessions(sessions, family, as_of, window=LOOKBACK_DAYS):
    targets = {to_canonical(m) for m in SESSION_TEMPLATES[family]["muscles"]}
    eligible = [s for s in sessions if as_of - timedelta(days=window) <= s["begin_time"] <= as_of]
    def overlap(s):
        counts = s["primary_counts"]
        return sum(counts.get(m, 0) for m in targets) / s["set_count"]
    tiers = (
        ("exact_family", [s for s in eligible if s["family"] == family]),
        ("similar_primary_muscles", [s for s in eligible if len(set(s["primary_counts"]) & targets) >= min(2, len(targets)) and overlap(s) >= .6]),
        ("muscle_region", [s for s in eligible if overlap(s) >= .5]),
        ("general_personal_history", eligible),
    )
    for source, pool in tiers:
        if len(pool) >= MIN_SESSIONS:
            return pool, source
    return [], "product_policy_fallback"


def envelope(sessions, family, as_of):
    result = {}
    for window in (30, 90, LOOKBACK_DAYS):
        pool, source = comparable_sessions(sessions, family, as_of, window)
        comparable = [s for s in pool if s["workload_confident"]]
        result[str(window)] = {
            "source": source, "session_count": len(pool),
            "normalized_workload": distribution([s["normalized_workload"] for s in comparable]),
            "working_sets": distribution([s["set_count"] for s in pool]),
            "exercises": distribution([s["exercise_count"] for s in pool]),
            "primary_movement_count": distribution([s["primary_movement_count"] for s in pool]),
            "movement_ids": sorted({m for s in pool for m in s["movement_ids"]}),
        }
    return result
