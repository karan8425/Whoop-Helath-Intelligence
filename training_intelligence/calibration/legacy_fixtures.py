"""TKI-5.3 (sections 8-9): the Sep-12 LEGACY FORENSIC FIXTURE.

THIS IS NOT A RECONSTRUCTED ORIGINAL DECISION SNAPSHOT. No decision-
snapshot mechanism existed before TKI-5.3 (see the reproducibility
audit in TRAINING_INTELLIGENCE_TKI53_REPORT.md, section D) and the
underlying WHOOP recovery reading for Sep-12 has since drifted (a live
re-query on 2026-09-13 returns 66%/"moderate" for Sep-11, not the 94%
the original forensic finding recorded - see section E of the same
report). Sep-12's original state is therefore classified
UNREPRODUCIBLE_LEGACY_STATE: it can be described from the surviving
forensic record, never replayed byte-for-byte.

This module recovers exactly the aggregate facts that survive in
TRAINING_INTELLIGENCE_TKI52_REPORT.md's "Forensic findings carried
forward" section - WHOOP Recovery 94%, Upper Push, TKI-3 range 10-10,
3 exercises, 10 sets, displayed (raw) volume 5,772 lb, corrected
cable-aware volume ~9,204 lb, strict Upper-Push-family median ~14,144
lb, recent Push-dominant median ~18,002 lb - and runs them through the
new workload_v2 gap-decomposition machinery.

What is explicitly NOT recovered and NOT fabricated: the original
per-exercise/per-set breakdown, and the original `binding_constraints`
list (which of historical_capacity/systemic_readiness/local_readiness/
session_structure were binding at that decision time). The forensic
narrative names "a short-window muscle-budget cap acting as a hard
capacity ceiling" as a root cause, not a readiness cap - recovery was
94% (high), so systemic_readiness being the binding justification is
implausible, but the exact original constraint name cannot be proven
from what survives. This module reports BOTH plausible outcomes
instead of picking one and presenting it as fact.
"""
from __future__ import annotations

from training_intelligence.calibration.workload_v2 import (
    decompose_gap, WITHIN_RANGE_RATIO_BAND, ACCEPTED_JUSTIFICATIONS,
)

SEP_12_FORENSIC_FACTS = {
    "local_date": "2026-09-12",
    "cache_timestamp_local": "2026-09-12T22:26:00-04:00",
    "whoop_recovery_score": 94,
    "family": "Upper Push",
    "tki3_feasible_range": (10, 10),
    "target_sets": 10,
    "delivered_sets": 10,
    "exercise_count": 3,
    "displayed_raw_volume_lb": 5772.0,
    "cable_aware_volume_lb": 9204.0,
    "strict_upper_push_median_lb": 14144.0,
    "recent_push_dominant_median_lb": 18002.0,
    "provenance": "TRAINING_INTELLIGENCE_TKI52_REPORT.md, 'Forensic findings carried forward'",
}

# Named, not fabricated: the two named root causes from the surviving
# forensic narrative that plausibly explain a hard capacity ceiling
# rather than a readiness-driven reduction, given 94% recovery (high).
PLAUSIBLE_ORIGINAL_BINDING_CONSTRAINTS = ("historical_capacity", "session_structure")


def sep12_legacy_fixture_analysis():
    """Runs the surviving Sep-12 aggregate facts through decompose_gap
    for BOTH the strict (exact-family) and recent (broader) reference
    windows, and BOTH raw and cable-aware volume readings. Returns a
    dict explicitly labeled with what is fixture-derived vs. what
    cannot be honestly resolved from the surviving record."""
    facts = SEP_12_FORENSIC_FACTS
    results = {}
    for volume_label, volume in (("raw", facts["displayed_raw_volume_lb"]),
                                  ("cable_aware", facts["cable_aware_volume_lb"])):
        for window_label, median in (("strict_exact_family", facts["strict_upper_push_median_lb"]),
                                      ("recent_push_dominant", facts["recent_push_dominant_median_lb"])):
            # Only the absolute-workload dimension survives in the forensic
            # record - per-set/set-count/exercise-count medians for that
            # historical cohort were never captured and are NOT invented
            # here, so this window only supports decompose_gap's absolute
            # ratio; the other three ratios are honestly reported as
            # NOT_RECOVERABLE rather than backfilled with plausible-looking
            # numbers.
            window = {
                "normalized_workload": {"median": median},
                "workload_per_set": {"median": None},
                "working_sets": {"median": None},
                "exercises": {"median": None},
            }
            gap = decompose_gap(volume, facts["delivered_sets"], facts["exercise_count"], window)
            low, high = WITHIN_RANGE_RATIO_BAND
            ratio = gap["absolute_ratio_to_median"]
            if ratio is None:
                status = "INSUFFICIENT_DATA"
            elif low <= ratio <= high:
                status = "WITHIN_PERSONAL_RANGE"
            elif ratio < low:
                status = "BELOW_PERSONAL_RANGE"
            else:
                status = "ABOVE_PERSONAL_RANGE"
            results[f"{volume_label}__{window_label}"] = {
                "estimated_workload": volume, "reference_median": median,
                "absolute_ratio_to_median": ratio, "status_without_justification_lookup": status,
                "workload_per_set_ratio": "NOT_RECOVERABLE_FROM_SURVIVING_RECORD",
                "set_count_ratio": "NOT_RECOVERABLE_FROM_SURVIVING_RECORD",
                "exercise_count_ratio": "NOT_RECOVERABLE_FROM_SURVIVING_RECORD",
            }
    return {
        "classification": "UNREPRODUCIBLE_LEGACY_STATE",
        "facts": facts,
        "decomposition": results,
        "justification_note": (
            "The original binding_constraints list was never persisted (no "
            "decision-snapshot mechanism existed before TKI-5.3) and cannot "
            "be recovered. Given 94% recovery (high), a systemic_readiness "
            "justification is implausible; the forensic narrative's root "
            f"cause #2 points instead at {PLAUSIBLE_ORIGINAL_BINDING_CONSTRAINTS}, "
            "both of which ARE in workload_v2's ACCEPTED_JUSTIFICATIONS "
            f"({ACCEPTED_JUSTIFICATIONS}) - but this module does not assert "
            "that they were actually binding that day, only that they are "
            "the most plausible surviving explanation."
        ),
        "quality_verdict": "QUESTIONABLE",
        "quality_verdict_note": "Not forced to PASS/SUPPORTED - workload was below every reference distribution regardless of raw-vs-cable-aware reading or window choice, and the deviation's justification cannot be honestly confirmed.",
    }
