import secrets

from fastapi import (
    FastAPI,
    Request,
    HTTPException,
    Form,
)

from fastapi.responses import (
    RedirectResponse,
    HTMLResponse,
)

from starlette.middleware.sessions import (
    SessionMiddleware,
)

from goal_progress import (
    goal_progress,
)

from config import (
    SESSION_SECRET,
    ADMIN_PASSWORD,
    validate_config,
)

from db import (
    init_db,
)

from analytics import (
    init_analytics,
)

from body_composition_progress import (
    body_composition_progress,
)

from baselines import (
    init_baselines,
)

from weekly_analytics import (
    weekly_health_summary,
)

from weekly_health_intelligence_store import (
    get_weekly_health_intelligence,
)

from freshness import (
    freshness_status,
)

from automation_status import (
    init_automation_tables,
    latest_automation_run,
)

from healthkit_ingest import (
    init_apple_health_tables,
    require_ingest_key,
    ingest_healthkit_payload,
    latest_apple_health,
    apple_health_history_summary,
)

from apple_health_trends import (
    apple_health_trends,
)

from combined_coaching import (
    combined_daily_snapshot,
    combined_deterministic_coaching,
)

from ai_intelligence import (
    validate_combined_ai_connection,
)

from goals import (
    init_goal_profiles,
    get_active_goal,
    get_goal_history,
    save_goal_profile,
    backfill_active_goal_start_snapshot,
)

from daily_coaching_service import (
    get_daily_coaching,
)

from daily_health_intelligence_store import (
    get_daily_health_intelligence,
)

from todays_plan_store import (
    get_or_build_todays_plan,
)
from today_experience import build_today_experience

from whoop_webhook import (
    router as whoop_webhook_router,
)


# ============================================================
# CONFIGURATION
# ============================================================

validate_config()

app = FastAPI(
    title="WHOOP Health Intelligence",
    version="0.5.5",
)

app.include_router(
    whoop_webhook_router
)

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    https_only=True,
)


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
def startup():

    init_db()
    init_analytics()
    init_baselines()
    init_automation_tables()
    init_apple_health_tables()
    init_goal_profiles()


# ============================================================
# AUTHENTICATION
# ============================================================

def require_admin(
    request: Request,
):

    if (
        request.session.get(
            "admin_authenticated"
        )
        is not True
    ):

        raise HTTPException(
            status_code=401,
            detail="Admin login required.",
        )


# ============================================================
# HOME
# ============================================================

@app.get(
    "/",
    response_class=HTMLResponse,
)
async def home(
    request: Request,
):

    if (
        request.session.get(
            "admin_authenticated"
        )
        is not True
    ):

        return """
        <html>
            <body>
                <h1>WHOOP Health Intelligence</h1>

                <form
                    method="post"
                    action="/admin/login"
                >
                    <input
                        type="password"
                        name="password"
                        placeholder="Admin password"
                    >

                    <button>
                        Sign in
                    </button>
                </form>
            </body>
        </html>
        """

    return """
    <html>
        <body>

            <h1>
                WHOOP Health Intelligence
            </h1>

            <p>
                <b>Phase 5D.3</b>
            </p>

            <ul>

                <li>
                    <a href="/health-intelligence/today">
                        Daily Health Intelligence
                    </a>
                </li>

                <li>
                    <a href="/todays-plan">
                        Today's Deterministic Plan
                    </a>
                </li>

                <li>
                    <a href="/apple-health/latest">
                        Latest Apple Health / Hume data
                    </a>
                </li>

                <li>
                    <a href="/apple-health/trends">
                        Apple Health trends
                    </a>
                </li>

                <li>
                    <a href="/goals/active">
                        Active goal
                    </a>
                </li>

                <li>
                    <a href="/goals/history">
                        Goal history
                    </a>
                </li>

                <li>
                    <a href="/freshness">
                        WHOOP freshness
                    </a>
                </li>

                <li>
                    <a href="/automation/latest-run">
                        Latest automation run
                    </a>
                </li>

            </ul>

        </body>
    </html>
    """


# ============================================================
# ADMIN LOGIN
# ============================================================

@app.post(
    "/admin/login"
)
async def login(
    request: Request,
    password: str = Form(...),
):

    if not secrets.compare_digest(
        password,
        ADMIN_PASSWORD,
    ):

        raise HTTPException(
            status_code=401,
            detail="Incorrect admin password.",
        )

    request.session[
        "admin_authenticated"
    ] = True

    return RedirectResponse(
        "/",
        status_code=303,
    )


# ============================================================
# HEALTH
# ============================================================

@app.get(
    "/health"
)
async def health():

    return {
        "status": "ok",
        "phase": "5D.3",
        "version": "0.5.5",
        "daily_coaching_cache": True,
        "daily_health_intelligence": True,
        "todays_plan_api": True,
        "whoop_webhook": True,
    }


# ============================================================
# WHOOP
# ============================================================

@app.get(
    "/freshness"
)
async def freshness(
    request: Request,
):

    require_admin(
        request
    )

    return {
        "status": "ok",
        **freshness_status(),
    }


# ============================================================
# AUTOMATION
# ============================================================

@app.get(
    "/automation/latest-run"
)
async def latest_run(
    request: Request,
):

    require_admin(
        request
    )

    return {
        "status": "ok",
        "run":
            latest_automation_run(),
    }


# ============================================================
# APPLE HEALTH INGEST
# ============================================================

@app.post(
    "/api/v1/apple-health/ingest"
)
async def apple_health_ingest(
    request: Request,
):

    require_ingest_key(
        request
    )

    payload = (
        await request.json()
    )

    return (
        ingest_healthkit_payload(
            payload
        )
    )


# ============================================================
# APPLE HEALTH ADMIN ENDPOINTS
# ============================================================

@app.get(
    "/apple-health/latest"
)
async def apple_health_latest(
    request: Request,
):

    require_admin(
        request
    )

    return {
        "status": "ok",
        **latest_apple_health(),
    }


@app.get(
    "/apple-health/history/summary"
)
async def apple_health_history_summary_route(
    request: Request,
):

    require_admin(
        request
    )

    return {
        "status": "ok",
        **apple_health_history_summary(),
    }


@app.get(
    "/apple-health/trends"
)
async def apple_health_trends_route(
    request: Request,
):

    require_admin(
        request
    )

    return (
        apple_health_trends()
    )


# ============================================================
# COMBINED COACHING ADMIN ENDPOINTS
#
# These remain available for diagnostics.
#
# The AI diagnostic endpoint below can still intentionally
# call the LLM. It is NOT used by the iPhone application.
# ============================================================

@app.get(
    "/coaching/combined/today"
)
async def combined_today(
    request: Request,
):

    require_admin(
        request
    )

    return (
        combined_daily_snapshot()
    )


@app.get(
    "/coaching/combined/recommendation"
)
async def combined_recommendation(
    request: Request,
):

    require_admin(
        request
    )

    return (
        combined_deterministic_coaching()
    )


@app.get(
    "/coaching/combined/ai"
)
async def combined_ai(
    request: Request,
):

    require_admin(
        request
    )

    try:

        return (
            validate_combined_ai_connection()
        )

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=(
                "Combined AI coaching failed: "
                f"{exc}"
            ),
        ) from exc


# ============================================================
# MOBILE COACHING API
#
# Repeated requests with unchanged meaningful inputs return
# the stored daily snapshot instead of calling the LLM again.
# ============================================================

@app.get(
    "/api/v1/coaching/today"
)
async def mobile_combined_coaching(
    request: Request,
):

    require_ingest_key(
        request
    )

    try:

        return (
            get_daily_coaching(
                force_refresh=False
            )
        )

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=(
                "Daily coaching failed: "
                f"{exc}"
            ),
        ) from exc


# ============================================================
# MOBILE GOAL PROGRESS
# ============================================================

@app.get(
    "/api/v1/goals/progress"
)
async def mobile_goal_progress(
    request: Request,
):

    require_ingest_key(
        request
    )

    return (
        goal_progress()
    )


# ============================================================
# GOAL ADMIN ENDPOINTS
# ============================================================

@app.get(
    "/goals/active"
)
async def goals_active(
    request: Request,
):

    require_admin(
        request
    )

    return {
        "status": "ok",
        "goal":
            get_active_goal(),
    }


@app.get(
    "/goals/history"
)
async def goals_history(
    request: Request,
):

    require_admin(
        request
    )

    return {
        "status": "ok",
        "goals":
            get_goal_history(),
    }


@app.post(
    "/goals"
)
async def goals_save(
    request: Request,
):

    require_admin(
        request
    )

    try:

        payload = (
            await request.json()
        )

        return {
            "status": "ok",
            "goal":
                save_goal_profile(
                    payload
                ),
        }

    except ValueError as exc:

        raise HTTPException(
            status_code=400,
            detail=str(
                exc
            ),
        ) from exc


# ============================================================
# ONE-TIME GOAL SNAPSHOT BACKFILL
# ============================================================

@app.post(
    "/goals/backfill-active-start"
)
async def goals_backfill_active_start(
    request: Request,
):

    require_admin(
        request
    )

    return (
        backfill_active_goal_start_snapshot()
    )


# ============================================================
# MOBILE GOAL API
# ============================================================

@app.get(
    "/api/v1/goals/active"
)
async def mobile_goals_active(
    request: Request,
):

    require_ingest_key(
        request
    )

    return {
        "status": "ok",
        "goal":
            get_active_goal(),
    }


@app.post(
    "/api/v1/goals"
)
async def mobile_goals_save(
    request: Request,
):

    require_ingest_key(
        request
    )

    try:

        payload = (
            await request.json()
        )

        return {
            "status": "ok",
            "goal":
                save_goal_profile(
                    payload
                ),
        }

    except ValueError as exc:

        raise HTTPException(
            status_code=400,
            detail=str(
                exc
            ),
        ) from exc


@app.get(
    "/goals/progress"
)
async def goals_progress(
    request: Request,
):

    require_admin(
        request
    )

    return (
        goal_progress()
    )


# ============================================================
# WEEKLY HEALTH ANALYTICS
#
# Deterministic diagnostic endpoint used to validate weekly
# calculations before building the weekly AI intelligence
# layer or exposing a mobile API.
#
# This endpoint does NOT call OpenAI.
# ============================================================

@app.get(
    "/health-intelligence/weekly/analytics"
)
async def weekly_health_analytics(
    request: Request,
):

    require_admin(
        request
    )

    try:

        return (
            weekly_health_summary()
        )

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=(
                "Weekly Health Analytics failed: "
                f"{exc}"
            ),
        ) from exc


# ============================================================
#Admin Disgnostic endpoint 
# ============================================================
@app.get(
    "/body-composition/progress"
)
async def body_composition_progress_admin(
    request: Request,
):
    require_admin(request)

    try:
        return body_composition_progress()

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Body Composition Progress failed: "
                f"{exc}"
            ),
        ) from exc


@app.get(
    "/api/v1/body-composition/progress"
)
async def body_composition_progress_mobile(
    request: Request,
):
    require_ingest_key(
        request
    )

    try:
        return body_composition_progress()

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Body Composition Progress failed: "
                f"{exc}"
            ),
        ) from exc
# ============================================================
    
# ============================================================
# WEEKLY HEALTH INTELLIGENCE AI TEST
#
# Admin-only diagnostic endpoint.
#
# This intentionally calls OpenAI once so we can validate the
# weekly intelligence output before adding persistence/cache.
# ============================================================

@app.get(
    "/health-intelligence/weekly"
)
async def weekly_health_intelligence(
    request: Request,
    force_refresh: bool = False,
):

    require_admin(
        request
    )

    try:

        return (
            get_weekly_health_intelligence(
                force_refresh=
                    force_refresh
            )
        )

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=(
                "Weekly Health Intelligence failed: "
                f"{exc}"
            ),
        ) from exc

# ============================================================
# MOBILE WEEKLY HEALTH INTELLIGENCE
#
# Returns cached Weekly Health Intelligence to the iPhone.
# Repeated requests with unchanged deterministic analytics
# do not call OpenAI.
# ============================================================

@app.get(
    "/api/v1/health-intelligence/weekly"
)
async def mobile_weekly_health_intelligence(
    request: Request,
):

    require_ingest_key(
        request
    )

    try:

        return (
            get_weekly_health_intelligence(
                force_refresh=False
            )
        )

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=(
                "Weekly Health Intelligence failed: "
                f"{exc}"
            ),
        ) from exc

# ============================================================
# BODY COMPOSITION TREND ANALYTICS
#
# Admin-only diagnostic endpoint used to validate Hume
# longitudinal body-composition calculations before these
# values become part of goal progress or the mobile API.
#
# This endpoint does NOT call OpenAI.
# ============================================================

@app.get(
    "/health-intelligence/body-composition/analytics"
)
async def body_composition_analytics(
    request: Request,
):

    require_admin(
        request
    )

    try:

        trends = (
            apple_health_trends()
        )

        return {
            "status":
                "ok",

            "methodology":
                trends.get(
                    "methodology"
                ),

            "body_composition":
                trends.get(
                    "body_composition"
                ),

            "body_composition_progress":
                trends.get(
                    "body_composition_progress"
                ),

            "source_transition":
                trends.get(
                    "source_transition"
                ),
        }

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=(
                "Body Composition Analytics failed: "
                f"{exc}"
            ),
        ) from exc
    
    require_admin(
        request
    )

    try:

        trends = (
            apple_health_trends()
        )

        return {
            "status":
                "ok",

            "methodology":
                trends.get(
                    "methodology"
                ),

            "body_composition":
                trends.get(
                    "body_composition"
                ),

            "source_transition":
                trends.get(
                    "source_transition"
                ),
        }

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=(
                "Body Composition Analytics failed: "
                f"{exc}"
            ),
        ) from exc


# ============================================================
# DAILY HEALTH INTELLIGENCE
# ============================================================

@app.get(
    "/health-intelligence/today"
)
async def health_intelligence_today(
    request: Request,
    force_refresh: bool = False,
):

    require_admin(
        request
    )

    try:

        return (
            get_daily_health_intelligence(
                force_refresh=
                    force_refresh
            )
        )

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=(
                "Daily Health Intelligence failed: "
                f"{exc}"
            ),
        ) from exc


# ============================================================
# MOBILE DAILY HEALTH INTELLIGENCE
# ============================================================

@app.get(
    "/api/v1/health-intelligence/today"
)
async def mobile_health_intelligence_today(
    request: Request,
):

    require_ingest_key(
        request
    )

    try:

        return (
            get_daily_health_intelligence(
                force_refresh=False
            )
        )

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=(
                "Daily Health Intelligence failed: "
                f"{exc}"
            ),
        ) from exc


# ============================================================
# TODAY'S DETERMINISTIC PLAN
#
# This endpoint exposes the authoritative deterministic plan.
#
# It does NOT call OpenAI.
# ============================================================

@app.get(
    "/todays-plan"
)
async def todays_plan(
    request: Request,
):

    require_admin(
        request
    )

    try:

        return (
            get_or_build_todays_plan()
        )

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=(
                "Today's deterministic plan failed: "
                f"{exc}"
            ),
        ) from exc


# ============================================================
# MOBILE TODAY'S DETERMINISTIC PLAN
#
# Used by the iPhone for detailed Training, Nutrition,
# Hydration and Sleep drill-down screens.
#
# This endpoint does NOT call OpenAI.
# ============================================================

@app.get(
    "/api/v1/todays-plan"
)
async def mobile_todays_plan(
    request: Request,
):

    require_ingest_key(
        request
    )

    try:

        return (
            get_or_build_todays_plan()
        )

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=(
                "Today's deterministic plan failed: "
                f"{exc}"
            ),
        ) from exc


# ============================================================
# MOBILE TODAY EXPERIENCE
#
# Compact deterministic card contract. No OpenAI call.
# ============================================================

@app.get(
    "/api/v1/today"
)
async def mobile_today_experience(
    request: Request,
):

    require_ingest_key(
        request
    )

    try:
        plan = get_or_build_todays_plan()

        return build_today_experience(
            plan=plan
        )

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Today experience failed: "
                f"{exc}"
            ),
        ) from exc
