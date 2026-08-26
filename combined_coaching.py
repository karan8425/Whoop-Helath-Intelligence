import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from db import get_conn
from freshness import freshness_status
from healthkit_ingest import latest_apple_health
from automation_status import latest_automation_run

EASTERN = ZoneInfo("America/New_York")
DAILY_STEP_TARGET = int(os.getenv("DAILY_STEP_TARGET", "7000"))

def _lb(kg):
    return None if kg is None else kg * 2.2046226218

def _latest_whoop_daily(metric_date):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT metric_date,recovery_score,resting_heart_rate,hrv_rmssd_milli,
                       sleep_duration_hours,sleep_performance_percentage,
                       sleep_consistency_percentage,sleep_efficiency_percentage,
                       respiratory_rate,cycle_strain,cycle_calories,workout_count,
                       workout_total_strain,workout_total_duration_hours,workout_sports,
                       has_cycle,has_recovery,has_sleep,has_workout
                FROM whoop_daily_metrics WHERE metric_date=%s LIMIT 1
            """,(metric_date,))
            return cur.fetchone()

def _today_baselines(metric_date):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT metric_name,current_value,baseline_7,n_7,pct_vs_7,
                       baseline_30,n_30,pct_vs_30,baseline_90,n_90,pct_vs_90
                FROM whoop_daily_baselines
                WHERE metric_date=%s ORDER BY metric_name
            """,(metric_date,))
            return {r["metric_name"]:dict(r) for r in cur.fetchall()}

def combined_daily_snapshot():
    local_now=datetime.now(timezone.utc).astimezone(EASTERN)
    local_today=local_now.date()
    whoop_freshness=freshness_status()
    whoop=_latest_whoop_daily(local_today)
    apple=latest_apple_health()
    body=apple.get("body") or {}
    activity=apple.get("activity")
    weight=body.get("body_weight")
    body_fat=body.get("body_fat_percentage")
    lean=body.get("lean_body_mass")

    body_summary={"weight":None,"body_fat_percentage":None,"lean_body_mass":None}
    if weight:
        body_summary["weight"]={"kg":weight.get("value"),"lb":_lb(weight.get("value")),
        "source_name":weight.get("source_name"),"observed_at":weight.get("observed_at"),
        "classification":weight.get("classification"),"coaching_eligible":weight.get("coaching_eligible")}
    if body_fat:
        body_summary["body_fat_percentage"]={"value":body_fat.get("value"),
        "source_name":body_fat.get("source_name"),"observed_at":body_fat.get("observed_at"),
        "classification":body_fat.get("classification"),"coaching_eligible":body_fat.get("coaching_eligible")}
    if lean:
        body_summary["lean_body_mass"]={"kg":lean.get("value"),"lb":_lb(lean.get("value")),
        "source_name":lean.get("source_name"),"observed_at":lean.get("observed_at"),
        "classification":lean.get("classification"),"coaching_eligible":lean.get("coaching_eligible")}

    whoop_ready=bool(whoop and whoop_freshness.get("status")=="fresh"
                     and whoop_freshness.get("can_generate_current_recommendation") is True)
    activity_ready=bool(activity and activity.get("classification")=="current"
                        and activity.get("coaching_eligible") is True)
    body_ready=bool(weight and weight.get("coaching_eligible") is True
                    and body_fat and body_fat.get("coaching_eligible") is True)
    readiness={"whoop_current":whoop_ready,"body_composition_current":body_ready,
               "activity_current":activity_ready,
               "lean_mass_current":bool(lean and lean.get("coaching_eligible") is True),
               "combined_coaching_ready":bool(whoop_ready and body_ready and activity_ready)}
    notes=[]
    if lean and not lean.get("coaching_eligible"):
        notes.append("Lean body mass is retained as context but excluded from current coaching because it is stale or from a non-preferred source.")
    return {"status":"ok","coaching_date":local_today.isoformat(),"local_now":local_now.isoformat(),
            "data_readiness":readiness,"whoop_freshness":whoop_freshness,"whoop":whoop,
            "body_composition":body_summary,"activity":activity,"notes":notes}

def _current_whoop_recommendation(metric_date):
    run=latest_automation_run()
    if not run or str(run.get("metric_date")) != metric_date.isoformat():
        return None
    return run.get("deterministic_recommendation")

def _metric_reason(name, baselines):
    r=baselines.get(name)
    if not r: return None
    current=r.get("current_value"); b30=r.get("baseline_30"); pct=r.get("pct_vs_30")
    if current is None or b30 is None or pct is None: return None
    if name=="recovery_score": return f"Recovery is {current:.0f}, {pct:+.1f}% versus the 30-day personal baseline."
    if name=="hrv_rmssd_milli": return f"HRV is {current:.1f} ms, {pct:+.1f}% versus the 30-day personal baseline."
    if name=="resting_heart_rate": return f"Resting heart rate is {current:.0f} bpm, {pct:+.1f}% versus the 30-day personal baseline."
    if name=="sleep_duration_hours": return f"Sleep duration is {current:.2f} hours, {pct:+.1f}% versus the 30-day personal baseline."
    return None

def combined_deterministic_coaching():
    snapshot=combined_daily_snapshot()
    coaching_date=datetime.fromisoformat(snapshot["coaching_date"]).date()

    if not snapshot["data_readiness"]["combined_coaching_ready"]:
        return {"status":"not_ready","coaching_date":snapshot["coaching_date"],
                "data_readiness":snapshot["data_readiness"],
                "message":"Current WHOOP, body-composition, and activity data are not all ready for combined coaching.",
                "notes":snapshot.get("notes",[])}

    baselines=_today_baselines(coaching_date)
    whoop_rec=_current_whoop_recommendation(coaching_date)

    if whoop_rec:
        training=whoop_rec.get("training_recommendation","Normal")
        overall=whoop_rec.get("overall_status","Current WHOOP readiness available")
        confidence=whoop_rec.get("confidence","moderate")
    else:
        training="Normal"; overall="WHOOP recommendation not yet regenerated today"; confidence="low"

    reasons=[]
    for m in ("recovery_score","hrv_rmssd_milli","resting_heart_rate","sleep_duration_hours"):
        x=_metric_reason(m,baselines)
        if x: reasons.append(x)

    activity=snapshot.get("activity") or {}
    steps=int(activity.get("steps") or 0)
    remaining=max(0,DAILY_STEP_TARGET-steps)
    progress=(steps/DAILY_STEP_TARGET*100.0) if DAILY_STEP_TARGET>0 else None
    hour=datetime.fromisoformat(snapshot["local_now"]).hour

    if remaining==0:
        activity_status="target_met"
        activity_action="Daily step target is already met; additional movement can be based on preference and recovery."
    elif hour<12:
        activity_status="in_progress"
        activity_action=f"Build movement through the day; about {remaining:,} steps remain to the configured daily target."
    elif hour<18:
        activity_status="below_target_so_far"
        activity_action=f"Add purposeful walking this afternoon; about {remaining:,} steps remain to the configured daily target."
    else:
        activity_status="below_target_late_day"
        activity_action=f"Activity is below the configured daily target; about {remaining:,} steps remain. Use an easy walk if it fits recovery and schedule."

    body=snapshot.get("body_composition") or {}
    body_context=[]
    w=body.get("weight"); bf=body.get("body_fat_percentage"); lm=body.get("lean_body_mass")
    if w and w.get("coaching_eligible"):
        body_context.append(f"Current Hume weight is {w['lb']:.1f} lb.")
    if bf and bf.get("coaching_eligible"):
        body_context.append(f"Current Hume body fat is {bf['value']:.1f}%.")
    if lm and not lm.get("coaching_eligible"):
        body_context.append("Lean body mass is excluded from current coaching because the latest sample is not a current preferred-source measurement.")

    actions=[]
    if whoop_rec:
        existing=whoop_rec.get("highest_impact_actions") or []
        if existing: actions.append(existing[0])
    actions.append(activity_action)

    return {"status":"ok","coaching_date":snapshot["coaching_date"],"overall_status":overall,
            "training_recommendation":training,"confidence":confidence,
            "physiology_reasons":reasons[:4],
            "activity_guidance":{"steps_so_far":steps,"configured_step_target":DAILY_STEP_TARGET,
            "step_progress_percentage":round(progress,1) if progress is not None else None,
            "steps_remaining":remaining,"status":activity_status,
            "active_energy_kcal":activity.get("active_energy_kcal"),
            "resting_energy_kcal":activity.get("resting_energy_kcal"),
            "walking_running_distance_km":activity.get("walking_running_distance_km"),
            "actions":[activity_action]},
            "body_composition_context":body_context,
            "highest_impact_actions":actions[:3],
            "data_readiness":snapshot["data_readiness"],
            "safety_note":"Training guidance is based on personal wearable trends and is not a medical diagnosis. Symptoms, illness, injury, medication changes, or clinician advice should override wearable-based recommendations.",
            "interpretation_note":"WHOOP remains authoritative for readiness/training. Hume and Apple Health add body-composition and activity context. Single-day weight/body-fat measurements are context only until historical trend baselines are built."}
