import secrets
from fastapi import FastAPI, Request, HTTPException, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from starlette.middleware.sessions import SessionMiddleware
from config import SESSION_SECRET, ADMIN_PASSWORD, validate_config
from db import init_db, table_counts
from analytics import init_analytics, rebuild_daily_metrics, validate_data_integrity, daily_metrics
from baselines import init_baselines, rebuild_baselines, validate_baselines
from freshness import freshness_status
from automation_status import init_automation_tables, latest_stored_intelligence, latest_automation_run, automation_summary
from debug_whoop_dates import latest_whoop_date_diagnostic
from healthkit_ingest import init_apple_health_tables, require_ingest_key, ingest_healthkit_payload, latest_apple_health

validate_config()
app=FastAPI(title="WHOOP Health Intelligence",version="0.5.1")
app.add_middleware(SessionMiddleware,secret_key=SESSION_SECRET,same_site="lax",https_only=True)

@app.on_event("startup")
def startup():
    init_db(); init_analytics(); init_baselines(); init_automation_tables(); init_apple_health_tables()

def require_admin(request):
    if request.session.get("admin_authenticated") is not True:
        raise HTTPException(status_code=401,detail="Admin login required.")

@app.get("/",response_class=HTMLResponse)
async def home(request:Request):
    if request.session.get("admin_authenticated") is not True:
        return """<html><body><h1>WHOOP Health Intelligence</h1><form method="post" action="/admin/login"><input type="password" name="password"><button>Sign in</button></form></body></html>"""
    return """<html><body><h1>WHOOP Health Intelligence</h1><p><b>Phase 5A.2</b></p><ul><li><a href="/apple-health/latest">View latest Apple Health / Hume body data</a></li><li><a href="/freshness">Check WHOOP freshness</a></li><li><a href="/automation/latest-run">View latest automation run</a></li></ul></body></html>"""

@app.post("/admin/login")
async def login(request:Request,password:str=Form(...)):
    if not secrets.compare_digest(password,ADMIN_PASSWORD):
        raise HTTPException(status_code=401,detail="Incorrect admin password.")
    request.session["admin_authenticated"]=True
    return RedirectResponse("/",303)

@app.get("/health")
async def health(): return {"status":"ok","phase":"5A.2","version":"0.5.1"}

@app.get("/freshness")
async def freshness(request:Request):
    require_admin(request); return {"status":"ok",**freshness_status()}

@app.get("/automation/latest-run")
async def latest_run(request:Request):
    require_admin(request); return {"status":"ok","run":latest_automation_run()}

@app.post("/api/v1/apple-health/ingest")
async def apple_health_ingest(request:Request):
    require_ingest_key(request)
    return ingest_healthkit_payload(await request.json())

@app.get("/apple-health/latest")
async def apple_health_latest(request:Request):
    require_admin(request); return {"status":"ok","body":latest_apple_health()}
