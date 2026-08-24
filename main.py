import secrets
from fastapi import FastAPI,Request,HTTPException,Form
from fastapi.responses import RedirectResponse,HTMLResponse
from starlette.middleware.sessions import SessionMiddleware
from config import SESSION_SECRET,ADMIN_PASSWORD,validate_config
from db import init_db,database_health,table_counts
from whoop import authorization_url,exchange_code,get_phase1_snapshot
from sync import full_historical_sync

validate_config()
app=FastAPI(title="WHOOP Health Intelligence",version="0.2.0")
app.add_middleware(SessionMiddleware,secret_key=SESSION_SECRET,same_site="lax",https_only=True)

@app.on_event("startup")
def startup(): init_db()

def is_admin(request): return request.session.get("admin_authenticated") is True
def require_admin(request):
    if not is_admin(request): raise HTTPException(status_code=401,detail="Admin login required.")

@app.get("/",response_class=HTMLResponse)
async def home(request:Request):
    if not is_admin(request):
        return '''<html><body style="font-family:Arial;max-width:760px;margin:50px auto"><h1>WHOOP Health Intelligence</h1><p>Private personal health intelligence service.</p><form method="post" action="/admin/login"><input type="password" name="password" placeholder="Admin password" required style="padding:10px;width:300px"><button type="submit" style="padding:10px">Sign in</button></form></body></html>'''
    return '''<html><body style="font-family:Arial;max-width:800px;margin:50px auto"><h1>WHOOP Health Intelligence</h1><p><b>Phase 2: Data Layer</b></p><ul><li><a href="/database/health">Test Supabase database</a></li><li><a href="/whoop/login">Reconnect WHOOP</a></li><li><a href="/whoop/snapshot">Recent WHOOP snapshot</a></li><li><a href="/whoop/sync/full">Run full historical WHOOP sync</a></li><li><a href="/database/counts">View database record counts</a></li><li><a href="/admin/logout">Sign out</a></li></ul></body></html>'''

@app.post("/admin/login")
async def admin_login(request:Request,password:str=Form(...)):
    if not secrets.compare_digest(password,ADMIN_PASSWORD): raise HTTPException(status_code=401,detail="Incorrect admin password.")
    request.session["admin_authenticated"]=True
    return RedirectResponse("/",status_code=303)

@app.get("/admin/logout")
async def logout(request:Request):
    request.session.clear(); return RedirectResponse("/",status_code=303)

@app.get("/health")
async def health(): return {"status":"ok","service":"whoop-health-intelligence","phase":2}

@app.get("/database/health")
async def db_health(request:Request):
    require_admin(request)
    try: return database_health()
    except Exception as exc: raise HTTPException(status_code=502,detail=f"Database connection failed: {exc}")

@app.get("/database/counts")
async def db_counts(request:Request):
    require_admin(request)
    try: return {"status":"ok","counts":table_counts()}
    except Exception as exc: raise HTTPException(status_code=502,detail=f"Could not read database counts: {exc}")

@app.get("/whoop/login")
async def whoop_login(request:Request):
    require_admin(request)
    state=secrets.token_urlsafe(6)[:8]; request.session["oauth_state"]=state
    return RedirectResponse(authorization_url(state))

@app.get("/whoop/callback")
async def whoop_callback(request:Request,code:str|None=None,state:str|None=None,error:str|None=None):
    require_admin(request)
    if error: raise HTTPException(status_code=400,detail=f"WHOOP authorization error: {error}")
    expected=request.session.pop("oauth_state",None)
    if not state or state!=expected: raise HTTPException(status_code=400,detail="Invalid OAuth state.")
    if not code: raise HTTPException(status_code=400,detail="Missing authorization code.")
    try: await exchange_code(code)
    except Exception as exc: raise HTTPException(status_code=502,detail=f"WHOOP token exchange failed: {exc}")
    return HTMLResponse('<html><body style="font-family:Arial;max-width:780px;margin:40px auto"><h2>WHOOP connected successfully.</h2><p>The token is now encrypted and stored in Supabase.</p><p><a href="/">Return to Phase 2 controls</a></p></body></html>')

@app.get("/whoop/snapshot")
async def snapshot(request:Request):
    require_admin(request)
    try: return await get_phase1_snapshot()
    except Exception as exc: raise HTTPException(status_code=502,detail=f"WHOOP API request failed: {exc}")

@app.get("/whoop/sync/full")
async def sync_full(request:Request):
    require_admin(request)
    try: return await full_historical_sync()
    except Exception as exc: raise HTTPException(status_code=502,detail=f"Historical sync failed: {exc}")
