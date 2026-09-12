"""Exercise -> muscle mapping.

Mapping precedence (section 5 of the TKI-1/TKI-2 assignment):

    1. existing reliable app/Tonal mapping   <- this is all we have today,
       and it is what this module uses exclusively.
    2. deterministic curated mapping         <- not needed yet; every
       Tonal movement already carries official muscle_groups metadata.
    3. explicit unknown                      <- returned whenever a
       movement's muscle_groups is empty or contains only labels
       `integrations.tonal.muscle_readiness._normalize_muscle` does not
       recognize. Never guessed.

`tonal_movements.muscle_groups` is a JSONB array from Tonal's own API.
integrations.tonal.muscle_readiness already establishes the convention
this module reuses: the first recognized label is the primary muscle,
every other recognized label is secondary. That convention is NOT
reinvented here - it is imported.
"""

from __future__ import annotations

from dataclasses import dataclass

from integrations.tonal.muscle_readiness import _normalize_muscle
from training_intelligence.stimulus.taxonomy import to_canonical

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
        if existing is None:
            unmapped.append(raw)
            continue
        canonical = to_canonical(existing)
        if canonical and canonical not in recognized:
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
