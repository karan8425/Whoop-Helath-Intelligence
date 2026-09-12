"""TKI-1: knowledge-layer schema, loader, and source-registry tests."""

import unittest

from training_intelligence.knowledge.loader import (
    load_rules,
    load_sources,
    rule_count_by_evidence_class,
    KNOWLEDGE_VERSION,
)
from training_intelligence.knowledge.schema import (
    EVIDENCE_CLASSES,
    REQUIRED_FIELDS,
    CONFIDENCE_LEVELS,
    validate_rule,
    KnowledgeValidationError,
)


class SourceRegistryTests(unittest.TestCase):
    def test_every_source_has_at_least_one_url_and_a_valid_tier(self):
        sources = load_sources()
        self.assertGreater(len(sources), 0)
        for source_id, source in sources.items():
            self.assertTrue(source["urls"], source_id)
            self.assertIn(source["tier"], (1, 2, 3), source_id)

    def test_no_duplicate_source_ids(self):
        sources = load_sources()
        # load_sources itself raises on duplicates; reaching here means
        # the file has none. Re-assert the count matches unique ids as a
        # second, independent check.
        self.assertEqual(len(sources), len(set(sources.keys())))

    def test_expected_organizations_present(self):
        sources = load_sources()
        organizations = {s["organization"] for s in sources.values()}
        for expected in ("American College of Sports Medicine", "World Health Organization",
                         "Stronger by Science", "Barbell Medicine", "Tonal", "WHOOP"):
            self.assertIn(expected, organizations)


class KnowledgeRuleLoadingTests(unittest.TestCase):
    def test_loads_at_least_one_rule_per_required_category(self):
        rules = load_rules()
        categories = {rule.category for rule in rules}
        for expected in ("hypertrophy", "strength", "progression", "fatigue", "session_selection"):
            self.assertIn(expected, categories)

    def test_every_rule_has_every_required_field_populated(self):
        rules = load_rules()
        for rule in rules:
            for field_name in REQUIRED_FIELDS:
                self.assertTrue(hasattr(rule, field_name), f"{rule.rule_id} missing {field_name}")
                value = getattr(rule, field_name)
                self.assertNotIn(value, (None, "", ()), f"{rule.rule_id}.{field_name} is empty")

    def test_no_duplicate_rule_ids_across_files(self):
        rules = load_rules()
        ids = [rule.rule_id for rule in rules]
        self.assertEqual(len(ids), len(set(ids)))

    def test_evidence_classes_are_all_known(self):
        rules = load_rules()
        for rule in rules:
            self.assertIn(rule.evidence_class, EVIDENCE_CLASSES)

    def test_confidence_levels_are_all_known(self):
        rules = load_rules()
        for rule in rules:
            self.assertIn(rule.confidence, CONFIDENCE_LEVELS)

    def test_knowledge_version_is_stable_string(self):
        self.assertEqual(KNOWLEDGE_VERSION, "TKI_V1")

    def test_rule_count_by_evidence_class_sums_to_total(self):
        rules = load_rules()
        counts = rule_count_by_evidence_class(rules)
        self.assertEqual(sum(counts.values()), len(rules))


class RuleValidationTests(unittest.TestCase):
    """Directly exercises validate_rule with hand-built (not file-loaded)
    payloads, so schema regressions are caught independent of the
    checked-in rule content."""

    def setUp(self):
        self.sources = {
            "ACSM_2026_RT_POSITION_STAND": {"tier": 1},
            "STRONGER_BY_SCIENCE_STRENGTH_GUIDE": {"tier": 2},
            "WHOOP_RECOVERY_GUIDANCE": {"tier": 3},
        }

    def _valid_rule(self, **overrides):
        rule = {
            "rule_id": "TEST_001",
            "name": "Test rule",
            "category": "fatigue",
            "evidence_class": "product_policy",
            "source_ids": ["WHOOP_RECOVERY_GUIDANCE"],
            "description": "A description.",
            "conditions": ["x == y"],
            "action": ["do_something"],
            "confidence": "high",
            "version": 1,
        }
        rule.update(overrides)
        return rule

    def test_missing_required_field_rejected(self):
        rule = self._valid_rule()
        del rule["description"]
        with self.assertRaises(KnowledgeValidationError):
            validate_rule(rule, self.sources)

    def test_unknown_source_id_rejected(self):
        rule = self._valid_rule(source_ids=["NOT_IN_REGISTRY"])
        with self.assertRaises(KnowledgeValidationError):
            validate_rule(rule, self.sources)

    def test_unknown_evidence_class_rejected(self):
        rule = self._valid_rule(evidence_class="settled_fact")
        with self.assertRaises(KnowledgeValidationError):
            validate_rule(rule, self.sources)

    def test_product_policy_may_cite_any_tier_source(self):
        rule = self._valid_rule(evidence_class="product_policy", source_ids=["WHOOP_RECOVERY_GUIDANCE"])
        validated = validate_rule(rule, self.sources)
        self.assertEqual(validated.evidence_class, "product_policy")

    def test_evidence_tier1_claim_requires_a_tier1_source(self):
        """The central A/B/C/D distinction from the assignment: a
        product-policy decision must never be mislabeled as an
        ACSM-tier conclusion."""
        rule = self._valid_rule(
            evidence_class="evidence_tier1",
            source_ids=["WHOOP_RECOVERY_GUIDANCE"],  # tier 3, not tier 1
        )
        with self.assertRaises(KnowledgeValidationError):
            validate_rule(rule, self.sources)

        rule["source_ids"] = ["ACSM_2026_RT_POSITION_STAND"]
        validated = validate_rule(rule, self.sources)
        self.assertEqual(validated.evidence_class, "evidence_tier1")

    def test_invalid_version_rejected(self):
        rule = self._valid_rule(version=0)
        with self.assertRaises(KnowledgeValidationError):
            validate_rule(rule, self.sources)


if __name__ == "__main__":
    unittest.main()
