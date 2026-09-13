"""TKI-5.3 (sections 5-8, 29-30): a lightweight, shadow-only, IMMUTABLE
decision-snapshot store - not an event-sourcing platform.

Persists exactly what a calibrated shadow prescription saw and decided,
so a future engine version (or a human) can reproduce and explain a
past decision without re-deriving it from mutable live state. This is
entirely separate from `todays_plan_cache` (the live, mutable, engine-
routed cache TKI-6 introduced) - it is never read by
`get_or_build_todays_plan()`, never invalidated by a cache rebuild, and
storing a snapshot never touches or overwrites the live plan cache row.
Rows are INSERT-ONLY; nothing here ever UPDATEs a previously-stored
snapshot - "immutable" is enforced by never issuing an UPDATE, not just
documented.

No secrets are stored - the payload is the same shape
build_calibrated_shadow_prescription() already returns (real personal
training data, already visible to this same user's own account), never
a credential or token.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from db import get_conn

TABLE_NAME = "training_decision_snapshots"
SNAPSHOT_SCHEMA_VERSION = 1

# The exact set of decision-critical fields a replay is checked against
# (section 29) - not every diagnostic field (e.g. raw envelope dumps),
# to keep the equivalence check meaningful rather than a byte-for-byte
# dump comparison that would break on any harmless additive field.
DECISION_CRITICAL_FIELDS = (
    "readiness", "local_readiness", "goal_mode", "selected_session_family",
    "feasible_range", "dose", "composition", "estimated_total_volume",
    "workload_sanity_v2", "quality_v2",
)


def _canonical_json(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def ensure_table():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS public.{TABLE_NAME} (
                    id BIGSERIAL PRIMARY KEY,
                    decision_id TEXT NOT NULL UNIQUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    local_date DATE NOT NULL,
                    as_of TIMESTAMPTZ NOT NULL,
                    engine_version TEXT NOT NULL,
                    snapshot_payload JSONB NOT NULL
                )
                """
            )
            cur.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_local_date
                ON public.{TABLE_NAME} (local_date DESC)
                """
            )


def build_snapshot(as_of: datetime, result: dict, local_date=None) -> dict:
    """Pure function - shapes a build_calibrated_shadow_prescription()
    result into the immutable snapshot payload. Does not persist."""
    return {
        "decision_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"tki53-decision:{as_of.isoformat()}:{result.get('selected_session_family')}")),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "local_date": (local_date or as_of.date()).isoformat(),
        "as_of": as_of.isoformat(),
        "engine_version": {
            "calibration_model_version": result.get("calibration_model_version"),
            "prescription_model_version": result.get("prescription_model_version"),
            "goal_policy_version": result.get("goal_policy_version"),
            "workload_v2_policy_version": (result.get("workload_sanity_v2") or {}).get("workload_v2_policy_version"),
            "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
        },
        "goal_mode": result.get("goal_mode"),
        "source_revisions": {
            # As-of-bound, not "latest live" - these describe WHICH
            # inputs the engine actually used, not what exists today.
            "readiness_metric_date": (result.get("readiness") or {}).get("metric_date"),
            "readiness_recovery_score": (result.get("readiness") or {}).get("recovery_score"),
        },
        "readiness_input": result.get("readiness"),
        "local_readiness_input": result.get("local_readiness"),
        "selected_session": result.get("selected_session_family"),
        "feasible_dose": result.get("feasible_range"),
        "capacity_reference": result.get("personal_capacity_reference"),
        "composition": result.get("composition"),
        "progression_provenance": [
            {"movement_id": e.get("movement_id"), "movement_name": e.get("movement_name"),
             "progression_state": e.get("progression_state"), "confidence": e.get("confidence"),
             "comparable_history": e.get("comparable_history")}
            for e in (result.get("exercises") or [])
        ],
        "workload_reference": result.get("workload_reference"),
        "workload_sanity_v2": result.get("workload_sanity_v2"),
        "final_prescription": {
            "dose": result.get("dose"),
            "exercises": result.get("exercises"),
            "estimated_total_volume": result.get("estimated_total_volume"),
        },
        "final_quality_verdict": result.get("quality_v2") or result.get("quality"),
    }


def save_snapshot(as_of: datetime, result: dict, local_date=None) -> dict:
    """SHADOW ONLY. Never called from any live request path - the caller
    is responsible for invoking this only from a diagnostic/shadow
    context. INSERT-only; a re-save of the same (as_of, family) decision
    is idempotent via decision_id's own deterministic derivation, never
    an UPDATE of prior content."""
    ensure_table()
    snapshot = build_snapshot(as_of, result, local_date)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO public.{TABLE_NAME}
                    (decision_id, local_date, as_of, engine_version, snapshot_payload)
                VALUES (%s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (decision_id) DO NOTHING
                RETURNING id, decision_id, created_at
                """,
                (
                    snapshot["decision_id"], snapshot["local_date"], as_of,
                    _canonical_json(snapshot["engine_version"]), _canonical_json(snapshot),
                ),
            )
            row = cur.fetchone()
    return {"snapshot": snapshot, "stored": row is not None, "row": dict(row) if row else None}


def load_snapshot(decision_id: str) -> dict | None:
    ensure_table()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT decision_id, created_at, local_date, as_of, engine_version, snapshot_payload "
                f"FROM public.{TABLE_NAME} WHERE decision_id = %s",
                (decision_id,),
            )
            row = cur.fetchone()
    if not row:
        return None
    payload = row["snapshot_payload"]
    return json.loads(payload) if isinstance(payload, str) else payload


def _normalize(value):
    """Canonicalize a value through the same JSON round-trip a stored
    snapshot always goes through (tuple -> list, non-JSON scalar -> str
    via default=str) so a loaded-from-DB snapshot compares equal to an
    in-memory result carrying the identical data in native Python
    types. This is a type-normalization step, not a leniency rule -
    it changes nothing about which values are considered different."""
    return json.loads(json.dumps(value, default=str))


def compare_to_snapshot(snapshot: dict, fresh_result: dict) -> dict:
    """Section 29: semantic equivalence over the decision-critical
    fields only - never a byte-for-byte dump comparison (which would
    break on harmless additive diagnostic fields)."""
    fresh_snapshot = build_snapshot(
        datetime.fromisoformat(snapshot["as_of"]), fresh_result, datetime.fromisoformat(snapshot["local_date"]).date(),
    )
    mismatches = {}
    for field in DECISION_CRITICAL_FIELDS:
        old = snapshot.get(field) if field in snapshot else _extract(snapshot, field)
        new = fresh_snapshot.get(field) if field in fresh_snapshot else _extract(fresh_snapshot, field)
        if _normalize(old) != _normalize(new):
            mismatches[field] = {"snapshot": old, "replay": new}
    return {"equivalent": not mismatches, "mismatched_fields": list(mismatches.keys()), "detail": mismatches}


def _extract(snapshot, field):
    """A few DECISION_CRITICAL_FIELDS live under renamed/nested keys in
    the snapshot shape - this maps them without duplicating the shape."""
    mapping = {
        "selected_session_family": "selected_session",
        "feasible_range": "feasible_dose",
        "composition": "composition",
        "dose": lambda s: (s.get("final_prescription") or {}).get("dose"),
        "estimated_total_volume": lambda s: (s.get("final_prescription") or {}).get("estimated_total_volume"),
        "quality_v2": "final_quality_verdict",
        "readiness": "readiness_input",
        "local_readiness": "local_readiness_input",
    }
    target = mapping.get(field, field)
    return target(snapshot) if callable(target) else snapshot.get(target)
