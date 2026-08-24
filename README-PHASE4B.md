# WHOOP Health Intelligence — Phase 4B

Phase 4B connects the validated WHOOP intelligence pipeline to the OpenAI Responses API.

Architecture:

WHOOP -> Supabase -> Daily Metrics -> Personal Baselines -> Trend Signals ->
Deterministic Recommendation -> OpenAI Explanation Layer

Important safety design:
The AI model cannot change the deterministic Push / Normal / Moderate /
Active Recovery / Rest classification. The application overwrites the model's
training_recommendation field with the deterministic value before returning it.

## New Render environment variables

Required:
OPENAI_API_KEY

Optional:
OPENAI_MODEL

If OPENAI_MODEL is omitted, the app uses:
gpt-5.6-luna

Do NOT put the API key in GitHub, .env.example with a real value, screenshots,
or chat messages.

## Deployment

Upload/replace:
- ai_intelligence.py (new)
- main.py (replace)
- requirements.txt (replace)
- README-PHASE4B.md (optional)

Commit:
Add Phase 4B OpenAI intelligence

After Render redeploys:
1. Confirm /health reports phase 4B, version 0.4.1.
2. Sign in.
3. Open "Validate OpenAI intelligence connection".
4. Send the returned JSON with no secrets included.

## Important

This connects the application to the OpenAI API. It does NOT yet make your
Supabase/WHOOP database directly queryable from an existing ChatGPT conversation.
A later ChatGPT App/MCP layer can expose safe read-only endpoints to ChatGPT.
