"""Deterministic Goal Setting V2 timeline + compatibility engine.

No LLM. No persistence. Pure functions over numbers + dates. All pace
assumptions come from goal_pace_config.

Public surface
--------------
  compatibility_check(current, target)            -> dict
  timeline_options(current, target, priority)     -> {comfortable, recommended, faster}
  evaluate_custom_date(current, target, date_iso) -> dict
  build_preview(...)                              -> full preview payload
"""

import math
from datetime import date, datetime, timedelta

import goal_pace_config as cfg


# ============================================================
# small helpers
# ============================================================

def _num(value):
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _round(value, digits=1):
    v = _num(value)
    return None if v is None else round(v, digits)


def _today():
    return datetime.now().date()


def _parse_date(value):
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def _add_weeks(start, weeks):
    """Calendar-safe: add whole days, never month arithmetic."""
    return start + timedelta(days=int(round(weeks * 7)))


# ============================================================
# derived body-composition
# ============================================================

def _composition(weight_lb, body_fat_pct):
    w = _num(weight_lb)
    bf = _num(body_fat_pct)
    if w is None or bf is None:
        return {"weight_lb": _round(w), "body_fat_percentage": _round(bf, 2),
                "fat_mass_lb": None, "lean_mass_lb": None}
    fat = w * bf / 100.0
    return {
        "weight_lb": _round(w),
        "body_fat_percentage": _round(bf, 2),
        "fat_mass_lb": _round(fat),
        "lean_mass_lb": _round(w - fat),
    }


# ============================================================
# compatibility check
# ============================================================

def compatibility_check(current, target):
    """Compare implied target lean mass with the current estimate.

    States: compatible / potential_lean_mass_loss / requires_lean_mass_gain
            / mathematically_inconsistent / insufficient_data
    """

    cw = _num(current.get("weight_lb"))
    cbf = _num(current.get("body_fat_percentage"))
    tw = _num(target.get("target_weight_lb"))
    tbf = _num(target.get("target_body_fat_percentage"))

    cur = _composition(cw, cbf)
    tgt = _composition(tw, tbf)

    if None in (cw, cbf, tw, tbf):
        return {
            "state": "insufficient_data",
            "current": cur,
            "target_implied": tgt,
            "lean_mass_delta_lb": None,
            "message": "Need current weight, current body fat, target weight "
                       "and target body fat to check the targets.",
        }

    if not (cfg.MIN_PLAUSIBLE_BODY_FAT_PCT <= tbf <= cfg.MAX_PLAUSIBLE_BODY_FAT_PCT) \
       or tw <= 0 or tbf >= 100:
        return {
            "state": "mathematically_inconsistent",
            "current": cur,
            "target_implied": tgt,
            "lean_mass_delta_lb": None,
            "message": "Those target numbers do not form a valid body "
                       "composition.",
        }

    lean_delta = tgt["lean_mass_lb"] - cur["lean_mass_lb"]
    tol = cfg.LEAN_MASS_TOLERANCE_LB

    if abs(lean_delta) <= tol:
        state = "compatible"
        message = (
            f"{tw:.0f} lb at {tbf:.0f}% implies about "
            f"{tgt['lean_mass_lb']:.0f} lb of lean mass - roughly your current "
            f"estimate ({cur['lean_mass_lb']:.0f} lb)."
        )
    elif lean_delta < -tol:
        state = "potential_lean_mass_loss"
        message = (
            f"{tw:.0f} lb at {tbf:.0f}% implies about "
            f"{tgt['lean_mass_lb']:.0f} lb of lean mass - roughly "
            f"{abs(lean_delta):.0f} lb below your current estimate. A slower "
            f"pace with resistance training protects lean mass."
        )
    else:
        state = "requires_lean_mass_gain"
        message = (
            f"{tw:.0f} lb at {tbf:.0f}% implies about "
            f"{tgt['lean_mass_lb']:.0f} lb of lean mass - about "
            f"{lean_delta:.0f} lb above your current estimate, which means "
            f"building muscle, not only losing fat."
        )

    return {
        "state": state,
        "current": cur,
        "target_implied": tgt,
        "lean_mass_delta_lb": _round(lean_delta),
        "message": message,
    }


# ============================================================
# timeline maths
# ============================================================

def _weeks_for_rate(current_weight, total_change_lb, weekly_rate_fraction):
    weekly_lb = weekly_rate_fraction * current_weight
    if weekly_lb <= 0:
        return None
    return max(1, math.ceil(abs(total_change_lb) / weekly_lb))


def _pace_option(*, band, current_weight, total_change_lb, direction):
    rate = cfg.PACE_BANDS[band]
    weeks = _weeks_for_rate(current_weight, total_change_lb, rate)
    if weeks is None:
        return None
    target_date = _add_weeks(_today(), weeks)
    avg_weekly = total_change_lb / weeks  # signed
    return {
        "band": band,
        "weekly_rate_percent": _round(rate * 100, 2),
        "estimated_weeks": weeks,
        "estimated_target_date": target_date.isoformat(),
        "required_average_weekly_change_lb": _round(avg_weekly, 2),
        "pace_display": f"~{abs(round(avg_weekly, 1)):.1f} lb/week "
                        f"{'loss' if direction < 0 else 'gain'}",
    }


def timeline_options(current, target, lean_mass_priority=None):
    cw = _num(current.get("weight_lb"))
    tw = _num(target.get("target_weight_lb"))
    priority = (lean_mass_priority or cfg.DEFAULT_LEAN_MASS_PRIORITY)

    if cw is None or tw is None:
        return {
            "status": "insufficient_data",
            "message": "Need current weight and target weight to estimate a "
                       "timeline.",
            "options": {}, "recommended_band": None,
        }

    total_change = tw - cw  # signed; negative = loss
    if abs(total_change) < cfg.MIN_MEANINGFUL_WEIGHT_CHANGE_LB:
        return {
            "status": "maintain",
            "message": "The target weight is essentially your current weight - "
                       "this reads as a maintain goal.",
            "options": {}, "recommended_band": None,
        }

    direction = -1 if total_change < 0 else 1
    options = {}
    for band in ("comfortable", "recommended", "faster"):
        opt = _pace_option(band=band, current_weight=cw,
                           total_change_lb=total_change, direction=direction)
        if opt:
            options[band] = opt

    recommended_band = cfg.PRIORITY_DEFAULT_BAND.get(
        priority, "recommended"
    )
    if recommended_band not in options:
        recommended_band = "recommended" if "recommended" in options else next(
            iter(options), None
        )

    return {
        "status": "ok",
        "direction": "loss" if direction < 0 else "gain",
        "total_change_lb": _round(total_change),
        "lean_mass_priority": priority,
        "recommended_band": recommended_band,
        "options": options,
    }


# ============================================================
# custom date
# ============================================================

def evaluate_custom_date(current, target, custom_date_iso):
    cw = _num(current.get("weight_lb"))
    tw = _num(target.get("target_weight_lb"))
    if cw is None or tw is None:
        return {"timeline_status": "insufficient_data",
                "message": "Need current and target weight to assess a date."}

    try:
        d = _parse_date(custom_date_iso)
    except Exception:
        return {"timeline_status": "insufficient_data",
                "message": "Could not read that date."}

    today = _today()
    days_available = (d - today).days
    if days_available <= 0:
        return {"timeline_status": "outside_supported_range",
                "message": "That date is not in the future.",
                "chosen_date": d.isoformat()}

    weeks_available = days_available / 7.0
    total_change = tw - cw
    direction = "loss" if total_change < 0 else "gain"
    required_weekly = total_change / weeks_available            # signed lb
    required_weekly_pct = abs(required_weekly) / cw * 100.0

    if required_weekly_pct <= cfg.COMFORTABLE_WEEKLY_RATE * 100:
        status = "comfortable"
    elif required_weekly_pct <= cfg.RECOMMENDED_WEEKLY_RATE * 100:
        status = "recommended"
    elif required_weekly_pct <= cfg.FASTER_WEEKLY_RATE * 100:
        status = "faster"
    elif required_weekly_pct <= cfg.MAXIMUM_SUPPORTED_WEEKLY_RATE * 100:
        status = "faster_but_supported"
    else:
        status = "outside_supported_range"

    out = {
        "chosen_date": d.isoformat(),
        "weeks_available": _round(weeks_available, 1),
        "required_weekly_change_lb": _round(required_weekly, 2),
        "required_weekly_percent_change": _round(required_weekly_pct, 2),
        "direction": direction,
        "timeline_status": status,
        "pace_display": f"~{abs(round(required_weekly, 1)):.1f} lb/week",
    }

    if status == "outside_supported_range":
        rec = timeline_options(current, target).get("options", {}).get("recommended")
        out["message"] = ("This date would require a faster rate of change "
                          "than this plan supports.")
        out["recommended_target_date"] = (
            rec.get("estimated_target_date") if rec else None
        )
        out["recommended_pace_display"] = rec.get("pace_display") if rec else None
    else:
        out["message"] = "This date is within the supported plan range."
    return out


# ============================================================
# full preview
# ============================================================

def build_preview(*, goal_type, current, target, lean_mass_priority=None,
                  custom_target_date=None):
    priority = lean_mass_priority or cfg.DEFAULT_LEAN_MASS_PRIORITY
    compat = compatibility_check(current, target)
    tl = timeline_options(current, target, priority)
    custom = (
        evaluate_custom_date(current, target, custom_target_date)
        if custom_target_date else None
    )

    return {
        "goal_type": goal_type,
        "lean_mass_priority": priority,
        "current": compat["current"],
        "target": {
            "target_weight_lb": _num(target.get("target_weight_lb")),
            "target_body_fat_percentage": _num(target.get("target_body_fat_percentage")),
            **{k: compat["target_implied"][k]
               for k in ("fat_mass_lb", "lean_mass_lb")},
        },
        "compatibility": compat,
        "timeline_options": tl.get("options", {}),
        "recommended_band": tl.get("recommended_band"),
        "timeline_status_summary": tl.get("status"),
        "custom_timeline": custom,
        "methodology": cfg.METHODOLOGY_TEXT,
    }
