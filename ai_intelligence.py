import json
import os
import re

from openai import OpenAI

from recommendations import daily_recommendation
from trends import latest_signals
from combined_coaching import combined_deterministic_coaching
from goal_progress import goal_progress


SYSTEM_PROMPT = """
You are the explanation layer for a private personal WHOOP Health Intelligence application.

The application already has deterministic analytics and a deterministic training recommendation.
Your job is to explain and prioritize that output. You MUST NOT change the deterministic
training recommendation.

Rules:
1. Treat the provided deterministic recommendation as authoritative for the training category.
2. Use only the supplied data. Do not invent symptoms, nutrition intake, training plans, diagnoses,
   medical history, causes, or missing measurements.
3. Distinguish observation from hypothesis. Never claim causation from correlation.
4. Prioritize personal baselines over population norms.
5. Mention uncertainty when data coverage is incomplete.
6. Keep the daily brief concise and practical.
7. Do not diagnose medical conditions.
8. If symptoms, illness, injury, medication changes, or clinician advice are relevant, state that
   they should override wearable-based training guidance.
9. Return JSON only, matching the requested object keys exactly.
"""


COMBINED_SYSTEM_PROMPT = """
You are the explanation layer for a private personal Health Intelligence application.

The application combines:
1. WHOOP physiology and recovery
2. Hume body-composition data through Apple Health
3. Apple Health activity data
4. A persistent personal fitness goal and phase
5. A deterministic goal-progress engine

A deterministic engine has already decided the training recommendation.
You MUST NOT change that training recommendation.

Rules:
1. Treat the deterministic training recommendation as authoritative.
2. WHOOP is authoritative for recovery, HRV, resting heart rate, sleep, strain and readiness.
3. Hume and Apple Health add body-composition and activity context.
4. The active fitness phase and goal-progress engine provide the objective context for whether
   current behaviors and body-composition data are moving toward the configured goal.
5. Use only the supplied data.
6. Do not infer body-composition trends from a single measurement.
7. Do not claim weight loss, fat loss, muscle gain, or muscle loss unless the supplied trend data
   explicitly supports that statement.
8. If the goal-progress status is insufficient_data, building baseline, or otherwise uncertain,
   explicitly preserve that uncertainty. Do not call the phase on-track or off-track.
9. If lean body mass is excluded or stale, do not use it to make conclusions.
10. If strength or nutrition tracking is marked not_connected, do not claim adherence to those goals.
11. A configured protein target is a target only. It is not evidence of actual protein intake.
12. Personal baselines take priority over population norms.
13. Distinguish observations from hypotheses and never imply causation.
14. Keep the briefing concise and decision-oriented.
15. Do not diagnose medical conditions.
16. Return JSON only with exactly the requested keys.
"""


def _client():
    api_key = os.getenv(
        "OPENAI_API_KEY",
        ""
    )

    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not configured in Render. "
            "Add it as a secret environment variable."
        )

    return OpenAI(
        api_key=api_key
    )


def _model():
    return os.getenv(
        "OPENAI_MODEL",
        "gpt-5.6-luna"
    )


def _strip_json_fence(text):
    text = text.strip()

    if text.startswith("```"):

        text = re.sub(
            r"^```(?:json)?\s*",
            "",
            text
        )

        text = re.sub(
            r"\s*```$",
            "",
            text
        )

    return text.strip()


# ============================================================
# WHOOP-ONLY AI BRIEF
# ============================================================

def build_ai_payload():
    recommendation = daily_recommendation()
    signals = latest_signals()

    return {
        "metric_date":
            recommendation.get(
                "metric_date"
            ),

        "deterministic_recommendation": {
            "training_recommendation":
                recommendation.get(
                    "training_recommendation"
                ),

            "overall_status":
                recommendation.get(
                    "overall_status"
                ),

            "confidence":
                recommendation.get(
                    "confidence"
                ),

            "reasons":
                recommendation.get(
                    "reasons",
                    []
                ),

            "recovery_priorities":
                recommendation.get(
                    "recovery_priorities",
                    []
                ),

            "highest_impact_actions":
                recommendation.get(
                    "highest_impact_actions",
                    []
                ),
        },

        "domains":
            signals.get(
                "domains",
                {}
            ),

        "signals": [
            {
                "metric_name":
                    x.get(
                        "metric_name"
                    ),

                "current_value":
                    x.get(
                        "current_value"
                    ),

                "baseline_7":
                    x.get(
                        "baseline_7"
                    ),

                "pct_vs_7":
                    x.get(
                        "pct_vs_7"
                    ),

                "baseline_30":
                    x.get(
                        "baseline_30"
                    ),

                "pct_vs_30":
                    x.get(
                        "pct_vs_30"
                    ),

                "baseline_90":
                    x.get(
                        "baseline_90"
                    ),

                "pct_vs_90":
                    x.get(
                        "pct_vs_90"
                    ),

                "directional_signal":
                    x.get(
                        "directional_signal"
                    ),

                "trend":
                    x.get(
                        "trend"
                    ),

                "coverage_30_percentage":
                    x.get(
                        "coverage_30_percentage"
                    ),

                "coverage_90_percentage":
                    x.get(
                        "coverage_90_percentage"
                    ),

                "confidence":
                    x.get(
                        "confidence"
                    ),
            }
            for x in signals.get(
                "signals",
                []
            )
        ],
    }


def generate_daily_ai_brief():
    payload = build_ai_payload()

    fixed_training = (
        payload[
            "deterministic_recommendation"
        ][
            "training_recommendation"
        ]
    )

    user_prompt = f"""
Create today's health intelligence briefing from the structured data below.

The training recommendation MUST remain exactly:
{fixed_training}

Return one JSON object with exactly these keys:

{{
  "date": "YYYY-MM-DD",
  "headline": "short overall status",
  "training_recommendation": "{fixed_training}",
  "today_summary": "2-4 concise sentences",
  "why_it_matters": [
    "up to 3 evidence-based observations"
  ],
  "recovery_priority": "one concise priority",
  "highest_impact_action": "one concrete action",
  "trend_to_watch": "one meaningful trend or 'No major warning trend'",
  "confidence": "high|moderate|low",
  "uncertainty_note": "brief explanation of data limitations",
  "medical_safety_note": "brief wearable-data safety note"
}}

Do not add markdown.
Do not add any keys.
Do not change the training recommendation.

DATA:
{json.dumps(payload, default=str)}
"""

    client = _client()

    response = client.responses.create(
        model=_model(),
        instructions=SYSTEM_PROMPT,
        input=user_prompt,
    )

    raw = _strip_json_fence(
        response.output_text
    )

    try:
        parsed = json.loads(
            raw
        )

    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "OpenAI returned output "
            f"that was not valid JSON: {raw[:500]}"
        ) from exc

    parsed[
        "training_recommendation"
    ] = fixed_training

    parsed[
        "date"
    ] = payload.get(
        "metric_date"
    )

    required = {
        "date",
        "headline",
        "training_recommendation",
        "today_summary",
        "why_it_matters",
        "recovery_priority",
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
            "OpenAI JSON is missing required fields: "
            + ", ".join(
                sorted(
                    missing
                )
            )
        )

    return {
        "model":
            _model(),

        "deterministic_training_recommendation":
            fixed_training,

        "brief":
            parsed,
    }


def validate_ai_connection():
    result = generate_daily_ai_brief()

    checks = {
        "model_present":
            bool(
                result.get(
                    "model"
                )
            ),

        "deterministic_recommendation_present":
            bool(
                result.get(
                    "deterministic_training_recommendation"
                )
            ),

        "brief_present":
            isinstance(
                result.get(
                    "brief"
                ),
                dict
            ),

        "recommendation_preserved":
            (
                result[
                    "brief"
                ].get(
                    "training_recommendation"
                )
                ==
                result.get(
                    "deterministic_training_recommendation"
                )
            ),

        "headline_present":
            bool(
                result[
                    "brief"
                ].get(
                    "headline"
                )
            ),

        "summary_present":
            bool(
                result[
                    "brief"
                ].get(
                    "today_summary"
                )
            ),

        "action_present":
            bool(
                result[
                    "brief"
                ].get(
                    "highest_impact_action"
                )
            ),

        "safety_note_present":
            bool(
                result[
                    "brief"
                ].get(
                    "medical_safety_note"
                )
            ),
    }

    return {
        "status":
            "ok"
            if all(
                checks.values()
            )
            else "check_failed",

        "checks":
            checks,

        **result,
    }


# ============================================================
# GOAL-AWARE COMBINED AI BRIEF
# ============================================================

def build_combined_ai_payload():

    combined = (
        combined_deterministic_coaching()
    )

    if combined.get(
        "status"
    ) != "ok":

        raise RuntimeError(
            "Combined deterministic coaching "
            "is not ready: "
            + combined.get(
                "message",
                "Unknown reason"
            )
        )

    progress = (
        goal_progress()
    )

    if progress.get(
        "status"
    ) not in (
        "ok",
        "no_active_goal",
    ):
        raise RuntimeError(
            "Goal progress engine returned "
            "an unexpected status: "
            + str(
                progress.get(
                    "status"
                )
            )
        )

    return {
        "coaching_date":
            combined.get(
                "coaching_date"
            ),

        "deterministic_training_recommendation":
            combined.get(
                "training_recommendation"
            ),

        "overall_status":
            combined.get(
                "overall_status"
            ),

        "confidence":
            combined.get(
                "confidence"
            ),

        "physiology_reasons":
            combined.get(
                "physiology_reasons",
                []
            ),

        "activity_guidance":
            combined.get(
                "activity_guidance",
                {}
            ),

        "body_composition_context":
            combined.get(
                "body_composition_context",
                []
            ),

        "highest_impact_actions":
            combined.get(
                "highest_impact_actions",
                []
            ),

        "data_readiness":
            combined.get(
                "data_readiness",
                {}
            ),

        "interpretation_note":
            combined.get(
                "interpretation_note"
            ),

        "goal_progress": {
            "status":
                progress.get(
                    "status"
                ),

            "phase":
                progress.get(
                    "phase"
                ),

            "direction":
                progress.get(
                    "direction"
                ),

            "phase_start_date":
                progress.get(
                    "phase_start_date"
                ),

            "phase_age_days":
                progress.get(
                    "phase_age_days"
                ),

            "minimum_phase_age_days":
                progress.get(
                    "minimum_phase_age_days"
                ),

            "body_fat":
                progress.get(
                    "body_fat"
                ),

            "weight":
                progress.get(
                    "weight"
                ),

            "activity":
                progress.get(
                    "activity"
                ),

            "strength":
                progress.get(
                    "strength"
                ),

            "protein":
                progress.get(
                    "protein"
                ),

            "summary":
                progress.get(
                    "summary"
                ),
        },
    }


def generate_combined_ai_brief():

    payload = (
        build_combined_ai_payload()
    )

    fixed_training = (
        payload[
            "deterministic_training_recommendation"
        ]
    )

    goal = (
        payload.get(
            "goal_progress"
        )
        or {}
    )

    phase = (
        goal.get(
            "phase"
        )
        or "not configured"
    )

    direction = (
        goal.get(
            "direction"
        )
        or "unknown"
    )

    user_prompt = f"""
Create today's combined health and fitness coaching brief.

The training recommendation MUST remain exactly:
{fixed_training}

The active fitness phase is:
{phase}

The deterministic goal-progress direction is:
{direction}

Return one JSON object with exactly these keys:

{{
  "date": "YYYY-MM-DD",
  "headline": "short overall status",
  "training_recommendation": "{fixed_training}",
  "today_summary": "2-4 concise sentences",
  "training_focus": "one concise training instruction",
  "activity_priority": "one concise activity instruction",
  "body_composition_context": "one concise evidence-based statement",
  "goal_context": "one concise statement describing how today's recommendation supports the active goal",
  "why_it_matters": [
    "up to 4 evidence-based observations"
  ],
  "highest_impact_action": "one concrete highest-priority action",
  "trend_to_watch": "one meaningful trend or limitation",
  "confidence": "high|moderate|low",
  "uncertainty_note": "brief explanation of current data limitations",
  "medical_safety_note": "brief wearable-data safety note"
}}

Important:
- Do not add markdown.
- Do not add any keys.
- Do not change the training recommendation.
- Explicitly connect today's recommendation to the active goal phase when a goal is configured.
- The goal-progress engine is authoritative for the direction of goal progress.
- If goal direction is "insufficient_data", do NOT say the user is on-track or off-track.
- If the phase is "lean_cut", prioritize preservation of training quality, reasonable activity consistency,
  and body-fat progress. Do not recommend extra training volume merely to burn calories.
- If the phase is "maintenance", prioritize consistency and stability.
- If the phase is "lean_bulk", prioritize progressive resistance training and controlled progress rather
  than maximizing calorie expenditure.
- A protein target is only a configured target. If protein status is "not_connected", do not claim actual
  protein intake or adherence.
- A strength-session target is only a configured target. If strength status is "not_connected", do not claim
  that the target has or has not been achieved.
- Do not describe today's weight or body-fat measurement as a trend.
- Do not claim fat loss or weight loss unless the supplied goal-progress/trend data supports it.
- If lean mass is unavailable or excluded, do not infer muscle gain or loss.
- Activity guidance should account for time-of-day information already embedded in deterministic data.
- Use WHOOP physiology as the primary basis for training readiness.

DATA:
{json.dumps(payload, default=str)}
"""

    client = _client()

    response = (
        client.responses.create(
            model=_model(),
            instructions=
                COMBINED_SYSTEM_PROMPT,
            input=user_prompt,
        )
    )

    raw = _strip_json_fence(
        response.output_text
    )

    try:
        parsed = json.loads(
            raw
        )

    except json.JSONDecodeError as exc:

        raise RuntimeError(
            "OpenAI returned combined output "
            "that was not valid JSON: "
            f"{raw[:500]}"
        ) from exc

    # --------------------------------------------------------
    # Hard consistency guardrails
    # --------------------------------------------------------

    parsed[
        "training_recommendation"
    ] = fixed_training

    parsed[
        "date"
    ] = payload.get(
        "coaching_date"
    )

    required = {
        "date",
        "headline",
        "training_recommendation",
        "today_summary",
        "training_focus",
        "activity_priority",
        "body_composition_context",
        "goal_context",
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
            "OpenAI combined JSON "
            "is missing required fields: "
            + ", ".join(
                sorted(
                    missing
                )
            )
        )

    return {
        "model":
            _model(),

        "deterministic_training_recommendation":
            fixed_training,

        "goal_progress_direction":
            goal.get(
                "direction"
            ),

        "active_phase":
            goal.get(
                "phase"
            ),

        "brief":
            parsed,
    }


def validate_combined_ai_connection():

    result = (
        generate_combined_ai_brief()
    )

    checks = {
        "model_present":
            bool(
                result.get(
                    "model"
                )
            ),

        "deterministic_recommendation_present":
            bool(
                result.get(
                    "deterministic_training_recommendation"
                )
            ),

        "brief_present":
            isinstance(
                result.get(
                    "brief"
                ),
                dict
            ),

        "recommendation_preserved":
            (
                result[
                    "brief"
                ].get(
                    "training_recommendation"
                )
                ==
                result.get(
                    "deterministic_training_recommendation"
                )
            ),

        "active_phase_present":
            bool(
                result.get(
                    "active_phase"
                )
            ),

        "goal_direction_present":
            bool(
                result.get(
                    "goal_progress_direction"
                )
            ),

        "headline_present":
            bool(
                result[
                    "brief"
                ].get(
                    "headline"
                )
            ),

        "training_focus_present":
            bool(
                result[
                    "brief"
                ].get(
                    "training_focus"
                )
            ),

        "activity_priority_present":
            bool(
                result[
                    "brief"
                ].get(
                    "activity_priority"
                )
            ),

        "body_context_present":
            bool(
                result[
                    "brief"
                ].get(
                    "body_composition_context"
                )
            ),

        "goal_context_present":
            bool(
                result[
                    "brief"
                ].get(
                    "goal_context"
                )
            ),

        "action_present":
            bool(
                result[
                    "brief"
                ].get(
                    "highest_impact_action"
                )
            ),

        "safety_note_present":
            bool(
                result[
                    "brief"
                ].get(
                    "medical_safety_note"
                )
            ),
    }

    return {
        "status":
            "ok"
            if all(
                checks.values()
            )
            else "check_failed",

        "checks":
            checks,

        **result,
    }
