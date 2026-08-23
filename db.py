import sqlite3
from pathlib import Path
from cryptography.fernet import Fernet
from config import TOKEN_ENCRYPTION_KEY

DB_PATH = Path("whoop.db")

def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with _connect() as conn:
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS oauth_tokens (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                encrypted_token BLOB NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            '''
        )
        conn.commit()

def _fernet():
    return Fernet(TOKEN_ENCRYPTION_KEY.encode())

def save_token_json(token_json: str):
    encrypted = _fernet().encrypt(token_json.encode())
    with _connect() as conn:
        conn.execute(
            '''
            INSERT INTO oauth_tokens (id, encrypted_token, updated_at)
            VALUES (1, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                encrypted_token = excluded.encrypted_token,
                updated_at = CURRENT_TIMESTAMP
            ''',
            (encrypted,),
        )
        conn.commit()

def load_token_json():
    with _connect() as conn:
        row = conn.execute("SELECT encrypted_token FROM oauth_tokens WHERE id = 1").fetchone()
    if not row:
        return None
    return _fernet().decrypt(row["encrypted_token"]).decode()
