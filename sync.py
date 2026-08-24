from db import *
from whoop import whoop_get

async def incremental_sync():
    before = table_counts()

    profile = await whoop_get("/v2/user/profile/basic")
    upsert_profile(profile)

    body = await whoop_get("/v2/user/measurement/body")
    upsert_body(body)

    datasets = [
        ("cycles", "/v2/cycle", upsert_cycle),
        ("recoveries", "/v2/recovery", upsert_recovery),
        ("sleeps", "/v2/activity/sleep", upsert_sleep),
        ("workouts", "/v2/activity/workout", upsert_workout),
    ]
    processed = {}

    for name, path, upsert in datasets:
        page = await whoop_get(path, {"limit": 25})
        records = page.get("records", [])
        for record in records:
            upsert(record)
        processed[name] = len(records)

    after = table_counts()
    new_rows = {k: after[k] - before.get(k,0) for k in after}

    return {
        "status":"completed",
        "recent_records_processed":processed,
        "counts_before":before,
        "counts_after":after,
        "new_rows":new_rows
    }
