"""Program Intelligence V2 - Daily Program Adaptation Engine.

Shadow-only. Nothing here is imported by main.py's live routes except
the one new admin-only, read-only diagnostic endpoint
(GET /training-intelligence/program-adaptation). Not connected to
/api/v1/todays-plan; no mobile output changes; no TRAINING_
INTELLIGENCE_MOBILE_ENABLED-gated behavior is touched.

Decides HOW to execute a program's training intent today (keep/shift/
substitute/reduce/recovery/rest), reusing - never duplicating - the
existing readiness, stimulus-ledger, capacity, progression, and
workload-sanity components already built by TKI-5.x/TKI-7 and the
Program Intelligence V1 layer.
"""
