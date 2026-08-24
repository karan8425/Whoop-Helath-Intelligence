# Phase 3A timezone fix

Replace only `analytics.py`.

Issue fixed:
WHOOP sometimes returns `timezone_offset = "Z"` for UTC. PostgreSQL cannot cast `Z` directly to an interval.

The patched query treats:
- `Z`
- empty timezone offsets
- null timezone offsets

as zero offset, while preserving normal offsets such as `-04:00`.

After Render redeploys:
1. Confirm `/health` still reports Phase 3A version 0.3.0.
2. Sign in.
3. Click `Build / rebuild daily metrics`.
4. If successful, click `Validate daily metrics`.
