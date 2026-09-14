"""M1.0: Decision Ledger domain/service layer - the only place this
codebase issues SQL against `recommendation_events` /
`recommendation_candidates` / `recommendation_outcomes`. Mirrors
`training_intelligence.programs.repository`'s own plain-psycopg,
row-to-dict convention (no ORM).

Recommendation orchestration (e.g. `programs.adaptation.shadow`) never
imports this module directly - see `v2_1_integration.py` for the one,
narrow, additive call site.
"""
from __future__ import annotations

from psycopg.types.json import Jsonb

from db import get_conn
from training_intelligence.ledger.hashing import canonical_json, compute_input_hash, compute_decision_id
from training_intelligence.ledger.schema import EVENTS_TABLE, CANDIDATES_TABLE, OUTCOMES_TABLE

REQUIRED_CANDIDATE_KEYS = ("candidate_key", "candidate_payload", "engine_version", "feature_schema_version")


class LedgerWriteError(Exception):
    """Raised on any failure to durably record a decision - callers must
    never treat a recommendation as "recorded" without this succeeding.
    Wraps the underlying DB error; never silently swallowed."""


class LedgerValidationError(ValueError):
    """A candidate/event payload failed the pre-write shape checks in
    this module, before any SQL was issued."""


def _validate_candidates(candidates: list[dict]):
    if not candidates:
        raise LedgerValidationError("at least one candidate is required")
    keys_seen = set()
    selected_count = 0
    for c in candidates:
        missing = [k for k in REQUIRED_CANDIDATE_KEYS if not c.get(k) and c.get(k) != 0]
        # candidate_payload may legitimately be an empty-ish dict in a
        # degenerate case, but must be PRESENT; re-check explicitly.
        if "candidate_payload" not in c or c["candidate_payload"] is None:
            missing = list(set(missing) | {"candidate_payload"})
        if missing:
            raise LedgerValidationError(f"malformed candidate (missing {missing}): {c.get('candidate_key')!r}")
        key = c["candidate_key"]
        if key in keys_seen:
            raise LedgerValidationError(f"duplicate candidate_key within one decision: {key!r}")
        keys_seen.add(key)
        if c.get("selected"):
            selected_count += 1
    if selected_count > 1:
        raise LedgerValidationError(f"at most one candidate may be selected, found {selected_count}")


def create_recommendation_event(
    *, user_id: str, recommendation_type: str, as_of, engine_name: str, engine_version: str,
    feature_schema_version: int, state_snapshot: dict, source_data_cutoff, prior_version=None,
    selected_recommendation=None, reason_codes=None, validator_result=None, latency_ms=None,
    decision_id: str | None = None, conn=None,
) -> dict:
    """Inserts one immutable `recommendation_events` row. Idempotent on
    `decision_id` (ON CONFLICT DO NOTHING, mirroring
    training_decision_snapshots' convention) - re-recording the exact
    same decision is a safe no-op, never a duplicate or an error."""
    decision_id = decision_id or compute_decision_id(
        recommendation_type=recommendation_type, user_id=user_id, as_of=as_of, engine_name=engine_name,
    )
    input_hash = compute_input_hash(state_snapshot)

    def write(connection):
        with connection.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO public.{EVENTS_TABLE}
                    (decision_id, user_id, recommendation_type, as_of, engine_name, engine_version,
                     feature_schema_version, prior_version, state_snapshot, selected_recommendation,
                     reason_codes, validator_result, source_data_cutoff, input_hash, latency_ms)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (decision_id) DO NOTHING
                RETURNING *
                """,
                (
                    decision_id, user_id, recommendation_type, as_of, engine_name, engine_version,
                    feature_schema_version, prior_version, Jsonb(state_snapshot),
                    Jsonb(selected_recommendation) if selected_recommendation is not None else None,
                    Jsonb(reason_codes) if reason_codes is not None else None,
                    Jsonb(validator_result) if validator_result is not None else None,
                    source_data_cutoff, input_hash, latency_ms,
                ),
            )
            row = cur.fetchone()
        if row is None:
            # Already existed (idempotent replay) - fetch the existing row
            # rather than claiming a fresh insert.
            with connection.cursor() as cur:
                cur.execute(f"SELECT * FROM public.{EVENTS_TABLE} WHERE decision_id = %s", (decision_id,))
                row = cur.fetchone()
        return dict(row, stored=row is not None)

    try:
        if conn is not None:
            return write(conn)
        with get_conn() as connection:
            return write(connection)
    except LedgerValidationError:
        raise
    except Exception as exc:  # pragma: no cover - exercised via Postgres tests
        raise LedgerWriteError(f"failed to write recommendation_event: {exc}") from exc


def add_recommendation_candidates(decision_id: str, candidates: list[dict], *, conn=None) -> list[dict]:
    """Inserts one row per candidate. V2.1 callers pass exactly one
    candidate (selected=True) - see `_validate_candidates`'s "at most
    one selected" check and v2_1_integration.py's own "never a
    fabricated alternative" contract. Raises LedgerValidationError
    before issuing any SQL if the batch is malformed."""
    _validate_candidates(candidates)

    def write(connection):
        rows = []
        with connection.cursor() as cur:
            for c in candidates:
                cur.execute(
                    f"""
                    INSERT INTO public.{CANDIDATES_TABLE}
                        (decision_id, candidate_key, rank, selected, eligible, candidate_payload,
                         feature_snapshot, score_components, total_score, rejection_reasons,
                         engine_version, feature_schema_version)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (decision_id, candidate_key) DO NOTHING
                    RETURNING *
                    """,
                    (
                        decision_id, c["candidate_key"], c.get("rank"), bool(c.get("selected", False)),
                        bool(c.get("eligible", True)), Jsonb(c["candidate_payload"]),
                        Jsonb(c["feature_snapshot"]) if c.get("feature_snapshot") is not None else None,
                        Jsonb(c["score_components"]) if c.get("score_components") is not None else None,
                        c.get("total_score"),
                        Jsonb(c["rejection_reasons"]) if c.get("rejection_reasons") is not None else None,
                        c["engine_version"], c["feature_schema_version"],
                    ),
                )
                row = cur.fetchone()
                if row is not None:
                    rows.append(dict(row))
            selected = [r for r in rows if r.get("selected")]
            if selected:
                cur.execute(
                    f"UPDATE public.{EVENTS_TABLE} SET selected_candidate_id = %s WHERE decision_id = %s",
                    (selected[0]["id"], decision_id),
                )
        return rows

    try:
        if conn is not None:
            return write(conn)
        with get_conn() as connection:
            return write(connection)
    except LedgerValidationError:
        raise
    except Exception as exc:  # pragma: no cover - exercised via Postgres tests (e.g. FK violation)
        raise LedgerWriteError(f"failed to write recommendation_candidates: {exc}") from exc


def create_recommendation_event_with_candidates(event_kwargs: dict, candidates: list[dict]) -> dict:
    """The one-transaction write Phase 6/7 require: event + candidate(s)
    committed together, or neither. `add_recommendation_candidates`'s
    own validation runs BEFORE any SQL for either table is issued."""
    _validate_candidates(candidates)
    with get_conn() as conn:
        event = create_recommendation_event(**event_kwargs, conn=conn)
        stored_candidates = add_recommendation_candidates(event["decision_id"], candidates, conn=conn)
    return {"event": event, "candidates": stored_candidates}


def record_recommendation_outcome(
    decision_id: str, *, user_id: str = "primary", user_action=None, chosen_candidate_id=None,
    actual_session=None, completion_fraction=None, session_rating=None, session_rpe=None,
    user_feedback=None, downstream_state=None,
) -> dict:
    """Upserts the single outcome row for a decision (unique on
    decision_id) - appended once, updated in place as more is learned.
    Never touches `recommendation_events`."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO public.{OUTCOMES_TABLE}
                    (decision_id, user_id, user_action, chosen_candidate_id, actual_session,
                     completion_fraction, session_rating, session_rpe, user_feedback, downstream_state)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (decision_id) DO UPDATE SET
                    user_action = EXCLUDED.user_action,
                    chosen_candidate_id = EXCLUDED.chosen_candidate_id,
                    actual_session = COALESCE(EXCLUDED.actual_session, public.{OUTCOMES_TABLE}.actual_session),
                    completion_fraction = COALESCE(EXCLUDED.completion_fraction, public.{OUTCOMES_TABLE}.completion_fraction),
                    session_rating = COALESCE(EXCLUDED.session_rating, public.{OUTCOMES_TABLE}.session_rating),
                    session_rpe = COALESCE(EXCLUDED.session_rpe, public.{OUTCOMES_TABLE}.session_rpe),
                    user_feedback = COALESCE(EXCLUDED.user_feedback, public.{OUTCOMES_TABLE}.user_feedback),
                    downstream_state = COALESCE(EXCLUDED.downstream_state, public.{OUTCOMES_TABLE}.downstream_state),
                    updated_at = NOW()
                RETURNING *
                """,
                (
                    decision_id, user_id, user_action, chosen_candidate_id,
                    Jsonb(actual_session) if actual_session is not None else None,
                    completion_fraction, session_rating, session_rpe,
                    Jsonb(user_feedback) if user_feedback is not None else None,
                    Jsonb(downstream_state) if downstream_state is not None else None,
                ),
            )
            row = cur.fetchone()
    return dict(row)


def get_recommendation_event(decision_id: str) -> dict | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT * FROM public.{EVENTS_TABLE} WHERE decision_id = %s", (decision_id,))
            row = cur.fetchone()
    return dict(row) if row else None


def get_recommendation_candidates(decision_id: str) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT * FROM public.{CANDIDATES_TABLE} WHERE decision_id = %s ORDER BY rank NULLS LAST, created_at",
                (decision_id,),
            )
            return [dict(r) for r in cur.fetchall()]


def get_recommendation_outcome(decision_id: str) -> dict | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT * FROM public.{OUTCOMES_TABLE} WHERE decision_id = %s", (decision_id,))
            row = cur.fetchone()
    return dict(row) if row else None


def get_recommendation_for_replay(decision_id: str) -> dict | None:
    """Phase 8's replay contract: everything needed to answer "what did
    the engine know, and what did it decide" for one stored decision,
    plus a live re-hash of the stored state_snapshot to prove the row
    has not been tampered with/corrupted since it was written."""
    event = get_recommendation_event(decision_id)
    if event is None:
        return None
    candidates = get_recommendation_candidates(decision_id)
    outcome = get_recommendation_outcome(decision_id)
    recomputed_hash = compute_input_hash(event["state_snapshot"])
    return {
        "event": event,
        "candidates": candidates,
        "outcome": outcome,
        "input_hash_verified": recomputed_hash == event["input_hash"],
        "recomputed_input_hash": recomputed_hash,
    }
