from datetime import datetime, timezone
from db import get_conn


DDL = """
CREATE TABLE IF NOT EXISTS health_goal_profiles (
    id BIGSERIAL PRIMARY KEY,
    phase TEXT NOT NULL,
    target_body_fat_percentage DOUBLE PRECISION,
    target_weight_lb DOUBLE PRECISION,
    daily_step_target INTEGER,
    strength_sessions_per_week INTEGER,
    protein_target_grams INTEGER,
    phase_start_date DATE NOT NULL,
    phase_end_date DATE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""


ALLOWED_PHASES = {
    "lean_cut",
    "maintenance",
    "lean_bulk",
}


def init_goal_profiles():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)

            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_health_goal_profiles_one_active
                ON health_goal_profiles ((is_active))
                WHERE is_active = TRUE
            """)


def _serialize(row):
    if not row:
        return None

    result = dict(row)

    for key in (
        "phase_start_date",
        "phase_end_date",
    ):
        if result.get(key):
            result[key] = result[key].isoformat()

    for key in (
        "created_at",
        "updated_at",
    ):
        if result.get(key):
            result[key] = result[key].isoformat()

    return result


def get_active_goal():
    init_goal_profiles()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT *
                FROM health_goal_profiles
                WHERE is_active = TRUE
                ORDER BY id DESC
                LIMIT 1
            """)

            return _serialize(
                cur.fetchone()
            )


def get_goal_history():
    init_goal_profiles()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT *
                FROM health_goal_profiles
                ORDER BY phase_start_date DESC, id DESC
            """)

            return [
                _serialize(row)
                for row in cur.fetchall()
            ]


def save_goal_profile(payload):
    init_goal_profiles()

    phase = (
        payload.get("phase")
        or ""
    ).strip().lower()

    if phase not in ALLOWED_PHASES:
        raise ValueError(
            "phase must be one of: "
            + ", ".join(
                sorted(ALLOWED_PHASES)
            )
        )

    phase_start_date = payload.get(
        "phase_start_date"
    )

    if not phase_start_date:
        phase_start_date = (
            datetime.now(
                timezone.utc
            )
            .date()
            .isoformat()
        )

    target_body_fat = payload.get(
        "target_body_fat_percentage"
    )

    target_weight_lb = payload.get(
        "target_weight_lb"
    )

    daily_step_target = payload.get(
        "daily_step_target"
    )

    strength_sessions = payload.get(
        "strength_sessions_per_week"
    )

    protein_target = payload.get(
        "protein_target_grams"
    )

    if (
        target_body_fat is not None
        and not 3 <= float(target_body_fat) <= 60
    ):
        raise ValueError(
            "target_body_fat_percentage must be between 3 and 60."
        )

    if (
        target_weight_lb is not None
        and not 70 <= float(target_weight_lb) <= 500
    ):
        raise ValueError(
            "target_weight_lb must be between 70 and 500."
        )

    if (
        daily_step_target is not None
        and not 0 <= int(daily_step_target) <= 50000
    ):
        raise ValueError(
            "daily_step_target must be between 0 and 50000."
        )

    if (
        strength_sessions is not None
        and not 0 <= int(strength_sessions) <= 14
    ):
        raise ValueError(
            "strength_sessions_per_week must be between 0 and 14."
        )

    if (
        protein_target is not None
        and not 0 <= int(protein_target) <= 500
    ):
        raise ValueError(
            "protein_target_grams must be between 0 and 500."
        )

    with get_conn() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                UPDATE health_goal_profiles
                SET
                    is_active = FALSE,
                    phase_end_date = %s::date - INTERVAL '1 day',
                    updated_at = NOW()
                WHERE is_active = TRUE
            """, (
                phase_start_date,
            ))

            cur.execute("""
                INSERT INTO health_goal_profiles (
                    phase,
                    target_body_fat_percentage,
                    target_weight_lb,
                    daily_step_target,
                    strength_sessions_per_week,
                    protein_target_grams,
                    phase_start_date,
                    is_active
                )
                VALUES (
                    %s,%s,%s,%s,%s,%s,%s,TRUE
                )
                RETURNING *
            """, (
                phase,
                target_body_fat,
                target_weight_lb,
                daily_step_target,
                strength_sessions,
                protein_target,
                phase_start_date,
            ))

            return _serialize(
                cur.fetchone()
            )
