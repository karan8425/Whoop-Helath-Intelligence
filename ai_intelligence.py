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
6. Tonal strength-training history, Strength Scores and muscle-training priorities

WHOOP remains authoritative for recovery, readiness and training intensity.

Tonal determines strength-training context such as:
- what muscle groups need direct training
- recent direct versus supporting muscle exposure
- strength-balance context
- the preferred strength-session focus

Rules:
1. When WHOOP readiness is available, the deterministic WHOOP training recommendation is authoritative.
2. You MUST NOT change the deterministic WHOOP training recommendation.
3. When WHOOP readiness is unavailable, you MUST NOT invent a training recommendation or intensity.
4. When WHOOP is unavailable, training_recommendation MUST remain null.
5. Tonal may still provide a strength-training focus while WHOOP is pending.
6. Do not describe a Tonal strength focus as approval to train at a particular intensity when WHOOP is pending.
7. WHOOP is authoritative for recovery, HRV, resting heart rate, sleep, strain and readiness.
8. Hume and Apple Health add body-composition and activity context.
9. The active fitness phase and goal-progress engine provide objective context for progress.
10. Use only the supplied data.
11. Do not infer body-composition trends from a single measurement.
12. Do not claim weight loss, fat loss, muscle gain, or muscle loss unless supplied trend data explicitly supports it.
13. If goal progress is insufficient_data, building baseline, or otherwise uncertain, preserve that uncertainty.
14. If lean body mass is excluded or stale, do not use it to make conclusions.
15. If nutrition tracking is marked not_connected, do not claim adherence.
16. A configured protein target is a target only, not evidence of actual intake.
17. Direct Tonal muscle exposure and secondary/supporting exposure are different.
18. Secondary Tonal exposure must not be described as equivalent to direct weekly training frequency.
19. Personal baselines take priority over population norms.
20. Distinguish observations from hypotheses and never imply causation.
21. Keep the briefing concise and decision-oriented.
22. Do not diagnose medical conditions.
23. Return JSON only with exactly the requested keys.
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


def _strip_json_fence(
    text
):

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

    recommendation = (
        daily_recommendation()
    )

    signals = (
        latest_signals()
    )

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

    payload = (
        build_ai_payload()
    )

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

    result = (
        generate_daily_ai_brief()
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
            (
                "ok"
                if all(
                    checks.values()
                )
                else "check_failed"
            ),

        "checks":
            checks,

        **result,
    }


# ============================================================
# GOAL + TONAL AWARE COMBINED AI BRIEF
# ============================================================

def build_combined_ai_payload():

    combined = (
        combined_deterministic_coaching()
    )

    combined_status = (
        combined.get(
            "status"
        )
    )

    if combined_status not in (
        "ok",
        "not_ready",
    ):

        raise RuntimeError(
            "Combined deterministic coaching "
            "returned unexpected status: "
            + str(
                combined_status
            )
        )

    whoop_ready = (
        combined_status
        == "ok"
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

    tonal = (
        combined.get(
            "tonal_training"
        )
        or {}
    )

    return {
        "coaching_date":
            combined.get(
                "coaching_date"
            ),

        "combined_status":
            combined_status,

        "whoop_ready":
            whoop_ready,

        "deterministic_training_recommendation":
            (
                combined.get(
                    "training_recommendation"
                )
                if whoop_ready
                else None
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

        "message":
            combined.get(
                "message"
            ),

        "notes":
            combined.get(
                "notes",
                []
            ),

        "tonal_training": {
            "status":
                tonal.get(
                    "status"
                ),

            "training_focus":
                tonal.get(
                    "training_focus"
                ),

            "recommended_session":
                tonal.get(
                    "recommended_session"
                ),

            "priority_muscles":
                tonal.get(
                    "priority_muscles",
                    []
                ),

            "covered_muscles":
                tonal.get(
                    "covered_muscles",
                    []
                ),

            "strength_scores":
                tonal.get(
                    "strength_scores"
                ),

            "strength_balance":
                tonal.get(
                    "strength_balance"
                ),

            "rationale":
                tonal.get(
                    "rationale",
                    []
                ),

            "interpretation_note":
                tonal.get(
                    "interpretation_note"
                ),
        },

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

    whoop_ready = (
        payload.get(
            "whoop_ready"
        )
    )

    fixed_training = (
        payload.get(
            "deterministic_training_recommendation"
        )
    )

    goal = (
        payload.get(
            "goal_progress"
        )
        or {}
    )

    tonal = (
        payload.get(
            "tonal_training"
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

    tonal_focus = (
        tonal.get(
            "training_focus"
        )
    )

    if whoop_ready:

        training_instruction = f"""
WHOOP readiness is available.

The deterministic training recommendation MUST remain exactly:
{fixed_training}

You may explain how the Tonal strength focus should be performed
within that readiness level.

You MUST NOT change the training recommendation.
"""

        recommendation_schema = (
            f'"{fixed_training}"'
        )

    else:

        training_instruction = """
WHOOP physiology is not ready for today's training recommendation.

You MUST NOT invent or infer Push, Normal, Moderate,
Active Recovery, Rest, or any other readiness/intensity category.

The training_recommendation field MUST be null.

You may still explain Tonal strength priorities and the preferred
strength-session focus, but clearly distinguish that from WHOOP
readiness or permission to train at a particular intensity.
"""

        recommendation_schema = (
            "null"
        )

    user_prompt = f"""
Create today's combined health and fitness coaching brief.

{training_instruction}

Active fitness phase:
{phase}

Deterministic goal-progress direction:
{direction}

Tonal strength-training focus:
{tonal_focus}

Return one JSON object with exactly these keys:

{{
  "date": "YYYY-MM-DD",
  "headline": "short overall status",
  "training_recommendation": {recommendation_schema},
  "strength_focus": "Tonal strength-session focus or null",
  "today_summary": "2-4 concise sentences",
  "training_focus": "one concise instruction that respects WHOOP readiness availability",
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
- Return JSON only.
- Do not add markdown.
- Do not add any keys.
- If WHOOP readiness is available, do not change the deterministic training recommendation.
- If WHOOP readiness is unavailable, training_recommendation MUST be null.
- If WHOOP readiness is unavailable, do not imply that Tonal determines training intensity.
- Tonal determines what muscle groups need training.
- WHOOP determines readiness and intensity.
- Direct Tonal muscle exposure is different from secondary/supporting exposure.
- Secondary Tonal exposure must not be described as satisfying direct weekly frequency.
- Explicitly connect today's guidance to the active goal phase when configured.
- The goal-progress engine is authoritative for goal direction.
- If goal direction is "insufficient_data", do NOT say the user is on-track or off-track.
- If the phase is "lean_cut", prioritize preservation of training quality,
  reasonable activity consistency and body-fat progress.
- Do not recommend extra training volume merely to burn calories.
- A protein target is only a configured target.
- If protein status is "not_connected", do not claim actual protein intake or adherence.
- Do not describe today's weight or body-fat measurement as a trend.
- Do not claim fat loss or weight loss unless supplied trend data supports it.
- If lean mass is unavailable or excluded, do not infer muscle gain or loss.
- Use only supplied data.
- Never diagnose medical conditions.

DATA:
{json.dumps(payload, default=str)}
"""

    client = (
        _client()
    )

    response = (
        client.responses.create(
            model=_model(),
            instructions=
                COMBINED_SYSTEM_PROMPT,
            input=user_prompt,
        )
    )

    raw = (
        _strip_json_fence(
            response.output_text
        )
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
    # HARD CONSISTENCY GUARDRAILS
    # --------------------------------------------------------

    parsed[
        "date"
    ] = payload.get(
        "coaching_date"
    )

    parsed[
        "strength_focus"
    ] = tonal_focus

    if whoop_ready:

        parsed[
            "training_recommendation"
        ] = fixed_training

    else:

        parsed[
            "training_recommendation"
        ] = None

    required = {
        "date",
        "headline",
        "training_recommendation",
        "strength_focus",
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

        "whoop_ready":
            whoop_ready,

        "deterministic_training_recommendation":
            fixed_training,

        "tonal_training_focus":
            tonal_focus,

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

    brief = (
        result.get(
            "brief"
        )
        or {}
    )

    whoop_ready = (
        result.get(
            "whoop_ready"
        )
    )

    checks = {
        "model_present":
            bool(
                result.get(
                    "model"
                )
            ),

        "brief_present":
            isinstance(
                brief,
                dict
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

        "strength_focus_present":
            bool(
                brief.get(
                    "strength_focus"
                )
            ),

        "headline_present":
            bool(
                brief.get(
                    "headline"
                )
            ),

        "training_focus_present":
            bool(
                brief.get(
                    "training_focus"
                )
            ),

        "activity_priority_present":
            bool(
                brief.get(
                    "activity_priority"
                )
            ),

        "body_context_present":
            bool(
                brief.get(
                    "body_composition_context"
                )
            ),

        "goal_context_present":
            bool(
                brief.get(
                    "goal_context"
                )
            ),

        "action_present":
            bool(
                brief.get(
                    "highest_impact_action"
                )
            ),

        "safety_note_present":
            bool(
                brief.get(
                    "medical_safety_note"
                )
            ),
    }

    if whoop_ready:

        checks[
            "deterministic_recommendation_present"
        ] = bool(
            result.get(
                "deterministic_training_recommendation"
            )
        )

        checks[
            "recommendation_preserved"
        ] = (
            brief.get(
                "training_recommendation"
            )
            ==
            result.get(
                "deterministic_training_recommendation"
            )
        )

    else:

        checks[
            "pending_whoop_training_is_null"
        ] = (
            brief.get(
                "training_recommendation"
            )
            is None
        )

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

        **result,
    }
