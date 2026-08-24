import secrets
from fastapi import FastAPI, Request, HTTPException, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from starlette.middleware.sessions import SessionMiddleware
from config import SESSION_SECRET, ADMIN_PASSWORD, validate_config
from db import init_db, table_counts
from analytics import init_analytics
from baselines import init_baselines
from freshness import freshness_status
from automation_status import init_automation_tables, latest_stored_intelligence, latest_automation_run, automation_summary
from healthkit_ingest import init_apple_health_tables, require_ingest_key, ingest_healthkit_payload, latest_apple_health

validate_config()
app=FastAPI(title="WHOOP Health Intelligence",version="0.5.0")
app.add_middleware(SessionMiddleware,secret_key=SESSION_SECRET,same_site="lax",https_only=True)

@app.on_event("startup")
def startup():
    init_db(); init_analytics(); init_baselines(); init_automation_tables(); init_apple_health_tables()

def require_admin(request):
    if request.session.get("admin_authenticated") is not True:
        raise HTTPException(401,detail="Admin login required.")

@app.get("/",response_class=HTMLResponse)
async def home(request:Request):
    if request.session.get("admin_authenticated") is not True:
        return """<html><body style="font-family:Arial;max-width:760px;margin:50px auto"><h1>WHOOP Health Intelligence</h1><form method="post" action="/admin/login"><input type="password" name="password" placeholder="Admin password" required><button type="submit">Sign in</button></form></body></html>"""
    return """<html><body style="font-family:Arial;max-width:980px;margin:50px auto"><h1>WHOOP Health Intelligence</h1><p><b>Phase 5A: Apple Health + Hume ingestion</b></p><ul>
    <li><a href="/apple-health/latest">View latest Apple Health / Hume data</a></li>
    <li><a href="/freshness">Check WHOOP freshness</a></li>
    <li><a href="/automation/status">View WHOOP automation status</a></li>
    <li><a href="/automation/latest-run">View latest WHOOP automation run</a></li>
    <li><a href="/intelligence/stored/latest">View latest WHOOP intelligence</a></li>
    <li><a href="/database/counts">View WHOOP source counts</a></li>
    <li><a href="/admin/logout">Sign out</a></li></ul></body></html>"""

@app.post("/admin/login")
async def login(request:Request,password:str=Form(...)):
    if not secrets.compare_digest(password,ADMIN_PASSWORD):
        raise HTTPException(401,detail="Incorrect admin password.")
    request.session["admin_authenticated"]=True
    return RedirectResponse("/",303)

@app.get("/admin/logout")
async def logout(request:Request):
    request.session.clear(); return RedirectResponse("/",303)

@app.get("/health")
async def health():
    return {"status":"ok","phase":"5A","version":"0.5.0"}

@app.get("/freshness")
async def freshness(request:Request):
    require_admin(request); return {"status":"ok",**freshness_status()}

@app.get("/database/counts")
async def counts(request:Request):
    require_admin(request); return {"status":"ok","counts":table_counts()}

@app.get("/automation/status")
async def status(request:Request):
    require_admin(request); return {"status":"ok",**automation_summary()}

@app.get("/automation/latest-run")
async def latest_run(request:Request):
    require_admin(request); return {"status":"ok","run":latest_automation_run()}

@app.get("/intelligence/stored/latest")
async def stored_latest(request:Request):
    require_admin(request); return {"status":"ok","intelligence":latest_stored_intelligence()}

@app.post("/api/v1/apple-health/ingest")
async def apple_health_ingest(request:Request):
    require_ingest_key(request)
    payload=await request.json()
    try:
        return ingest_healthkit_payload(payload)
    except Exception as exc:
        raise HTTPException(400,detail=f"Apple Health ingest failed: {exc}") from exc

@app.get("/apple-health/latest")
async def apple_health_latest(request:Request):
    require_admin(request); return {"status":"ok",**latest_apple_health()}
