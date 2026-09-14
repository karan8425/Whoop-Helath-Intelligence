"""M1.0: canonical serialization + deterministic identifiers for the
Decision Ledger. Pure functions - no DB, no engine logic.
"""
from __future__ import annotations

import hashlib
import json
import uuid

# Namespaced independently from calibration.snapshot's own uuid5
# namespace (training-intelligence-program-adaptation-v2) and
# programs.adaptation.snapshot's (training-intelligence-program-
# adaptation-v2) so a ledger decision_id can never collide with either
# of those pre-existing, separately-scoped snapshot stores.
_LEDGER_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "training-intelligence-recommendation-ledger-v1")


def canonical_json(payload) -> str:
    """Deterministic, sorted-key, separator-tight JSON - the same
    payload always serializes identically regardless of dict insertion
    order. `default=str` covers datetimes/UUIDs/Decimals without
    inventing a custom encoder."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def compute_input_hash(state_snapshot: dict) -> str:
    """A stable SHA-256 of the canonicalized state_snapshot - two
    decisions computed from identical inputs hash identically; any
    difference in what the engine actually saw changes the hash."""
    return hashlib.sha256(canonical_json(state_snapshot).encode("utf-8")).hexdigest()


def compute_decision_id(*, recommendation_type: str, user_id: str, as_of, engine_name: str) -> str:
    """Deterministic natural key: the same (type, user, as_of, engine)
    tuple always yields the same decision_id, so re-recording an
    unchanged decision is an idempotent no-op (INSERT ... ON CONFLICT
    DO NOTHING) rather than a duplicate row - mirroring
    training_decision_snapshots' own established insert-only
    idempotency convention."""
    as_of_iso = as_of.isoformat() if hasattr(as_of, "isoformat") else str(as_of)
    key = f"{recommendation_type}:{user_id}:{as_of_iso}:{engine_name}"
    return str(uuid.uuid5(_LEDGER_NAMESPACE, key))
