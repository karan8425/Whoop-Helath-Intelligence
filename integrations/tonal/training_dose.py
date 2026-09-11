"""Personalized training-dose engine (Training-B2).

Replaces the static SESSION_RULES dosage table (fixed "Push=16 sets" style
bands) with a dose derived from the user's own Tonal training history:

    PERSONAL HISTORICAL DOSE
    x WHOOP SYSTEMIC CAPACITY
    x LOCAL MUSCLE READINESS / AVAILABLE MUSCLE BUDGET
    x RECENT TRAINING LOAD
    = TODAY'S TARGET DOSE

WHOOP adjusts the personal baseline. WHOOP does not define it.

This module is intentionally read-only with respect to muscle readiness and
movement eligibility (B1/B1.1) - it only answers "how much", never "what" or
"whether a movement is allowed". It never resurrects a B1-suppressed muscle.

Every public function accepts optional pre-fetched data (`sessions=`,
`rows=`, `now=`) so it is deterministically testable without a live database
connection, while defaulting to real queries against the existing
tonal_workouts / tonal_sets / tonal_movements / tonal_workout_overrides
schema when called with no overrides.
"""

from collections import defaultdict
from datetime import timedelta

from db import get_conn

from integrations.tonal.muscle_readiness import (
    PROGRAMMING_MUSCLES,
    PRIMARY_SET_WEIGHT,
    SECONDARY_SET_WEIGHT,
    SUPPLEMENTAL_WEIGHT,
    ABBREVIATED_REASON,
    _normalize_muscle,
    _hours,
)


# ============================================================
# CONFIGURATION (centralized, deterministic)
# ============================================================

BASELINE_WINDOWS_DAYS = (7, 14, 30, 90)

# WHOOP recovery-score bands, matching _latest_readiness()'s bands exactly.
RECOVERY_BAND_RANGES = {
    "high": (80.0, 100.0),
    "good": (67.0, 80.0),
    "moderate": (45.0, 67.0),
    "low": (25.0, 45.0),
    "very_low": (0.0, 25.0),
}

# WHOOP capacity multiplier applied to the PERSONAL comparable baseline.
# These are modifiers, not absolute set prescriptions.
WHOOP_CAPACITY_RANGES = {
    "high": (1.05, 1.15),
    "good": (0.90, 1.05),
    "moderate": (0.70, 0.85),
    "low": (0.30, 0.50),
    "very_low": (0.0, 0.0),
}

# Recent-load comparison thresholds (recent 7d effective sets vs personal
# weekly baseline). Deterministic, not randomized.
RECENT_LOAD_HIGH_RATIO = 1.4
RECENT_LOAD_ELEVATED_RATIO = 1.15
RECENT_LOAD_LOW_RATIO = 0.60

RECENT_LOAD_HIGH_MULTIPLIER = 0.80
RECENT_LOAD_ELEVATED_MULTIPLIER = 0.92
RECENT_LOAD_LOW_MULTIPLIER = 1.08
RECENT_LOAD_NEUTRAL_MULTIPLIER = 1.0

# Minimum qualifying comparable sessions required before recent-load can
# ever grant an *increase*. Missing/stale history must never be read as
# "low load" that unlocks more work.
MIN_SESSIONS_FOR_LOAD_INCREASE = 3

# Fallback per-muscle set baseline used only when a muscle has no
# attributable session history at all (tier-4 conservative fallback).
CONSERVATIVE_MUSCLE_SET_BASELINE = 3.0

# Comparable-session similarity.
MIN_HIGH_QUALITY_COMPARABLE_SESSIONS = 3
MIN_BROAD_REGION_COMPARABLE_SESSIONS = 2
COMPARABLE_LOOKBACK_DAYS = 90

UPPER_MUSCLES = {"Chest", "Back", "Shoulders", "Biceps", "Triceps"}
LOWER_MUSCLES = {"Glutes", "Hamstrings", "Quads"}

# Confidence scoring thresholds.
FRESH_TONAL_HOURS = 72.0
STALE_TONAL_HOURS = 120.0


def _clamp(value, low, high):
    return max(low, min(high, value))


def _median(values):
    values = sorted(values)
    n = len(values)
    if n == 0:
        return None
    mid = n // 2
    if n % 2:
        return float(values[mid])
    return (values[mid - 1] + values[mid]) / 2.0


def _percentile(values, pct):
    """Nearest-rank percentile - no extra dependency, deterministic."""
    values = sorted(values)
    if not values:
        return None
    if len(values) == 1:
        return float(values[0])
    k = _clamp(pct / 100.0 * (len(values) - 1), 0, len(values) - 1)
    lo = int(k)
    hi = min(lo + 1, len(values) - 1)
    frac = k - lo
    return values[lo] + (values[hi] - values[lo]) * frac


# ============================================================
# STEP 1/2 - SESSION HISTORY + QUALITY FILTERING
# ============================================================

def load_session_history(now, lookback_days=COMPARABLE_LOOKBACK_DAYS):
    """One row per Tonal workout, with the override/exclusion metadata
    needed for quality filtering. Real schema: tonal_workouts (+ optional
    tonal_workout_overrides)."""

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    w.activity_id, w.begin_time, w.workout_type,
                    w.duration_seconds, w.total_reps, w.total_volume,
                    w.set_count, w.movement_count,
                    COALESCE(o.include_in_training_analysis, TRUE) AS included,
                    o.exclusion_reason
                FROM tonal_workouts w
                LEFT JOIN tonal_workout_overrides o USING (activity_id)
                WHERE w.begin_time <= %s AND w.begin_time >= %s
                ORDER BY w.begin_time DESC
                """,
                (now, now - timedelta(days=lookback_days)),
            )
            return cur.fetchall()


def filter_qualifying_sessions(sessions):
    """Exclude/down-weight sessions that should not define the normal dose.

    Returns (qualifying, excluded) where `excluded` is a list of
    {activity_id, reason} for reporting. Robust statistics (median/
    percentile) are used downstream specifically so a handful of
    legitimately large or small sessions can't distort the baseline the
    way a mean would - so we do NOT drop large workouts here, only ones
    that are structurally unusable or explicitly excluded.
    """

    qualifying = []
    excluded = []

    for row in sessions:
        activity_id = row.get("activity_id")

        if row.get("included") is False:
            reason = (
                " ".join(str(row.get("exclusion_reason") or "").casefold().split())
            )
            if reason == ABBREVIATED_REASON:
                # Supplemental/abbreviated: down-weighted, not counted as a
                # qualifying comparable session for baseline purposes.
                excluded.append({"activity_id": activity_id, "reason": "abbreviated_session"})
            else:
                excluded.append({"activity_id": activity_id, "reason": reason or "explicit_override_excluded"})
            continue

        set_count = row.get("set_count") or 0
        volume = row.get("total_volume") or 0
        movement_count = row.get("movement_count") or 0

        if not set_count or not movement_count:
            excluded.append({"activity_id": activity_id, "reason": "malformed_missing_set_or_movement_count"})
            continue

        if volume is None or volume < 0:
            excluded.append({"activity_id": activity_id, "reason": "unusable_load_data"})
            continue

        if set_count < 2:
            excluded.append({"activity_id": activity_id, "reason": "abbreviated_session"})
            continue

        qualifying.append(row)

    return qualifying, excluded


# ============================================================
# STEP 2 - SESSION-LEVEL ROLLING BASELINES
# ============================================================

def _session_window_stats(sessions, now, window_days):
    cutoff = now - timedelta(days=window_days)
    in_window = [s for s in sessions if s["begin_time"] >= cutoff]

    sets = [float(s.get("set_count") or 0) for s in in_window]
    exercises = [float(s.get("movement_count") or 0) for s in in_window]
    reps = [float(s.get("total_reps") or 0) for s in in_window]
    volumes = [float(s.get("total_volume") or 0) for s in in_window]
    durations = [
        float(s["duration_seconds"]) / 60.0
        for s in in_window
        if s.get("duration_seconds")
    ]

    weeks = max(window_days / 7.0, 1e-9)

    return {
        "window_days": window_days,
        "qualifying_workout_count": len(in_window),
        "median_sets": _median(sets),
        "median_exercises": _median(exercises),
        "median_reps": _median(reps),
        "median_volume": _median(volumes),
        "median_duration_minutes": _median(durations) if durations else None,
        "duration_sample_count": len(durations),
        "p25_volume": _percentile(volumes, 25),
        "p50_volume": _percentile(volumes, 50),
        "p75_volume": _percentile(volumes, 75),
        "p25_sets": _percentile(sets, 25),
        "p50_sets": _percentile(sets, 50),
        "p75_sets": _percentile(sets, 75),
        "sessions_per_week": len(in_window) / weeks,
        "volume_per_week": sum(volumes) / weeks if volumes else 0.0,
        "sets_per_week": sum(sets) / weeks if sets else 0.0,
    }


def compute_session_baselines(now, sessions=None, windows=BASELINE_WINDOWS_DAYS):
    """Rolling session-level personal baselines over each window in `windows`.

    Uses only qualifying sessions (see filter_qualifying_sessions).
    """

    if sessions is None:
        sessions = load_session_history(now, lookback_days=max(windows))

    qualifying, excluded = filter_qualifying_sessions(sessions)

    return {
        "windows": {
            days: _session_window_stats(qualifying, now, days)
            for days in windows
        },
        "excluded_session_count": len(excluded),
        "excluded_sessions": excluded,
        "total_sessions_considered": len(sessions),
    }


# ============================================================
# STEP 2 - MUSCLE-LEVEL ROLLING BASELINES
# ============================================================

def _load_muscle_set_rows(now, lookback_days):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT w.activity_id, w.begin_time,
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
            return cur.fetchall()


def _muscle_exposures(rows):
    """Group raw set rows into per-session, per-muscle effective-set
    exposure, using the exact B1 semantics (primary=1.0, secondary=0.35).
    Unmapped sets are not counted toward any muscle budget.
    """

    sessions = defaultdict(lambda: defaultdict(lambda: {"sets": 0.0, "volume": 0.0}))
    unmapped = 0

    for row in rows:
        groups = []
        for raw in row.get("muscle_groups") or []:
            muscle = _normalize_muscle(raw)
            if muscle and muscle not in groups:
                groups.append(muscle)
        if not groups:
            unmapped += 1
            continue

        included = row.get("included") is True
        supplemental = (
            not included
            and " ".join(str(row.get("exclusion_reason") or "").casefold().split())
            == ABBREVIATED_REASON
        )
        if not included and not supplemental:
            continue

        weight = SUPPLEMENTAL_WEIGHT if supplemental else 1.0
        key = (row.get("activity_id"), row.get("begin_time"))
        volume = float(row.get("volume") or 0.0)

        sessions[key][groups[0]]["sets"] += PRIMARY_SET_WEIGHT * weight
        sessions[key][groups[0]]["volume"] += volume * weight
        for muscle in groups[1:]:
            sessions[key][muscle]["sets"] += SECONDARY_SET_WEIGHT * weight
            sessions[key][muscle]["volume"] += volume * SECONDARY_SET_WEIGHT * weight

    history = defaultdict(list)
    for (_, when), muscles in sessions.items():
        for muscle, exposure in muscles.items():
            history[muscle].append({"when": when, **exposure})

    return history, unmapped


def compute_muscle_baselines(now, rows=None, windows=BASELINE_WINDOWS_DAYS):
    """Per-muscle rolling effective-set / volume / frequency baselines."""

    lookback = max(windows)
    if rows is None:
        rows = _load_muscle_set_rows(now, lookback)

    history, unmapped = _muscle_exposures(rows)

    output = {}
    for muscle in PROGRAMMING_MUSCLES:
        exposures = history.get(muscle, [])
        entry = {"windows": {}}

        last_primary = None
        for item in exposures:
            if item["sets"] > 0 and (last_primary is None or item["when"] > last_primary):
                last_primary = item["when"]

        hours_since = _hours(now, last_primary)
        entry["hours_since_meaningful_exposure"] = round(hours_since, 1) if hours_since is not None else None
        entry["session_count_90d"] = len(exposures)

        for days in windows:
            cutoff_hours = days * 24.0
            in_window = [e for e in exposures if _hours(now, e["when"]) <= cutoff_hours]
            effective_sets = sum(e["sets"] for e in in_window)
            volume = sum(e["volume"] for e in in_window)
            weeks = max(days / 7.0, 1e-9)
            entry["windows"][days] = {
                "effective_sets": round(effective_sets, 2),
                "volume": round(volume, 1),
                "session_count": len(in_window),
                "effective_sets_per_week": round(effective_sets / weeks, 2),
            }

        output[muscle] = entry

    return {"muscles": output, "unmapped_sets_ignored": unmapped}


# ============================================================
# STEP 3 - COMPARABLE SESSION BASELINES
# ============================================================

def _session_similarity(session, target_muscles, session_type_hint):
    """Deterministic 0-1 similarity score. Title/workout_type text is never
    required to match - most Tonal sessions are "Custom"."""

    score = 0.0
    covered = set()
    session_type = (session.get("workout_type") or "").strip().lower()
    hint = (session_type_hint or "").strip().lower()

    # Movement/size similarity: sessions of comparable exercise count score
    # higher than tiny or oversized outliers relative to what we're
    # planning today.
    movement_count = session.get("movement_count") or 0
    if 2 <= movement_count <= 6:
        score += 0.2

    if hint and session_type:
        if hint in session_type or session_type in hint:
            score += 0.3
        elif ("upper" in hint and "upper" in session_type) or (
            "lower" in hint and "lower" in session_type
        ):
            score += 0.2
        elif "full" in hint and "full" in session_type:
            score += 0.2

    # We don't have a direct per-session muscle-overlap column without
    # rejoining tonal_sets/tonal_movements per session; conservatively
    # treat sessions within the requested body-region family as a partial
    # muscle-overlap match using workout_type text, which Tonal populates
    # for guided programs even when the title is "Custom".
    region_upper = any(m in UPPER_MUSCLES for m in target_muscles)
    region_lower = any(m in LOWER_MUSCLES for m in target_muscles)
    if region_upper and "upper" in session_type:
        score += 0.3
        covered.add("region")
    if region_lower and "lower" in session_type:
        score += 0.3
        covered.add("region")
    if "core" in session_type and "Core" in target_muscles:
        score += 0.2
        covered.add("region")
    if "full" in session_type:
        score += 0.15

    return min(1.0, score)


def select_comparable_sessions(
    sessions,
    target_muscles,
    session_type_hint,
    now,
    lookback_days=COMPARABLE_LOOKBACK_DAYS,
):
    """Fallback hierarchy:
      1. high-quality comparable sessions in the last 30-90d
      2. broader muscle-region sessions
      3. user's global qualifying-session baseline
      4. conservative configured fallback (handled by caller)

    Returns (sessions_used, baseline_source, similarity_confidence).
    """

    qualifying, _ = filter_qualifying_sessions(sessions)

    scored = [
        (s, _session_similarity(s, target_muscles, session_type_hint))
        for s in qualifying
    ]

    tier1_cutoff = now - timedelta(days=30)
    tier1 = [s for s, sc in scored if sc >= 0.5 and s["begin_time"] >= tier1_cutoff]
    if len(tier1) < MIN_HIGH_QUALITY_COMPARABLE_SESSIONS:
        tier1_90 = [s for s, sc in scored if sc >= 0.5]
        if len(tier1_90) >= MIN_HIGH_QUALITY_COMPARABLE_SESSIONS:
            return tier1_90, "comparable_sessions_30_90d", "high"

    if len(tier1) >= MIN_HIGH_QUALITY_COMPARABLE_SESSIONS:
        return tier1, "comparable_sessions_30_90d", "high"

    tier2 = [s for s, sc in scored if sc >= 0.2]
    if len(tier2) >= MIN_BROAD_REGION_COMPARABLE_SESSIONS:
        return tier2, "broader_muscle_region_sessions", "medium"

    if qualifying:
        return qualifying, "global_qualifying_session_baseline", "low"

    return [], "conservative_configured_fallback", "low"


def compute_comparable_baseline(
    now,
    sessions,
    target_muscles,
    session_type_hint,
):
    used, source, similarity_confidence = select_comparable_sessions(
        sessions, target_muscles, session_type_hint, now
    )

    if not used:
        return {
            "source": source,
            "session_count": 0,
            "median_sets": None,
            "median_exercises": None,
            "median_volume": None,
            "median_duration_minutes": None,
            "p25_volume": None,
            "p50_volume": None,
            "p75_volume": None,
            "similarity_confidence": similarity_confidence,
        }

    sets = [float(s.get("set_count") or 0) for s in used]
    exercises = [float(s.get("movement_count") or 0) for s in used]
    volumes = [float(s.get("total_volume") or 0) for s in used]
    durations = [
        float(s["duration_seconds"]) / 60.0 for s in used if s.get("duration_seconds")
    ]

    return {
        "source": source,
        "session_count": len(used),
        "median_sets": _median(sets),
        "median_exercises": _median(exercises),
        "median_volume": _median(volumes),
        "median_duration_minutes": _median(durations) if durations else None,
        "duration_sample_count": len(durations),
        "p25_volume": _percentile(volumes, 25),
        "p50_volume": _percentile(volumes, 50),
        "p75_volume": _percentile(volumes, 75),
        "similarity_confidence": similarity_confidence,
    }


# ============================================================
# STEP 5 - WHOOP SYSTEMIC CAPACITY MULTIPLIER
# ============================================================

def whoop_capacity_multiplier(readiness_band, recovery_score):
    """Deterministic multiplier chosen by where `recovery_score` falls
    inside its band's configured range - never randomized, never a flat
    per-band constant."""

    lo, hi = WHOOP_CAPACITY_RANGES.get(readiness_band, (1.0, 1.0))
    if lo == hi:
        return lo

    band_lo, band_hi = RECOVERY_BAND_RANGES.get(readiness_band, (0.0, 100.0))
    if recovery_score is None or band_hi <= band_lo:
        fraction = 0.5
    else:
        fraction = _clamp((recovery_score - band_lo) / (band_hi - band_lo), 0.0, 1.0)

    return round(lo + fraction * (hi - lo), 3)


# ============================================================
# STEP 6 - RECENT LOAD MODIFIER
# ============================================================

def recent_load_modifier(session_baselines, muscles_ready):
    """Compares recent (7d) session load against the personal 30d weekly
    rate. Missing/stale history is treated as insufficient evidence, never
    as "low load that unlocks more work".
    """

    windows = session_baselines["windows"]
    w7 = windows.get(7, {})
    w30 = windows.get(30, {})

    baseline_sets_per_week = w30.get("sets_per_week") or 0.0
    recent_sets_per_week = w7.get("sets_per_week") or 0.0
    has_sufficient_history = (w30.get("qualifying_workout_count") or 0) >= MIN_SESSIONS_FOR_LOAD_INCREASE

    if baseline_sets_per_week <= 0:
        return {
            "multiplier": RECENT_LOAD_NEUTRAL_MULTIPLIER,
            "ratio": None,
            "reason": "Insufficient personal history to compare recent load against a baseline; using a neutral modifier.",
        }

    ratio = recent_sets_per_week / baseline_sets_per_week

    if ratio >= RECENT_LOAD_HIGH_RATIO:
        return {
            "multiplier": RECENT_LOAD_HIGH_MULTIPLIER,
            "ratio": round(ratio, 2),
            "reason": f"Recent 7-day set volume is {round(ratio * 100)}% of the personal weekly baseline - reducing today's dose.",
        }

    if ratio >= RECENT_LOAD_ELEVATED_RATIO:
        return {
            "multiplier": RECENT_LOAD_ELEVATED_MULTIPLIER,
            "ratio": round(ratio, 2),
            "reason": "Recent 7-day load is moderately elevated versus baseline - trimming today's dose slightly.",
        }

    if ratio <= RECENT_LOAD_LOW_RATIO and has_sufficient_history and muscles_ready:
        return {
            "multiplier": RECENT_LOAD_LOW_MULTIPLIER,
            "ratio": round(ratio, 2),
            "reason": "Recent load is below the personal baseline and target muscles are ready - allowing a modest increase.",
        }

    return {
        "multiplier": RECENT_LOAD_NEUTRAL_MULTIPLIER,
        "ratio": round(ratio, 2),
        "reason": "Recent load is within the normal range of the personal baseline.",
    }


# ============================================================
# STEP 7 - PER-MUSCLE BUDGET
# ============================================================

def muscle_budget(
    muscle,
    readiness_entry,
    muscle_baseline_entry,
    whoop_multiplier,
    recent_load_multiplier,
):
    """Effective-set budget for one target muscle today.

    Consistent with B1: SUPPRESSED and FATIGUED always receive zero direct
    budget here. B2 may reduce a muscle's dose; it must never resurrect a
    B1-suppressed muscle.
    """

    state = readiness_entry.get("readiness_state") if readiness_entry else None

    if state == "SUPPRESSED":
        return {
            "muscle": muscle,
            "state": state,
            "budget_effective_sets": 0.0,
            "reason": "SUPPRESSED by B1 muscle readiness - zero direct working-set budget (invariant).",
        }

    if state == "FATIGUED":
        return {
            "muscle": muscle,
            "state": state,
            "budget_effective_sets": 0.0,
            "reason": "FATIGUED - zero direct budget by default.",
        }

    baseline_window = (muscle_baseline_entry or {}).get("windows", {}).get(14, {})
    baseline_sets_per_week = baseline_window.get("effective_sets_per_week") or 0.0
    # Convert a weekly rate to a reasonable per-session share; most
    # programs hit a given muscle roughly 1-2x/week.
    session_share = baseline_sets_per_week / 1.5 if baseline_sets_per_week else 0.0
    personal_baseline = session_share or CONSERVATIVE_MUSCLE_SET_BASELINE
    has_history = bool(session_share)

    if state == "RECOVERING":
        budget = personal_baseline * 0.5
        reason = "RECOVERING - local budget reduced from personal baseline; WHOOP is applied once at session dose."
    elif state == "READY":
        budget = personal_baseline
        reason = "READY - normal local personal budget; systemic modifiers are applied once at session dose."
    elif state == "FRESH":
        budget = personal_baseline
        if has_history:
            budget *= 1.1
            reason = "FRESH - modest local readiness allowance from personal history."
        else:
            reason = "FRESH - conservative local budget because personal muscle history is sparse."
    else:
        budget = personal_baseline * 0.5
        reason = f"Unrecognized readiness state '{state}' - using a conservative local budget."

    return {
        "muscle": muscle,
        "state": state,
        "budget_effective_sets": round(max(0.0, budget), 2),
        "reason": reason,
    }


# ============================================================
# STEP 14 - CONFIDENCE
# ============================================================

def dose_confidence(comparable_baseline, tonal_freshness_hours, session_baselines):
    score = 0
    count = comparable_baseline.get("session_count") or 0
    if count >= MIN_HIGH_QUALITY_COMPARABLE_SESSIONS:
        score += 2
    elif count >= 1:
        score += 1

    if tonal_freshness_hours is not None and tonal_freshness_hours <= FRESH_TONAL_HOURS:
        score += 1
    elif tonal_freshness_hours is not None and tonal_freshness_hours <= STALE_TONAL_HOURS:
        score += 0
    else:
        score -= 1

    if comparable_baseline.get("source") == "comparable_sessions_30_90d":
        score += 1
    elif comparable_baseline.get("source") == "conservative_configured_fallback":
        score -= 1

    if (session_baselines["windows"].get(30, {}).get("qualifying_workout_count") or 0) < MIN_SESSIONS_FOR_LOAD_INCREASE:
        score -= 1

    if score >= 3:
        return "HIGH"
    if score >= 1:
        return "MEDIUM"
    return "LOW"


# ============================================================
# STEP 8 - TOP-LEVEL ORCHESTRATION
# ============================================================

def compute_dose_target(
    now,
    readiness_band,
    recovery_score,
    target_muscles,
    session_type_hint,
    muscle_readiness_by_name,
    tonal_freshness_hours,
    sessions=None,
    muscle_rows=None,
):
    """Returns the full Step 8 structured dose target.

    `muscle_readiness_by_name` is {muscle: readiness_entry} from B1's
    calculate_muscle_readiness() output - never recomputed here.
    """

    if sessions is None:
        sessions = load_session_history(now)

    session_baselines = compute_session_baselines(now, sessions=sessions)
    muscle_baselines = compute_muscle_baselines(now, rows=muscle_rows)

    comparable = compute_comparable_baseline(now, sessions, target_muscles, session_type_hint)

    whoop_multiplier = whoop_capacity_multiplier(readiness_band, recovery_score)

    target_ready = all(
        (muscle_readiness_by_name.get(m) or {}).get("readiness_state") in ("READY", "FRESH")
        for m in target_muscles
    ) if target_muscles else False

    load = recent_load_modifier(session_baselines, target_ready)

    confidence = dose_confidence(comparable, tonal_freshness_hours, session_baselines)

    combined_multiplier = whoop_multiplier * load["multiplier"]

    # ---- muscle budgets (never resurrects a suppressed muscle) ----
    muscle_budgets = {}
    for muscle in target_muscles:
        entry = muscle_readiness_by_name.get(muscle)
        baseline_entry = muscle_baselines["muscles"].get(muscle)
        muscle_budgets[muscle] = muscle_budget(
            muscle, entry, baseline_entry, whoop_multiplier, load["multiplier"]
        )

    total_muscle_budget = sum(b["budget_effective_sets"] for b in muscle_budgets.values())

    # ---- session-level target, derived from the comparable baseline ----
    baseline_sets = comparable.get("median_sets")
    baseline_exercises = comparable.get("median_exercises")
    baseline_volume = comparable.get("p50_volume")
    baseline_volume_low = comparable.get("p25_volume")
    baseline_volume_high = comparable.get("p75_volume")
    baseline_duration = comparable.get("median_duration_minutes")

    if baseline_sets is None:
        # Tier-4 conservative fallback: no comparable/global history at all.
        baseline_sets = CONSERVATIVE_MUSCLE_SET_BASELINE * max(len(target_muscles), 1)
        baseline_exercises = max(len(target_muscles), 1)

    raw_target_sets = baseline_sets * combined_multiplier
    personalized_low_floor_applied = False
    if readiness_band == "low" and target_ready:
        # Preserve a reduced but meaningful hypertrophy signal when useful
        # muscles are locally ready. This floor scales from the user's own
        # comparable productive dose, never from a generic number of sets.
        personalized_floor = baseline_sets * 0.50
        if raw_target_sets < personalized_floor:
            raw_target_sets = personalized_floor
            personalized_low_floor_applied = True
    # A session can never usefully exceed what its target muscles can
    # absorb today - the per-muscle budget is a hard, not advisory, cap.
    dose_limited_by = None
    if target_muscles and total_muscle_budget > 0 and raw_target_sets > total_muscle_budget:
        raw_target_sets = total_muscle_budget
        dose_limited_by = "muscle_readiness_budget"
    elif target_muscles and total_muscle_budget == 0:
        raw_target_sets = 0
        dose_limited_by = "muscle_readiness_budget"

    working_sets_target = max(0, round(raw_target_sets))

    exercise_count_target = (
        max(1, round((baseline_exercises or 1) )) if working_sets_target > 0 else 0
    )

    if baseline_volume is not None:
        volume_target = baseline_volume * combined_multiplier
        volume_low = (baseline_volume_low or baseline_volume * 0.8) * combined_multiplier
        volume_high = (baseline_volume_high or baseline_volume * 1.2) * combined_multiplier
    else:
        volume_target = volume_low = volume_high = None

    if baseline_duration is not None and comparable.get("duration_sample_count", 0) > 0:
        duration_target = baseline_duration * combined_multiplier
        duration_low = duration_target * 0.85
        duration_high = duration_target * 1.15
        duration_available = True
    else:
        duration_target = duration_low = duration_high = None
        duration_available = False

    return {
        "baseline": {
            "source": comparable.get("source"),
            "session_count": comparable.get("session_count"),
            "median_sets": comparable.get("median_sets"),
            "median_volume": comparable.get("median_volume"),
            "median_duration_minutes": comparable.get("median_duration_minutes"),
            "similarity_confidence": comparable.get("similarity_confidence"),
        },
        "modifiers": {
            "whoop_capacity": whoop_multiplier,
            "recent_load": load["multiplier"],
            "recent_load_reason": load["reason"],
            "recent_load_ratio": load["ratio"],
            "combined": round(combined_multiplier, 3),
            "personalized_low_floor_applied": personalized_low_floor_applied,
            "personalized_low_floor_fraction": 0.50 if personalized_low_floor_applied else None,
            "confidence": confidence,
        },
        "target": {
            "exercise_count": exercise_count_target,
            "working_sets": working_sets_target,
            "volume_low": round(volume_low, 1) if volume_low is not None else None,
            "volume_target": round(volume_target, 1) if volume_target is not None else None,
            "volume_high": round(volume_high, 1) if volume_high is not None else None,
            "duration_low_minutes": round(duration_low, 1) if duration_low is not None else None,
            "duration_high_minutes": round(duration_high, 1) if duration_high is not None else None,
            "duration_available": duration_available,
        },
        "muscle_budgets": muscle_budgets,
        "dose_limited_by": dose_limited_by,
        "dose_confidence": confidence,
        "session_baselines": session_baselines,
        "muscle_baselines": muscle_baselines,
    }
