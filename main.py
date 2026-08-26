import secrets

from fastapi import FastAPI, Request, HTTPException, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from starlette.middleware.sessions import SessionMiddleware

from config import SESSION_SECRET, ADMIN_PASSWORD, validate_config
from db import init_db

from analytics import (
    init_analytics,
)

from baselines import (
    init_baselines,
)

from freshness import freshness_status

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

from apple_health_trends import apple_health_trends

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


# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------

validate_config()

app = FastAPI(
    title="WHOOP Health Intelligence",
    version="0.5.3",
)

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    https_only=True,
)


# ---------------------------------------------------------
# Startup
# ---------------------------------------------------------

@app.on_event("startup")
def startup():
    init_db()
    init_analytics()
    init_baselines()
    init_automation_tables()
    init_apple_health_tables()
    init_goal_profiles()


# ---------------------------------------------------------
# Authentication
# ---------------------------------------------------------

def require_admin(request: Request):
    if request.session.get("admin_authenticated") is not True:
        raise HTTPException(
            status_code=401,
            detail="Admin login required.",
        )


# ---------------------------------------------------------
# Home / Health
# ---------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):

    if request.session.get("admin_authenticated") is not True:
        return """
        <html>
            <body>
                <h1>WHOOP Health Intelligence</h1>

                <form method="post" action="/admin/login">
                    <input
                        type="password"
                        name="password"
                        placeholder="Admin password"
                    >
                    <button>Sign in</button>
                </form>
            </body>
        </html>
        """

    return """
    <html>
        <body>
            <h1>WHOOP Health Intelligence</h1>

            <p><b>Phase 5D.1</b></p>

            <ul>
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


@app.post("/admin/login")
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

    request.session["admin_authenticated"] = True

    return RedirectResponse(
        "/",
        status_code=303,
    )


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "phase": "5D.1",
        "version": "0.5.3",
    }


# ---------------------------------------------------------
# WHOOP
# ---------------------------------------------------------

@app.get("/freshness")
async def freshness(request: Request):
    require_admin(request)

    return {
        "status": "ok",
        **freshness_status(),
    }


# ---------------------------------------------------------
# Automation
# ---------------------------------------------------------

@app.get("/automation/latest-run")
async def latest_run(request: Request):
    require_admin(request)

    return {
        "status": "ok",
        "run": latest_automation_run(),
    }


# ---------------------------------------------------------
# Apple Health Ingest
# ---------------------------------------------------------

@app.post("/api/v1/apple-health/ingest")
async def apple_health_ingest(request: Request):

    require_ingest_key(request)

    payload = await request.json()

    return ingest_healthkit_payload(
        payload
    )


# ---------------------------------------------------------
# Apple Health Admin Endpoints
# ---------------------------------------------------------

@app.get("/apple-health/latest")
async def apple_health_latest(request: Request):
    require_admin(request)

    return {
        "status": "ok",
        **latest_apple_health(),
    }


@app.get("/apple-health/history/summary")
async def apple_health_history_summary_route(
    request: Request,
):
    require_admin(request)

    return {
        "status": "ok",
        **apple_health_history_summary(),
    }


@app.get("/apple-health/trends")
async def apple_health_trends_route(
    request: Request,
):
    require_admin(request)

    return apple_health_trends()


# ---------------------------------------------------------
# Combined Coaching
# ---------------------------------------------------------

@app.get("/coaching/combined/today")
async def combined_today(request: Request):
    require_admin(request)

    return combined_daily_snapshot()


@app.get("/coaching/combined/recommendation")
async def combined_recommendation(
    request: Request,
):
    require_admin(request)

    return combined_deterministic_coaching()


@app.get("/coaching/combined/ai")
async def combined_ai(request: Request):
    require_admin(request)

    try:
        return validate_combined_ai_connection()

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Combined AI coaching failed: "
                f"{exc}"
            ),
        ) from exc


# ---------------------------------------------------------
# Mobile Coaching API
# ---------------------------------------------------------

@app.get("/api/v1/coaching/today")
async def mobile_combined_coaching(
    request: Request,
):

    require_ingest_key(request)

    try:
        return validate_combined_ai_connection()

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Combined AI coaching failed: "
                f"{exc}"
            ),
        ) from exc


# ---------------------------------------------------------
# Goal Admin Endpoints
# ---------------------------------------------------------

@app.get("/goals/active")
async def goals_active(request: Request):
    require_admin(request)

    return {
        "status": "ok",
        "goal": get_active_goal(),
    }


@app.get("/goals/history")
async def goals_history(request: Request):
    require_admin(request)

    return {
        "status": "ok",
        "goals": get_goal_history(),
    }


@app.post("/goals")
async def goals_save(request: Request):
    require_admin(request)

    try:
        payload = await request.json()

        return {
            "status": "ok",
            "goal": save_goal_profile(
                payload
            ),
        }

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc


# ---------------------------------------------------------
# One-Time Goal Snapshot Backfill
# ---------------------------------------------------------

@app.get("/goals/backfill-active-start")
async def goals_backfill_active_start(
    request: Request,
):

    require_admin(request)

    return (
        backfill_active_goal_start_snapshot()
    )


# ---------------------------------------------------------
# Mobile Goal API
# ---------------------------------------------------------

@app.get("/api/v1/goals/active")
async def mobile_goals_active(
    request: Request,
):

    require_ingest_key(request)

    return {
        "status": "ok",
        "goal": get_active_goal(),
    }


@app.post("/api/v1/goals")
async def mobile_goals_save(
    request: Request,
):

    require_ingest_key(request)

    try:
        payload = await request.json()

        return {
            "status": "ok",
            "goal": save_goal_profile(
                payload
            ),
        }

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc
