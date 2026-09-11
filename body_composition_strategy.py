"""Goal-aware, non-diagnostic body-composition strategy for Training-B3."""

from datetime import date, datetime, timedelta
from statistics import mean


THRESHOLDS = {
    "weight_lb": 1.0,
    "body_fat_percentage": 0.5,
    "fat_mass_lb": 1.0,
    "lean_mass_lb": 1.5,
}


def _day(value):
    value = value.get("date") if isinstance(value, dict) else value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _value(row):
    return float(row.get("value"))


def _window_change(rows, days, end_date=None):
    valid = [r for r in (rows or []) if r.get("value") is not None]
    if len(valid) < 2:
        return None
    end = end_date or max(_day(r) for r in valid)
    recent = [_value(r) for r in valid if _day(r) > end - timedelta(days=max(3, days // 3))]
    baseline = [_value(r) for r in valid if end - timedelta(days=days) <= _day(r) <= end - timedelta(days=max(3, days // 3))]
    if not recent or not baseline:
        return None
    return round(mean(recent) - mean(baseline), 3)


def classify(goal_contract, hume_history, strength_trajectory="INSUFFICIENT_DATA"):
    # V2 goals carry goal_type; legacy active goals may carry only phase.
    goal_type = (goal_contract or {}).get("goal_type") or (goal_contract or {}).get("phase")
    series = hume_history or {}
    keys = {
        "weight_lb": "weight", "body_fat_percentage": "body_fat_percentage",
        "fat_mass_lb": "fat_mass", "lean_mass_lb": "lean_mass",
        "skeletal_muscle_mass_lb": "skeletal_muscle_mass",
    }
    windows = {
        days: {name: _window_change(series.get(source), days) for name, source in keys.items()}
        for days in (7, 14, 30, 90)
    }
    confirmed = windows[30] if any(v is not None for v in windows[30].values()) else windows[14]
    fat = confirmed.get("fat_mass_lb")
    bf = confirmed.get("body_fat_percentage")
    lean = confirmed.get("lean_mass_lb")
    weight = confirmed.get("weight_lb")
    signal = "INSUFFICIENT_DATA"
    strategy = "INSUFFICIENT_DATA"
    if goal_type in ("lose_body_fat", "lean_cut", "recomposition") and sum(v is not None for v in (fat, bf, lean, weight)) >= 2:
        fat_down = (fat is not None and fat < -THRESHOLDS["fat_mass_lb"]) or (bf is not None and bf < -THRESHOLDS["body_fat_percentage"])
        lean_down = lean is not None and lean < -THRESHOLDS["lean_mass_lb"]
        strength_down = strength_trajectory == "DECLINING"
        weight_stable = weight is not None and abs(weight) <= THRESHOLDS["weight_lb"]
        lean_up = lean is not None and lean > THRESHOLDS["lean_mass_lb"]
        if fat_down and lean_up and weight_stable:
            strategy, signal = "RECOMPOSITION", "POSSIBLE_RECOMPOSITION"
        elif fat_down and lean_down:
            strategy = "FAT_LOSS_WITH_POSSIBLE_LEAN_LOSS"
            signal = "LEAN_MASS_RISK" if strength_down else "INSUFFICIENT_DATA"
        elif fat_down and not lean_down:
            strategy, signal = "FAT_LOSS_WITH_LEAN_PRESERVATION", "PLAN_WORKING"
        elif weight is not None and weight < -THRESHOLDS["weight_lb"] and not fat_down:
            strategy = "WEIGHT_LOSS_WITHOUT_CLEAR_FAT_LOSS"
        elif lean_up and (bf is None or bf <= 0):
            strategy, signal = "RECOMPOSITION", "POSSIBLE_RECOMPOSITION"
        else:
            strategy = "NO_MEANINGFUL_CHANGE"
    return {
        "classification": strategy,
        "training_strategy_signal": signal,
        "goal_type": goal_type,
        "methodology": "Rolling multi-measurement trends influence strategy, never same-day muscle readiness or spot reduction.",
        "trend_windows": windows,
        "regional_fat": {"status": "regional_history_not_available", "provenance": "Hume"},
        "training_response": (
            "Preserve mechanical tension and reduce unnecessary volume before intensity when fatigue is supported."
            if signal == "LEAN_MASS_RISK" else
            "Maintain the productive personalized resistance-training strategy; do not add volume merely to accelerate fat loss."
        ),
    }
