import hashlib
import json
from datetime import datetime, timezone

from db import get_conn

from daily_health_intelligence import (
    build_daily_health_ai_payload,
    _mock_daily_health_intelligence,
)


# ============================================================
# CONFIGURATION
# ============================================================

TABLE_NAME = (
    "daily_health_intelligence"
)

INTELLIGENCE_VERSION = 1


# ============================================================
# HELPERS
# ============================================================

def _utc_now():

    return datetime.now(
        timezone.utc
    )


def _canonical_json(
    payload,
):

    return json.dumps(
        payload,
        sort_keys=True,
        separators=(
            ",",
            ":",
        ),
        default=str,
    )


def _fingerprint(
    payload,
):

    canonical = (
        _canonical_json(
            payload
        )
    )

    return hashlib.sha256(
        canonical.encode(
            "utf-8"
        )
    ).hexdigest()


def _json_value(
    value,
):

    if isinstance(
        value,
        str,
    ):

        try:
            return json.loads(
                value
            )

        except json.JSONDecodeError:
            return value

    return value


# ============================================================
# DATABASE SETUP
# ============================================================

def ensure_table():

    with get_conn() as conn:

        with conn.cursor() as cur:

            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS public.{TABLE_NAME} (
                    id BIGSERIAL PRIMARY KEY,

                    plan_date DATE NOT NULL,

                    plan_fingerprint TEXT NOT NULL,

                    intelligence_version INTEGER NOT NULL,

                    model_name TEXT,

                    deterministic_payload JSONB NOT NULL,

                    intelligence_payload JSONB NOT NULL,

                    created_at TIMESTAMPTZ NOT NULL
                        DEFAULT NOW(),

                    updated_at TIMESTAMPTZ NOT NULL
                        DEFAULT NOW(),

                    UNIQUE (
                        plan_date,
                        plan_fingerprint,
                        intelligence_version
                    )
                )
                """
            )

            cur.execute(
                f"""
                CREATE INDEX IF NOT EXISTS
                    idx_{TABLE_NAME}_plan_date
                ON public.{TABLE_NAME} (
                    plan_date DESC
                )
                """
            )

        conn.commit()


# ============================================================
# READ
# ============================================================

def load_cached_intelligence(
    plan_date,
    fingerprint,
):

    ensure_table()

    with get_conn() as conn:

        with conn.cursor() as cur:

            cur.execute(
                f"""
                SELECT
                    id,
                    plan_date,
                    plan_fingerprint,
                    intelligence_version,
                    model_name,
                    deterministic_payload,
                    intelligence_payload,
                    created_at,
                    updated_at
                FROM public.{TABLE_NAME}
                WHERE
                    plan_date = %s
                    AND plan_fingerprint = %s
                    AND intelligence_version = %s
                LIMIT 1
                """,
                (
                    plan_date,
                    fingerprint,
                    INTELLIGENCE_VERSION,
                ),
            )

            row = (
                cur.fetchone()
            )

    if not row:
        return None

    row = dict(
        row
    )

    row[
        "deterministic_payload"
    ] = _json_value(
        row.get(
            "deterministic_payload"
        )
    )

    row[
        "intelligence_payload"
    ] = _json_value(
        row.get(
            "intelligence_payload"
        )
    )

    return row


# ============================================================
# WRITE
# ============================================================

def save_intelligence(
    deterministic_payload,
    intelligence_result,
):

    ensure_table()

    plan_date = (
        deterministic_payload.get(
            "plan_date"
        )
    )

    if not plan_date:

        raise RuntimeError(
            "Cannot save daily health intelligence "
            "without plan_date."
        )

    fingerprint = (
        _fingerprint(
            deterministic_payload
        )
    )

    model_name = (
        intelligence_result.get(
            "model"
        )
    )

    intelligence_payload = (
        intelligence_result.get(
            "brief"
        )
        or {}
    )

    if not intelligence_payload:

        raise RuntimeError(
            "Cannot save empty intelligence payload."
        )

    with get_conn() as conn:

        with conn.cursor() as cur:

            cur.execute(
                f"""
                INSERT INTO public.{TABLE_NAME} (
                    plan_date,
                    plan_fingerprint,
                    intelligence_version,
                    model_name,
                    deterministic_payload,
                    intelligence_payload,
                    created_at,
                    updated_at
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s::jsonb,
                    %s::jsonb,
                    %s,
                    %s
                )
                ON CONFLICT (
                    plan_date,
                    plan_fingerprint,
                    intelligence_version
                )
                DO UPDATE SET
                    model_name =
                        EXCLUDED.model_name,

                    deterministic_payload =
                        EXCLUDED.deterministic_payload,

                    intelligence_payload =
                        EXCLUDED.intelligence_payload,

                    updated_at =
                        EXCLUDED.updated_at

                RETURNING
                    id,
                    plan_date,
                    plan_fingerprint,
                    intelligence_version,
                    model_name,
                    deterministic_payload,
                    intelligence_payload,
                    created_at,
                    updated_at
                """,
                (
                    plan_date,
                    fingerprint,
                    INTELLIGENCE_VERSION,
                    model_name,
                    _canonical_json(
                        deterministic_payload
                    ),
                    _canonical_json(
                        intelligence_payload
                    ),
                    _utc_now(),
                    _utc_now(),
                ),
            )

            row = (
                cur.fetchone()
            )

        conn.commit()

    return dict(
        row
    )


# ============================================================
# CACHE SERVICE
# ============================================================

def get_or_create_intelligence(
    generator=None,
    force_refresh=False,
):

    deterministic_payload = (
        build_daily_health_ai_payload()
    )

    plan_date = (
        deterministic_payload.get(
            "plan_date"
        )
    )

    fingerprint = (
        _fingerprint(
            deterministic_payload
        )
    )

    if not force_refresh:

        cached = (
            load_cached_intelligence(
                plan_date,
                fingerprint,
            )
        )

        if cached:

            return {
                "status":
                    "ok",

                "cache": {
                    "source":
                        "stored",

                    "llm_called":
                        False,

                    "fingerprint":
                        fingerprint,

                    "record_id":
                        cached.get(
                            "id"
                        ),
                },

                "model":
                    cached.get(
                        "model_name"
                    ),

                "plan_date":
                    str(
                        cached.get(
                            "plan_date"
                        )
                    ),

                "brief":
                    cached.get(
                        "intelligence_payload"
                    ),
            }

    if generator is None:

        raise RuntimeError(
            "No intelligence generator was supplied "
            "for a cache miss."
        )

    generated = (
        generator()
    )

    if generated.get(
        "status"
    ) != "ok":

        raise RuntimeError(
            "Daily health intelligence generator "
            "did not return status=ok."
        )

    saved = (
        save_intelligence(
            deterministic_payload,
            generated,
        )
    )

    return {
        "status":
            "ok",

        "cache": {
            "source":
                (
                    "forced_refresh"
                    if force_refresh
                    else "generated"
                ),

            "llm_called":
                (
                    generated.get(
                        "model"
                    )
                    !=
                    "local-health-intelligence-test"
                ),

            "fingerprint":
                fingerprint,

            "record_id":
                saved.get(
                    "id"
                ),
        },

        "model":
            generated.get(
                "model"
            ),

        "plan_date":
            plan_date,

        "brief":
            generated.get(
                "brief"
            ),
    }


# ============================================================
# LOCAL CACHE VALIDATION
# ============================================================

def validate_daily_health_intelligence_store():

    ensure_table()

    deterministic_payload = (
        build_daily_health_ai_payload()
    )

    fingerprint = (
        _fingerprint(
            deterministic_payload
        )
    )

    plan_date = (
        deterministic_payload.get(
            "plan_date"
        )
    )

    # --------------------------------------------------------
    # Remove only this local validation fingerprint so that
    # the first request definitely exercises the save path.
    # --------------------------------------------------------

    with get_conn() as conn:

        with conn.cursor() as cur:

            cur.execute(
                f"""
                DELETE FROM public.{TABLE_NAME}
                WHERE
                    plan_date = %s
                    AND plan_fingerprint = %s
                    AND intelligence_version = %s
                """,
                (
                    plan_date,
                    fingerprint,
                    INTELLIGENCE_VERSION,
                ),
            )

        conn.commit()

    # --------------------------------------------------------
    # First request:
    # must generate using local mock and save.
    # --------------------------------------------------------

    first = (
        get_or_create_intelligence(
            generator=
                _mock_daily_health_intelligence,
            force_refresh=False,
        )
    )

    # --------------------------------------------------------
    # Second request:
    # generator deliberately omitted.
    #
    # If caching is broken, this must fail.
    # --------------------------------------------------------

    second = (
        get_or_create_intelligence(
            generator=None,
            force_refresh=False,
        )
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

    checks = {

        "first_response_present":
            first.get(
                "status"
            )
            == "ok",

        "second_response_present":
            second.get(
                "status"
            )
            == "ok",

        "first_response_generated":
            first_cache.get(
                "source"
            )
            == "generated",

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

        "fingerprint_present":
            bool(
                fingerprint
            ),

        "same_fingerprint":
            (
                first_cache.get(
                    "fingerprint"
                )
                ==
                second_cache.get(
                    "fingerprint"
                )
                ==
                fingerprint
            ),

        "same_record":
            (
                first_cache.get(
                    "record_id"
                )
                ==
                second_cache.get(
                    "record_id"
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

        "training_stable":
            (
                first_brief.get(
                    "training"
                )
                ==
                second_brief.get(
                    "training"
                )
            ),

        "nutrition_stable":
            (
                first_brief.get(
                    "nutrition"
                )
                ==
                second_brief.get(
                    "nutrition"
                )
            ),

        "hydration_stable":
            (
                first_brief.get(
                    "hydration"
                )
                ==
                second_brief.get(
                    "hydration"
                )
            ),

        "sleep_stable":
            (
                first_brief.get(
                    "sleep"
                )
                ==
                second_brief.get(
                    "sleep"
                )
            ),

        "mock_model_preserved":
            (
                second.get(
                    "model"
                )
                ==
                "local-health-intelligence-test"
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

        "plan_date":
            plan_date,

        "fingerprint":
            fingerprint,

        "first_cache":
            first_cache,

        "second_cache":
            second_cache,

        "model":
            second.get(
                "model"
            ),

        "openai_called":
            False,

        "headline":
            second_brief.get(
                "headline"
            ),
    }


# ============================================================
# TERMINAL TEST
# ============================================================

def main():

    result = (
        validate_daily_health_intelligence_store()
    )

    print()

    print(
        "DAILY HEALTH INTELLIGENCE STORE VALIDATION"
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