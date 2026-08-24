import secrets
from fastapi import FastAPI,Request,HTTPException,Form
from fastapi.responses import RedirectResponse,HTMLResponse
from starlette.middleware.sessions import SessionMiddleware
from config import SESSION_SECRET,ADMIN_PASSWORD,validate_config
from db import init_db,database_health,table_counts
from whoop import authorization_url,exchange_code,get_phase1_snapshot
from sync import sync_batch,sync_status,init_sync_state

validate_config()
app=FastAPI(title="WHOOP Health Intelligence",version="0.2.1")
app.add_middleware(SessionMiddleware,secret_key=SESSION_SECRET,same_site="lax",https_only=True)

@app.on_event("startup")
def startup():
    init_db(); init_sync_state()

def is_admin(request): return request.session.get("admin_authenticated") is True
def require_admin(request):
    if not is_admin(request): raise HTTPException(status_code=401,detail="Admin login required.")

@app.get("/",response_class=HTMLResponse)
async def home(request:Request):
    if not is_admin(request):
        return """<html><body style="font-family:Arial;max-width:760px;margin:50px auto"><h1>WHOOP Health Intelligence</h1><form method="post" action="/admin/login"><input type="password" name="password" placeholder="Admin password" required><button type="submit">Sign in</button></form></body></html>"""
    return """<html><body style="font-family:Arial;max-width:850px;margin:50px auto"><h1>WHOOP Health Intelligence</h1><p><b>Phase 2: Resumable Historical Import</b></p><p>Run small batches until sync status says complete = true.</p><ul><li><a href="/database/health">Test Supabase database</a></li><li><a href="/whoop/login">Reconnect WHOOP</a></li><li><a href="/whoop/snapshot">Recent WHOOP snapshot</a></li><li><a href="/whoop/sync/batch">Run next historical sync batch</a></li><li><a href="/whoop/sync/status">View historical sync status</a></li><li><a href="/database/counts">View database record counts</a></li><li><a href="/admin/logout">Sign out</a></li></ul></body></html>"""

@app.post("/admin/login")
async def admin_login(request:Request,password:str=Form(...)):
    if not secrets.compare_digest(password,ADMIN_PASSWORD): raise HTTPException(status_code=401,detail="Incorrect admin password.")
    request.session["admin_authenticated"]=True
    return RedirectResponse("/",status_code=303)

@app.get("/admin/logout")
async def logout(request:Request):
    request.session.clear(); return RedirectResponse("/",status_code=303)

@app.get("/health")
async def health(): return {"status":"ok","phase":2,"version":"0.2.1"}

@app.get("/database/health")
async def db_health(request:Request):
    require_admin(request); return database_health()

@app.get("/database/counts")
async def db_counts(request:Request):
    require_admin(request); return {"status":"ok","counts":table_counts()}

@app.get("/whoop/login")
async def whoop_login(request:Request):
    require_admin(request)
    state=secrets.token_urlsafe(6)[:8]; request.session["oauth_state"]=state
    return RedirectResponse(authorization_url(state))

@app.get("/whoop/callback")
async def callback(request:Request,code:str|None=None,state:str|None=None,error:str|None=None):
    require_admin(request)
    if error: raise HTTPException(400,detail=error)
    expected=request.session.pop("oauth_state",None)
    if not state or state!=expected: raise HTTPException(400,detail="Invalid OAuth state.")
    if not code: raise HTTPException(400,detail="Missing authorization code.")
    await exchange_code(code)
    return HTMLResponse('<h2>WHOOP connected successfully.</h2><p>Token stored encrypted in Supabase.</p><a href="/">Return</a>')

@app.get("/whoop/snapshot")
async def snapshot(request:Request):
    require_admin(request); return await get_phase1_snapshot()

@app.get("/whoop/sync/batch")
async def batch(request:Request):
    require_admin(request)
    try: return await sync_batch(3)
    except Exception as exc: raise HTTPException(502,detail=f"Historical sync batch failed: {exc}")

@app.get("/whoop/sync/status")
async def status(request:Request):
    require_admin(request); return sync_status()
