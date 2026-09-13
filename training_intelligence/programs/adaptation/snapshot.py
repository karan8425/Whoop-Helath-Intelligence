"""Program Intelligence V2 Phase 14 - immutable adaptation-decision
snapshots.

Reuses the EXISTING shadow-only training_decision_snapshots table and
its exact conventions (training_intelligence.calibration.snapshot.py:
ensure_table()'s CREATE TABLE IF NOT EXISTS pattern, insert-only
semantics, deterministic decision_id, canonical-JSON payload) rather
than creating a second table - the table itself is a generic
{decision_id, created_at, local_date, as_of, engine_version,
snapshot_payload} store with nothing calibration-specific in its
SCHEMA (only training_intelligence.calibration.snapshot.py's own
build_snapshot() Python function is shaped for that milestone's
payload). This module writes its OWN differently-shaped payload into
the SAME table, under a differently-namespaced decision_id so the two
kinds of decision can never collide.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from db import get_conn
from training_intelligence.calibration.snapshot import TABLE_NAME, ensure_table, _canonical_json, _normalize

ADAPTATION_SNAPSHOT_SCHEMA_VERSION = 1

_ADAPTATION_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "training-intelligence-program-adaptation-v2")

# The decision-critical fields a replay is checked against (mirrors
# calibration/snapshot.py's own DECISION_CRITICAL_FIELDS pattern).
DECISION_CRITICAL_FIELDS = (
    "program_id", "nominal_session_key", "action", "selected_session_key",
    "exercises", "workload_sanity", "time_context",
)


def build_adaptation_snapshot(as_of, program_id, result):
    decision_id = str(uuid.uuid5(_ADAPTATION_NAMESPACE, f"{as_of.isoformat()}:{program_id}"))
    nominal = result.get("nominal_session") or {}
    decision = result.get("decision") or {}
    selected = decision.get("selected_session") or {}
    return {
        "decision_id": decision_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "local_date": as_of.date().isoformat(),
        "as_of": as_of.isoformat(),
        "engine_version": {
            "adaptation_snapshot_schema_version": ADAPTATION_SNAPSHOT_SCHEMA_VERSION,
            "decision_engine_version": result.get("decision_engine_version"),
            "feasibility_version": result.get("feasibility_version"),
            "time_budget_version": result.get("time_budget_version"),
            "tonal_resolution_version": result.get("tonal_resolution_version"),
            "program_progress_version": result.get("program_progress_version"),
        },
        "program_id": str(program_id),
        "nominal_session_key": nominal.get("session_key"),
        "action": decision.get("action"),
        "selected_session_key": selected.get("session_key") if selected else None,
        "candidate_sessions": result.get("candidate_sessions"),
        "program_progress": result.get("program_progress"),
        "readiness": result.get("readiness"),
        "systemic_capacity": result.get("systemic_capacity"),
        "time_context": result.get("time_context"),
        "exercises": (result.get("decision") or {}).get("resolved_exercises"),
        "workload_sanity": result.get("workload_sanity"),
        "explanation": result.get("explanation"),
    }


def save_adaptation_snapshot(as_of, program_id, result):
    """SHADOW ONLY. INSERT-only - a re-save of the identical (as_of,
    program_id) decision is idempotent via decision_id's own
    deterministic derivation, never an UPDATE."""
    ensure_table()
    snapshot = build_adaptation_snapshot(as_of, program_id, result)
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


def load_adaptation_snapshot(decision_id):
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


def compare_adaptation_snapshot(snapshot, fresh_result, as_of, program_id):
    fresh_snapshot = build_adaptation_snapshot(as_of, program_id, fresh_result)
    mismatches = {}
    for field in DECISION_CRITICAL_FIELDS:
        old, new = snapshot.get(field), fresh_snapshot.get(field)
        if _normalize(old) != _normalize(new):
            mismatches[field] = {"snapshot": old, "replay": new}
    return {"equivalent": not mismatches, "mismatched_fields": list(mismatches.keys()), "detail": mismatches}
