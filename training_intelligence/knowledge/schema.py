"""Schema/validation for the Training Intelligence knowledge layer.

A "rule" here is a piece of provenance-tagged, machine-readable knowledge -
never executable prescription logic by itself. TKI-1 only stores and
validates these; nothing in TKI-1/TKI-2 acts on a rule's `action` field.

Evidence classes distinguish what KIND of claim a rule is making, per
TRAINING_INTELLIGENCE_KNOWLEDGE_BASE_V1.md section 2 (evidence hierarchy)
and section 4 (four-layer architecture):

  evidence_tier1  - Layer A / Tier 1: consensus position stands (ACSM, WHO).
                     Hard guardrails and defaults.
  evidence_tier2  - Layer A / Tier 2: evidence-based programming
                     interpretation (Stronger by Science, Barbell Medicine).
  evidence_tier3  - Layer A / Tier 3: platform-specific knowledge
                     (Tonal, WHOOP official material).
  product_policy  - Layer B: a decision this app makes. May cite evidence
                     sources as justification, but the action itself is a
                     product choice, not an established scientific finding.
  personalized    - Layer C / Tier 4: learned from this user's own history.
                     Not populated by TKI-1/TKI-2; reserved for TKI-3+.
  fallback_default - Layer D-adjacent: a value used only in the absence of
                     personalization (e.g. the ~10 sets/week reference).

A rule is never allowed to claim an evidence_class stronger than its
source_ids actually support - see `validate_rule`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

EVIDENCE_CLASSES = (
    "evidence_tier1",
    "evidence_tier2",
    "evidence_tier3",
    "product_policy",
    "personalized",
    "fallback_default",
)

# Evidence-class values that claim to BE a scientific/consensus finding
# (as opposed to a product decision informed by one). Only source_ids whose
# registry tier matches are allowed to back these classes.
_EVIDENCE_TIER_BY_CLASS = {
    "evidence_tier1": 1,
    "evidence_tier2": 2,
    "evidence_tier3": 3,
}

REQUIRED_FIELDS = (
    "rule_id",
    "name",
    "category",
    "evidence_class",
    "source_ids",
    "description",
    "conditions",
    "action",
    "confidence",
    "version",
)

CONFIDENCE_LEVELS = ("high", "medium", "low")


class KnowledgeValidationError(ValueError):
    pass


@dataclass(frozen=True)
class Rule:
    rule_id: str
    name: str
    category: str
    evidence_class: str
    source_ids: tuple
    description: str
    conditions: tuple
    action: tuple
    confidence: str
    version: int
    explanation: tuple = field(default_factory=tuple)


def validate_rule(raw: dict, sources_by_id: dict) -> Rule:
    """Validate one raw rule dict against the schema and the source
    registry. Raises KnowledgeValidationError with a specific reason;
    never silently drops or reinterprets a malformed rule."""

    missing = [f for f in REQUIRED_FIELDS if f not in raw or raw[f] in (None, "", [])]
    if missing:
        raise KnowledgeValidationError(
            f"rule {raw.get('rule_id', '<unknown>')} missing required field(s): {missing}"
        )

    rule_id = raw["rule_id"]

    if raw["evidence_class"] not in EVIDENCE_CLASSES:
        raise KnowledgeValidationError(
            f"rule {rule_id}: evidence_class {raw['evidence_class']!r} not in {EVIDENCE_CLASSES}"
        )

    if raw["confidence"] not in CONFIDENCE_LEVELS:
        raise KnowledgeValidationError(
            f"rule {rule_id}: confidence {raw['confidence']!r} not in {CONFIDENCE_LEVELS}"
        )

    source_ids = tuple(raw["source_ids"])
    unknown_sources = [sid for sid in source_ids if sid not in sources_by_id]
    if unknown_sources:
        raise KnowledgeValidationError(
            f"rule {rule_id}: source_ids not found in source registry: {unknown_sources}"
        )

    required_tier = _EVIDENCE_TIER_BY_CLASS.get(raw["evidence_class"])
    if required_tier is not None:
        tiers = {sources_by_id[sid]["tier"] for sid in source_ids}
        if required_tier not in tiers:
            raise KnowledgeValidationError(
                f"rule {rule_id}: claims {raw['evidence_class']} but none of its "
                f"source_ids are tier {required_tier} sources (do not represent a "
                "product-policy decision as an evidence-tier finding)"
            )

    if not isinstance(raw["version"], int) or raw["version"] < 1:
        raise KnowledgeValidationError(f"rule {rule_id}: version must be a positive integer")

    return Rule(
        rule_id=rule_id,
        name=raw["name"],
        category=raw["category"],
        evidence_class=raw["evidence_class"],
        source_ids=source_ids,
        description=raw["description"],
        conditions=tuple(raw["conditions"]),
        action=tuple(raw["action"]),
        confidence=raw["confidence"],
        version=raw["version"],
        explanation=tuple(raw.get("explanation") or ()),
    )
