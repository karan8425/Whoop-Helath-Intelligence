import secrets
from fastapi import FastAPI, Request, HTTPException, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from starlette.middleware.sessions import SessionMiddleware

from config import SESSION_SECRET, ADMIN_PASSWORD, validate_config
from db import init_db, table_counts
from analytics import init_analytics
from baselines import init_baselines
from freshness import freshness_status
from automation_status import (
    init_automation_tables,
    latest_stored_intelligence,
    latest_automation_run,
    automation_summary,
)
from coach_api import (
    require_action_api_key,
    coach_today,
    coach_status,
    coach_daily_history,
    coach_latest_baselines,
    coach_metric_history,
)

validate_config()

app = FastAPI(title="WHOOP Health Intelligence", version="0.4.4")
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    https_only=True,
)

@app.on_event("startup")
def startup():
    init_db()
    init_analytics()
    init_baselines()
    init_automation_tables()

def require_admin(request):
    if request.session.get("admin_authenticated") is not True:
        raise HTTPException(status_code=401, detail="Admin login required.")

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    if request.session.get("admin_authenticated") is not True:
        return """<html><body style="font-family:Arial;max-width:760px;margin:50px auto">
        <h1>WHOOP Health Intelligence</h1>
        <form method="post" action="/admin/login">
        <input type="password" name="password" placeholder="Admin password" required>
        <button type="submit">Sign in</button></form></body></html>"""

    return """<html><body style="font-family:Arial;max-width:980px;margin:50px auto">
    <h1>WHOOP Health Intelligence</h1>
    <p><b>Phase 4D: ChatGPT Read-Only Interface</b></p>
    <p>Your normal admin pages remain private. A separate API-key-protected read-only interface is available for a custom GPT Action.</p>
    <ul>
      <li><a href="/freshness">Check current WHOOP freshness</a></li>
      <li><a href="/automation/status">View automation status</a></li>
      <li><a href="/automation/latest-run">View latest automation run</a></li>
      <li><a href="/intelligence/stored/latest">View latest stored intelligence</a></li>
      <li><a href="/database/counts">View source database counts</a></li>
      <li><a href="/admin/logout">Sign out</a></li>
    </ul>
    </body></html>"""

@app.post("/admin/login")
async def login(request: Request, password: str = Form(...)):
    if not secrets.compare_digest(password, ADMIN_PASSWORD):
        raise HTTPException(status_code=401, detail="Incorrect admin password.")
    request.session["admin_authenticated"] = True
    return RedirectResponse("/", status_code=303)

@app.get("/admin/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)

@app.get("/health")
async def health():
    return {"status": "ok", "phase": "4D", "version": "0.4.4"}

@app.get("/freshness")
async def freshness(request: Request):
    require_admin(request)
    return {"status": "ok", **freshness_status()}

@app.get("/database/counts")
async def counts(request: Request):
    require_admin(request)
    return {"status": "ok", "counts": table_counts()}

@app.get("/automation/status")
async def status(request: Request):
    require_admin(request)
    return {"status": "ok", **automation_summary()}

@app.get("/automation/latest-run")
async def latest_run(request: Request):
    require_admin(request)
    return {"status": "ok", "run": latest_automation_run()}

@app.get("/intelligence/stored/latest")
async def stored_latest(request: Request):
    require_admin(request)
    return {"status": "ok", "intelligence": latest_stored_intelligence()}


# -------------------------------
# ChatGPT Action: read-only API
# -------------------------------

@app.get("/api/v1/coach/today")
async def api_coach_today(request: Request):
    require_action_api_key(request)
    return {"status": "ok", **coach_today()}

@app.get("/api/v1/coach/status")
async def api_coach_status(request: Request):
    require_action_api_key(request)
    return {"status": "ok", **coach_status()}

@app.get("/api/v1/coach/daily")
async def api_coach_daily(request: Request, days: int = 30):
    require_action_api_key(request)
    return {"status": "ok", **coach_daily_history(days)}

@app.get("/api/v1/coach/baselines")
async def api_coach_baselines(request: Request):
    require_action_api_key(request)
    return {"status": "ok", **coach_latest_baselines()}

@app.get("/api/v1/coach/metric-history")
async def api_metric_history(
    request: Request,
    metric: str = "hrv_rmssd_milli",
    days: int = 30,
):
    require_action_api_key(request)
    try:
        return {"status": "ok", **coach_metric_history(metric, days)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
