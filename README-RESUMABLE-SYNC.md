WHOOP Phase 2.1 resumable sync

Replace main.py and sync.py in GitHub. The import now saves WHOOP pagination checkpoints in Supabase and processes three pages per dataset per request.

After Render redeploys:
1. Sign in.
2. Run next historical sync batch.
3. Return to the home page and repeat.
4. Stop when View historical sync status reports "complete": true.
5. Check database record counts.

Existing records are upserted by their WHOOP IDs, so encountering previously imported records will not create duplicates.
