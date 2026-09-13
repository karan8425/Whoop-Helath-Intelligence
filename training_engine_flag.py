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
