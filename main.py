import secrets
from fastapi import FastAPI,Request,HTTPException,Form
from fastapi.responses import RedirectResponse,HTMLResponse
from starlette.middleware.sessions import SessionMiddleware
from config import SESSION_SECRET,ADMIN_PASSWORD,validate_config
from db import init_db,table_counts
from analytics import init_analytics
from baselines import init_baselines,rebuild_baselines,latest_baselines,validate_baselines,metric_history,METRICS

validate_config()
app=FastAPI(title="WHOOP Health Intelligence",version="0.3.3")
app.add_middleware(SessionMiddleware,secret_key=SESSION_SECRET,same_site="lax",https_only=True)

@app.on_event("startup")
def startup():
    init_db(); init_analytics(); init_baselines()

def require_admin(request):
    if request.session.get("admin_authenticated") is not True:
        raise HTTPException(401,detail="Admin login required.")

@app.get("/",response_class=HTMLResponse)
async def home(request:Request):
    if request.session.get("admin_authenticated") is not True:
        return """<html><body style="font-family:Arial;max-width:760px;margin:50px auto"><h1>WHOOP Health Intelligence</h1><form method="post" action="/admin/login"><input type="password" name="password" placeholder="Admin password" required><button type="submit">Sign in</button></form></body></html>"""
    return """<html><body style="font-family:Arial;max-width:920px;margin:50px auto"><h1>WHOOP Health Intelligence</h1><p><b>Phase 3B.1: Hardened Personal Baselines</b></p><p>Baselines use explicit preceding 7/14/30/90 calendar-day ranges. Missing physiological observations are excluded; zero-workout days remain real zeros.</p><ul>
    <li><a href="/analytics/baselines/rebuild">Rebuild hardened baselines</a></li>
    <li><a href="/analytics/baselines/validate">Validate hardened baselines</a></li>
    <li><a href="/analytics/baselines/latest">View latest baselines + coverage</a></li>
    <li><a href="/analytics/baselines/history?metric=hrv_rmssd_milli&limit=30">View HRV history</a></li>
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
async def health():
    return {"status":"ok","phase":"3B.1","version":"0.3.3"}

@app.get("/database/counts")
async def counts(request:Request):
    require_admin(request); return {"status":"ok","counts":table_counts()}

@app.get("/analytics/baselines/rebuild")
async def rebuild(request:Request):
    require_admin(request)
    try: return {"status":"ok",**rebuild_baselines()}
    except Exception as e: raise HTTPException(500,detail=f"Baseline rebuild failed: {e}")

@app.get("/analytics/baselines/latest")
async def latest(request:Request):
    require_admin(request); return {"status":"ok",**latest_baselines()}

@app.get("/analytics/baselines/validate")
async def validate(request:Request):
    require_admin(request); return {"status":"ok",**validate_baselines()}

@app.get("/analytics/baselines/history")
async def history(request:Request,metric:str="hrv_rmssd_milli",limit:int=30):
    require_admin(request)
    try: return {"status":"ok",**metric_history(metric,limit)}
    except ValueError as e: raise HTTPException(400,detail=str(e))

@app.get("/analytics/baselines/metrics")
async def metrics(request:Request):
    require_admin(request); return {"status":"ok","metrics":sorted(METRICS)}
