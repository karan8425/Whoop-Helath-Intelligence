from db import get_conn

DIRECTION = {
    "recovery_score": "higher_better",
    "hrv_rmssd_milli": "higher_better",
    "resting_heart_rate": "lower_better",
    "sleep_duration_hours": "higher_better",
    "sleep_performance_percentage": "higher_better",
    "sleep_consistency_percentage": "higher_better",
    "cycle_strain": "context_only",
    "workout_count": "context_only",
}

DOMAINS = {
    "recovery_physiology": [
        "recovery_score",
        "hrv_rmssd_milli",
        "resting_heart_rate",
    ],
    "sleep": [
        "sleep_duration_hours",
        "sleep_performance_percentage",
        "sleep_consistency_percentage",
    ],
    "training_context": [
        "cycle_strain",
        "workout_count",
    ],
}

def _coverage_confidence(coverage):
    if coverage is None:
        return "insufficient"
    if coverage >= 80:
        return "high"
    if coverage >= 60:
        return "moderate"
    if coverage >= 40:
        return "low"
    return "insufficient"

def _raw_deviation_label(pct):
    if pct is None:
        return "insufficient_data"
    if pct >= 10:
        return "well_above_baseline"
    if pct >= 5:
        return "above_baseline"
    if pct <= -10:
        return "well_below_baseline"
    if pct <= -5:
        return "below_baseline"
    return "near_baseline"

def _directional_signal(metric, pct):
    if pct is None:
        return "insufficient_data"

    direction = DIRECTION[metric]
    if direction == "context_only":
        return _raw_deviation_label(pct)

    adjusted = pct if direction == "higher_better" else -pct

    if adjusted >= 10:
        return "strong_positive"
    if adjusted >= 5:
        return "positive"
    if adjusted <= -10:
        return "strong_negative"
    if adjusted <= -5:
        return "negative"
    return "normal"

def _trend_signal(metric, baseline_7, baseline_30):
    if baseline_7 is None or baseline_30 in (None, 0):
        return {
            "trend": "insufficient_data",
            "recent_vs_30_pct": None,
        }

    pct = ((baseline_7 - baseline_30) / baseline_30) * 100.0
    direction = DIRECTION[metric]

    if direction == "context_only":
        if pct >= 10:
            trend = "recently_higher"
        elif pct <= -10:
            trend = "recently_lower"
        else:
            trend = "stable"
    else:
        adjusted = pct if direction == "higher_better" else -pct
        if adjusted >= 5:
            trend = "improving"
        elif adjusted <= -5:
            trend = "deteriorating"
        else:
            trend = "stable"

    return {
        "trend": trend,
        "recent_vs_30_pct": pct,
    }

def _score_directional_signal(signal):
    return {
        "strong_positive": 2,
        "positive": 1,
        "normal": 0,
        "negative": -1,
        "strong_negative": -2,
    }.get(signal)

def latest_signals():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(metric_date) AS d FROM whoop_daily_baselines")
            latest_date = cur.fetchone()["d"]
            if latest_date is None:
                return {"metric_date": None, "signals": [], "domains": {}}

            cur.execute("""
                SELECT
                  metric_name,
                  current_value,
                  baseline_7, n_7, pct_vs_7,
                  baseline_14, n_14, pct_vs_14,
                  baseline_30, n_30, pct_vs_30,
                  baseline_90, n_90, pct_vs_90
                FROM whoop_daily_baselines
                WHERE metric_date = %s
                ORDER BY metric_name
            """, (latest_date,))
            rows = cur.fetchall()

    signals = []
    by_metric = {}

    for row in rows:
        metric = row["metric_name"]

        coverage_7 = round((row["n_7"] / 7) * 100.0, 1)
        coverage_14 = round((row["n_14"] / 14) * 100.0, 1)
        coverage_30 = round((row["n_30"] / 30) * 100.0, 1)
        coverage_90 = round((row["n_90"] / 90) * 100.0, 1)

        trend = _trend_signal(metric, row["baseline_7"], row["baseline_30"])
        item = {
            "metric_name": metric,
            "directionality": DIRECTION[metric],
            "current_value": row["current_value"],
            "baseline_7": row["baseline_7"],
            "pct_vs_7": row["pct_vs_7"],
            "baseline_30": row["baseline_30"],
            "pct_vs_30": row["pct_vs_30"],
            "baseline_90": row["baseline_90"],
            "pct_vs_90": row["pct_vs_90"],
            "deviation_from_30d": _raw_deviation_label(row["pct_vs_30"]),
            "directional_signal": _directional_signal(metric, row["pct_vs_30"]),
            "trend": trend["trend"],
            "recent_7d_vs_30d_pct": trend["recent_vs_30_pct"],
            "coverage_7_percentage": coverage_7,
            "coverage_14_percentage": coverage_14,
            "coverage_30_percentage": coverage_30,
            "coverage_90_percentage": coverage_90,
            "confidence": _coverage_confidence(coverage_30),
        }
        signals.append(item)
        by_metric[metric] = item

    domains = {}

    for domain, metrics in DOMAINS.items():
        if domain == "training_context":
            domains[domain] = {
                "status": "context_only",
                "metrics": [
                    {
                        "metric_name": m,
                        "deviation": by_metric[m]["deviation_from_30d"],
                        "trend": by_metric[m]["trend"],
                        "confidence": by_metric[m]["confidence"],
                    }
                    for m in metrics if m in by_metric
                ],
            }
            continue

        scored = []
        for metric in metrics:
            item = by_metric.get(metric)
            if not item:
                continue
            score = _score_directional_signal(item["directional_signal"])
            if score is None or item["confidence"] == "insufficient":
                continue

            # Confidence weighting is deliberately simple and transparent.
            weight = {
                "high": 1.0,
                "moderate": 0.75,
                "low": 0.5,
            }.get(item["confidence"], 0.0)

            scored.append((score, weight))

        if not scored:
            status = "insufficient_data"
            composite_score = None
        else:
            weighted_sum = sum(score * weight for score, weight in scored)
            total_weight = sum(weight for _, weight in scored)
            composite_score = weighted_sum / total_weight if total_weight else None

            if composite_score is None:
                status = "insufficient_data"
            elif composite_score >= 1.0:
                status = "strong_positive"
            elif composite_score >= 0.35:
                status = "positive"
            elif composite_score <= -1.0:
                status = "strong_negative"
            elif composite_score <= -0.35:
                status = "negative"
            else:
                status = "normal"

        domains[domain] = {
            "status": status,
            "composite_score": composite_score,
            "metrics_used": len(scored),
        }

    directional_domains = [
        domains[d]["status"]
        for d in ("recovery_physiology", "sleep")
        if domains.get(d, {}).get("status") != "insufficient_data"
    ]

    status_score = {
        "strong_positive": 2,
        "positive": 1,
        "normal": 0,
        "negative": -1,
        "strong_negative": -2,
    }

    if directional_domains:
        overall_numeric = sum(status_score[s] for s in directional_domains) / len(directional_domains)
        if overall_numeric >= 1.0:
            overall = "strong_positive"
        elif overall_numeric >= 0.5:
            overall = "positive"
        elif overall_numeric <= -1.0:
            overall = "strong_negative"
        elif overall_numeric <= -0.5:
            overall = "negative"
        else:
            overall = "mixed_or_normal"
    else:
        overall = "insufficient_data"

    return {
        "metric_date": latest_date.isoformat(),
        "overall_signal": overall,
        "domains": domains,
        "signals": signals,
        "interpretation_note": (
            "These are statistical signals relative to personal baselines. "
            "They are not diagnoses and do not establish causation."
        ),
    }

def validate_signals():
    result = latest_signals()

    expected = set(DIRECTION)
    actual = {x["metric_name"] for x in result.get("signals", [])}

    checks = {
        "all_configured_metrics_present": expected == actual,
        "metric_count": len(actual),
        "expected_metric_count": len(expected),
        "recovery_domain_present": "recovery_physiology" in result.get("domains", {}),
        "sleep_domain_present": "sleep" in result.get("domains", {}),
        "training_context_present": "training_context" in result.get("domains", {}),
        "latest_date_present": result.get("metric_date") is not None,
    }

    return {
        "status": "ok" if all(checks.values()) else "check_failed",
        "checks": checks,
        "latest_signal_snapshot": result,
    }
