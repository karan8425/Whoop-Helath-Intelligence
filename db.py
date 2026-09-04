from contextlib import contextmanager
from contextvars import ContextVar
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from cryptography.fernet import Fernet
from config import DATABASE_URL, TOKEN_ENCRYPTION_KEY

# Set only inside request_scoped_connection(). When present, get_conn()
# reuses this connection instead of opening a new physical connection.
_active_connection = ContextVar("_active_connection", default=None)

@contextmanager
def get_conn():
    shared = _active_connection.get()
    if shared is not None:
        # Inside a request_scoped_connection() block: reuse the one
        # open connection. The outer block owns commit/rollback/close.
        yield shared
        return

    conn = psycopg.connect(DATABASE_URL, row_factory=dict_row, connect_timeout=15)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

@contextmanager
def request_scoped_connection():
    """
    Open exactly one physical DB connection and make every nested
    get_conn() call inside this block reuse it, instead of each one
    opening (and TLS/auth-handshaking) its own connection.

    Intended for read-heavy call graphs that invoke get_conn() many
    times to serve a single logical request (e.g. Goal Progress,
    which calls it dozens of times across apple_health_trends(),
    body_composition_progress(), and Tonal strength adherence) -
    per-call connection setup, not query time, dominates there.

    Safe for read-only nested get_conn() usage: everything reads
    through the one shared connection and is committed together (or
    rolled back together on error) when this block exits. Callers
    outside this block are completely unaffected - get_conn() keeps
    opening/closing its own connection exactly as before.
    """
    if _active_connection.get() is not None:
        # Already inside a request-scoped connection - nothing to do,
        # the enclosing block owns it.
        yield
        return

    conn = psycopg.connect(DATABASE_URL, row_factory=dict_row, connect_timeout=15)
    token = _active_connection.set(conn)
    try:
        yield
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _active_connection.reset(token)
        conn.close()

def init_db():
    statements = [
        """CREATE TABLE IF NOT EXISTS oauth_tokens (
            id SMALLINT PRIMARY KEY CHECK (id = 1),
            encrypted_token BYTEA NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS whoop_cycles (
            id BIGINT PRIMARY KEY, user_id BIGINT, created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ,
            start_time TIMESTAMPTZ, end_time TIMESTAMPTZ, timezone_offset TEXT, score_state TEXT,
            strain DOUBLE PRECISION, kilojoule DOUBLE PRECISION, average_heart_rate INTEGER,
            max_heart_rate INTEGER, score JSONB, raw_json JSONB NOT NULL,
            synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS whoop_recoveries (
            sleep_id UUID PRIMARY KEY, cycle_id BIGINT, user_id BIGINT, created_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ, score_state TEXT, recovery_score DOUBLE PRECISION,
            resting_heart_rate INTEGER, hrv_rmssd_milli DOUBLE PRECISION,
            spo2_percentage DOUBLE PRECISION, skin_temp_celsius DOUBLE PRECISION,
            score JSONB, raw_json JSONB NOT NULL, synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS whoop_sleeps (
            id UUID PRIMARY KEY, cycle_id BIGINT, user_id BIGINT, created_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ, start_time TIMESTAMPTZ, end_time TIMESTAMPTZ,
            timezone_offset TEXT, nap BOOLEAN, score_state TEXT, score JSONB,
            raw_json JSONB NOT NULL, synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS whoop_workouts (
            id UUID PRIMARY KEY, user_id BIGINT, created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ,
            start_time TIMESTAMPTZ, end_time TIMESTAMPTZ, timezone_offset TEXT,
            sport_id INTEGER, sport_name TEXT, score_state TEXT, strain DOUBLE PRECISION,
            average_heart_rate INTEGER, max_heart_rate INTEGER, score JSONB,
            raw_json JSONB NOT NULL, synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS whoop_body_measurements (
            id SMALLINT PRIMARY KEY CHECK (id = 1),
            height_meter DOUBLE PRECISION,
            weight_kilogram DOUBLE PRECISION,
            max_heart_rate INTEGER,
            raw_json JSONB NOT NULL,
            observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS whoop_profiles (
            user_id BIGINT PRIMARY KEY, email TEXT, first_name TEXT, last_name TEXT,
            raw_json JSONB NOT NULL, observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS whoop_sync_runs (
            id BIGSERIAL PRIMARY KEY, sync_type TEXT NOT NULL, started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at TIMESTAMPTZ, status TEXT NOT NULL DEFAULT 'running', counts JSONB, error TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS whoop_sync_state (
            dataset TEXT PRIMARY KEY, next_token TEXT,
            completed BOOLEAN NOT NULL DEFAULT FALSE,
            records_processed BIGINT NOT NULL DEFAULT 0,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""",
    ]
    with get_conn() as conn:
        with conn.cursor() as cur:
            for s in statements:
                cur.execute(s)

    # Migrate the earlier body measurement schema if it exists with user_id instead of id.
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name='whoop_body_measurements'
            """)
            cols = {r["column_name"] for r in cur.fetchall()}
            if "id" not in cols:
                cur.execute("DROP TABLE IF EXISTS whoop_body_measurements")
                cur.execute("""CREATE TABLE whoop_body_measurements (
                    id SMALLINT PRIMARY KEY CHECK (id = 1),
                    height_meter DOUBLE PRECISION,
                    weight_kilogram DOUBLE PRECISION,
                    max_heart_rate INTEGER,
                    raw_json JSONB NOT NULL,
                    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )""")

def database_health():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT NOW() AS server_time")
            row = cur.fetchone()
    return {"database":"connected","status":"ok","server_time":row["server_time"].isoformat()}

def _fernet():
    return Fernet(TOKEN_ENCRYPTION_KEY.encode())

def save_token_json(token_json: str):
    encrypted = _fernet().encrypt(token_json.encode())
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO oauth_tokens (id, encrypted_token, updated_at)
                           VALUES (1,%s,NOW())
                           ON CONFLICT (id) DO UPDATE SET encrypted_token=EXCLUDED.encrypted_token, updated_at=NOW()""",
                        (encrypted,))

def load_token_json():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT encrypted_token FROM oauth_tokens WHERE id=1")
            row = cur.fetchone()
    return None if not row else _fernet().decrypt(bytes(row["encrypted_token"])).decode()

def upsert_cycle(r):
    score = r.get("score") or {}
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO whoop_cycles
            (id,user_id,created_at,updated_at,start_time,end_time,timezone_offset,score_state,strain,kilojoule,average_heart_rate,max_heart_rate,score,raw_json,synced_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
            ON CONFLICT(id) DO UPDATE SET user_id=EXCLUDED.user_id,created_at=EXCLUDED.created_at,updated_at=EXCLUDED.updated_at,
            start_time=EXCLUDED.start_time,end_time=EXCLUDED.end_time,timezone_offset=EXCLUDED.timezone_offset,score_state=EXCLUDED.score_state,
            strain=EXCLUDED.strain,kilojoule=EXCLUDED.kilojoule,average_heart_rate=EXCLUDED.average_heart_rate,max_heart_rate=EXCLUDED.max_heart_rate,
            score=EXCLUDED.score,raw_json=EXCLUDED.raw_json,synced_at=NOW()""",
            (r.get("id"),r.get("user_id"),r.get("created_at"),r.get("updated_at"),r.get("start"),r.get("end"),
             r.get("timezone_offset"),r.get("score_state"),score.get("strain"),score.get("kilojoule"),
             score.get("average_heart_rate"),score.get("max_heart_rate"),Jsonb(score),Jsonb(r)))

def upsert_recovery(r):
    if not r.get("sleep_id"): return
    score = r.get("score") or {}
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO whoop_recoveries
            (sleep_id,cycle_id,user_id,created_at,updated_at,score_state,recovery_score,resting_heart_rate,hrv_rmssd_milli,spo2_percentage,skin_temp_celsius,score,raw_json,synced_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
            ON CONFLICT(sleep_id) DO UPDATE SET cycle_id=EXCLUDED.cycle_id,user_id=EXCLUDED.user_id,created_at=EXCLUDED.created_at,
            updated_at=EXCLUDED.updated_at,score_state=EXCLUDED.score_state,recovery_score=EXCLUDED.recovery_score,
            resting_heart_rate=EXCLUDED.resting_heart_rate,hrv_rmssd_milli=EXCLUDED.hrv_rmssd_milli,
            spo2_percentage=EXCLUDED.spo2_percentage,skin_temp_celsius=EXCLUDED.skin_temp_celsius,
            score=EXCLUDED.score,raw_json=EXCLUDED.raw_json,synced_at=NOW()""",
            (r.get("sleep_id"),r.get("cycle_id"),r.get("user_id"),r.get("created_at"),r.get("updated_at"),r.get("score_state"),
             score.get("recovery_score"),score.get("resting_heart_rate"),score.get("hrv_rmssd_milli"),
             score.get("spo2_percentage"),score.get("skin_temp_celsius"),Jsonb(score),Jsonb(r)))

def upsert_sleep(r):
    if not r.get("id"): return
    score = r.get("score") or {}
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO whoop_sleeps
            (id,cycle_id,user_id,created_at,updated_at,start_time,end_time,timezone_offset,nap,score_state,score,raw_json,synced_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
            ON CONFLICT(id) DO UPDATE SET cycle_id=EXCLUDED.cycle_id,user_id=EXCLUDED.user_id,created_at=EXCLUDED.created_at,
            updated_at=EXCLUDED.updated_at,start_time=EXCLUDED.start_time,end_time=EXCLUDED.end_time,timezone_offset=EXCLUDED.timezone_offset,
            nap=EXCLUDED.nap,score_state=EXCLUDED.score_state,score=EXCLUDED.score,raw_json=EXCLUDED.raw_json,synced_at=NOW()""",
            (r.get("id"),r.get("cycle_id"),r.get("user_id"),r.get("created_at"),r.get("updated_at"),r.get("start"),r.get("end"),
             r.get("timezone_offset"),r.get("nap"),r.get("score_state"),Jsonb(score),Jsonb(r)))

def upsert_workout(r):
    if not r.get("id"): return
    score = r.get("score") or {}
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO whoop_workouts
            (id,user_id,created_at,updated_at,start_time,end_time,timezone_offset,sport_id,sport_name,score_state,strain,average_heart_rate,max_heart_rate,score,raw_json,synced_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
            ON CONFLICT(id) DO UPDATE SET user_id=EXCLUDED.user_id,created_at=EXCLUDED.created_at,updated_at=EXCLUDED.updated_at,
            start_time=EXCLUDED.start_time,end_time=EXCLUDED.end_time,timezone_offset=EXCLUDED.timezone_offset,sport_id=EXCLUDED.sport_id,
            sport_name=EXCLUDED.sport_name,score_state=EXCLUDED.score_state,strain=EXCLUDED.strain,
            average_heart_rate=EXCLUDED.average_heart_rate,max_heart_rate=EXCLUDED.max_heart_rate,
            score=EXCLUDED.score,raw_json=EXCLUDED.raw_json,synced_at=NOW()""",
            (r.get("id"),r.get("user_id"),r.get("created_at"),r.get("updated_at"),r.get("start"),r.get("end"),r.get("timezone_offset"),
             r.get("sport_id"),r.get("sport_name"),r.get("score_state"),score.get("strain"),score.get("average_heart_rate"),
             score.get("max_heart_rate"),Jsonb(score),Jsonb(r)))

def upsert_body(r):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO whoop_body_measurements
            (id,height_meter,weight_kilogram,max_heart_rate,raw_json,observed_at)
            VALUES (1,%s,%s,%s,%s,NOW())
            ON CONFLICT(id) DO UPDATE SET
            height_meter=EXCLUDED.height_meter,weight_kilogram=EXCLUDED.weight_kilogram,
            max_heart_rate=EXCLUDED.max_heart_rate,raw_json=EXCLUDED.raw_json,observed_at=NOW()""",
            (r.get("height_meter"),r.get("weight_kilogram"),r.get("max_heart_rate"),Jsonb(r)))

def upsert_profile(r):
    if r.get("user_id") is None: return
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO whoop_profiles (user_id,email,first_name,last_name,raw_json,observed_at)
            VALUES (%s,%s,%s,%s,%s,NOW())
            ON CONFLICT(user_id) DO UPDATE SET email=EXCLUDED.email,first_name=EXCLUDED.first_name,last_name=EXCLUDED.last_name,
            raw_json=EXCLUDED.raw_json,observed_at=NOW()""",
            (r.get("user_id"),r.get("email"),r.get("first_name"),r.get("last_name"),Jsonb(r)))

def table_counts():
    tables=["whoop_cycles","whoop_recoveries","whoop_sleeps","whoop_workouts","whoop_body_measurements","whoop_profiles"]
    out={}
    with get_conn() as conn:
        with conn.cursor() as cur:
            for t in tables:
                cur.execute(f"SELECT COUNT(*) AS n FROM {t}")
                out[t]=cur.fetchone()["n"]
    return out

def body_measurement():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT height_meter, weight_kilogram, max_heart_rate, observed_at
                           FROM whoop_body_measurements WHERE id=1""")
            row = cur.fetchone()
    if not row:
        return None
    return {**row, "observed_at": row["observed_at"].isoformat()}

def data_ranges():
    queries = {
        "cycles": ("whoop_cycles","start_time"),
        "recoveries": ("whoop_recoveries","created_at"),
        "sleeps": ("whoop_sleeps","start_time"),
        "workouts": ("whoop_workouts","start_time"),
    }
    out={}
    with get_conn() as conn:
        with conn.cursor() as cur:
            for name,(table,col) in queries.items():
                cur.execute(f"SELECT MIN({col}) AS oldest, MAX({col}) AS newest, COUNT(*) AS records FROM {table}")
                row=cur.fetchone()
                out[name]={
                    "oldest": row["oldest"].isoformat() if row["oldest"] else None,
                    "newest": row["newest"].isoformat() if row["newest"] else None,
                    "records": row["records"],
                }
    return out
