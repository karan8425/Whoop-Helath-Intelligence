import json
import os
import re

from openai import OpenAI

from weekly_analytics import (
    weekly_health_summary,
)


SYSTEM_PROMPT = """
You are the explanation and prioritization layer for a private personal
Health Intelligence application.

A deterministic weekly analytics engine has already classified the user's
physiology, training load, strength adherence, activity adherence and
body-composition context.

Your job is to explain what happened this week, what likely drove it,
what is improving or limiting progress, and what should change next week.

Rules:
1. The supplied deterministic weekly analytics are authoritative.
2. Do not change any deterministic classification.
3. Do not recalculate metrics or replace supplied values.
4. Use only the supplied data.
5. Distinguish observation, correlation and hypothesis.
6. Do not imply causation from correlation.
7. Personal baselines take priority over population norms.
8. Lower training load is contextual, not automatically favorable or unfavorable.
9. Do not say the user did not train if WHOOP workouts occurred.
10. Strength adherence refers only to Tonal workouts eligible under the
    strength-analysis rules.
11. Do not treat excluded Tonal workouts as qualifying strength sessions.
12. Do not claim body-composition improvement or regression when the supplied
    body-composition status is immature or insufficient.
13. Do not treat configured goals as completed behavior.
14. Do not invent nutrition intake, exercise completion, hydration intake,
    symptoms, diagnoses, medications or medical history.
15. If evidence is insufficient, preserve the uncertainty.
16. Do not diagnose medical conditions.
17. Keep the response concise, practical and decision-oriented.
18. Return JSON only with exactly the requested keys.
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

def build_weekly_health_ai_payload():

    weekly = (
        weekly_health_summary()
    )

    if weekly.get(
        "status"
    ) != "ok":

        raise RuntimeError(
            "Weekly deterministic health analytics "
            "are not ready."
        )

    return {
        "metric_date":
            weekly.get(
                "metric_date"
            ),

        "period":
            weekly.get(
                "period"
            ),

        "overall_trajectory":
            weekly.get(
                "overall_trajectory"
            ),

        "physiology_trajectory":
            weekly.get(
                "physiology_trajectory"
            ),

        "training_context":
            weekly.get(
                "training_context"
            ),

        "strength_adherence":
            weekly.get(
                "strength_adherence"
            ),

        "activity_adherence":
            weekly.get(
                "activity_adherence"
            ),

        "goal_adherence":
            weekly.get(
                "goal_adherence"
            ),

        "body_composition_context":
            weekly.get(
                "body_composition_context"
            ),

        "goal_context":
            weekly.get(
                "goal_context"
            ),

        "key_signals":
            weekly.get(
                "key_signals"
            ),

        "current_week":
            weekly.get(
                "current_week"
            ),

        "previous_week":
            weekly.get(
                "previous_week"
            ),

        "baseline_30":
            weekly.get(
                "baseline_30"
            ),

        "baseline_90":
            weekly.get(
                "baseline_90"
            ),

        "metric_comparisons":
            weekly.get(
                "metric_comparisons"
            ),
    }


# ============================================================
# AI SYNTHESIS
# ============================================================

def generate_weekly_health_intelligence():

    payload = (
        build_weekly_health_ai_payload()
    )

    overall = (
        payload.get(
            "overall_trajectory"
        )
        or {}
    )

    physiology = (
        payload.get(
            "physiology_trajectory"
        )
        or {}
    )

    training = (
        payload.get(
            "training_context"
        )
        or {}
    )

    strength = (
        payload.get(
            "strength_adherence"
        )
        or {}
    )

    activity = (
        payload.get(
            "activity_adherence"
        )
        or {}
    )

    body = (
        payload.get(
            "body_composition_context"
        )
        or {}
    )

    fixed_overall = (
        overall.get(
            "trajectory"
        )
    )

    fixed_physiology = (
        physiology.get(
            "trajectory"
        )
    )

    fixed_training = (
        training.get(
            "load_status"
        )
    )

    fixed_strength = (
        strength.get(
            "status"
        )
    )

    fixed_activity = (
        activity.get(
            "status"
        )
    )

    fixed_body = (
        body.get(
            "status"
        )
    )

    user_prompt = f"""
Create a weekly health intelligence brief from the deterministic analytics below.

These classifications MUST remain unchanged:

Overall trajectory:
{fixed_overall}

Physiology trajectory:
{fixed_physiology}

Training load:
{fixed_training}

Strength adherence:
{fixed_strength}

Activity adherence:
{fixed_activity}

Body-composition status:
{fixed_body}

Return one JSON object with exactly these keys:

{{
  "period_end_date": "YYYY-MM-DD",

  "headline": "short overall weekly assessment",

  "weekly_summary": "2-4 concise sentences explaining the week",

  "status": {{
    "overall": "{fixed_overall}",
    "physiology": "{fixed_physiology}",
    "training_load": "{fixed_training}",
    "strength_adherence": "{fixed_strength}",
    "activity_adherence": "{fixed_activity}",
    "body_composition": "{fixed_body}"
  }},

  "what_improved": [
    "up to 3 evidence-based improvements"
  ],

  "what_limited_progress": [
    "up to 3 evidence-based constraints"
  ],

  "likely_drivers": [
    {{
      "observation": "measured observation",
      "hypothesis": "carefully worded possible explanation"
    }}
  ],

  "strength_review": "brief interpretation of Tonal strength adherence",

  "activity_review": "brief interpretation of step adherence",

  "body_composition_review": "brief interpretation preserving maturity limits",

  "next_week_priority": "single highest-priority adjustment",

  "next_week_actions": [
    "2-4 concrete actions"
  ],

  "trend_to_watch": "one important trend to monitor next week",

  "confidence": "high|moderate|low",

  "uncertainty_note": "brief explanation of limitations",

  "medical_safety_note": "brief wearable-data safety note"
}}

Important:
- Do not add markdown.
- Do not add extra keys.
- Do not change any supplied classification.
- Do not describe a lower WHOOP training-load week as automatically good or bad.
- Do not say the user did no training if WHOOP recorded workouts.
- If Tonal strength adherence is below target because qualifying sessions are zero,
  describe this specifically as qualifying strength-session adherence.
- Do not count explicitly excluded Tonal workouts as strength-goal completion.
- Do not claim body-fat loss, weight loss or muscle gain if body-composition status
  is immature or insufficient.
- If physiology improved while training load fell materially, explain that the
  relationship is observational and that reduced training stress is a plausible
  contributor, not proven causation.
- Prioritize one main adjustment rather than producing a long checklist.
- Keep the output concise and practical.

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
    # REQUIRED STRUCTURE
    # ========================================================

    required = {
        "period_end_date",
        "headline",
        "weekly_summary",
        "status",
        "what_improved",
        "what_limited_progress",
        "likely_drivers",
        "strength_review",
        "activity_review",
        "body_composition_review",
        "next_week_priority",
        "next_week_actions",
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
            "Weekly health intelligence JSON "
            "is missing required fields: "
            + ", ".join(
                sorted(
                    missing
                )
            )
        )

    status = (
        parsed.get(
            "status"
        )
    )

    if not isinstance(
        status,
        dict,
    ):

        raise RuntimeError(
            "Weekly health intelligence status "
            "must be an object."
        )

    # ========================================================
    # HARD CONSISTENCY GUARDRAILS
    # ========================================================

    parsed[
        "period_end_date"
    ] = payload.get(
        "metric_date"
    )

    parsed[
        "status"
    ][
        "overall"
    ] = fixed_overall

    parsed[
        "status"
    ][
        "physiology"
    ] = fixed_physiology

    parsed[
        "status"
    ][
        "training_load"
    ] = fixed_training

    parsed[
        "status"
    ][
        "strength_adherence"
    ] = fixed_strength

    parsed[
        "status"
    ][
        "activity_adherence"
    ] = fixed_activity

    parsed[
        "status"
    ][
        "body_composition"
    ] = fixed_body

    return {
        "status":
            "ok",

        "model":
            _model(),

        "period_end_date":
            payload.get(
                "metric_date"
            ),

        "brief":
            parsed,
    }


# ============================================================
# MOCK GENERATOR FOR LOCAL STRUCTURE VALIDATION
# ============================================================

def _mock_weekly_health_intelligence():

    payload = (
        build_weekly_health_ai_payload()
    )

    overall = (
        payload.get(
            "overall_trajectory"
        )
        or {}
    )

    physiology = (
        payload.get(
            "physiology_trajectory"
        )
        or {}
    )

    training = (
        payload.get(
            "training_context"
        )
        or {}
    )

    strength = (
        payload.get(
            "strength_adherence"
        )
        or {}
    )

    activity = (
        payload.get(
            "activity_adherence"
        )
        or {}
    )

    body = (
        payload.get(
            "body_composition_context"
        )
        or {}
    )

    return {
        "status":
            "ok",

        "model":
            "local-weekly-health-intelligence-test",

        "period_end_date":
            payload.get(
                "metric_date"
            ),

        "brief": {

            "period_end_date":
                payload.get(
                    "metric_date"
                ),

            "headline":
                "Weekly health intelligence validation",

            "weekly_summary":
                (
                    "This is a structural validation response. "
                    "No OpenAI request was made."
                ),

            "status": {
                "overall":
                    overall.get(
                        "trajectory"
                    ),

                "physiology":
                    physiology.get(
                        "trajectory"
                    ),

                "training_load":
                    training.get(
                        "load_status"
                    ),

                "strength_adherence":
                    strength.get(
                        "status"
                    ),

                "activity_adherence":
                    activity.get(
                        "status"
                    ),

                "body_composition":
                    body.get(
                        "status"
                    ),
            },

            "what_improved": [
                (
                    "Validated deterministic weekly "
                    "physiology structure."
                )
            ],

            "what_limited_progress": [
                (
                    "Validated deterministic weekly "
                    "goal-adherence structure."
                )
            ],

            "likely_drivers": [
                {
                    "observation":
                        (
                            "Weekly deterministic analytics "
                            "were available."
                        ),

                    "hypothesis":
                        (
                            "No causal inference is made "
                            "during local validation."
                        ),
                }
            ],

            "strength_review":
                (
                    "Follow the deterministic Tonal "
                    "strength-adherence result."
                ),

            "activity_review":
                (
                    "Follow the deterministic Apple Health "
                    "activity-adherence result."
                ),

            "body_composition_review":
                (
                    "Follow the deterministic "
                    "body-composition maturity guardrail."
                ),

            "next_week_priority":
                (
                    "Follow the highest-priority deterministic "
                    "weekly adjustment."
                ),

            "next_week_actions": [
                (
                    "Use the validated weekly analytics "
                    "to guide the next planning period."
                )
            ],

            "trend_to_watch":
                (
                    "Monitor the primary deterministic "
                    "weekly trend."
                ),

            "confidence":
                "high",

            "uncertainty_note":
                (
                    "This is a local structural validation "
                    "response."
                ),

            "medical_safety_note":
                (
                    "Wearable guidance is informational and "
                    "is not a medical diagnosis."
                ),
        },
    }


# ============================================================
# LOCAL VALIDATION
# ============================================================

def validate_weekly_health_intelligence():

    result = (
        _mock_weekly_health_intelligence()
    )

    brief = (
        result.get(
            "brief"
        )
        or {}
    )

    status = (
        brief.get(
            "status"
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

        "headline_present":
            bool(
                brief.get(
                    "headline"
                )
            ),

        "weekly_summary_present":
            bool(
                brief.get(
                    "weekly_summary"
                )
            ),

        "overall_status_present":
            bool(
                status.get(
                    "overall"
                )
            ),

        "physiology_status_present":
            bool(
                status.get(
                    "physiology"
                )
            ),

        "training_status_present":
            bool(
                status.get(
                    "training_load"
                )
            ),

        "strength_status_present":
            bool(
                status.get(
                    "strength_adherence"
                )
            ),

        "activity_status_present":
            bool(
                status.get(
                    "activity_adherence"
                )
            ),

        "body_status_present":
            bool(
                status.get(
                    "body_composition"
                )
            ),

        "priority_present":
            bool(
                brief.get(
                    "next_week_priority"
                )
            ),

        "actions_present":
            isinstance(
                brief.get(
                    "next_week_actions"
                ),
                list,
            ),

        "drivers_present":
            isinstance(
                brief.get(
                    "likely_drivers"
                ),
                list,
            ),

        "mock_model_preserved":
            result.get(
                "model"
            )
            == "local-weekly-health-intelligence-test",
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
        validate_weekly_health_intelligence()
    )

    print()

    print(
        "WEEKLY HEALTH INTELLIGENCE VALIDATION"
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