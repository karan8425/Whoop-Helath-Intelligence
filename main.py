import secrets
from fastapi import FastAPI, Request, HTTPException, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from starlette.middleware.sessions import SessionMiddleware

from config import SESSION_SECRET, ADMIN_PASSWORD, validate_config
from db import init_db, table_counts
from analytics import init_analytics
from baselines import init_baselines
from recommendations import daily_recommendation, validate_recommendation

validate_config()

app = FastAPI(title="WHOOP Health Intelligence", version="0.4.0")
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

    return """<html><body style="font-family:Arial;max-width:940px;margin:50px auto">
    <h1>WHOOP Health Intelligence</h1>
    <p><b>Phase 4A: Daily Recommendation Engine</b></p>
    <p>This phase converts validated WHOOP signals into a deterministic daily recommendation. No LLM is used yet.</p>
    <ul>
      <li><a href="/intelligence/today">View today's recommendation</a></li>
      <li><a href="/intelligence/validate">Validate recommendation engine</a></li>
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
    return {"status": "ok", "phase": "4A", "version": "0.4.0"}

@app.get("/database/counts")
async def counts(request: Request):
    require_admin(request)
    return {"status": "ok", "counts": table_counts()}

@app.get("/intelligence/today")
async def intelligence_today(request: Request):
    require_admin(request)
    return {"status": "ok", **daily_recommendation()}

@app.get("/intelligence/validate")
async def intelligence_validate(request: Request):
    require_admin(request)
    return validate_recommendation()
