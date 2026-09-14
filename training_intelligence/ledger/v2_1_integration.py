"""M1.0 Phase 7: the ONE narrow, additive point where V2.1's existing
Program Intelligence recommendation is observed and recorded in the
Decision Ledger.

This module NEVER imports anything from `programs.adaptation.shadow`
except its own public, unmodified `build_daily_program_adaptation()`
entry point, and never inspects/alters its return value before it is
fully computed - the ledger write happens strictly after, and its
result is layered on top, never merged into,
`build_daily_program_adaptation()`'s own return shape. Calling this
function instead of `build_daily_program_adaptation()` directly is
opt-in; nothing in V2.1's own call graph (admin endpoint, tests,
replay/regression scripts) is changed to route through it.

V2.1 emits exactly one recommendation - so exactly one candidate is
ever written here, `selected=True`, never a fabricated alternative.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from training_intelligence.programs.adaptation.shadow import build_daily_program_adaptation
from training_intelligence.ledger.repository import (
    create_recommendation_event_with_candidates, LedgerWriteError, LedgerValidationError,
)

RECOMMENDATION_TYPE = "program_adaptation_v2"
ENGINE_NAME = "training_intelligence.programs.adaptation.shadow.build_daily_program_adaptation"

# Versions the SHAPE of `state_snapshot`/`candidate_payload` this
# integration writes for program_adaptation_v2 decisions - independent
# of the engine's own internal sub-version constants (which are
# preserved verbatim inside state_snapshot["engine_version_detail"]) and
# of ledger.schema.LEDGER_SCHEMA_VERSION (the ledger TABLES' own shape).
V2_1_FEATURE_SCHEMA_VERSION = 1

# The exact set of top-level result fields curated into state_snapshot -
# named explicitly (not "everything") so a future additive field on the
# result never silently changes what gets persisted, and so this stays
# auditable against the "no unrelated personal data" requirement.
_STATE_SNAPSHOT_RESULT_FIELDS = (
    "active_program", "nominal_session", "candidate_sessions",
    "program_progress", "readiness", "systemic_capacity", "time_context",
)
_ENGINE_VERSION_FIELDS = (
    "decision_version", "adaptation_taxonomy_version", "decision_engine_version",
    "feasibility_version", "tonal_resolution_version", "program_progress_version",
    "time_budget_version",
)


def _build_state_snapshot(result: dict, as_of: datetime) -> dict:
    """Curated, replay-sufficient state - the same fields
    `TRAINING_INTELLIGENCE_PROGRAM_LAYER_V2_REPORT.md` already
    documents as the engine's real inputs (schedule/progress/
    readiness/systemic-capacity/time context). Contains only this
    user's own already-computed training-intelligence state (opaque
    user_id elsewhere, never name/email/DOB, never a token) - no field
    here is copied from `readiness`/`active_program` etc. beyond what
    `build_daily_program_adaptation()` itself already returns."""
    snapshot = {k: result.get(k) for k in _STATE_SNAPSHOT_RESULT_FIELDS}
    snapshot["engine_version_detail"] = {k: result.get(k) for k in _ENGINE_VERSION_FIELDS}
    snapshot["as_of"] = as_of.isoformat()
    snapshot["feature_schema_version"] = V2_1_FEATURE_SCHEMA_VERSION
    return snapshot


def build_ledger_write_kwargs(result: dict, as_of: datetime, user_id: str, *, latency_ms=None):
    """Pure: shapes one `build_daily_program_adaptation()` result
    (`status == "ok"`) into the exact `(event_kwargs, [candidate])`
    `create_recommendation_event_with_candidates()` expects - no DB
    access, so this is directly unit-testable against a hand-built
    synthetic result matching the engine's real shape. Exactly one
    candidate, `selected=True` - V2.1 never emits a fabricated
    alternative."""
    decision = result["decision"]
    engine_version = str(result.get("decision_version"))
    state_snapshot = _build_state_snapshot(result, as_of)

    candidate = {
        "candidate_key": "v2_1-selected",
        "rank": 1,
        "selected": True,
        "eligible": True,
        "candidate_payload": decision,
        # No feature vector / score exists yet for V2.1's single-
        # recommendation engine - left null rather than invented; these
        # are the exact fields a future multi-candidate engine (V3)
        # will start populating without a schema change.
        "feature_snapshot": None,
        "score_components": None,
        "total_score": None,
        "rejection_reasons": None,
        "engine_version": engine_version,
        "feature_schema_version": V2_1_FEATURE_SCHEMA_VERSION,
    }
    event_kwargs = dict(
        user_id=user_id, recommendation_type=RECOMMENDATION_TYPE, as_of=as_of,
        engine_name=ENGINE_NAME, engine_version=engine_version,
        feature_schema_version=V2_1_FEATURE_SCHEMA_VERSION, state_snapshot=state_snapshot,
        source_data_cutoff=as_of, selected_recommendation=decision,
        reason_codes=decision.get("reason_codes"), validator_result=decision.get("quality_verdict"),
        latency_ms=int(round(latency_ms)) if latency_ms is not None else None,
    )
    return event_kwargs, [candidate]


def record_program_adaptation_recommendation(
    user_id: str, as_of: datetime | None = None, *, persist_ledger: bool = True, **engine_kwargs,
) -> dict:
    """Calls the existing, unmodified `build_daily_program_adaptation()`
    and - for a real ("status": "ok") recommendation - records it in
    the Decision Ledger as a single selected candidate. Returns
    `{"result": <the exact, unmodified V2.1 result>, "ledger": {...}}`.

    `persist_ledger=False` reproduces plain `build_daily_program_
    adaptation()` behavior (useful for tests/replay that must not
    write). A ledger failure is never swallowed: `ledger["stored"]` is
    False with an `"error"` message rather than pretending success -
    callers decide whether that should fail their own request."""
    as_of = as_of or datetime.now(timezone.utc)

    t0 = time.perf_counter()
    result = build_daily_program_adaptation(user_id, as_of, **engine_kwargs)
    engine_elapsed_ms = (time.perf_counter() - t0) * 1000

    if result.get("status") != "ok":
        return {
            "result": result,
            "ledger": {"stored": False, "reason": f"status={result.get('status')!r} - nothing to record"},
        }

    event_kwargs, candidates = build_ledger_write_kwargs(result, as_of, user_id, latency_ms=engine_elapsed_ms)

    if not persist_ledger:
        return {"result": result, "ledger": {"stored": False, "reason": "persist_ledger=False"}}

    t1 = time.perf_counter()
    try:
        written = create_recommendation_event_with_candidates(event_kwargs, candidates)
    except (LedgerWriteError, LedgerValidationError) as exc:
        # Explicit, surfaced failure - never claim the recommendation was
        # recorded when it was not. No credential/health-history detail
        # beyond the exception's own message is logged here.
        return {"result": result, "ledger": {"stored": False, "error": str(exc)}}
    ledger_elapsed_ms = (time.perf_counter() - t1) * 1000

    return {
        "result": result,
        "ledger": {
            "stored": True,
            "decision_id": written["event"]["decision_id"],
            "candidate_count": len(written["candidates"]),
            "engine_ms": round(engine_elapsed_ms, 2),
            "ledger_transaction_ms": round(ledger_elapsed_ms, 2),
        },
    }
