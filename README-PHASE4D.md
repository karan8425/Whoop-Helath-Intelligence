# Phase 4D — Connect WHOOP Health Intelligence to ChatGPT Plus

Current recommendation:
Use a Custom GPT Action, not MCP, for ChatGPT Plus.

OpenAI currently supports GPT Actions for connecting custom GPTs to external APIs.
The Action uses a bearer API key and the supplied OpenAPI schema.

## Files

Upload to GitHub:
- coach_api.py (new)
- main.py (replace)

Reference files:
- openapi-health.json
- CUSTOM-GPT-INSTRUCTIONS.md

## Render secret

Generate a NEW random secret specifically for the ChatGPT Action.

Recommended command on Mac:

python3 -c "import secrets; print(secrets.token_urlsafe(48))"

Add the result to BOTH:
1. Render Web Service environment:
   CHATGPT_ACTION_API_KEY
2. Your Custom GPT Action authentication as a Bearer API key.

Do not reuse:
- WHOOP secret
- OpenAI API key
- Supabase password
- admin password

## Deploy

Commit:
Add Phase 4D ChatGPT read-only API

Confirm:
https://whoop-health-intelligence.onrender.com/health

returns:
phase = 4D
version = 0.4.4

## Custom GPT setup

In ChatGPT:
1. Explore GPTs / My GPTs -> Create.
2. Name: WHOOP Health & Fitness Coach
3. Add the contents of CUSTOM-GPT-INSTRUCTIONS.md to Instructions.
4. Go to Actions -> Create new action.
5. Authentication:
   API key
   Bearer
6. Paste the CHATGPT_ACTION_API_KEY value.
7. Import/paste openapi-health.json as the schema.
8. Test `getWhoopPipelineStatus`.
9. Test `getTodayHealthIntelligence`.
10. Save the GPT as "Only me".

Important:
The API is read-only.
It does not expose WHOOP credentials, OAuth tokens, Supabase credentials, or OpenAI keys.
