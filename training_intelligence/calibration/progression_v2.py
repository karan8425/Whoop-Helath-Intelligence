"""TKI-5.4: progression evidence V2 - a deterministic, tiered evidence
hierarchy replacing v1's single all-or-nothing exact-mode/exact-load
comparator (training_intelligence.calibration.progression, kept
unchanged for continuity and rollback comparison).

Root cause (TKI-5.4 baseline replay, 78 live lifting dates, 266
exercise-instances, 74.1% LOW confidence): 58.4% of LOW results were
movements with rich total history that v1 discarded almost entirely
because most sets were logged in a Smart Weight mode other than
"standard" (mode fragmentation); 38.1% were movements where v1's fixed
5%-of-one-center load window excluded otherwise-sufficient nearby
sessions once a user's load had progressed even slightly. Only 3.6%
were genuinely sparse history. This module targets the first two,
architecturally - not by lowering a sample-count threshold.

PRODUCT POLICY / CALIBRATION PARAMETER throughout. No physiological
claims. Never invents certainty where evidence does not exist (Tier 5
remains a real, expected outcome for genuinely sparse movements).

V2.1 fix (PROGRESSION_EVIDENCE_VERSION 1 -> 2): `_sessions_by_mode_class`
was applying the standard-mode-only `unassisted()` spotter-assistance
check to every Smart Weight mode, which meant this module's own
PARTIAL-mode (eccentric/chains/progressive) evidence path documented
above was never actually reachable for most movements - see
`_sessions_by_mode_class`'s docstring for the full forensic finding.
"""
from __future__ import annotations

from collections import defaultdict
from statistics import median

from integrations.tonal.progressive_overload import CONFIG
from training_intelligence.calibration.history import (
    valid_rows, mode as row_mode, pattern, COMPOUNDS, number, unassisted,
)
from training_intelligence.calibration.mode_compatibility import (
    MODE_COMPATIBILITY_POLICY_VERSION, classify as mode_classify,
)

PROGRESSION_EVIDENCE_VERSION = 2

MAX_EXACT_SESSIONS = 6
MAX_COMPATIBLE_SESSIONS = 6

# Tier 1 ("near-exact load"): the tighter of a 5% relative band or a
# 2 lb floor. The floor is grounded in Tonal's own real resistance
# granularity (progressive_overload.CONFIG["resistance_increment_lb"]
# = 1.0 lb) - two real increments, not an arbitrary number - so light
# loads are never given an unrealistically narrow absolute window.
LOAD_MATCH_TIER1_FRACTION = 0.05
LOAD_MATCH_TIER1_FLOOR_LB = 2.0

# Tier 2 ("adjacent Tonal-compatible load"): a materially wider band
# for sessions one real plateau-step away from the current one (e.g. a
# user who has since progressed 1-2 real increments). Confidence from
# this tier is capped at MEDIUM (see _confidence_for_tier) precisely
# because it trades load precision for sample count.
LOAD_MATCH_TIER2_FRACTION = 0.15
LOAD_MATCH_TIER2_FLOOR_LB = 4.0

MIN_SESSIONS_TIER1_HIGH = 4
MIN_SESSIONS_TIER1_MEDIUM = 2
MIN_SESSIONS_TIER2 = 3
MIN_SESSIONS_TIER3 = 4
MIN_SESSIONS_TIER4_TREND = 3

# RIR plausibility bounds - Tonal repsInReserve is a small non-negative
# integer in ordinary use; anything far outside this is a data-quality
# outlier, not a real effort reading.
RIR_PLAUSIBLE_RANGE = (0, 10)
RIR_MIN_AVAILABILITY_FOR_USABLE = 0.5
RIR_MAX_OUTLIER_FRACTION_FOR_MEDIUM = 0.1
RIR_MIN_AVAILABILITY_FOR_HIGH = 0.9


def rir_reliability(rows_for_movement):
    """Section 11: classify effort-signal reliability for a movement's
    history. Never assumed reliable by default."""
    if not rows_for_movement:
        return "UNUSABLE"
    values = [number((r.get("raw_data") or {}).get("repsInReserve")) for r in rows_for_movement]
    available = [v for v in values if v is not None]
    availability = len(available) / len(values)
    if availability < RIR_MIN_AVAILABILITY_FOR_USABLE:
        return "UNUSABLE"
    lo, hi = RIR_PLAUSIBLE_RANGE
    outliers = [v for v in available if not (lo <= v <= hi)]
    outlier_fraction = len(outliers) / len(available) if available else 0.0
    if outlier_fraction > RIR_MAX_OUTLIER_FRACTION_FOR_MEDIUM:
        return "LOW"
    if availability >= RIR_MIN_AVAILABILITY_FOR_HIGH:
        return "HIGH"
    return "MEDIUM"


def _sessions_by_mode_class(rows, movement_id, as_of):
    """All valid rows for this movement (ANY Smart Weight mode), grouped
    into sessions (most-recent-first, matching valid_rows' own
    ordering), each session tagged EXACT/PARTIAL/UNKNOWN via
    mode_compatibility - UNKNOWN sessions are never used.

    V2.1 fix: `unassisted()` is a spotter-assistance heuristic - it
    compares avg_weight to base_weight to catch standard-mode reps
    where a spotter meaningfully reduced the felt load. It was never
    valid for eccentric/chains/progressive rows: those PARTIAL-
    compatible modes, per mode_compatibility.py's own documented
    policy, DELIBERATELY layer extra resistance on top of base_weight
    at points in the rep, so avg_weight legitimately differs from
    base_weight there even under full, unassisted effort (this was
    confirmed against real Development data: PARTIAL-mode sessions
    consistently show avg_weight ~5-10% ABOVE base_weight, the
    signature of the mode's own mechanics, not of a spotter reducing
    load). progression.py (v1) scoped this correctly with
    `mode(r) == "standard" and unassisted(r)`; that scoping was lost
    when this module generalized matching to every Smart Weight mode,
    which meant `evidence()` silently discarded every PARTIAL-mode
    session's rows before they could ever reach mode classification -
    the mode-compatible fallback that mode_compatibility.py's policy
    explicitly allows was never actually reachable in practice. Now
    the assistance check is scoped back to standard-mode rows only,
    where it remains a legitimate check for exact-mode evidence.

    V2.1 fix #2: a real Tonal `activity_id` is not always one uniform
    Smart Weight mode throughout (e.g. flex-mode warm-up sets followed
    by eccentric-mode working sets in the same workout) - confirmed
    against real Development history. Grouping used to be keyed by
    activity_id alone and classified the WHOLE group from only its
    first row's mode, which could silently fold a genuinely UNKNOWN-
    mode set (flex/burnout) into a PARTIAL-classified group just
    because it shared an activity_id with an eccentric/chains set - a
    real equivalence claim this policy explicitly refuses to make.
    Grouping is now keyed by (activity_id, mode) so a pool never
    crosses a real mode boundary and every group is classified from
    its own actual, shared mode."""
    rows = [r for r in valid_rows(rows, as_of)
            if str(r["movement_id"]) == str(movement_id)
            and (row_mode(r) != "standard" or unassisted(r))]
    order, grouped = [], defaultdict(list)
    for r in rows:
        key = (str(r["activity_id"]), row_mode(r))
        if key not in grouped:
            order.append(key)
        grouped[key].append(r)
    exact, compatible = [], []
    for key in order:
        session_rows = grouped[key]
        session_mode = key[1]  # this group's own actual, shared mode
        classification = mode_classify(session_mode)
        if classification == "EXACT" and len(exact) < MAX_EXACT_SESSIONS:
            exact.append(session_rows)
        elif classification == "PARTIAL" and len(compatible) < MAX_COMPATIBLE_SESSIONS:
            compatible.append(session_rows)
    return exact, compatible


def _closest_performed_load(session_rows, center):
    return min((r["base_weight"] for r in session_rows), key=lambda x: (abs(x - center), x))


def _within(rows_pool, center, fraction, floor_lb):
    tolerance = max(floor_lb, center * fraction)
    return [r for r in rows_pool if abs(r["base_weight"] - center) <= tolerance]


def _confidence_for_tier(tier, session_count):
    if tier == 1:
        return "HIGH" if session_count >= MIN_SESSIONS_TIER1_HIGH else (
            "MEDIUM" if session_count >= MIN_SESSIONS_TIER1_MEDIUM else "LOW")
    if tier == 2:
        return "MEDIUM" if session_count >= MIN_SESSIONS_TIER2 else "LOW"
    if tier == 3:
        # Mode-compatible fallback is capped below HIGH regardless of
        # sample count - one confidence tier down from what the same
        # count would earn under exact-mode evidence (mode_compatibility
        # .PARTIAL_CONFIDENCE_PENALTY_TIERS).
        return "MEDIUM" if session_count >= MIN_SESSIONS_TIER3 else "LOW"
    return "LOW"  # tier 4 (trend-only) and tier 5 (insufficient)


def evidence(rows, movement_id, as_of):
    """Builds the section-10 tiered evidence pool. Returns the realized
    tier (1-5), the pool of rows backing it, and full provenance."""
    exact, compatible = _sessions_by_mode_class(rows, movement_id, as_of)
    exact_mode_sessions, compatible_mode_sessions = len(exact), len(compatible)
    all_sessions = exact + compatible  # already most-recent-first within each group
    if not all_sessions:
        return {
            "evidence_tier": 5, "pool": [], "anchor_session": None,
            "exact_mode_sessions": 0, "compatible_mode_sessions": 0,
            "reason_codes": ["no_usable_mode_history"],
        }
    # Anchor on the single most recent usable session (exact preferred
    # when tied on recency is impossible by construction - each list is
    # independently most-recent-first, so compare by begin_time).
    anchor = min(all_sessions, key=lambda s: -s[0]["begin_time"].timestamp())
    center = median(r["base_weight"] for r in anchor)

    tier1_pool = []
    for session in exact:
        tier1_pool.extend(_within(session, center, LOAD_MATCH_TIER1_FRACTION, LOAD_MATCH_TIER1_FLOOR_LB))
    tier1_sessions = len({str(r["activity_id"]) for r in tier1_pool})

    tier2_pool = []
    for session in exact:
        tier2_pool.extend(_within(session, center, LOAD_MATCH_TIER2_FRACTION, LOAD_MATCH_TIER2_FLOOR_LB))
    tier2_sessions = len({str(r["activity_id"]) for r in tier2_pool})

    tier3_pool = []
    for session in compatible:
        tier3_pool.extend(_within(session, center, LOAD_MATCH_TIER2_FRACTION, LOAD_MATCH_TIER2_FLOOR_LB))
    tier3_sessions = len({str(r["activity_id"]) for r in tier3_pool})

    if tier1_sessions >= MIN_SESSIONS_TIER1_MEDIUM:
        return {"evidence_tier": 1, "pool": tier1_pool, "anchor_session": anchor,
                "exact_mode_sessions": exact_mode_sessions, "compatible_mode_sessions": compatible_mode_sessions,
                "reason_codes": ["exact_mode_matched_load"]}
    if tier2_sessions >= MIN_SESSIONS_TIER2:
        return {"evidence_tier": 2, "pool": tier2_pool, "anchor_session": anchor,
                "exact_mode_sessions": exact_mode_sessions, "compatible_mode_sessions": compatible_mode_sessions,
                "reason_codes": ["exact_mode_adjacent_load_widened"]}
    if tier3_sessions >= MIN_SESSIONS_TIER3:
        return {"evidence_tier": 3, "pool": tier3_pool, "anchor_session": anchor,
                "exact_mode_sessions": exact_mode_sessions, "compatible_mode_sessions": compatible_mode_sessions,
                "reason_codes": ["mode_compatible_fallback_used"]}
    if len(all_sessions) >= MIN_SESSIONS_TIER4_TREND:
        trend_pool = [r for session in all_sessions for r in session]
        return {"evidence_tier": 4, "pool": trend_pool, "anchor_session": anchor,
                "exact_mode_sessions": exact_mode_sessions, "compatible_mode_sessions": compatible_mode_sessions,
                "reason_codes": ["broad_trend_only_insufficient_for_load_match"]}
    return {"evidence_tier": 5, "pool": [r for session in all_sessions for r in session], "anchor_session": anchor,
            "exact_mode_sessions": exact_mode_sessions, "compatible_mode_sessions": compatible_mode_sessions,
            "reason_codes": ["insufficient_matched_sessions"]}


def prescribe_v2(profile, rows, as_of, band, sets):
    """Drop-in-compatible replacement for progression.prescribe() - same
    return keys, plus a `progression_evidence` provenance object
    (section 15). Confidence and progression_state are now derived from
    the realized evidence tier, not a fixed single-comparator gate."""
    movement_id = profile["movement_id"]
    ev = evidence(rows, movement_id, as_of)
    tier, pool = ev["evidence_tier"], ev["pool"]
    kind = "compound" if pattern(profile) in COMPOUNDS else "isolation"
    lo, hi = CONFIG["rep_range"][kind]
    rir_band = CONFIG["rir"].get(band, CONFIG["rir"]["moderate"])[kind]
    rest = CONFIG["rest_seconds"][kind]
    sessions_in_pool = len({str(r["activity_id"]) for r in pool})
    confidence = _confidence_for_tier(tier, sessions_in_pool)
    reliability = rir_reliability([r for r in valid_rows(rows, as_of)
                                    if str(r["movement_id"]) == str(movement_id)])

    state, trend = "REBUILD", "INSUFFICIENT_DATA"
    load, reps = None, lo
    reason_codes = list(ev["reason_codes"])

    if tier <= 4 and pool:
        latest_activity = max(pool, key=lambda r: r["begin_time"])["activity_id"]
        latest = [r for r in pool if r["activity_id"] == latest_activity]
        center = median(r["base_weight"] for r in latest)
        load = _closest_performed_load(latest, center)
        reps = max(lo, min(hi, round(median(r["rep_count"] for r in latest))))
        summaries, session_time = defaultdict(list), {}
        for r in pool:
            activity = str(r["activity_id"])
            summaries[activity].append(r["rep_count"])
            session_time[activity] = max(session_time.get(activity, r["begin_time"]), r["begin_time"])
        # Explicit recency ordering by begin_time - never rely on dict
        # insertion order (fragile) or activity_id string order (a UUID
        # has no chronological meaning) to decide "most recent first".
        ordered = sorted(summaries.items(), key=lambda kv: session_time[kv[0]], reverse=True)
        performance = [median(v) for _, v in ordered]
        if len(performance) >= 3:
            change = (median(performance[:2]) / median(performance[2:])) - 1
            trend = "DECLINING" if change <= -.08 else ("IMPROVING" if change >= .08 else "STABLE")

        if tier in (1, 2, 3) and sessions_in_pool >= MIN_SESSIONS_TIER1_MEDIUM:
            state = "HOLD"
            if trend == "DECLINING" and band in ("low", "moderate"):
                state, sets = "REDUCE", max(1, sets - 1)
                reason_codes.append("declining_matched_performance")
            elif tier in (1, 2) and band in ("good", "high") and load is not None and load < 100:
                if reliability in ("HIGH", "MEDIUM"):
                    def reliable_ceiling(r):
                        effort = number((r.get("raw_data") or {}).get("repsInReserve"))
                        return r["rep_count"] >= hi and effort is not None and rir_band[0] <= effort <= 5
                    ceiling_sessions = {str(r["activity_id"]) for r in pool if reliable_ceiling(r)}
                    if len(ceiling_sessions) >= 2 and all(reliable_ceiling(r) for r in latest):
                        state, load, reps = "PROGRESS_LOAD", min(100, load + CONFIG["resistance_increment_lb"]), lo
                        reason_codes.append("effort_verified_ceiling")
                    elif reps < hi:
                        state, reps = "PROGRESS_REPS", min(hi, reps + 1)
                else:
                    # RIR is not trustworthy for this movement - progress
                    # by reps only, never claim a verified-effort load
                    # ceiling from unreliable data (section 11/14).
                    reason_codes.append("rir_unreliable_effort_downgraded_to_reps")
                    if reps < hi:
                        state, reps = "PROGRESS_REPS", min(hi, reps + 1)
            elif band in ("good", "high") and reps < hi:
                state, reps = "PROGRESS_REPS", min(hi, reps + 1)

    reasons = {
        "REBUILD": "Insufficient matched-mode/matched-load working-set evidence; no cross-mode equivalence claimed beyond documented partial compatibility.",
        "HOLD": "Repeat observed working resistance; matched evidence does not yet justify load progression.",
        "REDUCE": "Matched-evidence rep performance declined under reduced systemic readiness.",
        "PROGRESS_REPS": "Matched evidence supports a bounded rep step.",
        "PROGRESS_LOAD": "Repeated matched-load working sets reached the ceiling with reliably-verified effort headroom.",
    }
    labels = {"REBUILD": "REBUILD", "HOLD": "HOLD", "REDUCE": "REDUCE",
              "PROGRESS_REPS": "ADD REPS", "PROGRESS_LOAD": "ADD LOAD"}

    anchor = ev["anchor_session"]
    recency_days = (as_of - max(r["begin_time"] for r in anchor)).total_seconds() / 86400 if anchor else None
    temporal_span_days = ((max(r["begin_time"] for r in pool) - min(r["begin_time"] for r in pool)).total_seconds() / 86400
                           if pool else None)

    return {
        "sets": sets, "target_resistance_lb": load, "target_reps_per_set": reps,
        "rep_range": {"minimum": lo, "maximum": hi},
        "target_rir": {"minimum": rir_band[0], "maximum": rir_band[1]},
        "rest_seconds": {"minimum": rest[0], "maximum": rest[1]},
        "progression_state": state, "progression_label": labels[state],
        "rationale": reasons[state], "trajectory": trend, "confidence": confidence,
        "smart_weight_mode": "standard", "cross_mode_fallback": tier == 3,
        "comparable_performance": {
            "count": sessions_in_pool, "matched_set_count": len(pool), "smart_weight_mode": "standard",
            "records": [{"activity_id": str(r["activity_id"]), "set_index": r["set_index"],
                         "load": r["base_weight"], "reps": r["rep_count"],
                         "rir": number((r.get("raw_data") or {}).get("repsInReserve"))} for r in pool],
        },
        "progression_evidence": {
            "progression_evidence_version": PROGRESSION_EVIDENCE_VERSION,
            "mode_compatibility_policy_version": MODE_COMPATIBILITY_POLICY_VERSION,
            "confidence": confidence, "evidence_tier": tier,
            "exact_mode_sessions": ev["exact_mode_sessions"],
            "compatible_mode_sessions": ev["compatible_mode_sessions"],
            "matched_working_sets": len(pool),
            "load_match_quality": {1: "exact", 2: "adjacent", 3: "cross_mode", 4: "trend_only", 5: "none"}[tier],
            "effort_quality": reliability,
            "recency_days": round(recency_days, 2) if recency_days is not None else None,
            "temporal_span_days": round(temporal_span_days, 2) if temporal_span_days is not None else None,
            "reason_codes": reason_codes,
        },
    }
