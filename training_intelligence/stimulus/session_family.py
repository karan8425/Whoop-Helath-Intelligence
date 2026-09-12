"""Aggregates recent Tonal workouts into the EXISTING B3 session-family
taxonomy (integrations.tonal.training_priority.SESSION_TEMPLATES), instead
of inventing a parallel one.

SESSION_TEMPLATES is currently used only to score candidate FUTURE
sessions; nothing in production classifies a HISTORICAL workout into one
of these families. This module adds exactly that (read-only, additive),
reusing the templates' muscle lists and `minimum_eligible` thresholds
unchanged as the classification rule, so a historical "Upper Pull" means
the same thing this app's own forward-looking scorer means by it.

This performs pure aggregation - it does not rank families and does not
feed any current recommendation path.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from integrations.tonal.training_priority import SESSION_TEMPLATES
from training_intelligence.stimulus.ledger import _is_working_set, _session_weight, _require_aware
from training_intelligence.stimulus.mapping import classify_muscle_groups
from training_intelligence.stimulus.taxonomy import to_canonical

OTHER_FAMILY = "Other"

# SESSION_TEMPLATES muscles are the existing Title-Case taxonomy; convert
# once to the canonical taxonomy this package uses everywhere else.
_CANONICAL_TEMPLATES = {
    family: {
        "muscles": frozenset(
            canonical
            for canonical in (to_canonical(muscle) for muscle in spec["muscles"])
            if canonical is not None
        ),
        "minimum_eligible": spec["minimum_eligible"],
    }
    for family, spec in SESSION_TEMPLATES.items()
}


def classify_workout_family(trained_primary_muscles: frozenset) -> str:
    """Deterministic: picks the template with the most overlapping
    primary-trained muscles that still meets its own minimum_eligible
    threshold, breaking ties toward the more specific (fewer untouched
    template muscles) template. Returns OTHER_FAMILY if no template
    qualifies."""

    best_family = None
    best_score = None
    for family, spec in _CANONICAL_TEMPLATES.items():
        overlap = len(trained_primary_muscles & spec["muscles"])
        if overlap < spec["minimum_eligible"]:
            continue
        extra = len(spec["muscles"] - trained_primary_muscles)
        score = (overlap, -extra, family)
        if best_score is None or score > best_score:
            best_score = score
            best_family = family
    return best_family or OTHER_FAMILY


def aggregate_session_families(rows, as_of: datetime, window_days: int) -> dict:
    """Pure function over already-fetched rows (see ledger.load_rows) -
    groups working sets by workout, classifies each workout into a
    session family, and aggregates per family within the window."""

    as_of = _require_aware(as_of)
    cutoff = as_of - timedelta(days=window_days)

    by_workout = defaultdict(list)
    for row in rows:
        begin_time = row.get("begin_time")
        if begin_time is None or begin_time > as_of or begin_time < cutoff:
            continue
        if not _is_working_set(row):
            continue
        if _session_weight(row) is None:
            continue
        by_workout[row.get("activity_id")].append(row)

    families = defaultdict(lambda: {
        "sessions": 0, "working_sets": 0, "volume_lb": 0.0,
        "movements": set(), "last_session_at": None,
    })

    for activity_id, workout_rows in by_workout.items():
        trained_primary = set()
        for row in workout_rows:
            classification = classify_muscle_groups(row.get("muscle_groups"))
            if classification.is_mapped:
                trained_primary.add(classification.primary)
        family = classify_workout_family(frozenset(trained_primary))

        bucket = families[family]
        bucket["sessions"] += 1
        begin_time = max(r["begin_time"] for r in workout_rows)
        if bucket["last_session_at"] is None or begin_time > bucket["last_session_at"]:
            bucket["last_session_at"] = begin_time
        for row in workout_rows:
            weight = _session_weight(row)
            bucket["working_sets"] += 1
            bucket["volume_lb"] += float(row.get("volume") or 0.0) * weight
            bucket["movements"].add(row.get("movement_id"))

    # Field names are window-agnostic ("sessions", not "sessions_7d") -
    # the caller indicates which window this is by the outer key it stores
    # this dict under (e.g. {7: {...}, 14: {...}}), same convention as
    # ledger.build_ledger_windows.
    return {
        family: {
            "sessions": bucket["sessions"],
            "last_session_date": bucket["last_session_at"].date().isoformat() if bucket["last_session_at"] else None,
            "working_sets": bucket["working_sets"],
            "comparable_volume_lb": round(bucket["volume_lb"], 1),
            "exercise_exposure": len(bucket["movements"]),
        }
        for family, bucket in families.items()
    }


def session_family_windows(rows, as_of: datetime, windows) -> dict:
    """Same rows-in, multiple-windows-out convention as
    ledger.build_ledger_windows."""
    return {window: aggregate_session_families(rows, as_of, window) for window in windows}
