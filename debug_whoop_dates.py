from db import get_conn

def _s(row):
    if not row:
        return None
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()}

def latest_whoop_date_diagnostic():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    r.sleep_id AS recovery_sleep_id,
                    r.cycle_id AS recovery_cycle_id,
                    r.created_at AS recovery_created_at,
                    r.updated_at AS recovery_updated_at,
                    r.recovery_score,
                    r.resting_heart_rate,
                    r.hrv_rmssd_milli,
                    r.score_state AS recovery_score_state,
                    s.id AS linked_sleep_id,
                    s.cycle_id AS linked_sleep_cycle_id,
                    s.created_at AS sleep_created_at,
                    s.updated_at AS sleep_updated_at,
                    s.start_time AS sleep_start,
                    s.end_time AS sleep_end,
                    s.timezone_offset AS sleep_timezone_offset,
                    s.nap AS sleep_is_nap,
                    s.score_state AS sleep_score_state,
                    c.id AS linked_cycle_id,
                    c.created_at AS cycle_created_at,
                    c.updated_at AS cycle_updated_at,
                    c.start_time AS cycle_start,
                    c.end_time AS cycle_end,
                    c.timezone_offset AS cycle_timezone_offset,
                    c.score_state AS cycle_score_state
                FROM whoop_recoveries r
                LEFT JOIN whoop_sleeps s ON s.id = r.sleep_id
                LEFT JOIN whoop_cycles c ON c.id = r.cycle_id
                WHERE r.recovery_score IS NOT NULL
                ORDER BY r.updated_at DESC NULLS LAST, r.created_at DESC NULLS LAST
                LIMIT 1
            """)
            linked = cur.fetchone()
    return {
        "linked_latest_recovery": _s(linked),
        "diagnostic_goal": "Confirm which timestamp should define the coaching date before rebuilding history."
    }
