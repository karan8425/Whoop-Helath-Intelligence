"""TKI-6: single source of truth for which engine powers today's
training prescription in /api/v1/todays-plan.

Read by both todays_plan.py (which engine to invoke) and
todays_plan_store.py (which cache partition to use) - a single shared
module so the two can never disagree about which engine is active.

Development-only. Production never sets this variable, and its absence
is the safe, unchanged default (B3).
"""

import os

ENGINE_B3 = "b3"
ENGINE_TKI = "tki"

VALID_ENGINES = (ENGINE_B3, ENGINE_TKI)
DEFAULT_ENGINE = ENGINE_B3

ENV_VAR_NAME = "TRAINING_PRESCRIPTION_ENGINE"


def resolve_training_prescription_engine() -> str:
    """Missing variable -> B3 (unchanged behavior). Invalid value ->
    B3, with a logged warning (never a hard failure - this must never
    be able to break /api/v1/todays-plan by itself)."""
    raw = os.getenv(ENV_VAR_NAME)

    if raw is None:
        return DEFAULT_ENGINE

    value = raw.strip().lower()

    if value not in VALID_ENGINES:
        print(
            f"TRAINING_PRESCRIPTION_ENGINE_INVALID value={raw!r} "
            f"valid_values={VALID_ENGINES} falling_back_to={DEFAULT_ENGINE}",
            flush=True,
        )
        return DEFAULT_ENGINE

    return value


# ============================================================
# TKI-7: calibrated Training Intelligence mobile flag.
#
# Independent of, and higher-precedence than, TRAINING_PRESCRIPTION_
# ENGINE above (which still gates TKI-5.1's raw, uncalibrated shadow
# engine for anyone still using it). This flag governs ONLY whether
# /api/v1/todays-plan's training section is built from the fully
# calibrated TKI-5.2/5.3/5.4 stack (training_intelligence.calibration.
# orchestrator). Training only - Nutrition/Sleep/Activity/Recovery/
# Goal Progress never read this flag.
#
# Development-only. Production never sets this variable, and its
# absence is the safe, unchanged default (current Development mobile
# behavior, i.e. whatever TRAINING_PRESCRIPTION_ENGINE already
# resolves to).
# ============================================================

MOBILE_ENV_VAR_NAME = "TRAINING_INTELLIGENCE_MOBILE_ENABLED"
SHADOW_COMPARE_ENV_VAR_NAME = "TRAINING_INTELLIGENCE_SHADOW_COMPARE_ENABLED"

# A third, distinct cache-partition identity (todays_plan_store.py's
# ENGINE_PLAN_VERSION_OFFSET) - separate from ENGINE_B3/ENGINE_TKI so
# flipping TRAINING_INTELLIGENCE_MOBILE_ENABLED can never collide with,
# or be masked by, either existing engine's cached plan.
ENGINE_TRAINING_INTELLIGENCE = "training_intelligence"


def _flag_enabled(env_var_name: str) -> bool:
    return (os.getenv(env_var_name) or "").strip().lower() in ("1", "true", "yes")


def training_intelligence_mobile_enabled() -> bool:
    return _flag_enabled(MOBILE_ENV_VAR_NAME)


def training_intelligence_shadow_compare_enabled() -> bool:
    return _flag_enabled(SHADOW_COMPARE_ENV_VAR_NAME)


def resolve_effective_training_engine() -> str:
    """The engine identity that actually determines which cache
    partition a plan belongs to. TRAINING_INTELLIGENCE_MOBILE_ENABLED
    takes precedence over TRAINING_PRESCRIPTION_ENGINE when true; when
    false/absent, behavior is byte-identical to the pre-TKI-7
    resolve_training_prescription_engine() result."""
    if training_intelligence_mobile_enabled():
        return ENGINE_TRAINING_INTELLIGENCE
    return resolve_training_prescription_engine()
