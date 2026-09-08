from datetime import datetime, timezone

from db import get_conn
import goal_pace_config as pace_cfg
import goal_timeline_engine as timeline_engine


KG_TO_LB = 2.2046226218

# Additive columns for the Goal Setting V2 timeline contract. Every column is
# nullable / default-safe so existing (V1) goal rows keep working unchanged.
_V2_COLUMNS = (
    ("goal_type", "TEXT"),
    ("lean_mass_priority", "TEXT"),
    ("phase_start_fat_mass_lb", "DOUBLE PRECISION"),
    ("phase_start_lean_mass_lb", "DOUBLE PRECISION"),
    ("target_fat_mass_lb", "DOUBLE PRECISION"),
    ("target_lean_mass_lb", "DOUBLE PRECISION"),
    ("target_date", "DATE"),
    ("aspirational_target_date", "DATE"),
    ("selected_pace", "TEXT"),
    ("expected_weekly_weight_change_lb", "DOUBLE PRECISION"),
    ("timeline_status", "TEXT"),
    ("goal_version", "INTEGER NOT NULL DEFAULT 1"),
)


DDL = """
CREATE TABLE IF NOT EXISTS health_goal_profiles (
    id BIGSERIAL PRIMARY KEY,
    phase TEXT NOT NULL,
    target_body_fat_percentage DOUBLE PRECISION,
    target_weight_lb DOUBLE PRECISION,
    daily_step_target INTEGER,
    strength_sessions_per_week INTEGER,
    protein_target_grams INTEGER,

    phase_start_weight_lb DOUBLE PRECISION,
    phase_start_body_fat_percentage DOUBLE PRECISION,
    phase_start_recorded_at TIMESTAMPTZ,

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
                ALTER TABLE health_goal_profiles
                ADD COLUMN IF NOT EXISTS phase_start_weight_lb
                DOUBLE PRECISION
            """)

            cur.execute("""
                ALTER TABLE health_goal_profiles
                ADD COLUMN IF NOT EXISTS phase_start_body_fat_percentage
                DOUBLE PRECISION
            """)

            cur.execute("""
                ALTER TABLE health_goal_profiles
                ADD COLUMN IF NOT EXISTS phase_start_recorded_at
                TIMESTAMPTZ
            """)

            for _col, _type in _V2_COLUMNS:
                cur.execute(
                    "ALTER TABLE health_goal_profiles "
                    f"ADD COLUMN IF NOT EXISTS {_col} {_type}"
                )

            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS
                idx_health_goal_profiles_one_active
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
        "target_date",
        "aspirational_target_date",
    ):
        if result.get(key):
            result[key] = result[key].isoformat()

    for key in (
        "phase_start_recorded_at",
        "created_at",
        "updated_at",
    ):
        if result.get(key):
            result[key] = result[key].isoformat()

    return result


def get_active_goal(as_of=None):
    # Replay is strictly read-only. Historical goal rows are selected by the
    # best timestamp the current schema retains; in-place edits cannot be
    # reconstructed and are reported as a replay limitation.
    if as_of is None:
        init_goal_profiles()

    with get_conn() as conn:
        with conn.cursor() as cur:

            if as_of is None:
                cur.execute("""
                    SELECT * FROM health_goal_profiles
                    WHERE is_active = TRUE
                    ORDER BY id DESC LIMIT 1
                """)
            else:
                cur.execute("""
                    SELECT * FROM health_goal_profiles
                    WHERE created_at <= %s
                      AND phase_start_date <= %s
                    ORDER BY created_at DESC, id DESC LIMIT 1
                """, (as_of, as_of.date()))

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


def _latest_hume_start_snapshot():
    """
    Capture the latest preferred-source Hume weight and body-fat
    measurements when a new goal phase begins.
    """

    weight_lb = None
    body_fat_percentage = None
    recorded_at = None

    with get_conn() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT
                    value,
                    observed_at
                FROM apple_health_body_samples
                WHERE metric_name = 'body_weight'
                  AND source_bundle_id = 'com.elink.fittrackhealth'
                ORDER BY observed_at DESC
                LIMIT 1
            """)

            weight = cur.fetchone()

            if weight:
                weight_lb = (
                    float(weight["value"])
                    * 2.2046226218
                )

                recorded_at = weight["observed_at"]

            cur.execute("""
                SELECT
                    value,
                    observed_at
                FROM apple_health_body_samples
                WHERE metric_name = 'body_fat_percentage'
                  AND source_bundle_id = 'com.elink.fittrackhealth'
                ORDER BY observed_at DESC
                LIMIT 1
            """)

            body_fat = cur.fetchone()

            if body_fat:
                body_fat_percentage = float(
                    body_fat["value"]
                )

                if (
                    recorded_at is None
                    or body_fat["observed_at"] > recorded_at
                ):
                    recorded_at = body_fat["observed_at"]

    return {
        "phase_start_weight_lb": weight_lb,
        "phase_start_body_fat_percentage": body_fat_percentage,
        "phase_start_recorded_at": recorded_at,
    }


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

    start_snapshot = _latest_hume_start_snapshot()

    with get_conn() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                UPDATE health_goal_profiles
                SET
                    is_active = FALSE,
                    phase_end_date =
                        %s::date - INTERVAL '1 day',
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

                    phase_start_weight_lb,
                    phase_start_body_fat_percentage,
                    phase_start_recorded_at,

                    phase_start_date,
                    is_active
                )
                VALUES (
                    %s,%s,%s,%s,%s,%s,
                    %s,%s,%s,
                    %s,TRUE
                )
                RETURNING *
            """, (
                phase,
                target_body_fat,
                target_weight_lb,
                daily_step_target,
                strength_sessions,
                protein_target,

                start_snapshot[
                    "phase_start_weight_lb"
                ],

                start_snapshot[
                    "phase_start_body_fat_percentage"
                ],

                start_snapshot[
                    "phase_start_recorded_at"
                ],

                phase_start_date,
            ))

            return _serialize(
                cur.fetchone()
            )


def backfill_active_goal_start_snapshot():
    init_goal_profiles()

    active_goal = get_active_goal()

    if not active_goal:
        return {
            "status": "no_active_goal"
        }

    if (
        active_goal.get(
            "phase_start_weight_lb"
        ) is not None
        or active_goal.get(
            "phase_start_body_fat_percentage"
        ) is not None
    ):
        return {
            "status": "already_populated",
            "goal": active_goal,
        }

    phase_start_date = active_goal[
        "phase_start_date"
    ]

    with get_conn() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    value,
                    observed_at
                FROM apple_health_body_samples
                WHERE metric_name = 'body_weight'
                  AND source_bundle_id = 'com.elink.fittrackhealth'
                  AND observed_at <
                      (%s::date + INTERVAL '1 day')
                ORDER BY observed_at DESC
                LIMIT 1
                """,
                (phase_start_date,),
            )

            weight = cur.fetchone()

            cur.execute(
                """
                SELECT
                    value,
                    observed_at
                FROM apple_health_body_samples
                WHERE metric_name = 'body_fat_percentage'
                  AND source_bundle_id = 'com.elink.fittrackhealth'
                  AND observed_at <
                      (%s::date + INTERVAL '1 day')
                ORDER BY observed_at DESC
                LIMIT 1
                """,
                (phase_start_date,),
            )

            body_fat = cur.fetchone()

            weight_lb = None
            body_fat_percentage = None
            recorded_at = None

            if weight:
                weight_lb = (
                    float(weight["value"])
                    * 2.2046226218
                )

                recorded_at = weight["observed_at"]

            if body_fat:
                body_fat_percentage = float(
                    body_fat["value"]
                )

                if (
                    recorded_at is None
                    or body_fat["observed_at"] > recorded_at
                ):
                    recorded_at = body_fat["observed_at"]

            cur.execute(
                """
                UPDATE health_goal_profiles
                SET
                    phase_start_weight_lb = %s,
                    phase_start_body_fat_percentage = %s,
                    phase_start_recorded_at = %s,
                    updated_at = NOW()
                WHERE id = %s
                RETURNING *
                """,
                (
                    weight_lb,
                    body_fat_percentage,
                    recorded_at,
                    active_goal["id"],
                ),
            )

            return {
                "status": "ok",
                "goal": _serialize(
                    cur.fetchone()
                ),
            }


# ============================================================
# GOAL SETTING V2 - preview + activation
# ============================================================

ALLOWED_GOAL_TYPES = set(pace_cfg.GOAL_TYPE_TO_PHASE.keys())
ALLOWED_LEAN_MASS_PRIORITIES = {"preserve_build", "balanced", "faster"}


def _derived(weight_lb, body_fat_pct):
    if weight_lb is None or body_fat_pct is None:
        return None, None
    fat = float(weight_lb) * float(body_fat_pct) / 100.0
    return round(fat, 1), round(float(weight_lb) - fat, 1)


def current_body_state():
    """Latest reliable (Hume) body-composition state for the goal flow.

    Reuses the same source rule as phase-start snapshots. fat/lean mass are
    derived from the matched Hume weight + body fat (same rule as elsewhere).
    """

    snap = _latest_hume_start_snapshot()
    w = snap.get("phase_start_weight_lb")
    bf = snap.get("phase_start_body_fat_percentage")
    fat_mass, lean_mass = _derived(w, bf)

    available = w is not None and bf is not None
    return {
        "status": "ok" if available else "unavailable",
        "source": "Hume",
        "weight_lb": round(w, 1) if w is not None else None,
        "body_fat_percentage": round(bf, 2) if bf is not None else None,
        "fat_mass_lb": fat_mass,
        "lean_mass_lb": lean_mass,
        "recorded_at": (
            snap["phase_start_recorded_at"].isoformat()
            if snap.get("phase_start_recorded_at") else None
        ),
        "message": (
            None if available
            else "No reliable Hume weight / body-fat measurement is available "
                 "yet. Enter your current values to continue."
        ),
    }


def _resolve_current(payload):
    """Prefer app-known Hume values; fall back to explicit manual input."""

    state = current_body_state()
    manual = payload.get("current") or {}
    w = state["weight_lb"]
    bf = state["body_fat_percentage"]
    source = "Hume"
    if w is None and manual.get("weight_lb") is not None:
        w = float(manual["weight_lb"])
        source = "manual"
    if bf is None and manual.get("body_fat_percentage") is not None:
        bf = float(manual["body_fat_percentage"])
        source = "manual" if source == "manual" else "Hume+manual"
    fat_mass, lean_mass = _derived(w, bf)
    return {
        "weight_lb": w, "body_fat_percentage": bf,
        "fat_mass_lb": fat_mass, "lean_mass_lb": lean_mass,
        "source": source,
    }


def preview_goal(payload):
    """Deterministic, read-only. Never touches the active goal."""

    init_goal_profiles()

    goal_type = (payload.get("goal_type") or "").strip().lower()
    if goal_type not in ALLOWED_GOAL_TYPES:
        raise ValueError(
            "goal_type must be one of: "
            + ", ".join(sorted(ALLOWED_GOAL_TYPES))
        )

    priority = (
        payload.get("lean_mass_priority")
        or pace_cfg.DEFAULT_LEAN_MASS_PRIORITY
    )
    if priority not in ALLOWED_LEAN_MASS_PRIORITIES:
        raise ValueError(
            "lean_mass_priority must be one of: "
            + ", ".join(sorted(ALLOWED_LEAN_MASS_PRIORITIES))
        )

    current = _resolve_current(payload)
    target = payload.get("target") or {}

    preview = timeline_engine.build_preview(
        goal_type=goal_type,
        current=current,
        target={
            "target_weight_lb": target.get("target_weight_lb"),
            "target_body_fat_percentage": target.get("target_body_fat_percentage"),
        },
        lean_mass_priority=priority,
        custom_target_date=payload.get("custom_target_date"),
    )
    preview["status"] = "ok"
    preview["current"]["source"] = current["source"]
    return preview


def activate_goal(payload):
    """Persist the full V2 goal contract and start the phase atomically.

    Preserves prior phase rows unchanged (only flips is_active / phase_end_date).
    """

    init_goal_profiles()

    goal_type = (payload.get("goal_type") or "").strip().lower()
    if goal_type not in ALLOWED_GOAL_TYPES:
        raise ValueError("Unknown goal_type.")

    phase = pace_cfg.GOAL_TYPE_TO_PHASE[goal_type]
    priority = (
        payload.get("lean_mass_priority")
        or pace_cfg.DEFAULT_LEAN_MASS_PRIORITY
    )
    if priority not in ALLOWED_LEAN_MASS_PRIORITIES:
        raise ValueError("Unknown lean_mass_priority.")

    current = _resolve_current(payload)
    if current["weight_lb"] is None:
        raise ValueError(
            "A current weight is required to activate a goal."
        )

    target = payload.get("target") or {}
    target_weight = target.get("target_weight_lb")
    target_bf = target.get("target_body_fat_percentage")

    if target_weight is not None and not 70 <= float(target_weight) <= 500:
        raise ValueError("target_weight_lb must be between 70 and 500.")
    if target_bf is not None and not 3 <= float(target_bf) <= 60:
        raise ValueError("target_body_fat_percentage must be between 3 and 60.")

    preview = timeline_engine.build_preview(
        goal_type=goal_type, current=current,
        target={"target_weight_lb": target_weight,
                "target_body_fat_percentage": target_bf},
        lean_mass_priority=priority,
        custom_target_date=payload.get("custom_target_date"),
    )

    # Resolve the actionable timeline.
    selected_pace = (payload.get("selected_pace") or "").strip().lower()
    timeline_status = "not_configured"
    target_date = None
    aspirational_target_date = None
    expected_weekly = None

    if payload.get("custom_target_date"):
        custom = preview.get("custom_timeline") or {}
        status = custom.get("timeline_status")
        if status == "outside_supported_range":
            # Keep the aspirational date, but the actionable plan stays on the
            # recommended date - never a more aggressive prescription.
            aspirational_target_date = custom.get("chosen_date")
            rec = preview["timeline_options"].get("recommended") \
                or next(iter(preview["timeline_options"].values()), None)
            if rec:
                target_date = rec["estimated_target_date"]
                expected_weekly = rec["required_average_weekly_change_lb"]
            timeline_status = "outside_supported_range"
            selected_pace = "recommended"
        elif status in ("comfortable", "recommended", "faster",
                        "faster_but_supported"):
            target_date = custom.get("chosen_date")
            expected_weekly = custom.get("required_weekly_change_lb")
            timeline_status = "configured"
            selected_pace = "custom"
        else:
            timeline_status = "not_configured"
    elif selected_pace in ("comfortable", "recommended", "faster"):
        opt = preview["timeline_options"].get(selected_pace)
        if opt:
            target_date = opt["estimated_target_date"]
            expected_weekly = opt["required_average_weekly_change_lb"]
            timeline_status = "configured"
    else:
        # default to the recommended band
        band = preview.get("recommended_band")
        opt = preview["timeline_options"].get(band) if band else None
        if opt:
            selected_pace = band
            target_date = opt["estimated_target_date"]
            expected_weekly = opt["required_average_weekly_change_lb"]
            timeline_status = "configured"

    ti = preview["target"]
    now_date = datetime.now(timezone.utc).date().isoformat()

    with get_conn() as conn:
        with conn.cursor() as cur:

            # Close the current phase WITHOUT touching its start snapshot.
            cur.execute("""
                UPDATE health_goal_profiles
                SET is_active = FALSE,
                    phase_end_date = %s::date - INTERVAL '1 day',
                    updated_at = NOW()
                WHERE is_active = TRUE
            """, (now_date,))

            cur.execute("""
                INSERT INTO health_goal_profiles (
                    phase, goal_type, lean_mass_priority,
                    target_body_fat_percentage, target_weight_lb,
                    target_fat_mass_lb, target_lean_mass_lb,
                    daily_step_target, strength_sessions_per_week,
                    protein_target_grams,
                    phase_start_weight_lb, phase_start_body_fat_percentage,
                    phase_start_fat_mass_lb, phase_start_lean_mass_lb,
                    phase_start_recorded_at,
                    phase_start_date, target_date, aspirational_target_date,
                    selected_pace, expected_weekly_weight_change_lb,
                    timeline_status, goal_version, is_active
                )
                VALUES (
                    %s,%s,%s,
                    %s,%s,
                    %s,%s,
                    %s,%s,
                    %s,
                    %s,%s,
                    %s,%s,
                    NOW(),
                    %s,%s,%s,
                    %s,%s,
                    %s,2,TRUE
                )
                RETURNING *
            """, (
                phase, goal_type, priority,
                target_bf, target_weight,
                ti.get("fat_mass_lb"), ti.get("lean_mass_lb"),
                payload.get("daily_step_target"),
                payload.get("strength_sessions_per_week"),
                payload.get("protein_target_grams"),
                current["weight_lb"], current["body_fat_percentage"],
                current["fat_mass_lb"], current["lean_mass_lb"],
                now_date, target_date, aspirational_target_date,
                selected_pace or None, expected_weekly,
                timeline_status,
            ))

            goal = _serialize(cur.fetchone())

    # Today's cached plan embeds a goal_progress card built from the PREVIOUS
    # active goal. Drop today's cached plan now that the new goal is committed,
    # so a subsequent /api/v1/todays-plan or /api/v1/today cannot serve a plan
    # derived from the old goal contract. Goal Progress itself
    # (/api/v1/goals/progress) is computed fresh per request and is not cached
    # server-side. Cache maintenance must never fail an otherwise successful
    # activation (item 10).
    plan_invalidated = False
    try:
        from todays_plan_store import invalidate_todays_plan
        invalidate_todays_plan()
        plan_invalidated = True
    except Exception:
        plan_invalidated = False

    return {
        "status": "ok",
        "goal": goal,
        "preview": {
            "compatibility": preview["compatibility"]["state"],
            "timeline_status": timeline_status,
        },
        "cache": {"todays_plan_invalidated": plan_invalidated},
    }
