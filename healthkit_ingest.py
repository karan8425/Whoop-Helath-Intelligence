import os, secrets
from fastapi import HTTPException, Request
from psycopg.types.json import Jsonb
from db import get_conn

APPLE_HEALTH_INGEST_KEY=os.getenv("APPLE_HEALTH_INGEST_KEY","")

def require_ingest_key(request:Request):
    if not APPLE_HEALTH_INGEST_KEY:
        raise HTTPException(status_code=503,detail="APPLE_HEALTH_INGEST_KEY is not configured.")
    supplied=request.headers.get("authorization","")
    if not secrets.compare_digest(supplied,f"Bearer {APPLE_HEALTH_INGEST_KEY}"):
        raise HTTPException(status_code=401,detail="Invalid ingest key.")

def init_apple_health_tables():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS apple_health_body_samples(
            sample_id UUID PRIMARY KEY, metric_name TEXT NOT NULL, value DOUBLE PRECISION NOT NULL,
            unit TEXT NOT NULL, observed_at TIMESTAMPTZ NOT NULL, source_name TEXT,
            source_bundle_id TEXT, raw_json JSONB NOT NULL, received_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
            cur.execute("""CREATE INDEX IF NOT EXISTS idx_apple_health_body_metric_date
            ON apple_health_body_samples(metric_name,observed_at DESC)""")

def ingest_healthkit_payload(payload:dict):
    init_apple_health_tables()
    body=payload.get("body_samples") or []
    with get_conn() as conn:
        with conn.cursor() as cur:
            for s in body:
                cur.execute("""INSERT INTO apple_health_body_samples
                (sample_id,metric_name,value,unit,observed_at,source_name,source_bundle_id,raw_json)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(sample_id) DO UPDATE SET metric_name=EXCLUDED.metric_name,value=EXCLUDED.value,
                unit=EXCLUDED.unit,observed_at=EXCLUDED.observed_at,source_name=EXCLUDED.source_name,
                source_bundle_id=EXCLUDED.source_bundle_id,raw_json=EXCLUDED.raw_json,received_at=NOW()""",
                (s.get("sample_id"),s.get("metric_name"),s.get("value"),s.get("unit"),
                 s.get("observed_at"),s.get("source_name"),s.get("source_bundle_id"),Jsonb(s)))
    return {"status":"ok","body_samples_received":len(body)}

def latest_apple_health():
    init_apple_health_tables()
    out={}
    with get_conn() as conn:
        with conn.cursor() as cur:
            for metric in ["body_weight","body_fat_percentage","lean_body_mass"]:
                cur.execute("""SELECT metric_name,value,unit,observed_at,source_name,source_bundle_id,received_at
                FROM apple_health_body_samples WHERE metric_name=%s ORDER BY observed_at DESC LIMIT 1""",(metric,))
                r=cur.fetchone()
                if r:
                    out[metric]={**r,"observed_at":r["observed_at"].isoformat(),"received_at":r["received_at"].isoformat()}
    return out
