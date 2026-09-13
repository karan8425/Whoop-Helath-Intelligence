"""TKI-7: the ONE assembled Training Intelligence entry point.

Orchestrates the already-built, already-validated TKI-5.2/5.3/5.4
calibration stack (training_intelligence.calibration.shadow.
build_calibrated_shadow_prescription) plus decision-snapshot
persistence (TKI-5.3's snapshot.py). This module does not reimplement
any calibration/progression/workload/justification logic - it only
sequences existing calls and persists provenance. Every step below
maps directly onto an already-existing, already-tested function; see
each cited module's own report (TKI-5.2/5.3/5.4) for that step's
validation.

SHADOW MODE at the module level: importing/calling this file does not
touch any live route by itself. todays_plan.py decides, behind the
TRAINING_INTELLIGENCE_MOBILE_ENABLED flag (training_engine_flag.py),
whether to call it.
"""
from __future__ import annotations

from datetime import datetime, timezone

from training_intelligence.calibration.shadow import build_calibrated_shadow_prescription
from training_intelligence.calibration.snapshot import save_snapshot

ORCHESTRATOR_VERSION = 1


def build_training_intelligence_prescription(as_of, *, persist_snapshot: bool = True):
    """Conceptual sequence (TKI-7 section 5), each step delegated to its
    already-existing, already-tested owner - nothing is recomputed here:

      1-10. goal state, systemic readiness, local muscle readiness,
            recent stimulus, demonstrated capacity, TKI-4 session
            selection, feasible dose, goal posture, exercise selection,
            set allocation -> all internal to
            build_calibrated_shadow_prescription() (TKI-5.1/5.2).
      11. Progression Evidence V2 -> progression_v2.prescribe_v2(),
          called internally by the same function (TKI-5.4).
      12. cable-aware/normalized workload -> history.multipliers()/
          sessions_from_rows(), called internally (TKI-5.2).
      13. Workload Sanity V3 -> workload_v2.workload_sanity_v3(),
          called internally (TKI-5.4).
      14. Quality Verdict V3 -> shadow.quality_verdict_v3(), called
          internally (TKI-5.4).
      15. persist decision snapshot -> snapshot.save_snapshot(), called
          HERE (the one step this milestone actually adds).
      16. return the final result for the mobile adapter to shape.

    Returns (result, snapshot_info). snapshot_info is None when
    persist_snapshot=False (e.g. a pure read-only diagnostic call) or
    when the family was REST (no exercises to snapshot meaningfully
    still persists - a deliberate-recovery decision is itself a real,
    reproducible decision worth recording).
    """
    result = build_calibrated_shadow_prescription(as_of)
    snapshot_info = None
    if persist_snapshot and result.get("status") == "ok":
        try:
            snapshot_info = save_snapshot(as_of, result)
        except Exception as exc:
            # Persistence is provenance, not a correctness dependency -
            # a snapshot-store failure (e.g. a transient DB hiccup)
            # must never take down the actual prescription. Recorded
            # explicitly, never silently swallowed.
            snapshot_info = {"stored": False, "error": f"{type(exc).__name__}: {exc}"}
    return result, snapshot_info


def training_intelligence_diagnostic(as_of=None):
    """TKI-7 section 21: the Development/admin diagnostic payload -
    assembled TKI prescription, legacy (B3) prescription, a compact
    comparison, decision provenance, and the quality verdict. Read-only
    - never called from any user-facing route. Both engines are built
    from the SAME `as_of` so the comparison is apples-to-apples."""
    from training_intelligence.calibration.mobile_adapter import adapt_calibrated_to_workout_schema
    from integrations.tonal.workout_prescription import build_daily_workout_prescription

    as_of = as_of or datetime.now(timezone.utc)
    result, snapshot_info = build_training_intelligence_prescription(as_of, persist_snapshot=False)
    mobile = adapt_calibrated_to_workout_schema(as_of, result, snapshot_info)

    try:
        legacy = build_daily_workout_prescription(now=as_of)
    except Exception as exc:
        legacy = {"status": "error", "reason": f"{type(exc).__name__}: {exc}"}

    tki_session = mobile.get("session") or {}
    legacy_session = legacy.get("session") or {} if legacy.get("status") == "ok" else {}
    comparison = {
        "set_count_difference": (tki_session.get("total_sets") or 0) - (legacy_session.get("total_sets") or 0),
        "exercise_count_difference": (tki_session.get("exercise_count") or 0) - (legacy_session.get("exercise_count") or 0),
        "estimated_workload_difference": round(
            (tki_session.get("estimated_total_volume") or 0) - (legacy_session.get("estimated_total_volume") or 0), 1),
        "session_family_match": tki_session.get("session_type") == legacy_session.get("session_type"),
        "tki_session_family": tki_session.get("session_type"),
        "legacy_session_family": legacy_session.get("session_type"),
    }

    return {
        "as_of": as_of.isoformat(),
        "training_intelligence": {
            "status": result.get("status"),
            "mobile_prescription": mobile,
            "raw": {k: v for k, v in result.items() if k not in ("workload_reference",)},
        },
        "legacy": legacy,
        "comparison": comparison,
        "decision_provenance": {
            "decision_id": (snapshot_info or {}).get("snapshot", {}).get("decision_id") if snapshot_info else None,
            "note": "persist_snapshot=False for this diagnostic call - inspecting does not create a new immutable record.",
        },
        "quality_verdict": (result.get("quality_v3") or {}).get("verdict"),
    }
