from trends import latest_signals

TRAINING_LEVELS = [
    "Push",
    "Normal",
    "Moderate",
    "Active Recovery",
    "Rest",
]

def _metric_map(signals):
    return {x["metric_name"]: x for x in signals}

def _confidence_ok(item):
    return item and item.get("confidence") in {"high", "moderate"}

def _append_reason(reasons, text, priority=2):
    reasons.append({"priority": priority, "text": text})

def _training_recommendation(signal_data):
    domains = signal_data["domains"]
    signals = _metric_map(signal_data["signals"])

    recovery = domains.get("recovery_physiology", {}).get("status", "insufficient_data")
    sleep = domains.get("sleep", {}).get("status", "insufficient_data")

    rec = signals.get("recovery_score")
    hrv = signals.get("hrv_rmssd_milli")
    rhr = signals.get("resting_heart_rate")
    sleep_duration = signals.get("sleep_duration_hours")
    sleep_perf = signals.get("sleep_performance_percentage")
    cycle_strain = signals.get("cycle_strain")
    workout_count = signals.get("workout_count")

    reasons = []
    priorities = []
    actions = []

    # Guardrail: insufficient physiology means conservative output.
    physiology_confident = sum(
        1 for x in (rec, hrv, rhr)
        if _confidence_ok(x) and x.get("current_value") is not None
    )

    if physiology_confident < 2:
        return {
            "training_recommendation": "Moderate",
            "overall_status": "Limited data",
            "reasons": [
                {"priority": 1, "text": "Recent recovery data coverage is insufficient for a high-confidence training recommendation."}
            ],
            "recovery_priorities": [
                "Use perceived readiness and normal training tolerance as additional context."
            ],
            "highest_impact_actions": [
                "Avoid an unusually high training load until fresh recovery data is available."
            ],
            "confidence": "low",
        }

    # Hard negative guardrails.
    strong_negative_count = sum(
        1 for x in (rec, hrv, rhr)
        if x and x.get("directional_signal") == "strong_negative"
    )
    negative_count = sum(
        1 for x in (rec, hrv, rhr)
        if x and x.get("directional_signal") in {"negative", "strong_negative"}
    )

    if strong_negative_count >= 2:
        recommendation = "Rest"
        overall = "Recovery constrained"
    elif strong_negative_count >= 1 and negative_count >= 2:
        recommendation = "Active Recovery"
        overall = "Recovery below normal"
    elif recovery in {"strong_negative", "negative"} or sleep in {"strong_negative", "negative"}:
        recommendation = "Moderate"
        overall = "Mixed recovery"
    elif recovery == "strong_positive" and sleep == "strong_positive":
        recommendation = "Push"
        overall = "Strong readiness"
    elif recovery in {"strong_positive", "positive"} and sleep in {"positive", "normal", "strong_positive"}:
        recommendation = "Normal"
        overall = "Good readiness"
    else:
        recommendation = "Moderate"
        overall = "Mixed signals"

    # Reasons.
    if rec:
        sig = rec.get("directional_signal")
        if sig in {"strong_positive", "positive"}:
            _append_reason(
                reasons,
                f"Recovery score is {rec['current_value']:.0f}, {rec['pct_vs_30']:+.1f}% versus the 30-day personal baseline.",
                1,
            )
        elif sig in {"strong_negative", "negative"}:
            _append_reason(
                reasons,
                f"Recovery score is {rec['current_value']:.0f}, {rec['pct_vs_30']:+.1f}% versus the 30-day personal baseline.",
                1,
            )

    if hrv:
        sig = hrv.get("directional_signal")
        if sig in {"strong_positive", "positive", "negative", "strong_negative"}:
            _append_reason(
                reasons,
                f"HRV is {hrv['current_value']:.1f} ms, {hrv['pct_vs_30']:+.1f}% versus the 30-day baseline.",
                1 if sig in {"strong_negative", "negative"} else 2,
            )

    if rhr:
        sig = rhr.get("directional_signal")
        if sig in {"strong_positive", "positive", "negative", "strong_negative"}:
            _append_reason(
                reasons,
                f"Resting heart rate is {rhr['current_value']:.0f} bpm, {rhr['pct_vs_30']:+.1f}% versus the 30-day baseline.",
                1 if sig in {"strong_negative", "negative"} else 2,
            )

    if sleep_duration:
        sig = sleep_duration.get("directional_signal")
        if sig in {"strong_positive", "positive"}:
            _append_reason(
                reasons,
                f"Sleep duration was {sleep_duration['current_value']:.2f} hours, {sleep_duration['pct_vs_30']:+.1f}% versus the 30-day baseline.",
                2,
            )
        elif sig in {"negative", "strong_negative"}:
            _append_reason(
                reasons,
                f"Sleep duration was {sleep_duration['current_value']:.2f} hours, {sleep_duration['pct_vs_30']:+.1f}% versus the 30-day baseline.",
                1,
            )
            priorities.append("Prioritize a longer sleep opportunity tonight.")

    if sleep_perf and sleep_perf.get("directional_signal") in {"negative", "strong_negative"}:
        priorities.append("Reduce factors that may interfere with sleep quality tonight.")

    # Training context never independently makes strain good/bad.
    if cycle_strain and cycle_strain.get("current_value") is not None:
        if cycle_strain.get("deviation_from_30d") == "well_above_baseline":
            priorities.append("Avoid stacking another unusually high-strain day unless recovery remains strong.")
        elif cycle_strain.get("deviation_from_30d") == "well_below_baseline" and recommendation == "Push":
            actions.append("If planned, a higher-quality training session is reasonable given the current recovery signals.")

    if workout_count and workout_count.get("current_value", 0) >= 2:
        priorities.append("Account for multiple recorded workouts when judging total training volume.")

    # Recommendation-specific actions.
    if recommendation == "Push":
        actions.insert(0, "Proceed with the planned high-quality training session if subjective readiness also feels normal.")
        actions.append("Maintain normal fueling and hydration around training.")
    elif recommendation == "Normal":
        actions.insert(0, "Train as planned without deliberately adding extra volume.")
        actions.append("Keep recovery habits consistent tonight.")
    elif recommendation == "Moderate":
        actions.insert(0, "Reduce either training intensity or total volume by roughly one step from the original plan.")
        actions.append("Prioritize sleep and hydration before adding additional training stress.")
    elif recommendation == "Active Recovery":
        actions.insert(0, "Favor easy movement, mobility, walking, or low-intensity aerobic work.")
        actions.append("Delay hard training until recovery signals improve.")
    elif recommendation == "Rest":
        actions.insert(0, "Skip structured high-intensity training today.")
        actions.append("Focus on sleep, hydration, nutrition, and symptom awareness.")

    # De-duplicate while preserving order.
    seen = set()
    priorities = [x for x in priorities if not (x in seen or seen.add(x))]
    seen = set()
    actions = [x for x in actions if not (x in seen or seen.add(x))]

    reasons = sorted(reasons, key=lambda x: x["priority"])[:4]

    # Confidence from 30d coverage on physiology.
    coverages = [
        x.get("coverage_30_percentage", 0)
        for x in (rec, hrv, rhr)
        if x and x.get("current_value") is not None
    ]
    avg_cov = sum(coverages) / len(coverages) if coverages else 0
    if avg_cov >= 80:
        confidence = "high"
    elif avg_cov >= 60:
        confidence = "moderate"
    else:
        confidence = "low"

    return {
        "training_recommendation": recommendation,
        "overall_status": overall,
        "reasons": reasons,
        "recovery_priorities": priorities[:3],
        "highest_impact_actions": actions[:2],
        "confidence": confidence,
    }

def daily_recommendation():
    signals = latest_signals()
    recommendation = _training_recommendation(signals)

    return {
        "metric_date": signals.get("metric_date"),
        **recommendation,
        "domain_status": signals.get("domains", {}),
        "safety_note": (
            "This recommendation is based on personal WHOOP trends and is not a medical diagnosis. "
            "Symptoms, illness, injury, medication changes, or clinician advice should override this training recommendation."
        ),
    }

def validate_recommendation():
    result = daily_recommendation()

    checks = {
        "metric_date_present": result.get("metric_date") is not None,
        "valid_training_recommendation": result.get("training_recommendation") in TRAINING_LEVELS,
        "overall_status_present": bool(result.get("overall_status")),
        "reasons_present": len(result.get("reasons", [])) >= 1,
        "actions_present": len(result.get("highest_impact_actions", [])) >= 1,
        "confidence_present": result.get("confidence") in {"high", "moderate", "low"},
        "safety_note_present": bool(result.get("safety_note")),
    }

    return {
        "status": "ok" if all(checks.values()) else "check_failed",
        "checks": checks,
        "recommendation": result,
    }
