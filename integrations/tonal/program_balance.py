"""Bounded, deterministic program-selection calibration (no systemic dose).

Actual exposure uses the existing 30-day Tonal analytics. Recommendation
exposure is weaker evidence than performed training, never a claimed workout.
All history is bounded by the caller's as-of clock.
"""
from datetime import date, datetime, timezone
from math import exp
from statistics import median
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo('America/New_York')
READINESS_PRIORITY = {'FRESH': 70.0, 'READY': 35.0, 'RECOVERING': -25.0,
                      'FATIGUED': -150.0, 'SUPPRESSED': -1000.0}
PROFILES = {
    'baseline': {'coverage_max': 0., 'overlap_weights': (), 'stimulus_cap': None, 'aggregation': 'top_mean'},
    'coverage': {'coverage_max': 40., 'overlap_weights': (), 'stimulus_cap': None, 'aggregation': 'top_mean'},
    'coverage_rotation': {'coverage_max': 40., 'overlap_weights': (35., 20., 10.), 'stimulus_cap': None, 'aggregation': 'top_mean'},
    'balanced': {'coverage_max': 40., 'overlap_weights': (35., 20., 10.), 'stimulus_cap': 105., 'aggregation': 'top_mean'},
    'correlation': {'coverage_max': 40., 'overlap_weights': (35., 20., 10.), 'stimulus_cap': 105., 'aggregation': 'anchor_support'},
}
DEFAULT_CALIBRATION = 'correlation'  # Smallest candidate passing the fixed-window calibration.
GRACE_DAYS = 4.0
RAMP_DAYS = 10.0
HISTORY_DAYS = 30
SECONDARY_WEIGHT = .35
RECOMMENDATION_WEIGHT = .45
EXPOSURE_DECAY_DAYS = 7.
RECOVERING_COVERAGE_SCALE = .5
ACTUAL_ROTATION_MAX = 20.
ACTUAL_ROTATION_DECAY_DAYS = 3.
ANCHOR_WEIGHT = .8
OVERLAP_ROTATION_CAP = 50.
VIABLE_NEED_MARGIN = 35.
ELEVATED_COVERAGE = 15.
YESTERDAY_FOCUS_PENALTY = 35.
REPEATED_FOCUS_PENALTY = 15.
LOW_CONFIDENCE_REPEAT_PENALTY = 20.
REGION_BONUS = 20.
RELATIVE_EXPOSURE_SHARE = .5
FULFILLED_RECOMMENDATION_DISCOUNT = .5


def bounded_history(history, now):
    today = now.astimezone(EASTERN).date()
    output = []
    for row in history or []:
        when = row.get('plan_date')
        if isinstance(when, datetime): when = when.astimezone(EASTERN).date()
        elif isinstance(when, str): when = date.fromisoformat(when[:10])
        if when is not None and 0 < (today - when).days <= HISTORY_DAYS:
            output.append({**row, 'plan_date': when})
    return sorted(output, key=lambda r: r['plan_date'], reverse=True)


def _gap(row, key, now, fallback):
    when = row.get('last_' + key + '_trained_at')
    if when:
        when = datetime.fromisoformat(when) if isinstance(when, str) else when
        if when.tzinfo is None: when = when.replace(tzinfo=timezone.utc)
        if when > now: return HISTORY_DAYS  # Never treat a future row as exposure.
        return min(HISTORY_DAYS, (now - when).total_seconds() / 86400)
    value = row.get('days_since_' + key + '_training')
    return min(HISTORY_DAYS, max(0., float(value))) if value is not None else fallback


def coverage_state(muscles, readiness, history, now, maximum=40.):
    history = bounded_history(history, now)
    today = now.astimezone(EASTERN).date()
    rows = {}
    for muscle, data in muscles.items():
        primary = _gap(data, 'primary', now, HISTORY_DAYS)
        secondary = _gap(data, 'secondary', now, HISTORY_DAYS)
        effective_gap = min(primary, (1-SECONDARY_WEIGHT)*primary + SECONDARY_WEIGHT*secondary)
        actual = float(data.get('primary_sets') or 0) + SECONDARY_WEIGHT*float(data.get('secondary_sets') or 0)
        recommended = 0.
        recent = None
        for item in history:
            primary_names = item.get('primary_focus')
            if primary_names is None: primary_names = item.get('selected_muscles') or []
            weight = 1. if muscle in primary_names else SECONDARY_WEIGHT if muscle in (item.get('secondary_focus') or []) else 0.
            age = (today-item['plan_date']).days
            recommended += weight * exp(-age/EXPOSURE_DECAY_DAYS)
            if weight and recent is None: recent = age
        rows[muscle] = {'actual_primary_gap_days': primary, 'actual_secondary_gap_days': secondary,
                        'effective_gap_days': effective_gap, 'actual_effective_sets_30d': actual,
                        'recommended_exposure': recommended, 'days_since_recommendation': recent,
                        'actual_primary_sets_30d': data.get('primary_sets') or 0,
                        'actual_secondary_sets_30d': data.get('secondary_sets') or 0}
    reference = median([r['actual_effective_sets_30d'] for r in rows.values()]) if rows else 0.
    for muscle, row in rows.items():
        neglect = max(0., row['effective_gap_days']-GRACE_DAYS)
        relative = max(0., (reference-row['actual_effective_sets_30d'])/(reference+1.))
        raw = maximum * neglect/(RAMP_DAYS+neglect) * ((1.-RELATIVE_EXPOSURE_SHARE)+RELATIVE_EXPOSURE_SHARE*relative)
        raw /= 1.+RECOMMENDATION_WEIGHT*row['recommended_exposure']
        state = readiness.get(muscle, {}).get('readiness_state')
        scale = 0. if state in ('FATIGUED','SUPPRESSED') else RECOVERING_COVERAGE_SCALE if state == 'RECOVERING' else 1.
        row.update(coverage_score=round(raw*scale, 3), unscaled_coverage_score=round(raw,3),
                   readiness_scale=scale, relative_exposure_reference=reference)
    return rows


def overlap_rotation(muscles, history, actual, now, weights):
    current = set(muscles)
    if not current or not weights: return 0., []
    history = bounded_history(history, now) if now is not None else history
    parts=[]
    for index, weight in enumerate(weights):
        if index >= len(history): break
        entry=history[index]; previous=set(entry.get('selected_muscles') or [])
        common=current & previous
        overlap=len(common)/min(len(current),len(previous)) if previous else 0.
        # Performed training since a recommendation partially retires that
        # recommendation; actual recent overlap is counted separately below.
        fulfilled=0.
        if now is not None and entry.get('plan_date'):
            age=(now.astimezone(EASTERN).date()-entry['plan_date']).days
            fulfilled=sum(actual.get(m,{}).get('actual_primary_gap_days',HISTORY_DAYS)<age for m in common)/max(1,len(common))
        penalty=weight*overlap*(1.-FULFILLED_RECOMMENDATION_DISCOUNT*fulfilled)
        parts.append({'kind':'recommendation','index':index,'overlap':overlap,'fulfilled_fraction':fulfilled,'penalty':penalty})
    if actual:
        exposure=sum(exp(-actual.get(m,{}).get('actual_primary_gap_days',HISTORY_DAYS)/ACTUAL_ROTATION_DECAY_DAYS) for m in current)/len(current)
        parts.append({'kind':'actual_training','overlap':exposure,'penalty':ACTUAL_ROTATION_MAX*exposure})
    return round(min(OVERLAP_ROTATION_CAP, sum(p['penalty'] for p in parts)),3), parts
