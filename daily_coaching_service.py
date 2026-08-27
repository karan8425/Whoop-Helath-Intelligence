import hashlib
import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from db import get_conn

from combined_coaching import (
    combined_deterministic_coaching,
)

from goal_progress import (
    goal_progress,
)

from ai_intelligence import (
    validate_combined_ai_connection,
)

from integrations.tonal.workout_prescription import (
    build_daily_workout_prescription,
)

from daily_intelligence_store import (
    load_coaching_snapshot,
    load_workout_prescription,
    save_workout_prescription,
    log_intelligence_event,
)


EASTERN = ZoneInfo(
    "America/New_York"
)


# ============================================================
# HELPERS
# ============================================================

def _today():

    return (
        datetime.now(
            EASTERN
        ).date()
    )


def _json_default(value):

    if isinstance(
        value,
        (
            datetime,
            date,
        ),
    ):

        return value.isoformat()

    return str(
        value
    )


def _fingerprint(payload):

    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(
            ",",
            ":",
        ),
        default=_json_default,
    )

    return hashlib.sha256(
        canonical.encode(
            "utf-8"
        )
    ).hexdigest()


# ============================================================
# MEANINGFUL COACHING INPUTS
#
# Intentionally excludes continuously changing values such as:
# - live step count
# - active calories
# - resting calories
#
# Those can update in the app without triggering another LLM
# call or changing the morning coaching narrative.
# ============================================================

def _meaningful_inputs(
    deterministic,
    goal,
    workout_record,
):

    active_goal = (
        deterministic.get(
            "active_goal"
        )
        or {}
    )

    data_readiness = (
        deterministic.get(
            "data_readiness"
        )
        or {}
    )

    tonal_training = (
        deterministic.get(
            "tonal_training"
        )
        or {}
    )

    strength_scores = (
        tonal_training.get(
            "strength_scores"
        )
        or {}
    )

    recommended_session = (
        tonal_training.get(
            "recommended_session"
        )
        or {}
    )

    body_context = (
        deterministic.get(
            "body_composition_context"
        )
        or []
    )

    workout_fingerprint = None
    workout_version = None

    if workout_record:

        workout_fingerprint = (
            workout_record.get(
                "input_fingerprint"
            )
        )

        workout_version = (
            workout_record.get(
                "prescription_version"
            )
        )

    return {

        "coaching_date":
            deterministic.get(
                "coaching_date"
            ),

        "whoop": {

            "whoop_current":
                data_readiness.get(
                    "whoop_current"
                ),

            "training_recommendation":
                deterministic.get(
                    "training_recommendation"
                ),

            "overall_status":
                deterministic.get(
                    "overall_status"
                ),

            "confidence":
                deterministic.get(
                    "confidence"
                ),

            "physiology_reasons":
                deterministic.get(
                    "physiology_reasons",
                    [],
                ),
        },

        "tonal": {

            "strength_scores":
                strength_scores,

            "recommended_session":
                recommended_session,

            "training_focus":
                tonal_training.get(
                    "training_focus"
                ),

            "workout_prescription_fingerprint":
                workout_fingerprint,

            "workout_prescription_version":
                workout_version,
        },

        "goal": {

            "phase":
                active_goal.get(
                    "phase"
                ),

            "phase_start_date":
                active_goal.get(
                    "phase_start_date"
                ),

            "target_body_fat_percentage":
                active_goal.get(
                    "target_body_fat_percentage"
                ),

            "target_weight_lb":
                active_goal.get(
                    "target_weight_lb"
                ),

            "daily_step_target":
                active_goal.get(
                    "daily_step_target"
                ),

            "strength_sessions_per_week":
                active_goal.get(
                    "strength_sessions_per_week"
                ),

            "protein_target_grams":
                active_goal.get(
                    "protein_target_grams"
                ),

            "progress_direction":
                (
                    goal.get(
                        "direction"
                    )
                    if goal
                    else None
                ),
        },

        "body_composition":
            body_context,
    }


# ============================================================
# SNAPSHOT WRITER
#
# Store the complete AI API response.
#
# This allows the mobile app to receive exactly the same
# coaching response repeatedly without another OpenAI call.
# ============================================================

def _save_generated_response(
    coaching_date,
    fingerprint,
    deterministic,
    ai_response,
):

    data_readiness = (
        deterministic.get(
            "data_readiness"
        )
        or {}
    )

    tonal_training = (
        deterministic.get(
            "tonal_training"
        )
        or {}
    )

    active_goal = (
        deterministic.get(
            "active_goal"
        )
        or {}
    )

    model = (
        ai_response.get(
            "model"
        )
    )

    with get_conn() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                INSERT INTO public.daily_coaching_snapshots (
                    coaching_date,
                    status,
                    whoop_ready,
                    training_recommendation,
                    tonal_training_focus,
                    active_phase,
                    goal_progress_direction,
                    input_fingerprint,
                    deterministic_payload,
                    ai_brief,
                    ai_model,
                    generated_at,
                    updated_at
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    NOW(),
                    NOW()
                )
                ON CONFLICT (
                    coaching_date
                )
                DO UPDATE SET
                    status =
                        EXCLUDED.status,

                    whoop_ready =
                        EXCLUDED.whoop_ready,

                    training_recommendation =
                        EXCLUDED.training_recommendation,

                    tonal_training_focus =
                        EXCLUDED.tonal_training_focus,

                    active_phase =
                        EXCLUDED.active_phase,

                    goal_progress_direction =
                        EXCLUDED.goal_progress_direction,

                    input_fingerprint =
                        EXCLUDED.input_fingerprint,

                    deterministic_payload =
                        EXCLUDED.deterministic_payload,

                    ai_brief =
                        EXCLUDED.ai_brief,

                    ai_model =
                        EXCLUDED.ai_model,

                    generated_at =
                        EXCLUDED.generated_at,

                    updated_at =
                        NOW()

                RETURNING
                    id,
                    coaching_date,
                    generated_at
                """,
                (
                    coaching_date,

                    ai_response.get(
                        "status",
                        "ok",
                    ),

                    bool(
                        data_readiness.get(
                            "whoop_current"
                        )
                    ),

                    deterministic.get(
                        "training_recommendation"
                    ),

                    tonal_training.get(
                        "training_focus"
                    ),

                    active_goal.get(
                        "phase"
                    ),

                    ai_response.get(
                        "goal_progress_direction"
                    ),

                    fingerprint,

                    Jsonb(
                        deterministic
                    ),

                    Jsonb(
                        ai_response
                    ),

                    model,
                ),
            )

            row = (
                cur.fetchone()
            )

    log_intelligence_event(
        event_date=coaching_date,
        event_type=(
            "ai_coaching_generated"
        ),
        source=(
            "daily_coaching_service"
        ),
        event_fingerprint=(
            fingerprint
        ),
        details={
            "model":
                model,

            "training_recommendation":
                deterministic.get(
                    "training_recommendation"
                ),

            "tonal_training_focus":
                tonal_training.get(
                    "training_focus"
                ),

            "active_phase":
                active_goal.get(
                    "phase"
                ),
        },
    )

    return dict(
        row
    )


# ============================================================
# CACHED RESPONSE
# ============================================================

def _cached_response(
    snapshot,
):

    stored = (
        snapshot.get(
            "ai_brief"
        )
    )

    if not isinstance(
        stored,
        dict,
    ):

        return None

    result = dict(
        stored
    )

    result[
        "cache"
    ] = {

        "source":
            "stored",

        "llm_called":
            False,

        "generated_at":
            (
                snapshot.get(
                    "generated_at"
                ).isoformat()
                if snapshot.get(
                    "generated_at"
                )
                else None
            ),
    }

    return result


# ============================================================
# PRODUCTION AI GENERATOR
#
# This remains the real OpenAI path.
#
# It will be used on Render when meaningful data changed and a
# new coaching brief really is required.
# ============================================================

def _production_ai_generator():

    return (
        validate_combined_ai_connection()
    )


# ============================================================
# MAIN DAILY COACHING SERVICE
#
# ai_generator is injectable only so local tests can use a
# mock response without requiring OPENAI_API_KEY.
#
# Production callers do not supply ai_generator.
# ============================================================

def get_daily_coaching(
    force_refresh=False,
    ai_generator=None,
):

    coaching_date = (
        _today()
    )

    if ai_generator is None:

        ai_generator = (
            _production_ai_generator
        )

    # --------------------------------------------------------
    # 1. Current deterministic intelligence.
    #
    # No LLM call.
    # --------------------------------------------------------

    deterministic = (
        combined_deterministic_coaching()
    )

    # --------------------------------------------------------
    # 2. Current Tonal V3 workout prescription.
    #
    # No LLM call.
    # --------------------------------------------------------

    prescription = (
        build_daily_workout_prescription()
    )

    # --------------------------------------------------------
    # 3. Persist/reuse today's prescription.
    # --------------------------------------------------------

    save_workout_prescription(
        prescription,
        prescription_date=coaching_date,
    )

    workout_record = (
        load_workout_prescription(
            coaching_date
        )
    )

    # --------------------------------------------------------
    # 4. Deterministic goal progress.
    # --------------------------------------------------------

    goal = (
        goal_progress()
    )

    # --------------------------------------------------------
    # 5. Only material AI inputs.
    #
    # Live activity values are intentionally excluded.
    # --------------------------------------------------------

    meaningful_inputs = (
        _meaningful_inputs(
            deterministic,
            goal,
            workout_record,
        )
    )

    current_fingerprint = (
        _fingerprint(
            meaningful_inputs
        )
    )

    # --------------------------------------------------------
    # 6. Look for today's stored coaching.
    # --------------------------------------------------------

    existing = (
        load_coaching_snapshot(
            coaching_date
        )
    )

    if (
        existing
        and not force_refresh
        and existing.get(
            "input_fingerprint"
        )
        == current_fingerprint
    ):

        cached = (
            _cached_response(
                existing
            )
        )

        if cached is not None:

            log_intelligence_event(
                event_date=coaching_date,
                event_type=(
                    "ai_coaching_cache_hit"
                ),
                source=(
                    "daily_coaching_service"
                ),
                event_fingerprint=(
                    current_fingerprint
                ),
                details={
                    "llm_called":
                        False,
                },
            )

            return cached

    # --------------------------------------------------------
    # 7. No usable cache.
    #
    # Production:
    #   OpenAI is called here.
    #
    # Local validation:
    #   the injected mock function is called here instead.
    # --------------------------------------------------------

    ai_response = (
        ai_generator()
    )

    # --------------------------------------------------------
    # 8. Persist the exact generated response.
    # --------------------------------------------------------

    saved = (
        _save_generated_response(
            coaching_date,
            current_fingerprint,
            deterministic,
            ai_response,
        )
    )

    # --------------------------------------------------------
    # 9. Return result plus cache metadata.
    # --------------------------------------------------------

    result = dict(
        ai_response
    )

    result[
        "cache"
    ] = {

        "source":
            (
                "forced_refresh"
                if force_refresh
                else "generated"
            ),

        "llm_called":
            True,

        "generated_at":
            (
                saved.get(
                    "generated_at"
                ).isoformat()
                if saved.get(
                    "generated_at"
                )
                else None
            ),
    }

    return result


# ============================================================
# LOCAL MOCK AI RESPONSE
#
# Used ONLY by the validation function below.
#
# It does not call OpenAI.
# It does not require OPENAI_API_KEY.
# It costs nothing.
# ============================================================

def _mock_ai_generator():

    deterministic = (
        combined_deterministic_coaching()
    )

    recommendation = (
        deterministic.get(
            "training_recommendation"
        )
    )

    tonal_training = (
        deterministic.get(
            "tonal_training"
        )
        or {}
    )

    training_focus = (
        tonal_training.get(
            "training_focus"
        )
        or "Strength training"
    )

    active_goal = (
        deterministic.get(
            "active_goal"
        )
        or {}
    )

    phase = (
        active_goal.get(
            "phase"
        )
        or "not_configured"
    )

    coaching_date = (
        deterministic.get(
            "coaching_date"
        )
    )

    return {

        "status":
            "ok",

        "checks": {
            "mock_validation":
                True,
        },

        "model":
            "local-cache-test",

        "whoop_ready":
            (
                deterministic.get(
                    "data_readiness",
                    {}
                ).get(
                    "whoop_current"
                )
            ),

        "deterministic_training_recommendation":
            recommendation,

        "tonal_training_focus":
            training_focus,

        "goal_progress_direction":
            "validation",

        "active_phase":
            phase,

        "brief": {

            "date":
                coaching_date,

            "headline":
                (
                    "Local cache validation "
                    "coaching brief"
                ),

            "training_recommendation":
                recommendation,

            "strength_focus":
                training_focus,

            "today_summary":
                (
                    "This is a local validation response. "
                    "No OpenAI request was made."
                ),

            "training_focus":
                training_focus,

            "activity_priority":
                (
                    "Maintain the configured daily "
                    "activity target."
                ),

            "body_composition_context":
                (
                    "Body composition is retained as "
                    "deterministic context."
                ),

            "goal_context":
                (
                    f"Current phase: {phase}."
                ),

            "why_it_matters":
                [
                    (
                        "The local test validates that "
                        "daily coaching can be persisted "
                        "and reused."
                    )
                ],

            "highest_impact_action":
                (
                    "Validate that the second request "
                    "comes from stored coaching."
                ),

            "trend_to_watch":
                (
                    "No trend interpretation is being "
                    "tested in this mock response."
                ),

            "confidence":
                "high",

            "uncertainty_note":
                (
                    "This is a cache validation response, "
                    "not an AI-generated health briefing."
                ),

            "medical_safety_note":
                (
                    "Wearable guidance is informational "
                    "and is not a medical diagnosis."
                ),
        },
    }


# ============================================================
# LOCAL CACHE VALIDATION
#
# No OpenAI API call is made.
#
# First request:
#   may generate the MOCK response and store it.
#
# Second request:
#   MUST come from database cache.
# ============================================================

def validate_daily_coaching_cache():

    first = (
        get_daily_coaching(
            force_refresh=True,
            ai_generator=
                _mock_ai_generator,
        )
    )

    second = (
        get_daily_coaching(
            force_refresh=False,
            ai_generator=
                _mock_ai_generator,
        )
    )

    first_cache = (
        first.get(
            "cache"
        )
        or {}
    )

    second_cache = (
        second.get(
            "cache"
        )
        or {}
    )

    first_brief = (
        first.get(
            "brief"
        )
        or {}
    )

    second_brief = (
        second.get(
            "brief"
        )
        or {}
    )

    checks = {

        "first_response_present":
            isinstance(
                first,
                dict,
            ),

        "second_response_present":
            isinstance(
                second,
                dict,
            ),

        "first_response_generated":
            first_cache.get(
                "source"
            )
            == "forced_refresh",

        "second_response_cached":
            second_cache.get(
                "source"
            )
            == "stored",

        "second_request_no_llm":
            second_cache.get(
                "llm_called"
            )
            is False,

        "training_recommendation_stable":
            (
                first_brief.get(
                    "training_recommendation"
                )
                ==
                second_brief.get(
                    "training_recommendation"
                )
            ),

        "headline_stable":
            (
                first_brief.get(
                    "headline"
                )
                ==
                second_brief.get(
                    "headline"
                )
            ),

        "mock_model_preserved":
            (
                second.get(
                    "model"
                )
                == "local-cache-test"
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

        "first_cache":
            first_cache,

        "second_cache":
            second_cache,

        "training_recommendation":
            first_brief.get(
                "training_recommendation"
            ),

        "headline":
            first_brief.get(
                "headline"
            ),

        "test_model":
            first.get(
                "model"
            ),

        "openai_called":
            False,
    }


# ============================================================
# TERMINAL TEST
# ============================================================

def main():

    result = (
        validate_daily_coaching_cache()
    )

    print()

    print(
        "DAILY COACHING CACHE VALIDATION"
    )

    print(
        "=" * 78
    )

    print(
        json.dumps(
            result,
            indent=2,
            default=_json_default,
        )
    )

    print(
        "=" * 78
    )


if __name__ == "__main__":
    main()