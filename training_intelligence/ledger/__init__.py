"""Intelligence Architecture M1.0: Decision Ledger.

A durable, engine-agnostic record of every training-intelligence
recommendation - who/what produced it, exactly what state it saw, what
it selected, and (separately, appended later) what actually happened.

Shadow-only observation layer: nothing here changes what any engine
(V2.1 today, a future multi-candidate V3 later) decides. It is written
AFTER a decision is made, never consulted to make one. See
`v2_1_integration.py` for the narrow point where V2.1's existing,
unmodified `build_daily_program_adaptation()` result is observed and
recorded as a single, selected candidate - never a fabricated
alternative.
"""
