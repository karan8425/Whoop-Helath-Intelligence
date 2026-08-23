# WHOOP Health Intelligence — Phase 1

This repository contains the Phase 1 WHOOP connection app.

## What is hosted where

- **GitHub:** source code
- **GitHub Pages:** public privacy policy
- **Render:** live FastAPI application and WHOOP OAuth callback

Do not put WHOOP secrets, access tokens, refresh tokens, or `.env` files into GitHub.

## WHOOP scopes

Request:

- offline
- read:profile
- read:body_measurement
- read:recovery
- read:cycles
- read:sleep
- read:workout

## Render deployment

Build command:

`pip install -r requirements.txt`

Start command:

`uvicorn app.main:app --host 0.0.0.0 --port $PORT`

Required secret environment variables:

- WHOOP_CLIENT_ID
- WHOOP_CLIENT_SECRET
- WHOOP_REDIRECT_URI
- SESSION_SECRET
- TOKEN_ENCRYPTION_KEY

## GitHub Pages

The privacy policy is stored at:

`docs/index.html`

Enable GitHub Pages using:

- Branch: `main`
- Folder: `/docs`
