"""Program Intelligence V1 - generates program_movement_mappings rows
from REAL, already-synced public.tonal_movements data (never fabricated
movement names/IDs) for the exact (movement_pattern, primary_muscle,
exercise_role) combinations the two seed programs (seed.py) require.

Classification approach, in order:
  1. Reuse training_intelligence.calibration.history.pattern() and
     training_intelligence.stimulus.mapping.classify_muscle_groups()
     UNCHANGED for the six compound patterns those functions already
     recognize (horizontal_press, vertical_press, horizontal_pull,
     vertical_pull, hinge, squat_lunge) - no new pattern-classification
     logic for these; a movement's role (primary_compound vs.
     secondary_compound) is a PROGRAM-DESIGN choice, not an intrinsic
     movement property, so both roles map to the same movement pool.
  2. For patterns pattern() only lumps into one coarse "isolation"
     bucket (elbow flexion vs. extension, lateral vs. front raise,
     calf raise) or does not classify at all (the many core stability
     movements pattern() leaves "unknown" because their names contain
     none of its recognized keywords), this module adds a SEPARATE,
     clearly-labeled, name-keyword sub-classifier - documented here as
     a PRODUCT POLICY heuristic for program-mapping purposes only. It
     never modifies history.pattern() itself and never changes any
     TKI-5.x classification.
  3. Bilateral, non-alternating movements are ranked DIRECT; unilateral/
     alternating variants of the identical pattern+muscle are ranked
     CLOSE (same intent, different execution style) - never UNSUITABLE
     purely for being unilateral (a program MAY legitimately want that,
     e.g. the 'unilateral' exercise_role itself).
  4. Generic/custom-movement rows are never included (matches the
     existing TKI-5.x exclusion in history.valid_rows/select_pool).

Idempotent (ON CONFLICT DO NOTHING on the mapping's own unique key).
Not invoked automatically by any request path.
"""
from __future__ import annotations

from db import get_conn
from training_intelligence.calibration.history import pattern as classify_pattern
from training_intelligence.stimulus.mapping import classify_muscle_groups

MAPPING_SEED_VERSION = 1

# The exact (movement_pattern, primary_muscle) combinations the two
# seed programs' slots require (seed.py), for the 6 compound patterns
# history.pattern() already recognizes. Both primary_compound and
# secondary_compound roles are generated from the SAME movement pool
# (see module docstring point 1) except squat_lunge/unilateral, which
# is its own distinct exercise_role by definition.
_COMPOUND_TARGETS = (
    ("horizontal_press", "chest", ("primary_compound", "secondary_compound")),
    ("horizontal_pull", "back", ("primary_compound", "secondary_compound")),
    ("vertical_press", "shoulders", ("primary_compound", "secondary_compound")),
    ("vertical_pull", "back", ("primary_compound", "secondary_compound")),
    ("squat_lunge", "quads", ("primary_compound",)),
    ("squat_lunge", "glutes", ("unilateral",)),
    ("hinge", "hamstrings", ("primary_compound", "secondary_compound")),
    ("hinge", "glutes", ("primary_compound",)),
)

# name-keyword sub-classifiers for what history.pattern() either lumps
# into one "isolation" bucket or leaves "unknown" entirely. Each entry:
# (movement_subpattern, primary_muscle, exercise_role, keyword_any_of,
#  keyword_none_of). Applied over ALL non-generic/custom movements
# regardless of pattern()'s own bucket (isolation OR unknown), since
# e.g. "Skull Crusher" (a real, rich-history triceps extension per
# TKI-5.4's movement-depth audit) contains none of pattern()'s own
# recognized isolation keywords and falls to "unknown" there - that is
# a known, disclosed limit of history.pattern()'s coarse keyword list,
# unchanged here, worked around only for mapping-seed purposes.
_ISOLATION_TARGETS = (
    # (subpattern, primary_muscle, exercise_role, any_keywords, none_keywords, quality_override)
    ("elbow_flexion", "biceps", "isolation", ("curl",), (), None),
    ("elbow_extension", "triceps", "isolation", ("extension", "kickback", "skull crusher"), (), None),
    ("lateral_raise", "shoulders", "isolation", ("lateral raise",), (), None),
    # Front Raise is a real, distinct shoulder-isolation movement (same
    # muscle/role, a different movement plane - anterior vs. lateral
    # deltoid emphasis) - included as a lower-confidence FUNCTIONAL
    # fallback for a lateral_raise slot, never DIRECT/CLOSE, so a slot
    # requiring lateral_raise specifically is never silently satisfied
    # by front raise ahead of an actual lateral raise.
    ("lateral_raise", "shoulders", "isolation", ("front raise",), (), ("FUNCTIONAL", 0.4)),
    ("calf_raise", "calves", "isolation", ("calf raise",), (), None),
    ("core_anti_rotation", "core", "core", ("chop", "pallof", "rotational punch"), (), None),
    ("core_anti_extension", "core", "core", ("dead bug", "bird dog", "hollow", "w-hold", "plank"), ("bridge with row",), None),
    ("core_flexion", "core", "core", ("crunch", "v-up", "sit-up"), (), None),
)


def _load_real_movements(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT movement_id, name, muscle_groups, is_bilateral, is_alternating, accessory
            FROM public.tonal_movements
            WHERE NOT is_generic AND NOT custom_movement AND name IS NOT NULL
            """
        )
        return cur.fetchall()


def _quality_and_confidence(row):
    """DIRECT for a bilateral, non-alternating movement; CLOSE for a
    unilateral/alternating variant of the identical pattern+muscle
    match - same training intent, different execution style, never
    UNSUITABLE just for being unilateral."""
    if row["is_bilateral"] and not row["is_alternating"]:
        return "DIRECT", 0.9
    return "CLOSE", 0.7


def generate_mapping_rows():
    """Returns a list of dict rows ready for INSERT - pure, read-only
    against tonal_movements, no DB write performed here."""
    with get_conn() as conn:
        movements = _load_real_movements(conn)

    rows = []
    seen = set()

    def add(tonal_movement_id, movement_pattern, primary_muscle, exercise_role, quality, confidence,
            rationale_code, rationale_notes, required_accessory=None):
        key = (tonal_movement_id, movement_pattern, primary_muscle, exercise_role)
        if key in seen:
            return
        seen.add(key)
        rows.append({
            "tonal_movement_id": tonal_movement_id, "movement_pattern": movement_pattern,
            "primary_muscle": primary_muscle, "exercise_role": exercise_role,
            "mapping_quality": quality, "confidence": confidence,
            "rationale_code": rationale_code, "rationale_notes": rationale_notes,
            "required_accessory": required_accessory,
        })

    for movement in movements:
        movement_pattern = classify_pattern(movement)
        muscle = classify_muscle_groups(movement["muscle_groups"])
        if not muscle.is_mapped:
            continue

        for target_pattern, target_muscle, roles in _COMPOUND_TARGETS:
            if movement_pattern == target_pattern and muscle.primary == target_muscle:
                quality, confidence = _quality_and_confidence(movement)
                for role in roles:
                    add(
                        str(movement["movement_id"]), target_pattern, target_muscle, role, quality, confidence,
                        rationale_code=f"pattern_and_muscle_match:{target_pattern}:{target_muscle}",
                        rationale_notes=(
                            f"history.pattern()=={target_pattern!r} and classify_muscle_groups().primary=="
                            f"{target_muscle!r}; {'bilateral, non-alternating' if quality == 'DIRECT' else 'unilateral/alternating variant'}."
                        ),
                    )

        name_lower = (movement["name"] or "").casefold()
        for subpattern, target_muscle, role, any_keywords, none_keywords, quality_override in _ISOLATION_TARGETS:
            if muscle.primary != target_muscle:
                continue
            if not any(k in name_lower for k in any_keywords):
                continue
            if any(k in name_lower for k in none_keywords):
                continue
            if quality_override:
                quality, confidence = quality_override
            else:
                quality, confidence = _quality_and_confidence(movement)
            add(
                str(movement["movement_id"]), subpattern, target_muscle, role, quality, confidence,
                rationale_code=f"name_keyword_match:{subpattern}",
                rationale_notes=(
                    f"name contains one of {any_keywords!r} (mapping-seed sub-classifier, not history.pattern()); "
                    f"{'bilateral, non-alternating' if quality == 'DIRECT' else 'unilateral/alternating variant'}."
                ),
            )

    return rows


def seed_mappings():
    """Idempotent insert of generate_mapping_rows()'s output. Returns
    {"generated": n, "inserted": n}."""
    rows = generate_mapping_rows()
    inserted = 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            for row in rows:
                cur.execute(
                    """
                    INSERT INTO public.program_movement_mappings
                        (tonal_movement_id, movement_pattern, primary_muscle, exercise_role,
                         mapping_quality, confidence, rationale_code, rationale_notes, required_accessory)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (tonal_movement_id, movement_pattern, primary_muscle, exercise_role) DO NOTHING
                    RETURNING id
                    """,
                    (row["tonal_movement_id"], row["movement_pattern"], row["primary_muscle"], row["exercise_role"],
                     row["mapping_quality"], row["confidence"], row["rationale_code"], row["rationale_notes"],
                     row["required_accessory"]),
                )
                if cur.fetchone() is not None:
                    inserted += 1
    return {"generated": len(rows), "inserted": inserted}


if __name__ == "__main__":
    result = seed_mappings()
    print(f"generated={result['generated']} inserted={result['inserted']}")
