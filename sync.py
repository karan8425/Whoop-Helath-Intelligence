import asyncio
from db import *
from whoop import whoop_get

DATASETS = [
    ("cycles", "/v2/cycle", upsert_cycle),
    ("recoveries", "/v2/recovery", upsert_recovery),
    ("sleeps", "/v2/activity/sleep", upsert_sleep),
    ("workouts", "/v2/activity/workout", upsert_workout),
]

def init_sync_state():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS whoop_sync_state (
                dataset TEXT PRIMARY KEY, next_token TEXT,
                completed BOOLEAN NOT NULL DEFAULT FALSE,
                records_processed BIGINT NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
            for dataset, _, _ in DATASETS:
                cur.execute("INSERT INTO whoop_sync_state(dataset) VALUES (%s) ON CONFLICT(dataset) DO NOTHING", (dataset,))

def get_state(dataset):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM whoop_sync_state WHERE dataset=%s", (dataset,))
            return cur.fetchone()

def save_state(dataset, token, completed, added):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""UPDATE whoop_sync_state SET next_token=%s, completed=%s,
            records_processed=records_processed+%s, updated_at=NOW() WHERE dataset=%s""",
            (token, completed, added, dataset))

def sync_status():
    init_sync_state()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT dataset,completed,records_processed,updated_at FROM whoop_sync_state ORDER BY dataset")
            rows=cur.fetchall()
    return {"complete": all(r["completed"] for r in rows),
            "datasets":[{**r,"updated_at":r["updated_at"].isoformat()} for r in rows],
            "database_totals":table_counts()}

async def sync_batch(pages_per_dataset=3):
    init_sync_state()
    p=await whoop_get("/v2/user/profile/basic"); upsert_profile(p)
    b=await whoop_get("/v2/user/measurement/body"); upsert_body(b)
    result={}
    for dataset,path,upsert in DATASETS:
        state=get_state(dataset)
        if state["completed"]:
            result[dataset]={"status":"already_complete","added":0}; continue
        token=state["next_token"]; added=0; pages=0
        while pages < pages_per_dataset:
            params={"limit":25}
            if token: params["nextToken"]=token
            page=await whoop_get(path,params)
            records=page.get("records",[])
            for record in records:
                upsert(record); added+=1
            token=page.get("next_token"); pages+=1
            save_state(dataset,token,not bool(token),len(records))
            if not token: break
            await asyncio.sleep(0.5)
        result[dataset]={"status":"complete" if not token else "more_remaining",
                         "pages_processed_this_batch":pages,
                         "records_added_this_batch":added}
    return {"status":"batch_completed","result":result,**sync_status()}
