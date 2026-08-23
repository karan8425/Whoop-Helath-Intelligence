import json
from datetime import datetime, timezone
from urllib.parse import urlencode
import httpx

from .config import (
    WHOOP_AUTH_URL,
    WHOOP_TOKEN_URL,
    WHOOP_API_BASE,
    WHOOP_CLIENT_ID,
    WHOOP_CLIENT_SECRET,
    WHOOP_REDIRECT_URI,
    WHOOP_SCOPES,
)
from .db import load_token_json, save_token_json

def authorization_url(state: str) -> str:
    params = {
        "client_id": WHOOP_CLIENT_ID,
        "redirect_uri": WHOOP_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(WHOOP_SCOPES),
        "state": state,
    }
    return f"{WHOOP_AUTH_URL}?{urlencode(params)}"

async def exchange_code(code: str) -> dict:
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": WHOOP_CLIENT_ID,
        "client_secret": WHOOP_CLIENT_SECRET,
        "redirect_uri": WHOOP_REDIRECT_URI,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(WHOOP_TOKEN_URL, data=payload)
        response.raise_for_status()
        token = response.json()
    token["_obtained_at"] = int(datetime.now(timezone.utc).timestamp())
    save_token_json(json.dumps(token))
    return token

async def refresh_token(refresh_token: str) -> dict:
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": WHOOP_CLIENT_ID,
        "client_secret": WHOOP_CLIENT_SECRET,
        "scope": " ".join(WHOOP_SCOPES),
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(WHOOP_TOKEN_URL, data=payload)
        response.raise_for_status()
        token = response.json()
    token["_obtained_at"] = int(datetime.now(timezone.utc).timestamp())
    # WHOOP rotates refresh tokens. Persist the new complete token immediately.
    save_token_json(json.dumps(token))
    return token

def _expired(token: dict) -> bool:
    obtained = token.get("_obtained_at", 0)
    expires_in = token.get("expires_in", 0)
    # Refresh 60 seconds before expiry.
    return int(datetime.now(timezone.utc).timestamp()) >= obtained + max(expires_in - 60, 0)

async def get_valid_token() -> dict:
    raw = load_token_json()
    if not raw:
        raise RuntimeError("WHOOP account is not connected yet.")
    token = json.loads(raw)
    if _expired(token):
        if not token.get("refresh_token"):
            raise RuntimeError("WHOOP token expired and no refresh token is available. Reconnect WHOOP.")
        token = await refresh_token(token["refresh_token"])
    return token

async def whoop_get(path: str, params: dict | None = None):
    token = await get_valid_token()
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    url = f"{WHOOP_API_BASE}{path}"
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url, headers=headers, params=params)
        if response.status_code == 401 and token.get("refresh_token"):
            token = await refresh_token(token["refresh_token"])
            headers = {"Authorization": f"Bearer {token['access_token']}"}
            response = await client.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()

async def get_phase1_snapshot():
    profile = await whoop_get("/v2/user/profile/basic")
    body = await whoop_get("/v2/user/measurement/body")
    recoveries = await whoop_get("/v2/recovery", {"limit": 10})
    cycles = await whoop_get("/v2/cycle", {"limit": 10})
    sleeps = await whoop_get("/v2/activity/sleep", {"limit": 10})
    workouts = await whoop_get("/v2/activity/workout", {"limit": 10})
    return {
        "profile": profile,
        "body_measurement": body,
        "recovery": recoveries,
        "cycles": cycles,
        "sleep": sleeps,
        "workouts": workouts,
    }
