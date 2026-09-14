"""Intelligence Architecture M1.0: Decision Ledger - pure/in-memory
tests. No live database; opt-in Postgres coverage lives in
test_training_intelligence_recommendation_ledger_postgres.py, matching
the established convention.
"""
import unittest
from datetime import datetime, timezone

from training_intelligence.ledger.hashing import canonical_json, compute_input_hash, compute_decision_id
from training_intelligence.ledger.repository import _validate_candidates, LedgerValidationError
from training_intelligence.ledger.v2_1_integration import (
    build_ledger_write_kwargs, _build_state_snapshot, V2_1_FEATURE_SCHEMA_VERSION,
    RECOMMENDATION_TYPE, ENGINE_NAME,
)

AS_OF = datetime(2026, 9, 14, 8, 0, tzinfo=timezone.utc)


def _synthetic_v2_1_result():
    """A hand-built dict matching build_daily_program_adaptation()'s
    real ("status": "ok") shape closely enough to exercise the ledger's
    own shaping/validation logic without ever touching the database or
    the real engine."""
    return {
        "status": "ok",
        "decision_version": 1,
        "adaptation_taxonomy_version": 1,
        "decision_engine_version": 1,
        "feasibility_version": 1,
        "tonal_resolution_version": 1,
        "program_progress_version": 1,
        "time_budget_version": 1,
        "as_of": AS_OF.isoformat(),
        "active_program": {
            "program_id": "prog-uuid-1", "slug": "hypertrophy_upper_lower_4d_v1",
            "name": "Hypertrophy Upper/Lower (4-Day)", "goal_mode": "lean_bulk", "is_shadow_context": True,
        },
        "nominal_session": {"session_key": "upper_a", "session_name": "Upper A", "session_family": "Upper Mixed"},
        "candidate_sessions": [{"session": "upper_a", "position": 0, "program_need": 0.0}],
        "program_progress": {"program_progress_version": 1, "muscles": {}},
        "readiness": {"available": True, "recovery_score": 78.0, "readiness_band": "good"},
        "systemic_capacity": {"readiness_band": "good"},
        "time_context": {"available_duration_min": None, "fit_status": "UNKNOWN"},
        "decision": {
            "action": "KEEP",
            "selected_session": {"session_key": "upper_a", "session_name": "Upper A", "session_family": "Upper Mixed"},
            "confidence": "HIGH",
            "reason_codes": ["NOMINAL_SESSION_UPPER_A", "GOOD_SYSTEMIC_CAPACITY"],
            "dose": {"target_sets": 18, "delivered_sets": 18},
            "resolved_exercises": [{"movement_name": "Bench Press", "working_sets": 4}],
            "unresolved_required_slots": [],
            "estimated_total_volume": 16232.0,
            "quality_verdict": {"verdict": "QUESTIONABLE", "reasons": ["Weak paired progression evidence for: []"]},
        },
    }


class CanonicalJsonTests(unittest.TestCase):
    def test_key_order_independent(self):
        a = canonical_json({"b": 1, "a": 2})
        b = canonical_json({"a": 2, "b": 1})
        self.assertEqual(a, b)

    def test_datetime_and_nested_structures_serialize(self):
        payload = {"as_of": AS_OF, "nested": {"x": [1, 2, {"y": AS_OF}]}}
        out = canonical_json(payload)
        self.assertIsInstance(out, str)
        self.assertIn("2026-09-14", out)


class InputHashTests(unittest.TestCase):
    def test_identical_snapshots_hash_identically(self):
        s1 = {"a": 1, "b": {"c": 2}}
        s2 = {"b": {"c": 2}, "a": 1}
        self.assertEqual(compute_input_hash(s1), compute_input_hash(s2))

    def test_different_snapshots_hash_differently(self):
        self.assertNotEqual(compute_input_hash({"a": 1}), compute_input_hash({"a": 2}))

    def test_hash_is_stable_sha256_hex(self):
        h = compute_input_hash({"a": 1})
        self.assertEqual(len(h), 64)
        int(h, 16)  # raises ValueError if not valid hex


class DecisionIdTests(unittest.TestCase):
    def test_deterministic_for_same_inputs(self):
        a = compute_decision_id(recommendation_type="t", user_id="primary", as_of=AS_OF, engine_name="e")
        b = compute_decision_id(recommendation_type="t", user_id="primary", as_of=AS_OF, engine_name="e")
        self.assertEqual(a, b)

    def test_differs_when_as_of_differs(self):
        other = AS_OF.replace(hour=9)
        a = compute_decision_id(recommendation_type="t", user_id="primary", as_of=AS_OF, engine_name="e")
        b = compute_decision_id(recommendation_type="t", user_id="primary", as_of=other, engine_name="e")
        self.assertNotEqual(a, b)


class StateSnapshotTests(unittest.TestCase):
    def test_snapshot_contains_engine_version_detail(self):
        snap = _build_state_snapshot(_synthetic_v2_1_result(), AS_OF)
        self.assertEqual(snap["engine_version_detail"]["decision_version"], 1)
        self.assertEqual(snap["as_of"], AS_OF.isoformat())
        self.assertEqual(snap["feature_schema_version"], V2_1_FEATURE_SCHEMA_VERSION)

    def test_snapshot_contains_program_and_readiness_context(self):
        snap = _build_state_snapshot(_synthetic_v2_1_result(), AS_OF)
        self.assertEqual(snap["active_program"]["slug"], "hypertrophy_upper_lower_4d_v1")
        self.assertEqual(snap["nominal_session"]["session_key"], "upper_a")
        self.assertIn("readiness_band", snap["readiness"] or {})

    def test_no_identity_or_secret_fields_in_snapshot(self):
        # Program/session `name` fields (e.g. "Hypertrophy Upper/Lower",
        # "Upper A") are legitimate training metadata, not user identity
        # - only USER-identity/secret key markers are forbidden here.
        snap = _build_state_snapshot(_synthetic_v2_1_result(), AS_OF)
        blob = canonical_json(snap).lower()
        for forbidden in (
            "email", "first_name", "last_name", "\"dob\"", "date_of_birth",
            "password", "\"token\"", "access_token", "refresh_token", "secret", "ssn",
        ):
            self.assertNotIn(forbidden, blob, f"forbidden field marker {forbidden!r} found in state_snapshot")


class LedgerWriteKwargsTests(unittest.TestCase):
    def test_event_kwargs_carry_versions_and_cutoff(self):
        event_kwargs, candidates = build_ledger_write_kwargs(_synthetic_v2_1_result(), AS_OF, "primary")
        self.assertEqual(event_kwargs["recommendation_type"], RECOMMENDATION_TYPE)
        self.assertEqual(event_kwargs["engine_name"], ENGINE_NAME)
        self.assertEqual(event_kwargs["engine_version"], "1")
        self.assertEqual(event_kwargs["feature_schema_version"], V2_1_FEATURE_SCHEMA_VERSION)
        self.assertEqual(event_kwargs["source_data_cutoff"], AS_OF)
        self.assertEqual(event_kwargs["as_of"], AS_OF)

    def test_exactly_one_candidate_selected_true(self):
        _, candidates = build_ledger_write_kwargs(_synthetic_v2_1_result(), AS_OF, "primary")
        self.assertEqual(len(candidates), 1)
        self.assertTrue(candidates[0]["selected"])
        self.assertEqual(candidates[0]["candidate_key"], "v2_1-selected")

    def test_candidate_payload_is_the_real_decision_object(self):
        result = _synthetic_v2_1_result()
        _, candidates = build_ledger_write_kwargs(result, AS_OF, "primary")
        self.assertEqual(candidates[0]["candidate_payload"], result["decision"])

    def test_no_score_or_feature_vector_invented_for_v2_1(self):
        _, candidates = build_ledger_write_kwargs(_synthetic_v2_1_result(), AS_OF, "primary")
        self.assertIsNone(candidates[0]["total_score"])
        self.assertIsNone(candidates[0]["score_components"])
        self.assertIsNone(candidates[0]["feature_snapshot"])

    def test_validator_result_is_quality_verdict(self):
        result = _synthetic_v2_1_result()
        event_kwargs, _ = build_ledger_write_kwargs(result, AS_OF, "primary")
        self.assertEqual(event_kwargs["validator_result"], result["decision"]["quality_verdict"])

    def test_latency_ms_recorded_when_supplied(self):
        event_kwargs, _ = build_ledger_write_kwargs(_synthetic_v2_1_result(), AS_OF, "primary", latency_ms=12.7)
        self.assertEqual(event_kwargs["latency_ms"], 13)


class CandidateValidationTests(unittest.TestCase):
    def _candidate(self, **overrides):
        base = {
            "candidate_key": "c1", "candidate_payload": {"x": 1},
            "engine_version": "1", "feature_schema_version": 1, "selected": True,
        }
        base.update(overrides)
        return base

    def test_valid_single_selected_candidate_passes(self):
        _validate_candidates([self._candidate()])  # must not raise

    def test_empty_candidate_list_rejected(self):
        with self.assertRaises(LedgerValidationError):
            _validate_candidates([])

    def test_malformed_candidate_missing_payload_rejected(self):
        bad = self._candidate()
        del bad["candidate_payload"]
        with self.assertRaises(LedgerValidationError):
            _validate_candidates([bad])

    def test_malformed_candidate_missing_key_rejected(self):
        bad = self._candidate()
        del bad["candidate_key"]
        with self.assertRaises(LedgerValidationError):
            _validate_candidates([bad])

    def test_duplicate_candidate_key_rejected(self):
        with self.assertRaises(LedgerValidationError):
            _validate_candidates([self._candidate(candidate_key="c1"), self._candidate(candidate_key="c1", selected=False)])

    def test_multiple_selected_candidates_rejected(self):
        with self.assertRaises(LedgerValidationError):
            _validate_candidates([
                self._candidate(candidate_key="c1", selected=True),
                self._candidate(candidate_key="c2", selected=True),
            ])

    def test_zero_selected_candidates_allowed(self):
        # A future V3 write of an all-ineligible/no-winner candidate set
        # is legitimate - only >1 selected is ever rejected.
        _validate_candidates([self._candidate(candidate_key="c1", selected=False)])
