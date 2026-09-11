# Weekly Intelligence nullable-target regression

The weekly cache-miss path failed deterministically when the active goal had no
`daily_step_target`. `_activity_period()` bound the target twice: first in
`%s IS NOT NULL`, then in `steps >= %s`. PostgreSQL could infer the comparison
parameter from the `double precision` steps column, but could not infer the
independent first parameter when Python supplied `None`.

The fix removes the redundant null predicate and its parameter. A comparison
against SQL NULL does not satisfy the filtered count, so observations remain
available and zero days are counted as meeting an unspecified target. Existing
`no_goal`/`insufficient_data` classifications remain intact. No goal default,
readiness rule, freshness rule, schema or pipeline behavior changes.

The September 11 Development incident occurred before the daily metrics rebuild
started. The same exception was reproduced with the pipeline idle. Its primary
classification is **CLASS 1 — deterministic weekly endpoint defect**. This is a
separate query from the earlier body-composition nullable-as-of issue, and the
weekly predicate predates B3.1/B3.2.

## Dependency path

```text
mobile_weekly_health_intelligence
  -> get_weekly_health_intelligence / get_or_create_intelligence
     -> weekly_source_freshness (whoop_daily_metrics)
     -> weekly_health_intelligence cache lookup
     -> build_weekly_health_ai_payload -> weekly_health_summary
        -> rolling WHOOP period aggregates (whoop_daily_metrics)
        -> get_active_goal (health_goal_profiles)
        -> strength adherence (tonal_workouts, overrides, sets)
        -> activity adherence -> _activity_period (apple_health_daily_activity)
        -> body context -> apple_health_trends (body samples, active goal)
     -> AI generation on miss -> generate_weekly_health_intelligence
     -> weekly cache upsert
```

Weekly analytics computes its own historical baselines; it does not query the
materialized `whoop_daily_baselines` table. It does not call Goal Progress V1/V2,
Today Plan, or the standalone body-composition-progress endpoint. Its body
context uses the internal Apple Health trends implementation.

## Consistency limits

Source records commit per upsert. The metrics truncate and insert share one
transaction; the baseline truncate and all metric inserts share a different
transaction. These stages do not commit an intentional truncate-only gap, but
the complete pipeline is not a single atomic snapshot. Weekly reads open
independent connections, and AI generation rebuilds the deterministic payload.
These are separate consistency/latency considerations, not evidence that they
caused this SQL type error.

Do not substitute a blanket repeatable-read change without considering
PostgreSQL's documented TRUNCATE behavior: it takes an exclusive lock and is not
MVCC-safe for older snapshots. See [TRUNCATE documentation](https://www.postgresql.org/docs/current/sql-truncate.html)
and [transaction isolation](https://www.postgresql.org/docs/current/transaction-iso.html).

Future snapshot hardening should capture one dated deterministic input, generate
from that exact input, and preserve source-version identity through persistence.
Prior-period weekly results must keep their actual period date while today's
physiology is pending. No global lock, sleep, pipeline redesign or AI fallback
was added in this fix.

## Tests and observability

`test_weekly_activity_target.py` tests null/numeric/zero targets, empty periods,
optional null steps, future-row exclusion, and logging privacy.
`test_weekly_refresh_postgres.py` exercises the HTTP route with real read-only
Dev queries across stable/pending/refresh-transition/post-refresh/missing-
activity scenarios. It isolates authentication, AI, setup DDL and persistence.
A refresh transition is modeled between successive reads; no live pipeline
writes or load test are performed. These tests are not a hosted AI integration
or a proof of all possible concurrent interleavings.

Opt in with `WEEKLY_P0_POSTGRES_TESTS=1` and the existing Development database
credential in the environment. Never put a connection string in command
arguments or files. The tests enforce the Development target and explicitly
verify `SET TRANSACTION READ ONLY`. Seed unrelated startup configuration with
non-secret test values when running route tests locally.

Weekly failures now log exception class, source-file/function/line locations,
failure stage and duration, without exception messages, model responses,
connection strings, tokens, request headers or health payloads. The exception
still propagates; unrelated defects are not silently converted into success.
