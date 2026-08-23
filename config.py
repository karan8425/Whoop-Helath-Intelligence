import os
from dotenv import load_dotenv

load_dotenv()

WHOOP_CLIENT_ID = os.getenv("WHOOP_CLIENT_ID", "")
WHOOP_CLIENT_SECRET = os.getenv("WHOOP_CLIENT_SECRET", "")
WHOOP_REDIRECT_URI = os.getenv("WHOOP_REDIRECT_URI", "http://localhost:8000/whoop/callback")
SESSION_SECRET = os.getenv("SESSION_SECRET", "")
TOKEN_ENCRYPTION_KEY = os.getenv("TOKEN_ENCRYPTION_KEY", "")

WHOOP_AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
WHOOP_TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
WHOOP_API_BASE = "https://api.prod.whoop.com/developer"

WHOOP_SCOPES = [
    "offline",
    "read:profile",
    "read:body_measurement",
    "read:recovery",
    "read:cycles",
    "read:sleep",
    "read:workout",
]

def validate_config():
    missing = []
    for name, value in {
        "WHOOP_CLIENT_ID": WHOOP_CLIENT_ID,
        "WHOOP_CLIENT_SECRET": WHOOP_CLIENT_SECRET,
        "SESSION_SECRET": SESSION_SECRET,
        "TOKEN_ENCRYPTION_KEY": TOKEN_ENCRYPTION_KEY,
    }.items():
        if not value:
            missing.append(name)
    if missing:
        raise RuntimeError("Missing required environment variables: " + ", ".join(missing))
