import json
import os
import re

from openai import OpenAI

from recommendations import daily_recommendation
from trends import latest_signals


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


def _client():
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not configured in Render. Add it as a secret environment variable."
        )
    return OpenAI(api_key=api_key)


def _model():
    return os.getenv("OPENAI_MODEL", "gpt-5.6-luna")


def _strip_json_fence(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def build_ai_payload():
    recommendation = daily_recommendation()
    signals = latest_signals()

    # Send only the structured information needed for explanation.
    return {
        "metric_date": recommendation.get("metric_date"),
        "deterministic_recommendation": {
            "training_recommendation": recommendation.get("training_recommendation"),
            "overall_status": recommendation.get("overall_status"),
            "confidence": recommendation.get("confidence"),
            "reasons": recommendation.get("reasons", []),
            "recovery_priorities": recommendation.get("recovery_priorities", []),
            "highest_impact_actions": recommendation.get("highest_impact_actions", []),
        },
        "domains": signals.get("domains", {}),
        "signals": [
            {
                "metric_name": x.get("metric_name"),
                "current_value": x.get("current_value"),
                "baseline_7": x.get("baseline_7"),
                "pct_vs_7": x.get("pct_vs_7"),
                "baseline_30": x.get("baseline_30"),
                "pct_vs_30": x.get("pct_vs_30"),
                "baseline_90": x.get("baseline_90"),
                "pct_vs_90": x.get("pct_vs_90"),
                "directional_signal": x.get("directional_signal"),
                "trend": x.get("trend"),
                "coverage_30_percentage": x.get("coverage_30_percentage"),
                "coverage_90_percentage": x.get("coverage_90_percentage"),
                "confidence": x.get("confidence"),
            }
            for x in signals.get("signals", [])
        ],
    }


def generate_daily_ai_brief():
    payload = build_ai_payload()
    fixed_training = payload["deterministic_recommendation"]["training_recommendation"]

    user_prompt = f"""
Create today's health intelligence briefing from the structured data below.

The training recommendation MUST remain exactly: {fixed_training}

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

Do not add markdown. Do not add any keys. Do not change the training recommendation.

DATA:
{json.dumps(payload, default=str)}
"""

    client = _client()
    response = client.responses.create(
        model=_model(),
        instructions=SYSTEM_PROMPT,
        input=user_prompt,
    )

    raw = _strip_json_fence(response.output_text)

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"OpenAI returned output that was not valid JSON: {raw[:500]}"
        ) from exc

    # Safety/consistency guardrail: AI is not allowed to override deterministic classification.
    parsed["training_recommendation"] = fixed_training
    parsed["date"] = payload.get("metric_date")

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

    missing = required - set(parsed.keys())
    if missing:
        raise RuntimeError(
            "OpenAI JSON is missing required fields: " + ", ".join(sorted(missing))
        )

    return {
        "model": _model(),
        "deterministic_training_recommendation": fixed_training,
        "brief": parsed,
    }


def validate_ai_connection():
    result = generate_daily_ai_brief()

    checks = {
        "model_present": bool(result.get("model")),
        "deterministic_recommendation_present": bool(
            result.get("deterministic_training_recommendation")
        ),
        "brief_present": isinstance(result.get("brief"), dict),
        "recommendation_preserved": (
            result["brief"].get("training_recommendation")
            == result.get("deterministic_training_recommendation")
        ),
        "headline_present": bool(result["brief"].get("headline")),
        "summary_present": bool(result["brief"].get("today_summary")),
        "action_present": bool(result["brief"].get("highest_impact_action")),
        "safety_note_present": bool(result["brief"].get("medical_safety_note")),
    }

    return {
        "status": "ok" if all(checks.values()) else "check_failed",
        "checks": checks,
        **result,
    }
