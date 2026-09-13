"""Program Intelligence V2 - deterministic action/fit taxonomy.

No overlapping meanings; every action is a distinct, mutually
exclusive outcome of the decision hierarchy in decision.py.
"""
from __future__ import annotations

ADAPTATION_TAXONOMY_VERSION = 1

# KEEP: execute the nominal next session substantially as written.
# SHIFT: perform a different, legitimately-sequenced program session
#   earlier/later than nominal (e.g. Lower B ahead of Upper B).
# SUBSTITUTE: preserve the nominal session's intended stimulus but swap
#   its movement structure (a different resolved Tonal movement set)
#   because local readiness/equipment makes the nominal resolution
#   unsuitable - the SESSION stays the same, only its resolved
#   movements change. (V2 implements the eligibility/labeling for this;
#   actual alternate-movement selection reuses tonal_mapping.py's own
#   ranked alternates.)
# REDUCE: perform the nominal (or shifted) session at a reduced dose -
#   systemic capacity supports SOME training, not the full envelope.
# RECOVERY: active recovery / low-load movement only.
# REST: no meaningful training session is appropriate today.
# DEFER: the nominal session explicitly remains pending (not executed,
#   not cancelled) - used when no candidate is eligible today but REST/
#   RECOVERY is also not warranted (e.g. an outstanding session with no
#   time available at all).
ACTION_KEEP = "KEEP"
ACTION_SHIFT = "SHIFT"
ACTION_SUBSTITUTE = "SUBSTITUTE"
ACTION_REDUCE = "REDUCE"
ACTION_RECOVERY = "RECOVERY"
ACTION_REST = "REST"
ACTION_DEFER = "DEFER"

ACTIONS = (ACTION_KEEP, ACTION_SHIFT, ACTION_SUBSTITUTE, ACTION_REDUCE,
           ACTION_RECOVERY, ACTION_REST, ACTION_DEFER)

FIT_FITS = "FITS"
FIT_TIGHT = "TIGHT"
FIT_EXCEEDS = "EXCEEDS"
FIT_UNKNOWN = "UNKNOWN"
FIT_STATUSES = (FIT_FITS, FIT_TIGHT, FIT_EXCEEDS, FIT_UNKNOWN)

# PRODUCT POLICY: a session is "TIGHT" (fits, but with little margin)
# when available time is within this fraction of the estimated maximum
# duration - never a claim about physiology.
TIGHT_FIT_FRACTION = 0.9

# Local-readiness eligibility bands (section: hard rule - a systemically
# high WHOOP recovery must NEVER override a locally fatigued target
# muscle). READY/FRESH are eligible for full programming; RECOVERING is
# eligible only for reduced/secondary work; FATIGUED/SUPPRESSED are
# hard-ineligible as a PRIMARY target, regardless of systemic capacity.
READY_STATES = ("READY", "FRESH")
RECOVERING_STATES = ("RECOVERING",)
INELIGIBLE_PRIMARY_STATES = ("FATIGUED", "SUPPRESSED")
UNKNOWN_STATES = ("UNKNOWN",)
