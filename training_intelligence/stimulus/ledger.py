"""Deterministic weekly (7/14/30-day rolling) muscle stimulus ledger.

Built from actual tonal_workouts / tonal_sets / tonal_movements /
tonal_workout_overrides history - the same schema and inclusion
convention integrations.tonal.muscle_readiness already uses, reused here
rather than reinvented (see training_intelligence/stimulus/mapping.py and
policy.py docstrings).

TIME SEMANTICS (critical - section 7 of the TKI-1/TKI-2 assignment):
every query is bounded by `begin_time <= as_of`. No workout or set with a
begin_time after `as_of` is ever visible to this module, at any lookback
window. `as_of` must be an explicit, timezone-aware datetime; there is no
implicit "now" default here (unlike some older Tonal modules) so a
historical/replay caller can never accidentally fall back to the live
clock.

STIMULUS-SET ACCOUNTING (section 6): these are PRODUCT POLICY set counts,
not scientifically measured "effective sets" - see policy.py.

    direct_sets                = (working sets where this muscle is
                                   primary) x DIRECT_SET_CREDIT x
                                   session_weight
    secondary_set_equivalents  = (working sets where this muscle is
                                   secondary) x SECONDARY_SET_CREDIT x
                                   session_weight
    total_stimulus_sets        = direct_sets + secondary_set_equivalents

A "working set" requires rep_count > 0. Tonal's synced schema has no
explicit warmup/non-working flag; sets with a null or zero rep_count are
excluded as not-a-working-set, but a genuine warmup with recorded reps
cannot be distinguished from a working set with this data model - this is
a documented limitation, not a bug (see the audit in
TRAINING_INTELLIGENCE_TKI12_REPORT.md).

A workout excluded from training analysis
(tonal_workout_overrides.include_in_training_analysis = false) contributes
nothing, except the same "abbreviated freestyle session" carve-out
integrations.tonal.muscle_readiness already grants partial
(SUPPLEMENTAL_WEIGHT) credit for.

Sets whose movement has no recognized muscle mapping are excluded from
every muscle's totals and counted in the ledger's `unmapped_working_sets`
metadata instead of being silently dropped or guessed.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from db import get_conn
from training_intelligence.stimulus.mapping import classify_muscle_groups
from training_intelligence.stimulus.policy import (
    DIRECT_SET_CREDIT,
    SECONDARY_SET_CREDIT,
    LEDGER_WINDOWS_DAYS,
)
from training_intelligence.stimulus.taxonomy import CANONICAL_MUSCLES

# Reuse the exact supplemental-session policy already established in
# integrations.tonal.muscle_readiness rather than redefining it.
from integrations.tonal.muscle_readiness import SUPPLEMENTAL_WEIGHT, ABBREVIATED_REASON


def _require_aware(as_of: datetime) -> datetime:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be a timezone-aware datetime")
    return as_of


def load_rows(as_of: datetime, lookback_days: int, conn=None):
    """One row per (working) Tonal set within the lookback window, bounded
    by as_of. Never returns a row with begin_time > as_of."""

    as_of = _require_aware(as_of)
    query = """
        SELECT
            w.activity_id, w.begin_time,
            s.set_index, s.movement_id, s.rep_count, s.volume,
            COALESCE(o.include_in_training_analysis, TRUE) AS included,
            o.exclusion_reason,
            m.muscle_groups, m.name AS movement_name
        FROM tonal_workouts w
        JOIN tonal_sets s USING (activity_id)
        LEFT JOIN tonal_workout_overrides o USING (activity_id)
        LEFT JOIN tonal_movements m USING (movement_id)
        WHERE w.begin_time <= %s AND w.begin_time >= %s
        ORDER BY w.begin_time DESC, s.set_index
    """
    params = (as_of, as_of - timedelta(days=lookback_days))
    if conn is not None:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchall()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchall()


def _is_working_set(row) -> bool:
    rep_count = row.get("rep_count")
    return rep_count is not None and rep_count > 0


def _session_weight(row):
    """Mirrors integrations.tonal.muscle_readiness exactly: a fully
    included workout counts fully; an "abbreviated freestyle session"
    excluded from training analysis still counts at SUPPLEMENTAL_WEIGHT;
    anything else excluded contributes nothing."""

    included = row.get("included") is True
    if included:
        return 1.0
    reason = " ".join(str(row.get("exclusion_reason") or "").casefold().split())
    if reason == ABBREVIATED_REASON:
        return SUPPLEMENTAL_WEIGHT
    return None


def build_ledger_from_rows(rows, as_of: datetime, window_days: int) -> dict:
    """Pure function: accumulates an already-fetched row set (see
    load_rows) into the per-muscle ledger for exactly one rolling window.
    No DB access - this is what unit/temporal tests exercise directly."""

    as_of = _require_aware(as_of)
    cutoff = as_of - timedelta(days=window_days)

    accum = {
        muscle: {
            "direct_sets": 0.0,
            "secondary_set_equivalents": 0.0,
            "working_sets": 0.0,
            "recent_volume_lb": 0.0,
            "sessions": set(),
            "movements": set(),
            "last_trained_at": None,
        }
        for muscle in CANONICAL_MUSCLES
    }
    unmapped_working_sets = 0
    total_working_sets = 0

    for row in rows:
        begin_time = row.get("begin_time")
        if begin_time is None or begin_time > as_of or begin_time < cutoff:
            continue
        if not _is_working_set(row):
            continue

        weight = _session_weight(row)
        if weight is None:
            continue

        classification = classify_muscle_groups(row.get("muscle_groups"))
        if not classification.is_mapped:
            unmapped_working_sets += 1
            continue

        total_working_sets += 1
        volume = float(row.get("volume") or 0.0)
        activity_id = row.get("activity_id")
        movement_id = row.get("movement_id")

        primary = classification.primary
        bucket = accum[primary]
        bucket["direct_sets"] += DIRECT_SET_CREDIT * weight
        bucket["working_sets"] += 1
        bucket["recent_volume_lb"] += volume * weight
        bucket["sessions"].add(activity_id)
        bucket["movements"].add(movement_id)
        if bucket["last_trained_at"] is None or begin_time > bucket["last_trained_at"]:
            bucket["last_trained_at"] = begin_time

        for secondary in classification.secondary:
            sec_bucket = accum[secondary]
            sec_bucket["secondary_set_equivalents"] += SECONDARY_SET_CREDIT * weight
            sec_bucket["recent_volume_lb"] += volume * SECONDARY_SET_CREDIT * weight
            sec_bucket["sessions"].add(activity_id)
            sec_bucket["movements"].add(movement_id)
            # A secondary exposure still counts as contact with the muscle
            # for recency purposes (matches muscle_readiness.py's own
            # effective-exposure treatment), but last_trained_at above is
            # reserved for primary exposure specifically - see docstring.

    muscles = {}
    for muscle, bucket in accum.items():
        direct = round(bucket["direct_sets"], 3)
        secondary = round(bucket["secondary_set_equivalents"], 3)
        last_trained_at = bucket["last_trained_at"]
        days_since_trained = (
            round((as_of - last_trained_at).total_seconds() / 86400.0, 2)
            if last_trained_at is not None
            else None
        )
        muscles[muscle] = {
            "direct_sets": direct,
            "secondary_set_equivalents": secondary,
            "total_stimulus_sets": round(direct + secondary, 3),
            "sessions": len(bucket["sessions"]),
            "last_trained_at": last_trained_at.isoformat() if last_trained_at else None,
            "days_since_trained": days_since_trained,
            "exercise_count": len(bucket["movements"]),
            "working_sets": int(bucket["working_sets"]),
            "recent_volume_lb": round(bucket["recent_volume_lb"], 1),
        }

    return {
        "as_of": as_of.isoformat(),
        "window_days": window_days,
        "muscles": muscles,
        "data_quality": {
            "total_working_sets_considered": total_working_sets,
            "unmapped_working_sets": unmapped_working_sets,
        },
    }


def build_muscle_stimulus_ledger(as_of: datetime, window_days: int = 7, rows=None, conn=None) -> dict:
    """Single-window ledger. Fetches rows itself (bounded to
    window_days) unless `rows` is supplied (deterministic testing without
    a live database, matching the rest of this codebase's convention)."""

    as_of = _require_aware(as_of)
    if rows is None:
        rows = load_rows(as_of, window_days, conn=conn)
    return build_ledger_from_rows(rows, as_of, window_days)


def build_ledger_windows(as_of: datetime, windows=LEDGER_WINDOWS_DAYS, rows=None, conn=None) -> dict:
    """Computes every requested rolling window from ONE fetch (bounded to
    the widest window) instead of one query per window - see
    the performance discussion in the final report."""

    as_of = _require_aware(as_of)
    max_window = max(windows)
    if rows is None:
        rows = load_rows(as_of, max_window, conn=conn)
    return {window: build_ledger_from_rows(rows, as_of, window) for window in windows}
