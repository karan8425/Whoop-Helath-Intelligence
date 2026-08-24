import secrets
from fastapi import FastAPI,Request,HTTPException,Form
from fastapi.responses import RedirectResponse,HTMLResponse
from starlette.middleware.sessions import SessionMiddleware
from config import SESSION_SECRET,ADMIN_PASSWORD,validate_config
from db import init_db,table_counts
from analytics import init_analytics,rebuild_daily_metrics,validate_data_integrity,daily_metrics

validate_config()
app=FastAPI(title="WHOOP Health Intelligence",version="0.3.1")
app.add_middleware(SessionMiddleware,secret_key=SESSION_SECRET,same_site="lax",https_only=True)

@app.on_event("startup")
def startup():
    init_db(); init_analytics()

def require_admin(request):
    if request.session.get("admin_authenticated") is not True:
        raise HTTPException(401,detail="Admin login required.")

@app.get("/",response_class=HTMLResponse)
async def home(request:Request):
    if request.session.get("admin_authenticated") is not True:
        return """<html><body style="font-family:Arial;max-width:760px;margin:50px auto"><h1>WHOOP Health Intelligence</h1><form method="post" action="/admin/login"><input type="password" name="password" placeholder="Admin password" required><button type="submit">Sign in</button></form></body></html>"""
    return """<html><body style="font-family:Arial;max-width:900px;margin:50px auto"><h1>WHOOP Health Intelligence</h1><p><b>Phase 3A.1: Daily Data Integrity</b></p><p>This version creates a continuous calendar-day spine and explicitly shows days missing cycle/recovery/sleep data.</p><ul>
    <li><a href="/analytics/rebuild">Rebuild calendar-based daily metrics</a></li>
    <li><a href="/analytics/integrity">Validate data integrity</a></li>
    <li><a href="/analytics/daily?limit=14">View latest 14 daily metrics</a></li>
    <li><a href="/database/counts">View source database counts</a></li>
    <li><a href="/admin/logout">Sign out</a></li></ul></body></html>"""

@app.post("/admin/login")
async def login(request:Request,password:str=Form(...)):
    if not secrets.compare_digest(password,ADMIN_PASSWORD): raise HTTPException(401,detail="Incorrect admin password.")
    request.session["admin_authenticated"]=True
    return RedirectResponse("/",303)

@app.get("/admin/logout")
async def logout(request:Request):
    request.session.clear(); return RedirectResponse("/",303)

@app.get("/health")
async def health(): return {"status":"ok","phase":"3A.1","version":"0.3.1"}

@app.get("/database/counts")
async def counts(request:Request):
    require_admin(request); return {"status":"ok","counts":table_counts()}

@app.get("/analytics/rebuild")
async def rebuild(request:Request):
    require_admin(request)
    try: return {"status":"ok",**rebuild_daily_metrics()}
    except Exception as e: raise HTTPException(500,detail=f"Daily metrics rebuild failed: {e}")

@app.get("/analytics/integrity")
async def integrity(request:Request):
    require_admin(request)
    try: return {"status":"ok",**validate_data_integrity()}
    except Exception as e: raise HTTPException(500,detail=f"Data integrity validation failed: {e}")

@app.get("/analytics/daily")
async def daily(request:Request,limit:int=14):
    require_admin(request); return {"status":"ok","records":daily_metrics(limit)}
