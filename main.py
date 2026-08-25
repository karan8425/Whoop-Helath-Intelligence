import secrets
from fastapi import FastAPI, Request, HTTPException, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from starlette.middleware.sessions import SessionMiddleware

from config import SESSION_SECRET, ADMIN_PASSWORD, validate_config
from db import init_db, table_counts
from analytics import init_analytics, rebuild_daily_metrics, validate_data_integrity, daily_metrics
from baselines import init_baselines, rebuild_baselines, validate_baselines
from freshness import freshness_status
from automation_status import (
    init_automation_tables,
    latest_stored_intelligence,
    latest_automation_run,
    automation_summary,
)
from debug_whoop_dates import latest_whoop_date_diagnostic

validate_config()

app = FastAPI(title="WHOOP Health Intelligence", version="0.4.5")
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
    <p><b>Phase 4C.2: WHOOP Coaching-Date Alignment</b></p>
    <p>Recovery, HRV, RHR and overnight sleep are assigned to the local wake date.</p>
    <ul>
      <li><a href="/analytics/rebuild">Rebuild corrected daily metrics</a></li>
      <li><a href="/analytics/integrity">Validate corrected daily metrics</a></li>
      <li><a href="/analytics/daily?limit=7">View latest 7 corrected days</a></li>
      <li><a href="/analytics/baselines/rebuild">Rebuild personal baselines</a></li>
      <li><a href="/analytics/baselines/validate">Validate personal baselines</a></li>
      <li><a href="/freshness">Check corrected WHOOP freshness</a></li>
      <li><a href="/debug/latest-whoop-records">Inspect latest raw WHOOP timestamps</a></li>
      <li><a href="/automation/latest-run">View latest automation run</a></li>
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
    return {"status":"ok","phase":"4C.2","version":"0.4.5"}

@app.get("/database/counts")
async def counts(request: Request):
    require_admin(request)
    return {"status":"ok","counts":table_counts()}

@app.get("/analytics/rebuild")
async def rebuild(request: Request):
    require_admin(request)
    try:
        return {"status":"ok", **rebuild_daily_metrics()}
    except Exception as exc:
        raise HTTPException(500, detail=f"Daily metrics rebuild failed: {exc}") from exc

@app.get("/analytics/integrity")
async def integrity(request: Request):
    require_admin(request)
    return {"status":"ok", **validate_data_integrity()}

@app.get("/analytics/daily")
async def daily(request: Request, limit: int = 7):
    require_admin(request)
    return {"status":"ok","records":daily_metrics(limit)}

@app.get("/analytics/baselines/rebuild")
async def baseline_rebuild(request: Request):
    require_admin(request)
    try:
        return {"status":"ok", **rebuild_baselines()}
    except Exception as exc:
        raise HTTPException(500, detail=f"Baseline rebuild failed: {exc}") from exc

@app.get("/analytics/baselines/validate")
async def baseline_validate(request: Request):
    require_admin(request)
    return {"status":"ok", **validate_baselines()}

@app.get("/freshness")
async def freshness(request: Request):
    require_admin(request)
    return {"status":"ok", **freshness_status()}

@app.get("/automation/status")
async def automation_status_route(request: Request):
    require_admin(request)
    return {"status":"ok", **automation_summary()}

@app.get("/automation/latest-run")
async def latest_run(request: Request):
    require_admin(request)
    return {"status":"ok","run":latest_automation_run()}

@app.get("/intelligence/stored/latest")
async def stored_latest(request: Request):
    require_admin(request)
    return {"status":"ok","intelligence":latest_stored_intelligence()}

@app.get("/debug/latest-whoop-records")
async def debug_latest(request: Request):
    require_admin(request)
    return {"status":"ok", **latest_whoop_date_diagnostic()}
