"""Program Intelligence V1 - two ORIGINAL demonstration programs.

Not copied from Muscle & Strength or any other published source. Built
from general, widely-taught upper/lower hypertrophy-training structure
(horizontal/vertical push-pull pairing, a hinge+squat lower-body split,
isolation accessories) and this codebase's OWN existing goal-policy
documentation (training_intelligence/dose/goal_policy.py) - not a
specific authored workout's set/rep prescriptions. Every slot is an
abstract (movement_pattern, primary_muscle, exercise_role, rep/set/RIR
range) requirement; no exercise name, sourced program text, or
copyrighted description is stored anywhere here (Phase 6 - see
source_type='original', reference_notes below).

Idempotent: safe to run multiple times (ON CONFLICT DO NOTHING on the
program's unique slug; a program that already exists is left alone,
never re-seeded/duplicated). Not invoked automatically by any request
path - run explicitly (see the __main__ block) against the Development
database only.
"""
from __future__ import annotations

from db import get_conn

SEED_VERSION = 1

# PRODUCT POLICY: lean_cut trims the upper end of the set range relative
# to lean_bulk's baseline - directly reflecting goal_policy.py's own
# documented LEAN_CUT posture ("prefer_moderate_avoid_unnecessary_
# increase" / "targets the lower-middle of the feasible range"), not a
# new or competing claim about volume. Rep ranges and RIR are shared
# (goal mode does not change movement selection principles) - only the
# SET ceiling differs, and only for primary/secondary compounds (the
# only roles goal_policy.py's own posture note is actually about).
_SET_MAX_TRIM_BY_GOAL = {"lean_cut": 1}


def _trim_set_max(set_max, exercise_role, goal_mode):
    if exercise_role not in ("primary_compound", "secondary_compound"):
        return set_max
    trim = _SET_MAX_TRIM_BY_GOAL.get(goal_mode, 0)
    return max(set_max - trim, set_max - trim if set_max - trim >= 1 else set_max)


def _slot(idx, pattern, muscle, role, set_min, set_max, rep_min, rep_max, rir_min, rir_max,
          rest_min, rest_max, secondary=(), subpattern=None, required=True, substitution_group=None):
    return {
        "slot_index": idx, "movement_pattern": pattern, "movement_subpattern": subpattern,
        "primary_muscle": muscle, "secondary_muscles": list(secondary), "exercise_role": role,
        "required": required, "set_min": set_min, "set_max": set_max, "rep_min": rep_min, "rep_max": rep_max,
        "rir_min": rir_min, "rir_max": rir_max, "rest_seconds_min": rest_min, "rest_seconds_max": rest_max,
        "priority": 100, "substitution_group": substitution_group,
    }


def _upper_a_slots(goal_mode):
    return [
        _slot(0, "horizontal_press", "chest", "primary_compound", 3, _trim_set_max(4, "primary_compound", goal_mode),
              6, 10, 1, 3, 120, 180, secondary=("triceps", "shoulders")),
        _slot(1, "horizontal_pull", "back", "primary_compound", 3, _trim_set_max(4, "primary_compound", goal_mode),
              6, 10, 1, 3, 120, 180, secondary=("biceps",)),
        _slot(2, "vertical_press", "shoulders", "secondary_compound", 2, _trim_set_max(3, "secondary_compound", goal_mode),
              8, 12, 1, 3, 90, 150, secondary=("triceps",)),
        _slot(3, "vertical_pull", "back", "secondary_compound", 2, _trim_set_max(3, "secondary_compound", goal_mode),
              8, 12, 1, 3, 90, 150, secondary=("biceps",)),
        _slot(4, "elbow_flexion", "biceps", "isolation", 2, 3, 10, 15, 0, 2, 60, 90),
        _slot(5, "elbow_extension", "triceps", "isolation", 2, 3, 10, 15, 0, 2, 60, 90),
    ]


def _upper_b_slots(goal_mode):
    # A/B alternation: same movement-pattern vocabulary, primary/
    # secondary compound roles swapped between vertical and horizontal
    # emphasis, plus one different isolation accessory - standard
    # upper/lower A/B design, not a copy of Upper A.
    return [
        _slot(0, "vertical_press", "shoulders", "primary_compound", 3, _trim_set_max(4, "primary_compound", goal_mode),
              6, 10, 1, 3, 120, 180, secondary=("triceps",)),
        _slot(1, "vertical_pull", "back", "primary_compound", 3, _trim_set_max(4, "primary_compound", goal_mode),
              6, 10, 1, 3, 120, 180, secondary=("biceps",)),
        _slot(2, "horizontal_press", "chest", "secondary_compound", 2, _trim_set_max(3, "secondary_compound", goal_mode),
              8, 12, 1, 3, 90, 150, secondary=("triceps", "shoulders")),
        _slot(3, "horizontal_pull", "back", "secondary_compound", 2, _trim_set_max(3, "secondary_compound", goal_mode),
              8, 12, 1, 3, 90, 150, secondary=("biceps",)),
        _slot(4, "lateral_raise", "shoulders", "isolation", 2, 3, 12, 20, 0, 2, 45, 75),
        _slot(5, "elbow_extension", "triceps", "isolation", 2, 3, 10, 15, 0, 2, 60, 90),
    ]


def _lower_a_slots(goal_mode):
    return [
        _slot(0, "squat_lunge", "quads", "primary_compound", 3, _trim_set_max(4, "primary_compound", goal_mode),
              6, 10, 1, 3, 120, 180, secondary=("glutes",)),
        _slot(1, "hinge", "hamstrings", "primary_compound", 3, _trim_set_max(4, "primary_compound", goal_mode),
              6, 10, 1, 3, 120, 180, secondary=("glutes",)),
        _slot(2, "squat_lunge", "glutes", "unilateral", 2, _trim_set_max(3, "secondary_compound", goal_mode),
              8, 12, 1, 3, 90, 120, secondary=("quads",), subpattern="single_leg"),
        _slot(3, "calf_raise", "calves", "isolation", 3, 4, 10, 15, 0, 2, 45, 75),
        _slot(4, "core_anti_extension", "core", "core", 2, 3, 8, 15, 0, 2, 45, 75),
    ]


def _lower_b_slots(goal_mode):
    return [
        _slot(0, "hinge", "glutes", "primary_compound", 3, _trim_set_max(4, "primary_compound", goal_mode),
              6, 10, 1, 3, 120, 180, secondary=("hamstrings",)),
        _slot(1, "squat_lunge", "quads", "primary_compound", 3, _trim_set_max(4, "primary_compound", goal_mode),
              8, 12, 1, 3, 120, 180, secondary=("glutes",), subpattern="anterior_dominant"),
        _slot(2, "hinge", "hamstrings", "secondary_compound", 2, _trim_set_max(3, "secondary_compound", goal_mode),
              8, 12, 1, 3, 90, 150, secondary=("glutes",)),
        _slot(3, "squat_lunge", "glutes", "unilateral", 2, _trim_set_max(3, "secondary_compound", goal_mode),
              8, 12, 1, 3, 90, 120, secondary=("quads",), subpattern="single_leg"),
        _slot(4, "calf_raise", "calves", "isolation", 3, 4, 10, 15, 0, 2, 45, 75),
        _slot(5, "core_anti_rotation", "core", "core", 2, 3, 8, 15, 0, 2, 45, 75),
    ]


def _sessions_for(goal_mode):
    return [
        ("upper_a", "Upper A", "Upper Mixed", 0, _upper_a_slots(goal_mode)),
        ("lower_a", "Lower A", "Lower Body", 1, _lower_a_slots(goal_mode)),
        ("upper_b", "Upper B", "Upper Mixed", 2, _upper_b_slots(goal_mode)),
        ("lower_b", "Lower B", "Lower Body", 3, _lower_b_slots(goal_mode)),
    ]


SEED_PROGRAMS = [
    {
        "slug": "hypertrophy_upper_lower_4d_v1",
        "name": "Hypertrophy Upper/Lower (4-Day)",
        "description": (
            "A 4-day upper/lower split emphasizing balanced horizontal/"
            "vertical push-pull compound work with isolation accessories, "
            "targeting a moderate-to-higher volume hypertrophy posture."
        ),
        "goal_mode": "lean_bulk",  # goal_policy.py's own hypertrophy goal
        "experience_level": "intermediate",
        "split_type": "upper_lower",
        "days_per_week": 4,
        "duration_weeks": None,  # repeats indefinitely - weeks are optional, not modeled here
        "default_session_duration_min": None,  # runtime/user preference only, never a system default
        "source_type": "original",
        "source_name": None,
        "source_url": None,
        "reference_notes": (
            "Informed by widely-taught upper/lower hypertrophy structure "
            "(horizontal/vertical push-pull pairing, hinge+squat lower "
            "split) and this codebase's own goal_policy.py hypertrophy "
            "posture documentation - not derived from any single "
            "published program's specific prescriptions."
        ),
    },
    {
        "slug": "lean_cut_upper_lower_4d_v1",
        "name": "Lean Cut Upper/Lower (4-Day)",
        "description": (
            "The same 4-day upper/lower structural backbone, posture-"
            "adjusted for an energy-deficit lean-cut goal: preserves "
            "meaningful compound intensity while trimming the top of the "
            "primary/secondary-compound set range rather than adding volume."
        ),
        "goal_mode": "lean_cut",
        "experience_level": "intermediate",
        "split_type": "upper_lower",
        "days_per_week": 4,
        "duration_weeks": None,
        "default_session_duration_min": None,
        "source_type": "original",
        "source_name": None,
        "source_url": None,
        "reference_notes": (
            "Same structural backbone as hypertrophy_upper_lower_4d_v1, "
            "reusing training_intelligence.programs.seed's shared slot "
            "builders (not duplicated code) with goal_mode='lean_cut' - "
            "directly reflects goal_policy.py's own documented LEAN_CUT "
            "posture (preserve intensity, avoid unnecessary volume increase)."
        ),
    },
]


def seed_programs(conn=None):
    """Idempotent: ON CONFLICT (slug) DO NOTHING at the program level -
    if a program with this slug already exists, this function does not
    touch it (or re-insert its sessions/slots) at all. Returns
    {slug: {"inserted": bool, "program_id": str}}."""
    def run(connection):
        results = {}
        with connection.cursor() as cur:
            for spec in SEED_PROGRAMS:
                cur.execute(
                    """
                    INSERT INTO public.training_programs
                        (slug, name, description, goal_mode, experience_level, split_type, days_per_week,
                         duration_weeks, default_session_duration_min, source_type, source_name, source_url,
                         reference_notes, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active')
                    ON CONFLICT (slug) DO NOTHING
                    RETURNING id
                    """,
                    (spec["slug"], spec["name"], spec["description"], spec["goal_mode"], spec["experience_level"],
                     spec["split_type"], spec["days_per_week"], spec["duration_weeks"],
                     spec["default_session_duration_min"], spec["source_type"], spec["source_name"],
                     spec["source_url"], spec["reference_notes"]),
                )
                row = cur.fetchone()
                if row is None:
                    cur.execute("SELECT id FROM public.training_programs WHERE slug = %s", (spec["slug"],))
                    row = cur.fetchone()
                    results[spec["slug"]] = {"inserted": False, "program_id": str(row["id"])}
                    continue

                program_id = row["id"]
                results[spec["slug"]] = {"inserted": True, "program_id": str(program_id)}

                for session_key, session_name, session_family, sequence_index, slots in _sessions_for(spec["goal_mode"]):
                    cur.execute(
                        """
                        INSERT INTO public.training_program_sessions
                            (program_id, session_key, session_name, session_family, sequence_index)
                        VALUES (%s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        (program_id, session_key, session_name, session_family, sequence_index),
                    )
                    session_id = cur.fetchone()["id"]
                    for slot in slots:
                        cur.execute(
                            """
                            INSERT INTO public.training_session_slots
                                (session_id, slot_index, movement_pattern, movement_subpattern, primary_muscle,
                                 secondary_muscles, exercise_role, required, set_min, set_max, rep_min, rep_max,
                                 rir_min, rir_max, rest_seconds_min, rest_seconds_max, priority, substitution_group)
                            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            (session_id, slot["slot_index"], slot["movement_pattern"], slot["movement_subpattern"],
                             slot["primary_muscle"], _json(slot["secondary_muscles"]), slot["exercise_role"],
                             slot["required"], slot["set_min"], slot["set_max"], slot["rep_min"], slot["rep_max"],
                             slot["rir_min"], slot["rir_max"], slot["rest_seconds_min"], slot["rest_seconds_max"],
                             slot["priority"], slot["substitution_group"]),
                        )
        return results

    if conn is not None:
        return run(conn)
    with get_conn() as connection:
        return run(connection)


def _json(value):
    import json
    return json.dumps(value)


if __name__ == "__main__":
    # Explicit, manual invocation only - never imported/run by any
    # request path. Expects DATABASE_URL to already be exported by the
    # caller (see TRAINING_INTELLIGENCE_PROGRAM_LAYER_V1_REPORT.md
    # section 9's Keychain procedure) - this script does not read or
    # print it.
    result = seed_programs()
    for slug, info in result.items():
        print(f"{slug}: inserted={info['inserted']} program_id={info['program_id']}")
