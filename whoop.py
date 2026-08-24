import asyncio, json
from datetime import datetime, timezone
from urllib.parse import urlencode
import httpx
from config import *
from db import load_token_json, save_token_json

def authorization_url(state):
    params={"client_id":WHOOP_CLIENT_ID,"redirect_uri":WHOOP_REDIRECT_URI,"response_type":"code","scope":" ".join(WHOOP_SCOPES),"state":state}
    return f"{WHOOP_AUTH_URL}?{urlencode(params)}"

async def exchange_code(code):
    payload={"grant_type":"authorization_code","code":code,"client_id":WHOOP_CLIENT_ID,"client_secret":WHOOP_CLIENT_SECRET,"redirect_uri":WHOOP_REDIRECT_URI}
    async with httpx.AsyncClient(timeout=30) as client:
        r=await client.post(WHOOP_TOKEN_URL,data=payload); r.raise_for_status(); token=r.json()
    token["_obtained_at"]=int(datetime.now(timezone.utc).timestamp())
    save_token_json(json.dumps(token)); return token

async def refresh_token(refresh_token_value):
    payload={"grant_type":"refresh_token","refresh_token":refresh_token_value,"client_id":WHOOP_CLIENT_ID,"client_secret":WHOOP_CLIENT_SECRET,"scope":" ".join(WHOOP_SCOPES)}
    async with httpx.AsyncClient(timeout=30) as client:
        r=await client.post(WHOOP_TOKEN_URL,data=payload); r.raise_for_status(); token=r.json()
    token["_obtained_at"]=int(datetime.now(timezone.utc).timestamp())
    save_token_json(json.dumps(token)); return token

def _expired(token):
    now=int(datetime.now(timezone.utc).timestamp())
    return now >= token.get("_obtained_at",0)+max(token.get("expires_in",0)-60,0)

async def get_valid_token():
    raw=load_token_json()
    if not raw: raise RuntimeError("WHOOP account is not connected yet. Use Connect WHOOP again.")
    token=json.loads(raw)
    if _expired(token):
        if not token.get("refresh_token"): raise RuntimeError("WHOOP token expired. Reconnect WHOOP.")
        token=await refresh_token(token["refresh_token"])
    return token

async def whoop_get(path, params=None):
    token=await get_valid_token()
    url=f"{WHOOP_API_BASE}{path}"
    for attempt in range(4):
        headers={"Authorization":f"Bearer {token['access_token']}"}
        async with httpx.AsyncClient(timeout=45) as client:
            r=await client.get(url,headers=headers,params=params)
        if r.status_code==401 and token.get("refresh_token") and attempt==0:
            token=await refresh_token(token["refresh_token"]); continue
        if r.status_code==429 and attempt<3:
            try: wait=max(1,int(r.headers.get("X-RateLimit-Reset","5")))
            except ValueError: wait=5
            await asyncio.sleep(min(wait+1,60)); continue
        r.raise_for_status(); return r.json()
    raise RuntimeError("WHOOP request failed after retries.")

async def get_phase1_snapshot():
    return {
        "profile":await whoop_get("/v2/user/profile/basic"),
        "body_measurement":await whoop_get("/v2/user/measurement/body"),
        "recovery":await whoop_get("/v2/recovery",{"limit":10}),
        "cycles":await whoop_get("/v2/cycle",{"limit":10}),
        "sleep":await whoop_get("/v2/activity/sleep",{"limit":10}),
        "workouts":await whoop_get("/v2/activity/workout",{"limit":10}),
    }

async def paginate(path):
    params={"limit":25}
    while True:
        page=await whoop_get(path,params)
        for record in page.get("records",[]): yield record
        next_token=page.get("next_token")
        if not next_token: break
        params={"limit":25,"nextToken":next_token}
        await asyncio.sleep(0.75)
