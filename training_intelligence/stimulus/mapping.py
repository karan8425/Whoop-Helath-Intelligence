"""Exercise -> muscle mapping.

Mapping precedence (section 5 of the TKI-1/TKI-2 assignment):

    1. existing reliable app/Tonal mapping   <- integrations.tonal.
       muscle_readiness._normalize_muscle, reused as-is.
    2. deterministic curated mapping         <- taxonomy.
       DIRECT_TONAL_LABEL_TO_CANONICAL (currently just "Calves" - see
       TKI-2.1 CALVES FIX in taxonomy.py). Exact-string match only.
    3. explicit unknown                      <- returned whenever a
       movement's muscle_groups is empty or contains only labels neither
       of the above recognizes. Never guessed. "Handle Move" (Tonal's own
       is_generic freeform placeholder, confirmed via live-data forensics
       to collapse genuinely different real exercises with no
       deterministic identifier anywhere in the schema) is the main
       real-world example that correctly stays here - see
       TRAINING_INTELLIGENCE_TKI12_REPORT.md.

`tonal_movements.muscle_groups` is a JSONB array from Tonal's own API.
integrations.tonal.muscle_readiness already establishes the convention
this module reuses: the first recognized label (in Tonal's own listed
order) is the primary muscle, every other recognized label is secondary.
That convention is NOT reinvented here - it is imported, and it now
applies uniformly whether a label was recognized via step 1 or step 2
above, in whatever order Tonal itself listed them. One concrete,
already-observed consequence: "Racked Reverse Lunge" lists
["Calves","Glutes","Hamstrings","Quads","Abs","Shoulders"] - before the
Calves fix, "Calves" was silently skipped and "Glutes" became primary;
after the fix, "Calves" (now recognized, and still first in Tonal's
list) becomes primary instead. This is a real, disclosed change in this
one movement's classification, not a silent regression - see
TRAINING_INTELLIGENCE_TKI12_REPORT.md and
test_muscle_stimulus_ledger.py's calves-related tests.
"""

from __future__ import annotations

from dataclasses import dataclass

from integrations.tonal.muscle_readiness import _normalize_muscle
from training_intelligence.stimulus.taxonomy import to_canonical, canonical_from_raw_tonal_label

MAPPING_SOURCE_TONAL_OFFICIAL = "tonal_official_muscle_groups"
MAPPING_SOURCE_UNKNOWN = "unknown"


@dataclass(frozen=True)
class MuscleMapping:
    primary: str | None
    secondary: tuple
    source: str
    unmapped_raw_labels: tuple

    @property
    def is_mapped(self) -> bool:
        return self.primary is not None


def classify_muscle_groups(raw_muscle_groups) -> MuscleMapping:
    """raw_muscle_groups: the value of tonal_movements.muscle_groups for
    one movement (a list of Tonal's own muscle-group strings, or None)."""

    recognized = []
    unmapped = []
    for raw in raw_muscle_groups or []:
        existing = _normalize_muscle(raw)
        canonical = to_canonical(existing) if existing is not None else None
        if canonical is None:
            # Not in the existing B3/B4 taxonomy (or _normalize_muscle
            # didn't recognize the raw label at all) - try the small,
            # deterministic, TKI-owned direct table before giving up.
            canonical = canonical_from_raw_tonal_label(raw)
        if canonical is None:
            unmapped.append(raw)
            continue
        if canonical not in recognized:
            recognized.append(canonical)

    if not recognized:
        return MuscleMapping(
            primary=None,
            secondary=(),
            source=MAPPING_SOURCE_UNKNOWN,
            unmapped_raw_labels=tuple(unmapped),
        )

    return MuscleMapping(
        primary=recognized[0],
        secondary=tuple(recognized[1:]),
        source=MAPPING_SOURCE_TONAL_OFFICIAL,
        unmapped_raw_labels=tuple(unmapped),
    )


def movement_mapping_report(movements) -> dict:
    """movements: iterable of {"movement_id", "name", "muscle_groups"}
    (one row per distinct Tonal movement, e.g. from tonal_movements).

    Returns a deterministic coverage report - never silently drops an
    unmapped movement."""

    total = 0
    mapped = 0
    unmapped_list = []

    for movement in movements:
        total += 1
        classification = classify_muscle_groups(movement.get("muscle_groups"))
        if classification.is_mapped:
            mapped += 1
        else:
            unmapped_list.append({
                "movement_id": movement.get("movement_id"),
                "name": movement.get("name"),
                "raw_muscle_groups": movement.get("muscle_groups"),
            })

    coverage_pct = round((mapped / total) * 100.0, 1) if total else 0.0

    return {
        "total_movements": total,
        "mapped_movements": mapped,
        "unmapped_movements": len(unmapped_list),
        "mapping_coverage_pct": coverage_pct,
        "unmapped": unmapped_list,
    }
