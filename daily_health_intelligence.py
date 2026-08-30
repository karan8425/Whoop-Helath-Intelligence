import json
import os
import re

from openai import OpenAI

from todays_plan import (
    build_todays_plan,
)
from daily_coaching_summary import build_daily_coaching_summary


SYSTEM_PROMPT = """
You are the explanation and prioritization layer for a private personal
Health Intelligence application.

A deterministic health engine has already produced today's authoritative
plan across training, nutrition, hydration and sleep.

Your job is to explain and prioritize that plan.

Rules:
1. The supplied deterministic plan is authoritative.
2. Do not change training category, exercises, sets, reps, weights, calories,
   macros, hydration target, sleep target or time-in-bed target.
3. Use only the supplied data.
4. Do not invent symptoms, diagnoses, food intake, hydration intake,
   training completion, sleep completion, medications or medical history.
5. Distinguish observation from hypothesis.
6. Do not imply causation from correlation.
7. Personal baselines take priority over population norms.
8. If a field is unavailable, preserve the uncertainty.
9. Do not claim that nutrition, hydration, training or sleep targets were
   achieved unless actual adherence data is supplied.
10. Keep the response concise and decision-oriented.
11. Do not diagnose medical conditions.
12. Return JSON only with exactly the requested keys.
"""


# ============================================================
# OPENAI CLIENT
# ============================================================

def _client():

    api_key = os.getenv(
        "OPENAI_API_KEY",
        "",
    )

    if not api_key:

        raise RuntimeError(
            "OPENAI_API_KEY is not configured."
        )

    return OpenAI(
        api_key=api_key
    )


def _model():

    return os.getenv(
        "OPENAI_MODEL",
        "gpt-5.6-luna",
    )


def _strip_json_fence(
    text,
):

    text = text.strip()

    if text.startswith(
        "```"
    ):

        text = re.sub(
            r"^```(?:json)?\s*",
            "",
            text,
        )

        text = re.sub(
            r"\s*```$",
            "",
            text,
        )

    return text.strip()


# ============================================================
# PAYLOAD
# ============================================================

def build_daily_health_ai_payload():

    plan = (
        build_todays_plan()
    )

    if plan.get(
        "status"
    ) != "ok":

        raise RuntimeError(
            "Today's deterministic health plan "
            "is not ready."
        )

    return {
        "plan_date":
            plan.get(
                "plan_date"
            ),

        "training":
            plan.get(
                "training"
            ),

        "nutrition":
            plan.get(
                "nutrition"
            ),

        "hydration":
            plan.get(
                "hydration"
            ),

        "sleep":
            plan.get(
                "sleep"
            ),

        "available_sections":
            plan.get(
                "available_sections",
                [],
            ),

        "daily_coaching_summary":
            build_daily_coaching_summary(plan),
    }


# ============================================================
# AI SYNTHESIS
# ============================================================

def generate_daily_health_intelligence():

    payload = (
        build_daily_health_ai_payload()
    )

    training = (
        payload.get(
            "training"
        )
        or {}
    )

    nutrition = (
        payload.get(
            "nutrition"
        )
        or {}
    )

    hydration = (
        payload.get(
            "hydration"
        )
        or {}
    )

    sleep = (
        payload.get(
            "sleep"
        )
        or {}
    )

    fixed_training_category = (
        training.get(
            "category"
        )
    )

    fixed_training_session = (
        training.get(
            "session_type"
        )
    )

    fixed_calories = (
        nutrition.get(
            "calories"
        )
    )

    fixed_protein = (
        nutrition.get(
            "protein_g"
        )
    )

    fixed_carbs = (
        nutrition.get(
            "carbs_g"
        )
    )

    fixed_fat = (
        nutrition.get(
            "fat_g"
        )
    )

    fixed_hydration = (
        hydration.get(
            "daily_target_display"
        )
    )

    fixed_sleep_target = (
        sleep.get(
            "sleep_target_display"
        )
    )

    fixed_time_in_bed = (
        sleep.get(
            "time_in_bed_target_display"
        )
    )

    user_prompt = f"""
Create today's complete health intelligence brief from the structured plan below.

These deterministic values MUST remain unchanged:

Training category:
{fixed_training_category}

Training session:
{fixed_training_session}

Calories:
{fixed_calories}

Protein:
{fixed_protein} g

Carbs:
{fixed_carbs} g

Fat:
{fixed_fat} g

Hydration:
{fixed_hydration}

Sleep target:
{fixed_sleep_target}

Time in bed target:
{fixed_time_in_bed}

Return one JSON object with exactly these keys:

{{
  "date": "YYYY-MM-DD",
  "headline": "short overall status",
  "today_summary": "2-4 concise sentences",
  "training": {{
    "category": "{fixed_training_category}",
    "session": "{fixed_training_session}",
    "instruction": "one concise training instruction"
  }},
  "nutrition": {{
    "calories": {fixed_calories},
    "protein_g": {fixed_protein},
    "carbs_g": {fixed_carbs},
    "fat_g": {fixed_fat},
    "instruction": "one concise nutrition instruction"
  }},
  "hydration": {{
    "target": "{fixed_hydration}",
    "instruction": "one concise hydration instruction"
  }},
  "sleep": {{
    "sleep_target": "{fixed_sleep_target}",
    "time_in_bed_target": "{fixed_time_in_bed}",
    "instruction": "one concise sleep instruction"
  }},
  "why_it_matters": [
    "up to 4 evidence-based observations"
  ],
  "highest_impact_action": "one concrete highest-priority action",
  "trend_to_watch": "one meaningful trend or limitation",
  "confidence": "high|moderate|low",
  "uncertainty_note": "brief explanation of current limitations",
  "medical_safety_note": "brief wearable-data safety note"
}}

Important:
- Do not add markdown.
- Do not add any keys.
- Do not change any deterministic target.
- Do not claim the user completed any target.
- Do not treat configured nutrition or hydration targets as actual intake.
- Do not treat prescribed training as completed training.
- Do not infer muscle gain, fat loss or weight loss unless the supplied trend data supports it.
- Use the supplied sleep trend exactly as context rather than inventing sleep debt.
- If bedtime is unavailable, do not invent one.
- Keep the briefing practical and concise.

DATA:
{json.dumps(payload, default=str)}
"""

    client = (
        _client()
    )

    response = (
        client.responses.create(
            model=_model(),
            instructions=SYSTEM_PROMPT,
            input=user_prompt,
        )
    )

    raw = (
        _strip_json_fence(
            response.output_text
        )
    )

    try:

        parsed = (
            json.loads(
                raw
            )
        )

    except json.JSONDecodeError as exc:

        raise RuntimeError(
            "OpenAI returned invalid JSON: "
            f"{raw[:500]}"
        ) from exc

    # ========================================================
    # HARD CONSISTENCY GUARDRAILS
    # ========================================================

    parsed[
        "date"
    ] = payload.get(
        "plan_date"
    )

    parsed[
        "training"
    ][
        "category"
    ] = fixed_training_category

    parsed[
        "training"
    ][
        "session"
    ] = fixed_training_session

    parsed[
        "nutrition"
    ][
        "calories"
    ] = fixed_calories

    parsed[
        "nutrition"
    ][
        "protein_g"
    ] = fixed_protein

    parsed[
        "nutrition"
    ][
        "carbs_g"
    ] = fixed_carbs

    parsed[
        "nutrition"
    ][
        "fat_g"
    ] = fixed_fat

    parsed[
        "hydration"
    ][
        "target"
    ] = fixed_hydration

    parsed[
        "sleep"
    ][
        "sleep_target"
    ] = fixed_sleep_target

    parsed[
        "sleep"
    ][
        "time_in_bed_target"
    ] = fixed_time_in_bed

    required = {
        "date",
        "headline",
        "today_summary",
        "training",
        "nutrition",
        "hydration",
        "sleep",
        "why_it_matters",
        "highest_impact_action",
        "trend_to_watch",
        "confidence",
        "uncertainty_note",
        "medical_safety_note",
    }

    missing = (
        required
        - set(
            parsed.keys()
        )
    )

    if missing:

        raise RuntimeError(
            "Daily health intelligence JSON "
            "is missing required fields: "
            + ", ".join(
                sorted(
                    missing
                )
            )
        )

    return {
        "status":
            "ok",

        "model":
            _model(),

        "plan_date":
            payload.get(
                "plan_date"
            ),

        "brief":
            parsed,

        "daily_coaching_summary":
            payload.get("daily_coaching_summary"),
    }


# ============================================================
# MOCK GENERATOR FOR LOCAL VALIDATION
# ============================================================

def _mock_daily_health_intelligence():

    payload = (
        build_daily_health_ai_payload()
    )

    training = (
        payload.get(
            "training"
        )
        or {}
    )

    nutrition = (
        payload.get(
            "nutrition"
        )
        or {}
    )

    hydration = (
        payload.get(
            "hydration"
        )
        or {}
    )

    sleep = (
        payload.get(
            "sleep"
        )
        or {}
    )

    return {
        "status":
            "ok",

        "model":
            "local-health-intelligence-test",

        "plan_date":
            payload.get(
                "plan_date"
            ),

        "brief": {

            "date":
                payload.get(
                    "plan_date"
                ),

            "headline":
                "Daily health intelligence validation",

            "today_summary":
                (
                    "This is a local validation response. "
                    "No OpenAI request was made."
                ),

            "training": {
                "category":
                    training.get(
                        "category"
                    ),

                "session":
                    training.get(
                        "session_type"
                    ),

                "instruction":
                    (
                        "Follow the deterministic training plan."
                    ),
            },

            "nutrition": {
                "calories":
                    nutrition.get(
                        "calories"
                    ),

                "protein_g":
                    nutrition.get(
                        "protein_g"
                    ),

                "carbs_g":
                    nutrition.get(
                        "carbs_g"
                    ),

                "fat_g":
                    nutrition.get(
                        "fat_g"
                    ),

                "instruction":
                    (
                        "Follow the deterministic nutrition targets."
                    ),
            },

            "hydration": {
                "target":
                    hydration.get(
                        "daily_target_display"
                    ),

                "instruction":
                    (
                        "Spread hydration throughout the day."
                    ),
            },

            "sleep": {
                "sleep_target":
                    sleep.get(
                        "sleep_target_display"
                    ),

                "time_in_bed_target":
                    sleep.get(
                        "time_in_bed_target_display"
                    ),

                "instruction":
                    (
                        "Protect tonight's sleep opportunity."
                    ),
            },

            "why_it_matters": [
                (
                    "This validates the combined deterministic "
                    "health-intelligence contract."
                )
            ],

            "highest_impact_action":
                (
                    "Follow the highest-priority deterministic plan."
                ),

            "trend_to_watch":
                (
                    sleep.get(
                        "trend_summary"
                    )
                    or
                    "No major warning trend."
                ),

            "confidence":
                "high",

            "uncertainty_note":
                (
                    "This is a local validation response."
                ),

            "medical_safety_note":
                (
                    "Wearable guidance is informational and "
                    "is not a medical diagnosis."
                ),
        },

        "daily_coaching_summary":
            payload.get("daily_coaching_summary"),
    }


# ============================================================
# LOCAL VALIDATION
# ============================================================

def validate_daily_health_intelligence():

    result = (
        _mock_daily_health_intelligence()
    )

    brief = (
        result.get(
            "brief"
        )
        or {}
    )

    training = (
        brief.get(
            "training"
        )
        or {}
    )

    nutrition = (
        brief.get(
            "nutrition"
        )
        or {}
    )

    hydration = (
        brief.get(
            "hydration"
        )
        or {}
    )

    sleep = (
        brief.get(
            "sleep"
        )
        or {}
    )

    checks = {

        "status_ok":
            result.get(
                "status"
            )
            == "ok",

        "brief_present":
            isinstance(
                brief,
                dict,
            ),

        "training_category_present":
            bool(
                training.get(
                    "category"
                )
            ),

        "training_session_present":
            bool(
                training.get(
                    "session"
                )
            ),

        "nutrition_calories_present":
            nutrition.get(
                "calories"
            )
            is not None,

        "protein_present":
            nutrition.get(
                "protein_g"
            )
            is not None,

        "carbs_present":
            nutrition.get(
                "carbs_g"
            )
            is not None,

        "fat_present":
            nutrition.get(
                "fat_g"
            )
            is not None,

        "hydration_present":
            bool(
                hydration.get(
                    "target"
                )
            ),

        "sleep_target_present":
            bool(
                sleep.get(
                    "sleep_target"
                )
            ),

        "time_in_bed_present":
            bool(
                sleep.get(
                    "time_in_bed_target"
                )
            ),

        "headline_present":
            bool(
                brief.get(
                    "headline"
                )
            ),

        "action_present":
            bool(
                brief.get(
                    "highest_impact_action"
                )
            ),

        "mock_model_preserved":
            result.get(
                "model"
            )
            == "local-health-intelligence-test",
    }

    return {
        "status":
            (
                "ok"
                if all(
                    checks.values()
                )
                else "check_failed"
            ),

        "checks":
            checks,

        "model":
            result.get(
                "model"
            ),

        "openai_called":
            False,

        "brief":
            brief,
    }


# ============================================================
# TERMINAL TEST
# ============================================================

def main():

    result = (
        validate_daily_health_intelligence()
    )

    print()

    print(
        "DAILY HEALTH INTELLIGENCE VALIDATION"
    )

    print(
        "=" * 78
    )

    print(
        json.dumps(
            result,
            indent=2,
            default=str,
        )
    )

    print(
        "=" * 78
    )


if __name__ == "__main__":
    main()
