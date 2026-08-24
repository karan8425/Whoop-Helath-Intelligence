# Phase 3A duplicate-date fix

Replace only `analytics.py`.

Why this was needed:
Some WHOOP cycle records map to the same local calendar date. The analytics table intentionally has one row per day, so inserting every cycle caused a duplicate primary-key error.

The patch:
1. Converts WHOOP timezone offsets safely, including `Z`.
2. Calculates each cycle's local metric date.
3. If multiple cycles map to one date, retains the most recent cycle for that date.
4. Reports how many source dates contained multiple cycles so the behavior is visible rather than hidden.

After Render redeploys:
1. Confirm `/health` reports phase 3A / version 0.3.0.
2. Click `Build / rebuild daily metrics`.
3. Send the returned JSON.
4. If successful, click `Validate daily metrics` and send that JSON.
