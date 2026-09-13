"""Paired, completed working-set progression; standard-mode destination.

Filtering is performed on full eligible set history before the session limit.
No cross-mode conversion or synthetic load/rep pairing.
"""
from collections import defaultdict
from statistics import median
from integrations.tonal.progressive_overload import CONFIG
from training_intelligence.calibration.history import (
    valid_rows, mode, pattern, COMPOUNDS, number, quantile, unassisted,
)

MAX_SESSIONS = 6
LOAD_MATCH_FRACTION = .05


def matched_history(rows, movement_id, as_of):
    compatible = [r for r in valid_rows(rows, as_of)
                  if str(r["movement_id"]) == str(movement_id) and mode(r) == "standard" and unassisted(r)]
    sessions = []
    for row in compatible:
        activity = str(row["activity_id"])
        if activity not in sessions:
            sessions.append(activity)
    chosen = set(sessions[:MAX_SESSIONS])
    return [r for r in compatible if str(r["activity_id"]) in chosen]


def prescribe(profile, rows, as_of, band, sets):
    rr = matched_history(rows, profile["movement_id"], as_of)
    kind = "compound" if pattern(profile) in COMPOUNDS else "isolation"
    lo, hi = CONFIG["rep_range"][kind]
    rir = CONFIG["rir"].get(band, CONFIG["rir"]["moderate"])[kind]
    rest = CONFIG["rest_seconds"][kind]
    confidence, state, trend = "LOW", "REBUILD", "INSUFFICIENT_DATA"
    load, reps = None, lo
    pair = []
    if rr:
        latest = [r for r in rr if r["activity_id"] == rr[0]["activity_id"]]
        # A load actually performed, closest to the latest working-set median.
        center = median(r["base_weight"] for r in latest)
        load = min((r["base_weight"] for r in latest), key=lambda x: (abs(x-center), x))
        pair = [r for r in rr if abs(r["base_weight"] - load) <= max(1, load * LOAD_MATCH_FRACTION)]
        latest_pair = [r for r in latest if r in pair]
        reps = max(lo, min(hi, round(median(r["rep_count"] for r in latest_pair))))
        session_ids = {str(r["activity_id"]) for r in pair}
        confidence = "HIGH" if len(session_ids) >= 4 else ("MEDIUM" if len(session_ids) >= 2 else "LOW")
        summaries = defaultdict(list)
        for r in pair:
            summaries[str(r["activity_id"])].append(r["rep_count"])
        performance = [median(v) for v in summaries.values()]
        if len(performance) >= 3:
            change = (median(performance[:2]) / median(performance[2:])) - 1
            trend = "DECLINING" if change <= -.08 else ("IMPROVING" if change >= .08 else "STABLE")
        def reliable_ceiling(r):
            effort = number((r.get("raw_data") or {}).get("repsInReserve"))
            return r["rep_count"] >= hi and effort is not None and rir[0] <= effort <= 5
        ceiling_sessions = {str(r["activity_id"]) for r in pair if reliable_ceiling(r)}
        if len(session_ids) >= 2:
            state = "HOLD"
            if trend == "DECLINING" and band in ("low", "moderate"):
                state, sets = "REDUCE", max(1, sets - 1)
            elif band in ("good", "high") and len(ceiling_sessions) >= 2 and all(reliable_ceiling(r) for r in latest_pair) and load < 100:
                state, load, reps = "PROGRESS_LOAD", min(100, load + CONFIG["resistance_increment_lb"]), lo
            elif band in ("good", "high") and reps < hi:
                state, reps = "PROGRESS_REPS", min(hi, reps + 1)
    reasons = {
        "REBUILD": "Insufficient matched standard-mode working-set evidence; no cross-mode progression claimed.",
        "HOLD": "Repeat observed working resistance; matched evidence does not justify load progression.",
        "REDUCE": "Matched-load rep performance declined under reduced systemic readiness.",
        "PROGRESS_REPS": "Matched standard-mode working sets support a bounded rep step.",
        "PROGRESS_LOAD": "Repeated matched-load working sets reached the ceiling with recorded effort headroom.",
    }
    labels = {"REBUILD": "REBUILD", "HOLD": "HOLD", "REDUCE": "REDUCE",
              "PROGRESS_REPS": "ADD REPS", "PROGRESS_LOAD": "ADD LOAD"}
    return {
        "sets": sets, "target_resistance_lb": load, "target_reps_per_set": reps,
        "rep_range": {"minimum": lo, "maximum": hi},
        "target_rir": {"minimum": rir[0], "maximum": rir[1]},
        "rest_seconds": {"minimum": rest[0], "maximum": rest[1]},
        "progression_state": state, "progression_label": labels[state],
        "rationale": reasons[state], "trajectory": trend, "confidence": confidence,
        "smart_weight_mode": "standard", "cross_mode_fallback": False,
        "comparable_performance": {
            "count": len({str(r["activity_id"]) for r in rr}),
            "matched_set_count": len(pair), "smart_weight_mode": "standard",
            "records": [{"activity_id": str(r["activity_id"]), "set_index": r["set_index"],
                         "load": r["base_weight"], "reps": r["rep_count"],
                         "rir": number((r.get("raw_data") or {}).get("repsInReserve"))} for r in pair],
        },
    }


def movement_capacity(rows, movement_id, as_of):
    rr = matched_history(rows, movement_id, as_of)
    counts = defaultdict(int)
    for row in rr:
        counts[str(row["activity_id"])] += 1
    # Typical upper session structure, not an unbounded latest-session maximum.
    cap = int(quantile(list(counts.values()), .75)) if len(counts) >= 3 else 4
    return max(1, min(cap, 8))
