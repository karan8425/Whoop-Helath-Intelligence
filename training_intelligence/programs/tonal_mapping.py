"""Program Intelligence V1 Phase 3: deterministic Tonal-movement mapping
engine - resolves ONE abstract slot requirement (movement_pattern,
primary_muscle, exercise_role[, required_accessory]) into a ranked,
explainable list of candidate Tonal movements.

Explicitly excluded from this engine (future-milestone concerns, per
the milestone's own scope boundary): WHOOP recovery, today's readiness,
today's stimulus debt, arbitrary tonnage targets. Personal usage
history may only ever break a tie among otherwise-equal candidates -
it never overrides mapping_quality or a pattern/muscle/role mismatch.

TONAL_MAPPING_ENGINE_VERSION is bumped whenever the scoring formula
below changes, so a stored/cached ranking can be told apart from a
newer one later.
"""
from __future__ import annotations

from training_intelligence.programs.taxonomy import MAPPING_QUALITY_RANK

TONAL_MAPPING_ENGINE_VERSION = 1

# PRODUCT POLICY / CALIBRATION PARAMETER: bounded, named, versioned -
# never an opaque magic number. Quality dominates (100-point bands);
# confidence and the personal-usage tie-breaker can only move a
# candidate within its own quality band, never across bands.
QUALITY_BAND_POINTS = 100
CONFIDENCE_WEIGHT = 40  # of the 100 points in a quality band
ACCESSORY_MATCH_BONUS = 5
ACCESSORY_MISMATCH_PENALTY = 1000  # effectively disqualifying, but still ranked (not silently dropped)
PERSONAL_USAGE_TIE_BREAK_WEIGHT = 1  # smaller than any quality/confidence increment - tie-break ONLY


def score_candidate(mapping_row, required_accessory=None, personal_session_count=0):
    """Pure, deterministic. `mapping_row` is one row from
    repository.get_candidate_tonal_movements() (already joined against
    tonal_movements). Returns (score, reasons) - reasons explain WHY,
    never a bare number."""
    reasons = []
    quality = mapping_row["mapping_quality"]
    quality_rank = MAPPING_QUALITY_RANK[quality]
    score = quality_rank * QUALITY_BAND_POINTS
    reasons.append(f"{mapping_row['movement_pattern']} pattern match")
    reasons.append(f"{mapping_row['primary_muscle']} primary-muscle match")
    reasons.append(f"{mapping_row['exercise_role']} role match")
    reasons.append(f"mapping_quality={quality}")

    confidence = float(mapping_row["confidence"])
    score += confidence * CONFIDENCE_WEIGHT
    reasons.append(f"confidence={confidence:.2f}")

    if required_accessory:
        if mapping_row.get("tonal_accessory") == required_accessory:
            score += ACCESSORY_MATCH_BONUS
            reasons.append(f"required accessory '{required_accessory}' available")
        else:
            score -= ACCESSORY_MISMATCH_PENALTY
            reasons.append(f"required accessory '{required_accessory}' NOT available (movement has "
                            f"'{mapping_row.get('tonal_accessory')}')")

    if personal_session_count:
        # Tie-break only - weight is deliberately smaller than a single
        # confidence increment, so it can never outrank a better quality
        # or confidence candidate, only break a genuine tie between two
        # otherwise-equal ones.
        score += min(personal_session_count, 10) * PERSONAL_USAGE_TIE_BREAK_WEIGHT
        reasons.append(f"personal usage tie-break ({personal_session_count} prior sessions)")

    return round(score, 4), reasons


def rank_candidates(mapping_rows, required_accessory=None, personal_session_counts=None):
    """`personal_session_counts`: optional {tonal_movement_id: count},
    supplied by the caller (never computed here - this module never
    queries workout history itself, keeping it a pure ranking function
    over whatever candidates/usage data it's handed)."""
    personal_session_counts = personal_session_counts or {}
    ranked = []
    for row in mapping_rows:
        if row.get("is_generic") or row.get("custom_movement"):
            # Never eligible, regardless of mapping_quality - matches
            # the existing calibration-layer exclusion (history.
            # valid_rows, select_pool) of generic/custom movements.
            continue
        count = personal_session_counts.get(str(row["tonal_movement_id"]), 0)
        score, reasons = score_candidate(row, required_accessory, count)
        ranked.append({
            "movement_id": str(row["tonal_movement_id"]),
            "movement_name": row.get("movement_name"),
            "mapping_quality": row["mapping_quality"],
            "score": score,
            "reasons": reasons,
        })
    # Deterministic: score desc, then movement_id asc as a stable
    # tie-break so ordering never depends on incidental DB row order.
    return sorted(ranked, key=lambda c: (-c["score"], c["movement_id"]))


def candidates_for_slot(slot, mapping_rows, personal_session_counts=None):
    """Convenience wrapper taking a SessionSlot (models.py) directly."""
    return rank_candidates(mapping_rows, required_accessory=None, personal_session_counts=personal_session_counts)
