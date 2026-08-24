from db import *
from whoop import whoop_get, paginate

async def full_historical_sync():
    sync_id=begin_sync("full_historical")
    counts={"cycles_processed":0,"recoveries_processed":0,"sleeps_processed":0,"workouts_processed":0,"body_measurements_processed":0,"profiles_processed":0}
    try:
        p=await whoop_get("/v2/user/profile/basic"); upsert_profile(p); counts["profiles_processed"]=1
        b=await whoop_get("/v2/user/measurement/body"); upsert_body(b); counts["body_measurements_processed"]=1
        async for r in paginate("/v2/cycle"): upsert_cycle(r); counts["cycles_processed"]+=1
        async for r in paginate("/v2/recovery"): upsert_recovery(r); counts["recoveries_processed"]+=1
        async for r in paginate("/v2/activity/sleep"): upsert_sleep(r); counts["sleeps_processed"]+=1
        async for r in paginate("/v2/activity/workout"): upsert_workout(r); counts["workouts_processed"]+=1
        counts["database_totals"]=table_counts()
        finish_sync(sync_id,counts)
        return {"status":"completed","sync_id":sync_id,**counts}
    except Exception as exc:
        fail_sync(sync_id,exc); raise
