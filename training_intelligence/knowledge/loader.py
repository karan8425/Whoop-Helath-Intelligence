"""Loads and validates the versioned Training Intelligence knowledge layer.

Plain JSON is used deliberately, not YAML: PyYAML is present in this
environment's virtualenv but is not pinned in requirements.txt, so relying
on it would be an untracked, fragile Production dependency for no benefit
over JSON for this fully machine-authored/machine-read data.
"""

from __future__ import annotations

import json
from pathlib import Path

from training_intelligence.knowledge.schema import Rule, validate_rule, KnowledgeValidationError

_KNOWLEDGE_DIR = Path(__file__).resolve().parent
_RULES_DIR = _KNOWLEDGE_DIR / "rules"
_SOURCES_PATH = _KNOWLEDGE_DIR / "sources.json"

KNOWLEDGE_VERSION = "TKI_V1"


def load_sources() -> dict:
    """Returns {source_id: source_dict} from the source registry."""
    with open(_SOURCES_PATH, encoding="utf-8") as handle:
        payload = json.load(handle)
    sources = payload.get("sources") or []
    by_id = {}
    for source in sources:
        source_id = source.get("source_id")
        if not source_id:
            raise KnowledgeValidationError("source registry entry missing source_id")
        if source_id in by_id:
            raise KnowledgeValidationError(f"duplicate source_id in registry: {source_id}")
        if not source.get("urls"):
            raise KnowledgeValidationError(f"source {source_id} has no urls")
        if source.get("tier") not in (1, 2, 3):
            raise KnowledgeValidationError(f"source {source_id} has invalid tier: {source.get('tier')}")
        by_id[source_id] = source
    return by_id


def _rule_files():
    return sorted(_RULES_DIR.glob("*.json"))


def load_rules() -> tuple:
    """Loads every rule file, validates each rule against the source
    registry, and returns a tuple of Rule objects. Raises
    KnowledgeValidationError on the first invalid rule - a malformed rule
    is never silently dropped."""

    sources_by_id = load_sources()
    rules = []
    seen_ids = set()

    for path in _rule_files():
        with open(path, encoding="utf-8") as handle:
            raw_rules = json.load(handle)
        if not isinstance(raw_rules, list):
            raise KnowledgeValidationError(f"{path.name}: expected a JSON array of rules")
        for raw in raw_rules:
            rule = validate_rule(raw, sources_by_id)
            if rule.rule_id in seen_ids:
                raise KnowledgeValidationError(f"duplicate rule_id across files: {rule.rule_id}")
            seen_ids.add(rule.rule_id)
            rules.append(rule)

    return tuple(rules)


def rules_by_category(rules: tuple, category: str) -> tuple:
    return tuple(rule for rule in rules if rule.category == category)


def rule_count_by_evidence_class(rules: tuple) -> dict:
    counts: dict = {}
    for rule in rules:
        counts[rule.evidence_class] = counts.get(rule.evidence_class, 0) + 1
    return counts
