import secrets
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from starlette.middleware.sessions import SessionMiddleware

from config import SESSION_SECRET, validate_config
from db import init_db
from whoop import authorization_url, exchange_code, get_phase1_snapshot

validate_config()
init_db()

app = FastAPI(title="WHOOP Health Intelligence", version="0.1.0")
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    https_only=True,
)

@app.get("/", response_class=HTMLResponse)
async def home():
    return '''
    <html>
      <head><title>WHOOP Health Intelligence</title></head>
      <body style="font-family:Arial;max-width:780px;margin:40px auto;line-height:1.5">
        <h1>WHOOP Health Intelligence</h1>
        <p>Phase 1: connect your WHOOP account and verify data retrieval.</p>
        <p><a href="/whoop/login">Connect WHOOP</a></p>
        <p>After authorization, test <a href="/whoop/snapshot">/whoop/snapshot</a>.</p>
      </body>
    </html>
    '''

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/whoop/login")
async def whoop_login(request: Request):
    state = secrets.token_urlsafe(6)[:8]
    request.session["oauth_state"] = state
    return RedirectResponse(authorization_url(state))

@app.get("/whoop/callback")
async def whoop_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    if error:
        raise HTTPException(status_code=400, detail=f"WHOOP authorization error: {error}")
    expected_state = request.session.pop("oauth_state", None)
    if not state or state != expected_state:
        raise HTTPException(status_code=400, detail="Invalid OAuth state.")
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code.")
    try:
        await exchange_code(code)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"WHOOP token exchange failed: {exc}") from exc
    return HTMLResponse(
        '''
        <html><body style="font-family:Arial;max-width:780px;margin:40px auto">
        <h2>WHOOP connected successfully.</h2>
        <p>Next: <a href="/whoop/snapshot">retrieve your WHOOP snapshot</a>.</p>
        </body></html>
        '''
    )

@app.get("/whoop/snapshot")
async def whoop_snapshot():
    try:
        return await get_phase1_snapshot()
    except RuntimeError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"WHOOP API request failed: {exc}") from exc
