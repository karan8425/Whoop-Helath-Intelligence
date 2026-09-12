"""TKI-2: muscle stimulus ledger, session-family, and shadow-state tests.

All tests here are pure/in-memory (rows are hand-built, no live database),
matching the established convention in integrations/tonal/test_*.py and
test_training_replay.py. Real-Postgres coverage lives in
test_training_intelligence_postgres.py (opt-in).
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from training_intelligence.stimulus.ledger import (
    build_ledger_from_rows,
    build_muscle_stimulus_ledger,
    build_ledger_windows,
)
from training_intelligence.stimulus.mapping import classify_muscle_groups, movement_mapping_report
from training_intelligence.stimulus.policy import SECONDARY_SET_CREDIT, DIRECT_SET_CREDIT
from training_intelligence.stimulus.session_family import (
    classify_workout_family,
    aggregate_session_families,
    OTHER_FAMILY,
)
from training_intelligence.stimulus.shadow_state import build_shadow_training_state
from training_intelligence.stimulus.taxonomy import (
    CANONICAL_MUSCLES,
    CANONICAL_GROUPS_WITHOUT_EXISTING_SOURCE,
    to_canonical,
)

AS_OF = datetime(2026, 9, 12, 8, 0, tzinfo=timezone.utc)


def _row(days_ago, movement_id, muscle_groups, rep_count=10, volume=200.0,
         activity_id=None, included=True, exclusion_reason=None, movement_name=None):
    return {
        "activity_id": activity_id or f"activity-{days_ago}-{movement_id}",
        "begin_time": AS_OF - timedelta(days=days_ago),
        "set_index": 0,
        "movement_id": movement_id,
        "rep_count": rep_count,
        "volume": volume,
        "included": included,
        "exclusion_reason": exclusion_reason,
        "muscle_groups": muscle_groups,
        "movement_name": movement_name or movement_id,
    }


class MuscleMappingTests(unittest.TestCase):
    def test_recognized_labels_map_first_as_primary_rest_as_secondary(self):
        mapping = classify_muscle_groups(["Back", "Biceps"])
        self.assertEqual(mapping.primary, "back")
        self.assertEqual(mapping.secondary, ("biceps",))
        self.assertTrue(mapping.is_mapped)

    def test_unrecognized_label_is_explicit_unknown_not_guessed(self):
        mapping = classify_muscle_groups(["Neck"])
        self.assertFalse(mapping.is_mapped)
        self.assertEqual(mapping.unmapped_raw_labels, ("Neck",))

    def test_empty_muscle_groups_is_unknown(self):
        self.assertFalse(classify_muscle_groups([]).is_mapped)
        self.assertFalse(classify_muscle_groups(None).is_mapped)

    def test_abs_and_obliques_normalize_to_core(self):
        self.assertEqual(classify_muscle_groups(["Abs"]).primary, "core")
        self.assertEqual(classify_muscle_groups(["Obliques"]).primary, "core")

    def test_movement_mapping_report_lists_unmapped_explicitly(self):
        report = movement_mapping_report([
            {"movement_id": "m1", "name": "Lat Pulldown", "muscle_groups": ["Back"]},
            {"movement_id": "m2", "name": "Mystery Machine", "muscle_groups": ["Neck"]},
        ])
        self.assertEqual(report["total_movements"], 2)
        self.assertEqual(report["mapped_movements"], 1)
        self.assertEqual(report["unmapped_movements"], 1)
        self.assertEqual(report["unmapped"][0]["movement_id"], "m2")
        self.assertEqual(report["mapping_coverage_pct"], 50.0)


class TaxonomyTests(unittest.TestCase):
    def test_ten_canonical_groups_per_approved_spec(self):
        self.assertEqual(len(CANONICAL_MUSCLES), 10)
        self.assertIn("calves", CANONICAL_MUSCLES)

    def test_calves_has_no_existing_b3_source(self):
        # Documents the known taxonomy gap rather than hiding it.
        self.assertIn("calves", CANONICAL_GROUPS_WITHOUT_EXISTING_SOURCE)

    def test_existing_taxonomy_rolls_up_without_loss(self):
        for existing in ("Chest", "Back", "Shoulders", "Biceps", "Triceps",
                          "Core", "Glutes", "Hamstrings", "Quads"):
            self.assertIsNotNone(to_canonical(existing))

    def test_unknown_existing_label_returns_none(self):
        self.assertIsNone(to_canonical("Forearms"))


class StimulusSetAccountingTests(unittest.TestCase):
    def test_primary_working_set_gets_full_direct_credit(self):
        rows = [_row(1, "lat_pulldown", ["Back", "Biceps"], rep_count=10, volume=500.0)]
        ledger = build_ledger_from_rows(rows, AS_OF, 7)
        self.assertEqual(ledger["muscles"]["back"]["direct_sets"], DIRECT_SET_CREDIT)
        self.assertEqual(ledger["muscles"]["back"]["working_sets"], 1)

    def test_secondary_muscle_gets_configured_fractional_credit(self):
        rows = [_row(1, "lat_pulldown", ["Back", "Biceps"], rep_count=10, volume=500.0)]
        ledger = build_ledger_from_rows(rows, AS_OF, 7)
        self.assertEqual(ledger["muscles"]["biceps"]["secondary_set_equivalents"], SECONDARY_SET_CREDIT)
        self.assertEqual(ledger["muscles"]["biceps"]["direct_sets"], 0.0)

    def test_total_stimulus_sets_is_direct_plus_secondary(self):
        rows = [
            _row(1, "hammer_curl", ["Biceps"], rep_count=10, volume=100.0, activity_id="w1"),
            _row(1, "lat_pulldown", ["Back", "Biceps"], rep_count=10, volume=500.0, activity_id="w1"),
        ]
        ledger = build_ledger_from_rows(rows, AS_OF, 7)
        biceps = ledger["muscles"]["biceps"]
        self.assertAlmostEqual(
            biceps["total_stimulus_sets"],
            round(DIRECT_SET_CREDIT + SECONDARY_SET_CREDIT, 3),
        )

    def test_excluded_workout_contributes_nothing(self):
        rows = [_row(1, "m1", ["Back"], included=False, exclusion_reason="user marked invalid")]
        ledger = build_ledger_from_rows(rows, AS_OF, 7)
        self.assertEqual(ledger["muscles"]["back"]["total_stimulus_sets"], 0.0)
        self.assertEqual(ledger["data_quality"]["total_working_sets_considered"], 0)

    def test_abbreviated_freestyle_session_gets_supplemental_weight_not_full_or_zero(self):
        rows = [_row(1, "m1", ["Back"], included=False,
                      exclusion_reason="atypical abbreviated freestyle session", volume=100.0)]
        ledger = build_ledger_from_rows(rows, AS_OF, 7)
        back = ledger["muscles"]["back"]
        self.assertGreater(back["direct_sets"], 0.0)
        self.assertLess(back["direct_sets"], DIRECT_SET_CREDIT)

    def test_null_rep_count_excluded_as_not_a_working_set(self):
        rows = [_row(1, "m1", ["Back"], rep_count=None)]
        ledger = build_ledger_from_rows(rows, AS_OF, 7)
        self.assertEqual(ledger["muscles"]["back"]["total_stimulus_sets"], 0.0)

    def test_zero_rep_count_excluded_as_not_a_working_set(self):
        rows = [_row(1, "m1", ["Back"], rep_count=0)]
        ledger = build_ledger_from_rows(rows, AS_OF, 7)
        self.assertEqual(ledger["muscles"]["back"]["total_stimulus_sets"], 0.0)

    def test_unknown_exercise_excluded_and_counted_not_silently_dropped(self):
        rows = [_row(1, "mystery", ["Neck"], rep_count=10)]
        ledger = build_ledger_from_rows(rows, AS_OF, 7)
        for muscle in CANONICAL_MUSCLES:
            self.assertEqual(ledger["muscles"][muscle]["total_stimulus_sets"], 0.0)
        self.assertEqual(ledger["data_quality"]["unmapped_working_sets"], 1)


class RollingWindowTests(unittest.TestCase):
    def test_set_inside_7d_window_counted(self):
        rows = [_row(6, "m1", ["Back"])]
        ledger = build_ledger_from_rows(rows, AS_OF, 7)
        self.assertEqual(ledger["muscles"]["back"]["working_sets"], 1)

    def test_set_outside_7d_window_excluded(self):
        rows = [_row(8, "m1", ["Back"])]
        ledger = build_ledger_from_rows(rows, AS_OF, 7)
        self.assertEqual(ledger["muscles"]["back"]["working_sets"], 0)

    def test_set_inside_14d_but_outside_7d(self):
        rows = [_row(10, "m1", ["Back"])]
        seven = build_ledger_from_rows(rows, AS_OF, 7)
        fourteen = build_ledger_from_rows(rows, AS_OF, 14)
        self.assertEqual(seven["muscles"]["back"]["working_sets"], 0)
        self.assertEqual(fourteen["muscles"]["back"]["working_sets"], 1)

    def test_set_outside_14d_window_excluded(self):
        rows = [_row(15, "m1", ["Back"])]
        ledger = build_ledger_from_rows(rows, AS_OF, 14)
        self.assertEqual(ledger["muscles"]["back"]["working_sets"], 0)

    def test_set_inside_30d_but_outside_14d(self):
        rows = [_row(20, "m1", ["Back"])]
        fourteen = build_ledger_from_rows(rows, AS_OF, 14)
        thirty = build_ledger_from_rows(rows, AS_OF, 30)
        self.assertEqual(fourteen["muscles"]["back"]["working_sets"], 0)
        self.assertEqual(thirty["muscles"]["back"]["working_sets"], 1)

    def test_set_outside_30d_window_excluded(self):
        rows = [_row(31, "m1", ["Back"])]
        ledger = build_ledger_from_rows(rows, AS_OF, 30)
        self.assertEqual(ledger["muscles"]["back"]["working_sets"], 0)

    def test_build_ledger_windows_uses_one_row_set_for_all_windows(self):
        rows = [_row(1, "m1", ["Back"]), _row(20, "m2", ["Back"])]
        windows = build_ledger_windows(AS_OF, (7, 14, 30), rows=rows)
        self.assertEqual(windows[7]["muscles"]["back"]["working_sets"], 1)
        self.assertEqual(windows[30]["muscles"]["back"]["working_sets"], 2)


class RecencyAndSessionCountingTests(unittest.TestCase):
    def test_days_since_trained_from_last_primary_exposure(self):
        rows = [_row(3, "m1", ["Back"])]
        ledger = build_ledger_from_rows(rows, AS_OF, 7)
        self.assertEqual(ledger["muscles"]["back"]["days_since_trained"], 3.0)

    def test_days_since_trained_none_when_never_trained(self):
        ledger = build_ledger_from_rows([], AS_OF, 7)
        self.assertIsNone(ledger["muscles"]["back"]["days_since_trained"])

    def test_multiple_sets_same_workout_count_as_one_session(self):
        rows = [
            _row(1, "m1", ["Back"], activity_id="w1"),
            _row(1, "m2", ["Back"], activity_id="w1"),
            _row(1, "m3", ["Back"], activity_id="w1"),
        ]
        ledger = build_ledger_from_rows(rows, AS_OF, 7)
        self.assertEqual(ledger["muscles"]["back"]["sessions"], 1)
        self.assertEqual(ledger["muscles"]["back"]["working_sets"], 3)
        self.assertEqual(ledger["muscles"]["back"]["exercise_count"], 3)

    def test_same_workout_twice_across_windows_still_one_session_each(self):
        rows = [_row(1, "m1", ["Back"], activity_id="w1"), _row(8, "m2", ["Back"], activity_id="w2")]
        ledger = build_ledger_from_rows(rows, AS_OF, 14)
        self.assertEqual(ledger["muscles"]["back"]["sessions"], 2)


class DeterminismTests(unittest.TestCase):
    def test_same_input_produces_same_output(self):
        rows = [
            _row(1, "m1", ["Back", "Biceps"], activity_id="w1"),
            _row(3, "m2", ["Chest"], activity_id="w2"),
        ]
        first = build_ledger_from_rows(rows, AS_OF, 7)
        second = build_ledger_from_rows(list(rows), AS_OF, 7)
        self.assertEqual(first, second)

    def test_row_order_does_not_affect_output(self):
        rows = [
            _row(1, "m1", ["Back", "Biceps"], activity_id="w1"),
            _row(3, "m2", ["Chest"], activity_id="w2"),
        ]
        forward = build_ledger_from_rows(rows, AS_OF, 7)
        reversed_order = build_ledger_from_rows(list(reversed(rows)), AS_OF, 7)
        self.assertEqual(forward, reversed_order)


class TemporalLeakageTests(unittest.TestCase):
    """Mirrors the discipline established for REPLAY-TEMPORAL-LEAKAGE-P0:
    no query or accumulation may let a workout/set after `as_of` influence
    the result, at any window."""

    def test_future_workout_cannot_alter_historical_ledger(self):
        rows = [
            _row(1, "past", ["Back"], activity_id="past-workout"),
            {**_row(0, "future", ["Back"], activity_id="future-workout"),
             "begin_time": AS_OF + timedelta(days=1)},
        ]
        ledger = build_ledger_from_rows(rows, AS_OF, 7)
        self.assertEqual(ledger["muscles"]["back"]["working_sets"], 1)
        self.assertEqual(ledger["data_quality"]["total_working_sets_considered"], 1)

    def test_workout_beginning_exactly_at_as_of_is_included(self):
        rows = [{**_row(0, "m1", ["Back"]), "begin_time": AS_OF}]
        ledger = build_ledger_from_rows(rows, AS_OF, 7)
        self.assertEqual(ledger["muscles"]["back"]["working_sets"], 1)

    def test_workout_beginning_one_second_after_as_of_is_excluded(self):
        rows = [{**_row(0, "m1", ["Back"]), "begin_time": AS_OF + timedelta(seconds=1)}]
        ledger = build_ledger_from_rows(rows, AS_OF, 7)
        self.assertEqual(ledger["muscles"]["back"]["working_sets"], 0)

    def test_later_same_day_workout_cannot_alter_earlier_as_of_state(self):
        morning_cutoff = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
        rows = [
            {**_row(0, "morning", ["Back"], activity_id="morning-workout"),
             "begin_time": datetime(2026, 9, 12, 8, 0, tzinfo=timezone.utc)},
            {**_row(0, "evening", ["Back"], activity_id="evening-workout"),
             "begin_time": datetime(2026, 9, 12, 18, 0, tzinfo=timezone.utc)},
        ]
        ledger = build_ledger_from_rows(rows, morning_cutoff, 7)
        self.assertEqual(ledger["muscles"]["back"]["sessions"], 1)
        self.assertEqual(
            ledger["muscles"]["back"]["last_trained_at"],
            "2026-09-12T08:00:00+00:00",
        )

    def test_naive_as_of_rejected(self):
        with self.assertRaises(ValueError):
            build_muscle_stimulus_ledger(datetime(2026, 9, 12), rows=[])


class SessionFamilyTests(unittest.TestCase):
    def test_back_and_biceps_classified_as_upper_pull(self):
        family = classify_workout_family(frozenset({"back", "biceps"}))
        self.assertEqual(family, "Upper Pull")

    def test_lower_body_muscles_classified_as_lower_body(self):
        family = classify_workout_family(frozenset({"glutes", "hamstrings"}))
        self.assertEqual(family, "Lower Body")

    def test_insufficient_overlap_classified_as_other(self):
        family = classify_workout_family(frozenset({"calves"}))
        self.assertEqual(family, OTHER_FAMILY)

    def test_aggregate_session_families_groups_by_workout(self):
        rows = [
            _row(1, "m1", ["Back"], activity_id="w1"),
            _row(1, "m2", ["Biceps"], activity_id="w1"),
            _row(3, "m3", ["Quads"], activity_id="w2"),
            _row(3, "m4", ["Glutes"], activity_id="w2"),
        ]
        families = aggregate_session_families(rows, AS_OF, 7)
        self.assertIn("Upper Pull", families)
        self.assertIn("Lower Body", families)
        self.assertEqual(families["Upper Pull"]["sessions"], 1)
        self.assertEqual(families["Upper Pull"]["working_sets"], 2)

    def test_future_workout_excluded_from_session_families(self):
        rows = [{**_row(0, "m1", ["Back"]), "begin_time": AS_OF + timedelta(days=1)}]
        families = aggregate_session_families(rows, AS_OF, 7)
        self.assertEqual(families, {})


class ShadowStateTests(unittest.TestCase):
    def test_shadow_state_never_ranks_sessions(self):
        with patch(
            "training_intelligence.stimulus.shadow_state.get_active_goal",
            return_value={"phase": "lean_cut", "goal_type": "fat_loss"},
        ), patch(
            "training_intelligence.stimulus.shadow_state._latest_readiness",
            return_value={"recovery_score": 94.0, "readiness_band": "high"},
        ), patch(
            "training_intelligence.stimulus.shadow_state.calculate_muscle_readiness",
            return_value={"selection_confidence": "high", "latest_tonal_workout_at": None, "muscles": []},
        ):
            state = build_shadow_training_state(AS_OF, rows=[])
        self.assertIsNone(state["ranking"])
        self.assertEqual(state["mode"], "shadow")

    def test_shadow_state_lean_cut_goal_context(self):
        with patch(
            "training_intelligence.stimulus.shadow_state.get_active_goal",
            return_value={"phase": "lean_cut", "goal_type": "fat_loss"},
        ), patch(
            "training_intelligence.stimulus.shadow_state._latest_readiness",
            return_value={"recovery_score": 94.0, "readiness_band": "high"},
        ), patch(
            "training_intelligence.stimulus.shadow_state.calculate_muscle_readiness",
            return_value={"selection_confidence": "high", "latest_tonal_workout_at": None, "muscles": []},
        ):
            state = build_shadow_training_state(AS_OF, rows=[])
        self.assertEqual(state["goal"]["training_objective"], "preserve_or_gain_lean_mass")

    def test_shadow_state_unstated_phase_leaves_objective_unset(self):
        with patch(
            "training_intelligence.stimulus.shadow_state.get_active_goal",
            return_value={"phase": "maintenance", "goal_type": "recomposition"},
        ), patch(
            "training_intelligence.stimulus.shadow_state._latest_readiness",
            return_value={"recovery_score": 50.0, "readiness_band": "good"},
        ), patch(
            "training_intelligence.stimulus.shadow_state.calculate_muscle_readiness",
            return_value={"selection_confidence": "high", "latest_tonal_workout_at": None, "muscles": []},
        ):
            state = build_shadow_training_state(AS_OF, rows=[])
        self.assertIsNone(state["goal"]["training_objective"])

    def test_shadow_state_readiness_join_rolls_up_states(self):
        with patch(
            "training_intelligence.stimulus.shadow_state.get_active_goal",
            return_value={"phase": "lean_cut"},
        ), patch(
            "training_intelligence.stimulus.shadow_state._latest_readiness",
            return_value={"recovery_score": 94.0, "readiness_band": "high"},
        ), patch(
            "training_intelligence.stimulus.shadow_state.calculate_muscle_readiness",
            return_value={
                "selection_confidence": "high",
                "latest_tonal_workout_at": None,
                "muscles": [
                    {"muscle": "Back", "readiness_state": "FATIGUED", "readiness_score": 40.0,
                     "hours_since_primary_exposure": 20.0},
                    {"muscle": "Quads", "readiness_state": "FRESH", "readiness_score": 95.0,
                     "hours_since_primary_exposure": 200.0},
                ],
            },
        ):
            state = build_shadow_training_state(AS_OF, rows=[])
        self.assertEqual(state["muscles"]["back"]["readiness"], "fatigued")
        self.assertEqual(state["muscles"]["quads"]["readiness"], "fresh")

    def test_shadow_state_does_not_modify_readiness_calculation(self):
        """Read-only join: the mock's return value is passed straight
        through; nothing here recomputes readiness."""
        call_count = {"n": 0}

        def fake_readiness(now=None):
            call_count["n"] += 1
            return {"selection_confidence": "high", "latest_tonal_workout_at": None, "muscles": []}

        with patch("training_intelligence.stimulus.shadow_state.get_active_goal", return_value={}), \
             patch("training_intelligence.stimulus.shadow_state._latest_readiness",
                   return_value={"recovery_score": 50.0, "readiness_band": "good"}), \
             patch("training_intelligence.stimulus.shadow_state.calculate_muscle_readiness",
                   side_effect=fake_readiness):
            build_shadow_training_state(AS_OF, rows=[])
        self.assertEqual(call_count["n"], 1)


if __name__ == "__main__":
    unittest.main()
