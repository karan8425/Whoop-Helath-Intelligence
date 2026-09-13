"""Program Intelligence V1: taxonomy + Tonal-mapping-engine tests.

Pure/in-memory (no live database), matching the established convention.
Schema/repository/seed/user-state/temporal tests live in
test_training_intelligence_programs_postgres.py (opt-in, real
Development database).
"""
import unittest

from training_intelligence.programs import taxonomy
from training_intelligence.programs.tonal_mapping import (
    score_candidate, rank_candidates, TONAL_MAPPING_ENGINE_VERSION,
)
from training_intelligence.dose.goal_policy import GOAL_MODES
from training_intelligence.stimulus.taxonomy import CANONICAL_MUSCLES
from integrations.tonal.training_priority import SESSION_TEMPLATES


def _mapping_row(movement_id, pattern, muscle, role, quality, confidence=0.8, accessory=None,
                  is_generic=False, custom_movement=False, name=None):
    return {
        "tonal_movement_id": movement_id, "movement_pattern": pattern, "primary_muscle": muscle,
        "exercise_role": role, "mapping_quality": quality, "confidence": confidence,
        "tonal_accessory": accessory, "is_generic": is_generic, "custom_movement": custom_movement,
        "movement_name": name or movement_id,
    }


class TaxonomyTests(unittest.TestCase):
    def test_goals_mirror_goal_policy_exactly(self):
        self.assertEqual(set(taxonomy.GOALS), set(GOAL_MODES))

    def test_hypertrophy_gain_is_not_a_separate_alias(self):
        # lean_bulk IS the hypertrophy goal (goal_policy.py's own
        # training_objective="maximize_hypertrophy_stimulus") - no
        # competing "hypertrophy_gain" value should ever be introduced.
        self.assertNotIn("hypertrophy_gain", taxonomy.GOALS)
        self.assertIn("lean_bulk", taxonomy.GOALS)

    def test_session_families_mirror_session_templates_exactly(self):
        self.assertEqual(set(taxonomy.SESSION_FAMILIES), set(SESSION_TEMPLATES.keys()))

    def test_primary_muscles_mirror_canonical_muscles_exactly(self):
        self.assertEqual(set(taxonomy.PRIMARY_MUSCLES), set(CANONICAL_MUSCLES))

    def test_compound_patterns_reuse_history_taxonomy(self):
        from training_intelligence.calibration.history import COMPOUNDS
        self.assertEqual(set(taxonomy.COMPOUND_MOVEMENT_PATTERNS), set(COMPOUNDS))

    def test_movement_pattern_taxonomy_has_no_internal_fork(self):
        overlap = set(taxonomy.COMPOUND_MOVEMENT_PATTERNS) & set(taxonomy.ISOLATION_MOVEMENT_PATTERNS)
        self.assertEqual(overlap, set())

    def test_mapping_quality_rank_is_strictly_ordered(self):
        self.assertGreater(taxonomy.MAPPING_QUALITY_RANK["DIRECT"], taxonomy.MAPPING_QUALITY_RANK["CLOSE"])
        self.assertGreater(taxonomy.MAPPING_QUALITY_RANK["CLOSE"], taxonomy.MAPPING_QUALITY_RANK["FUNCTIONAL"])
        self.assertGreater(taxonomy.MAPPING_QUALITY_RANK["FUNCTIONAL"], taxonomy.MAPPING_QUALITY_RANK["UNSUITABLE"])


class TonalMappingEngineTests(unittest.TestCase):
    def test_direct_outranks_close(self):
        rows = [
            _mapping_row("m-close", "horizontal_press", "chest", "primary_compound", "CLOSE", confidence=0.95),
            _mapping_row("m-direct", "horizontal_press", "chest", "primary_compound", "DIRECT", confidence=0.5),
        ]
        ranked = rank_candidates(rows)
        self.assertEqual(ranked[0]["movement_id"], "m-direct")

    def test_close_outranks_functional(self):
        rows = [
            _mapping_row("m-func", "hinge", "hamstrings", "primary_compound", "FUNCTIONAL", confidence=0.99),
            _mapping_row("m-close", "hinge", "hamstrings", "primary_compound", "CLOSE", confidence=0.5),
        ]
        ranked = rank_candidates(rows)
        self.assertEqual(ranked[0]["movement_id"], "m-close")

    def test_unsuitable_ranks_below_functional_even_if_not_pre_filtered(self):
        rows = [
            _mapping_row("m-unsuitable", "squat_lunge", "quads", "primary_compound", "UNSUITABLE", confidence=1.0),
            _mapping_row("m-func", "squat_lunge", "quads", "primary_compound", "FUNCTIONAL", confidence=0.1),
        ]
        ranked = rank_candidates(rows)
        self.assertEqual(ranked[0]["movement_id"], "m-func")

    def test_required_accessory_mismatch_demotes_candidate(self):
        rows = [
            _mapping_row("m-wrong-acc", "vertical_pull", "back", "primary_compound", "DIRECT", accessory="StraightBar"),
            _mapping_row("m-right-acc", "vertical_pull", "back", "primary_compound", "DIRECT", accessory="Handles"),
        ]
        ranked = rank_candidates(rows, required_accessory="Handles")
        self.assertEqual(ranked[0]["movement_id"], "m-right-acc")
        # the mismatched one is still reported, never silently dropped
        self.assertEqual(len(ranked), 2)
        mismatch_reasons = next(c for c in ranked if c["movement_id"] == "m-wrong-acc")["reasons"]
        self.assertTrue(any("NOT available" in r for r in mismatch_reasons))

    def test_generic_and_custom_movements_are_never_eligible(self):
        rows = [
            _mapping_row("m-generic", "horizontal_press", "chest", "primary_compound", "DIRECT", is_generic=True),
            _mapping_row("m-custom", "horizontal_press", "chest", "primary_compound", "DIRECT", custom_movement=True),
            _mapping_row("m-real", "horizontal_press", "chest", "primary_compound", "CLOSE"),
        ]
        ranked = rank_candidates(rows)
        self.assertEqual([c["movement_id"] for c in ranked], ["m-real"])

    def test_deterministic_ordering_across_repeated_calls(self):
        rows = [
            _mapping_row("m-b", "elbow_flexion", "biceps", "isolation", "DIRECT", confidence=0.8),
            _mapping_row("m-a", "elbow_flexion", "biceps", "isolation", "DIRECT", confidence=0.8),
        ]
        first = rank_candidates(rows)
        second = rank_candidates(rows)
        self.assertEqual(first, second)
        # equal score -> stable tie-break by movement_id ascending
        self.assertEqual([c["movement_id"] for c in first], ["m-a", "m-b"])

    def test_personal_usage_only_breaks_ties_never_overrides_quality(self):
        rows = [
            _mapping_row("m-close-popular", "hinge", "glutes", "primary_compound", "CLOSE", confidence=0.99),
            _mapping_row("m-direct-unused", "hinge", "glutes", "primary_compound", "DIRECT", confidence=0.5),
        ]
        ranked = rank_candidates(rows, personal_session_counts={"m-close-popular": 50})
        self.assertEqual(ranked[0]["movement_id"], "m-direct-unused")

    def test_reasons_explain_the_match_not_just_a_bare_score(self):
        rows = [_mapping_row("m1", "horizontal_pull", "back", "secondary_compound", "DIRECT")]
        ranked = rank_candidates(rows)
        reasons = ranked[0]["reasons"]
        self.assertTrue(any("horizontal_pull" in r for r in reasons))
        self.assertTrue(any("back" in r for r in reasons))
        self.assertTrue(any("secondary_compound" in r for r in reasons))

    def test_engine_version_is_a_named_constant(self):
        self.assertIsInstance(TONAL_MAPPING_ENGINE_VERSION, int)


if __name__ == "__main__":
    unittest.main()
