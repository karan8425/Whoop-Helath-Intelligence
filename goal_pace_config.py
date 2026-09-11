"""Central configuration for Goal Setting V2 pace / timeline assumptions.

Every rate constant used by the timeline engine lives here - nothing is
buried in the logic. Rates are expressed as a FRACTION OF CURRENT BODY
WEIGHT PER WEEK, which is the guardrail representation for weight-loss
oriented goals (a 0.6%/week loss is sustainable for a 200 lb person and
for a 140 lb person; a fixed "1 lb/week" is not).

Methodology
-----------
* comfortable / recommended / faster are three sustainable bands. They are
  all conservative by design: even `faster` stays inside a rate that
  research generally associates with retained lean mass when protein and
  resistance training are adequate.
* `maximum_supported_weekly_rate` is the hard ceiling. A custom target
  date that implies a faster rate than this is reported as
  `outside_supported_range`; the app does NOT respond by prescribing an
  ever-larger calorie deficit.
* Lean-mass priority nudges which band is *recommended by default*, it
  does not change the band rates themselves:
    preserve_build -> recommend the comfortable band
    balanced       -> recommend the recommended band
    faster         -> recommend the faster band
"""

# Fraction of current body weight to lose per week.
COMFORTABLE_WEEKLY_RATE = 0.0040        # 0.40 % / week
RECOMMENDED_WEEKLY_RATE = 0.0065        # 0.65 % / week
FASTER_WEEKLY_RATE = 0.0090            # 0.90 % / week
MAXIMUM_SUPPORTED_WEEKLY_RATE = 0.0110  # 1.10 % / week  (hard ceiling)

# Below this many lb of implied change the target is treated as "maintain".
MIN_MEANINGFUL_WEIGHT_CHANGE_LB = 1.0

# Lean-mass compatibility tolerance. A target whose implied lean mass is
# within +/- this many lb of the current estimate is "compatible".
LEAN_MASS_TOLERANCE_LB = 3.0

# Bounds used only for basic sanity checks.
MIN_PLAUSIBLE_BODY_FAT_PCT = 3.0
MAX_PLAUSIBLE_BODY_FAT_PCT = 60.0

PACE_BANDS = {
    "comfortable": COMFORTABLE_WEEKLY_RATE,
    "recommended": RECOMMENDED_WEEKLY_RATE,
    "faster": FASTER_WEEKLY_RATE,
}

# Which band each lean-mass priority recommends by default.
PRIORITY_DEFAULT_BAND = {
    "preserve_build": "comfortable",
    "balanced": "recommended",
    "faster": "faster",
}

DEFAULT_LEAN_MASS_PRIORITY = "preserve_build"

# Goal types the V2 flow understands, and how each maps onto the existing
# `phase` vocabulary (health_goal_profiles.phase / ALLOWED_PHASES).
GOAL_TYPE_TO_PHASE = {
    "lose_body_fat": "lean_cut",
    "build_muscle": "lean_bulk",
    "recomposition": "lean_cut",
    "improve_fitness": "maintenance",
    "maintain": "maintenance",
}

# Goal types that are driven by the weight/body-fat timeline engine.
BODY_COMPOSITION_GOAL_TYPES = {"lose_body_fat", "recomposition", "build_muscle"}


METHODOLOGY_TEXT = {
    "rate_basis": (
        "Pace is a fraction of current body weight per week: "
        f"comfortable {COMFORTABLE_WEEKLY_RATE*100:.2f}%, "
        f"recommended {RECOMMENDED_WEEKLY_RATE*100:.2f}%, "
        f"faster {FASTER_WEEKLY_RATE*100:.2f}%. The supported ceiling is "
        f"{MAXIMUM_SUPPORTED_WEEKLY_RATE*100:.2f}%/week."
    ),
    "duration": (
        "estimated_weeks = ceil(|target_weight - current_weight| / "
        "(rate * current_weight)); target_date = today + estimated_weeks*7 "
        "calendar days."
    ),
    "guardrail": (
        "A custom date needing a rate above the supported ceiling returns "
        "timeline_status = outside_supported_range and a recommended date "
        "instead. The aspirational date is preserved separately; the "
        "actionable plan stays within guardrails and never drives a more "
        "aggressive calorie target."
    ),
    "lean_mass": (
        "Lean-mass priority only changes which band is recommended by "
        "default (preserve_build -> comfortable, balanced -> recommended, "
        "faster -> faster). It does not change the band rates."
    ),
}
