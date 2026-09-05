import json
from datetime import datetime, timezone

from db import get_conn

from integrations.tonal.strength_analytics import (
    strength_analytics,
)
from integrations.tonal.muscle_readiness import calculate_muscle_readiness


TARGET_FREQUENCY_7D = 2

SESSION_TEMPLATES = {
    "Upper Push": {
        "muscles": ["Chest", "Shoulders", "Triceps"],
        "minimum_eligible": 2,
    },
    "Upper Pull": {
        "muscles": ["Back", "Biceps"],
        "minimum_eligible": 2,
    },
    "Lower Body": {
        "muscles": ["Glutes", "Hamstrings", "Quads"],
        "minimum_eligible": 2,
    },
    "Upper Mixed": {
        "muscles": ["Chest", "Back", "Shoulders", "Biceps", "Triceps"],
        "minimum_eligible": 3,
    },
    "Core + Accessories": {
        "muscles": ["Core", "Biceps", "Triceps", "Shoulders"],
        "minimum_eligible": 2,
    },
    "Full Body": {
        "muscles": ["Chest", "Back", "Shoulders", "Glutes", "Hamstrings", "Quads", "Core"],
        "minimum_eligible": 3,
    },
}
YESTERDAY_FOCUS_PENALTY = 35.0
REPEATED_FOCUS_PENALTY = 15.0
RECOVERING_MUSCLE_PENALTY = 15.0


REGION_MUSCLES = {
    "upper": [
        "Chest",
        "Back",
        "Shoulders",
        "Biceps",
        "Triceps",
    ],
    "core": [
        "Core",
    ],
    "lower": [
        "Glutes",
        "Hamstrings",
        "Quads",
    ],
}


def _strength_imbalance(
    latest_scores: dict | None,
) -> dict:

    if not latest_scores:
        return {
            "available": False,
            "weakest_region": None,
            "strongest_region": None,
            "scores": {},
            "gap": None,
        }

    scores = {
        "upper": float(
            latest_scores.get(
                "upper",
                0,
            )
        ),
        "core": float(
            latest_scores.get(
                "core",
                0,
            )
        ),
        "lower": float(
            latest_scores.get(
                "lower",
                0,
            )
        ),
    }

    valid_scores = {
        key: value
        for key, value in scores.items()
        if value > 0
    }

    if not valid_scores:
        return {
            "available": False,
            "weakest_region": None,
            "strongest_region": None,
            "scores": scores,
            "gap": None,
        }

    weakest = min(
        valid_scores,
        key=valid_scores.get,
    )

    strongest = max(
        valid_scores,
        key=valid_scores.get,
    )

    return {
        "available": True,
        "weakest_region": weakest,
        "strongest_region": strongest,
        "scores": scores,
        "gap": round(
            valid_scores[
                strongest
            ]
            - valid_scores[
                weakest
            ],
            1,
        ),
    }


def _muscle_priority_score(
    muscle: str,
    data: dict,
    weakest_region: str | None,
) -> float:

    primary_sessions = (
        data.get(
            "primary_sessions",
            0,
        )
        or 0
    )

    secondary_sessions = (
        data.get(
            "secondary_sessions",
            0,
        )
        or 0
    )

    primary_sets = (
        data.get(
            "primary_sets",
            0,
        )
        or 0
    )

    secondary_sets = (
        data.get(
            "secondary_sets",
            0,
        )
        or 0
    )

    days_since_primary = data.get(
        "days_since_primary_training"
    )

    score = 0.0

    # ========================================================
    # DIRECT WEEKLY FREQUENCY
    # This is the strongest signal.
    # ========================================================

    if primary_sessions == 0:
        score += 100

    elif primary_sessions == 1:
        score += 55

    elif primary_sessions == 2:
        score += 5

    else:
        score -= 25

    # ========================================================
    # TIME SINCE DIRECT TRAINING
    # ========================================================

    if days_since_primary is None:
        score += 30

    elif days_since_primary >= 5:
        score += 25

    elif days_since_primary >= 3:
        score += 12

    elif days_since_primary < 1.5:
        score -= 25

    # ========================================================
    # DIRECT SET VOLUME
    # High direct volume reduces urgency even if frequency
    # is technically below target.
    # ========================================================

    if primary_sets >= 16:
        score -= 18

    elif primary_sets >= 10:
        score -= 10

    elif (
        primary_sets > 0
        and primary_sets <= 4
    ):
        score += 5

    # ========================================================
    # SECONDARY WORKLOAD
    # Secondary work does NOT satisfy direct frequency,
    # but meaningful supporting work should modestly reduce
    # urgency.
    # ========================================================

    if secondary_sessions >= 2:
        score -= 10

    elif secondary_sessions == 1:
        score -= 4

    if secondary_sets >= 12:
        score -= 8

    elif secondary_sets >= 6:
        score -= 4

    # ========================================================
    # STRENGTH-BALANCE BONUS
    # Modest boost only. Coverage remains the primary driver.
    # ========================================================

    if weakest_region:

        if muscle in REGION_MUSCLES.get(
            weakest_region,
            [],
        ):
            score += 20

    return round(
        score,
        1,
    )


def _build_session_focus(
    priority_muscles: list,
) -> dict:

    names = [
        item[
            "muscle"
        ]
        for item in priority_muscles
    ]

    lower = [
        muscle
        for muscle in names
        if muscle in (
            "Glutes",
            "Hamstrings",
            "Quads",
        )
    ]

    core = (
        "Core"
        if "Core" in names
        else None
    )

    upper_push = [
        muscle
        for muscle in names
        if muscle in (
            "Chest",
            "Shoulders",
            "Triceps",
        )
    ]

    upper_pull = [
        muscle
        for muscle in names
        if muscle in (
            "Back",
            "Biceps",
        )
    ]

    if (
        len(lower) >= 2
        and core
    ):
        return {
            "session_type":
                "Lower Body + Core",

            "primary_focus":
                lower[:3],

            "secondary_focus":
                [
                    "Core"
                ],
        }

    if len(lower) >= 2:
        return {
            "session_type":
                "Lower Body",

            "primary_focus":
                lower[:3],

            "secondary_focus":
                [],
        }

    if (
        len(upper_push) >= 2
        and core
    ):
        return {
            "session_type":
                "Upper Push + Core",

            "primary_focus":
                upper_push[:3],

            "secondary_focus":
                [
                    "Core"
                ],
        }

    if len(
        upper_push
    ) >= 2:
        return {
            "session_type":
                "Upper Push",

            "primary_focus":
                upper_push[:3],

            "secondary_focus":
                [],
        }

    if len(
        upper_pull
    ) >= 2:
        return {
            "session_type":
                "Upper Pull",

            "primary_focus":
                upper_pull[:2],

            "secondary_focus":
                [],
        }

    top_names = names[:3]

    return {
        "session_type":
            "Targeted Strength",

        "primary_focus":
            top_names,

        "secondary_focus":
            [],
    }


def _recommendation_history(now):
    target_date = now.date()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT plan_date, plan_payload #>> '{training,session_type}' AS focus
                FROM todays_plan_cache
                WHERE plan_date < %s
                  AND plan_payload #>> '{training,session_type}' IS NOT NULL
                ORDER BY plan_date DESC
                LIMIT 3
                """,
                (target_date,),
            )
            return cur.fetchall()


def _score_session_templates(ranked, readiness, history):
    priority = {row["muscle"]: float(row["priority_score"]) for row in ranked}
    readiness_by_muscle = {row["muscle"]: row for row in readiness["muscles"]}
    yesterday_focus = history[0].get("focus") if history else None
    repeated = [row.get("focus") for row in history]
    scored = []

    for name, template in SESSION_TEMPLATES.items():
        eligible = []
        for muscle in template["muscles"]:
            state = readiness_by_muscle[muscle]["readiness_state"]
            if state not in ("SUPPRESSED", "FATIGUED"):
                eligible.append(muscle)

        valid = len(eligible) >= template["minimum_eligible"]
        if name == "Full Body":
            upper = any(m in eligible for m in ("Chest", "Back", "Shoulders"))
            lower = any(m in eligible for m in ("Glutes", "Hamstrings", "Quads"))
            valid = valid and upper and lower
        if name == "Core + Accessories":
            valid = valid and "Core" in eligible

        score = -999.0
        rotation_penalty = 0.0
        if valid:
            components = []
            for muscle in eligible:
                item = readiness_by_muscle[muscle]
                value = priority.get(muscle, 0.0) + item["readiness_score"] * 0.45
                if item["readiness_state"] == "RECOVERING":
                    value -= RECOVERING_MUSCLE_PENALTY
                components.append(value)
            components.sort(reverse=True)
            score = sum(components[: template["minimum_eligible"]])
            if yesterday_focus == name:
                rotation_penalty += YESTERDAY_FOCUS_PENALTY
            rotation_penalty += repeated.count(name) * REPEATED_FOCUS_PENALTY
            if readiness["selection_confidence"] != "high" and name in repeated:
                rotation_penalty += 20.0
            score -= rotation_penalty

        scored.append({
            "session_type": name,
            "eligible": valid,
            "eligible_muscles": eligible,
            "score": round(score, 1),
            "rotation_penalty": rotation_penalty,
        })

    scored.sort(key=lambda row: row["score"], reverse=True)
    return scored


def _session_from_templates(scores, ranked):
    winner = next((row for row in scores if row["eligible"]), None)
    if not winner:
        return {"session_type": "Active Recovery", "primary_focus": [], "secondary_focus": []}

    rank_order = [row["muscle"] for row in ranked]
    eligible = set(winner["eligible_muscles"])
    ordered = [muscle for muscle in rank_order if muscle in eligible]
    primary_count = min(3, len(ordered))
    primary = ordered[:primary_count]
    secondary = ordered[primary_count:4]
    return {
        "session_type": winner["session_type"],
        "primary_focus": primary,
        "secondary_focus": secondary,
    }


def build_training_priority(now=None) -> dict:

    now = now or datetime.now(timezone.utc)

    analytics = (
        strength_analytics()
    )

    window_7 = (
        analytics[
            "windows"
        ][
            "7"
        ]
    )

    muscles = (
        window_7[
            "muscles"
        ]
    )

    latest_scores = (
        analytics.get(
            "latest_strength_scores"
        )
    )

    imbalance = (
        _strength_imbalance(
            latest_scores
        )
    )

    weakest_region = (
        imbalance.get(
            "weakest_region"
        )
    )

    ranked = []

    for muscle, data in muscles.items():

        priority_score = (
            _muscle_priority_score(
                muscle,
                data,
                weakest_region,
            )
        )

        ranked.append(
            {
                "muscle":
                    muscle,

                "priority_score":
                    priority_score,

                "primary_sessions_7d":
                    data.get(
                        "primary_sessions",
                        0,
                    ),

                "secondary_sessions_7d":
                    data.get(
                        "secondary_sessions",
                        0,
                    ),

                "primary_sets_7d":
                    data.get(
                        "primary_sets",
                        0,
                    ),

                "secondary_sets_7d":
                    data.get(
                        "secondary_sets",
                        0,
                    ),

                "days_since_primary_training":
                    data.get(
                        "days_since_primary_training"
                    ),

                "last_primary_trained_at":
                    data.get(
                        "last_primary_trained_at"
                    ),
            }
        )

    ranked.sort(
        key=lambda item:
            item[
                "priority_score"
            ],
        reverse=True,
    )

    priority_muscles = [
        item
        for item in ranked
        if item[
            "primary_sessions_7d"
        ] < TARGET_FREQUENCY_7D
    ]

    covered_muscles = [
        item
        for item in ranked
        if item[
            "primary_sessions_7d"
        ] >= TARGET_FREQUENCY_7D
    ]

    muscle_readiness = calculate_muscle_readiness(now=now)
    try:
        recommendation_history = _recommendation_history(now)
    except Exception:
        recommendation_history = []
    template_scores = _score_session_templates(
        ranked,
        muscle_readiness,
        recommendation_history,
    )
    session_focus = _session_from_templates(template_scores, ranked)

    rationale = []

    for item in priority_muscles[:5]:

        muscle = (
            item[
                "muscle"
            ]
        )

        primary_sessions = (
            item[
                "primary_sessions_7d"
            ]
        )

        secondary_sessions = (
            item[
                "secondary_sessions_7d"
            ]
        )

        days_since = (
            item[
                "days_since_primary_training"
            ]
        )

        if primary_sessions == 0:

            rationale.append(
                f"{muscle} has no direct "
                "training exposure in the "
                "last 7 days."
            )

        elif primary_sessions == 1:

            rationale.append(
                f"{muscle} has received "
                "1 of the targeted 2 "
                "direct weekly exposures."
            )

        if (
            secondary_sessions > 0
            and primary_sessions < 2
        ):

            rationale.append(
                f"{muscle} has had "
                f"{secondary_sessions} supporting "
                "exposure(s), but these do not "
                "replace direct training."
            )

        if (
            days_since is not None
            and days_since >= 4
        ):

            rationale.append(
                f"{muscle} was last trained "
                f"directly {days_since:.1f} days ago."
            )

    if imbalance.get(
        "available"
    ):

        weakest = (
            imbalance[
                "weakest_region"
            ]
        )

        strongest = (
            imbalance[
                "strongest_region"
            ]
        )

        scores = (
            imbalance[
                "scores"
            ]
        )

        rationale.append(
            f"Tonal Strength Score balance shows "
            f"{weakest} as the lowest region "
            f"({scores[weakest]:.0f}) versus "
            f"{strongest} at "
            f"{scores[strongest]:.0f}."
        )

    return {
        "status":
            "ok",

        "calculated_at":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "target_frequency_7d":
            TARGET_FREQUENCY_7D,

        "strength_scores":
            latest_scores,

        "strength_balance":
            imbalance,

        "recommended_session":
            session_focus,

        "muscle_readiness":
            muscle_readiness,

        "target_muscles":
            session_focus["primary_focus"] + session_focus["secondary_focus"],

        "suppressed_muscles": [
            item for item in muscle_readiness["muscles"]
            if item["readiness_state"] == "SUPPRESSED"
        ],

        "eligible_muscles": [
            item for item in muscle_readiness["muscles"]
            if item["readiness_state"] not in ("SUPPRESSED", "FATIGUED")
        ],

        "session_template_scores":
            template_scores,

        "recent_training_context": {
            "latest_tonal_workout_at": muscle_readiness["latest_tonal_workout_at"],
            "latest_workout_age_hours": muscle_readiness["latest_workout_age_hours"],
            "recommendation_history": recommendation_history,
        },

        "selection_confidence":
            muscle_readiness["selection_confidence"],

        "session_focus_reason": (
            "Highest-scoring coherent template after local muscle suppression, "
            "program-balance priority and recommendation-rotation penalties."
        ),

        "training_focus":
            session_focus[
                "session_type"
            ],

        "priority_muscles":
            priority_muscles[:6],

        "covered_muscles":
            covered_muscles,

        "ranked_muscles":
            ranked,

        "rationale":
            rationale[:10],

        "interpretation_note":
            (
                "Direct Tonal muscle exposure "
                "drives weekly training priority. "
                "Secondary workload is retained as "
                "supporting context but does not "
                "satisfy the 2x/week direct-frequency "
                "goal. Tonal determines what needs "
                "training; WHOOP remains authoritative "
                "for readiness and intensity."
            ),
    }


if __name__ == "__main__":

    print(
        json.dumps(
            build_training_priority(),
            indent=2,
            default=str,
        )
    )
