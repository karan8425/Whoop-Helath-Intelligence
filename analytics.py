from db import get_conn

DDL = """CREATE TABLE IF NOT EXISTS whoop_daily_metrics (
    metric_date DATE PRIMARY KEY,
    cycle_id BIGINT,
    recovery_score DOUBLE PRECISION,
    resting_heart_rate INTEGER,
    hrv_rmssd_milli DOUBLE PRECISION,
    spo2_percentage DOUBLE PRECISION,
    skin_temp_celsius DOUBLE PRECISION,
    cycle_strain DOUBLE PRECISION,
    cycle_kilojoule DOUBLE PRECISION,
    cycle_calories DOUBLE PRECISION,
    sleep_id UUID,
    sleep_start TIMESTAMPTZ,
    sleep_end TIMESTAMPTZ,
    sleep_duration_hours DOUBLE PRECISION,
    time_in_bed_hours DOUBLE PRECISION,
    sleep_performance_percentage DOUBLE PRECISION,
    sleep_consistency_percentage DOUBLE PRECISION,
    sleep_efficiency_percentage DOUBLE PRECISION,
    respiratory_rate DOUBLE PRECISION,
    rem_sleep_hours DOUBLE PRECISION,
    slow_wave_sleep_hours DOUBLE PRECISION,
    light_sleep_hours DOUBLE PRECISION,
    awake_hours DOUBLE PRECISION,
    disturbance_count INTEGER,
    workout_count INTEGER NOT NULL DEFAULT 0,
    workout_total_strain DOUBLE PRECISION,
    workout_max_strain DOUBLE PRECISION,
    workout_total_duration_hours DOUBLE PRECISION,
    workout_sports JSONB,
    source_updated_at TIMESTAMPTZ,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)"""

def init_analytics():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)

def rebuild_daily_metrics():
    init_analytics()
    sql = """
    TRUNCATE TABLE whoop_daily_metrics;

    WITH main_sleep AS (
      SELECT DISTINCT ON (cycle_id)
        cycle_id, id AS sleep_id, start_time AS sleep_start, end_time AS sleep_end,
        score, updated_at
      FROM whoop_sleeps
      WHERE nap = FALSE AND score_state = 'SCORED'
      ORDER BY cycle_id, end_time DESC NULLS LAST
    ),
    workouts AS (
      SELECT
        DATE(start_time + COALESCE(timezone_offset,'+00:00')::interval) AS local_date,
        COUNT(*)::int AS workout_count,
        SUM(COALESCE(strain,0)) AS workout_total_strain,
        MAX(strain) AS workout_max_strain,
        SUM(EXTRACT(EPOCH FROM (end_time-start_time))/3600.0) AS workout_total_duration_hours,
        jsonb_agg(sport_name ORDER BY start_time) AS workout_sports,
        MAX(updated_at) AS updated_at
      FROM whoop_workouts
      WHERE start_time IS NOT NULL
      GROUP BY 1
    )
    INSERT INTO whoop_daily_metrics (
      metric_date, cycle_id, recovery_score, resting_heart_rate, hrv_rmssd_milli,
      spo2_percentage, skin_temp_celsius, cycle_strain, cycle_kilojoule, cycle_calories,
      sleep_id, sleep_start, sleep_end, sleep_duration_hours, time_in_bed_hours,
      sleep_performance_percentage, sleep_consistency_percentage, sleep_efficiency_percentage,
      respiratory_rate, rem_sleep_hours, slow_wave_sleep_hours, light_sleep_hours, awake_hours,
      disturbance_count, workout_count, workout_total_strain, workout_max_strain,
      workout_total_duration_hours, workout_sports, source_updated_at
    )
    SELECT
      DATE(c.start_time + COALESCE(c.timezone_offset,'+00:00')::interval),
      c.id,
      r.recovery_score, r.resting_heart_rate, r.hrv_rmssd_milli, r.spo2_percentage, r.skin_temp_celsius,
      c.strain, c.kilojoule, CASE WHEN c.kilojoule IS NULL THEN NULL ELSE c.kilojoule / 4.184 END,
      s.sleep_id, s.sleep_start, s.sleep_end,
      CASE WHEN s.score IS NULL THEN NULL ELSE
        (COALESCE((s.score->'stage_summary'->>'total_light_sleep_time_milli')::double precision,0)
        +COALESCE((s.score->'stage_summary'->>'total_slow_wave_sleep_time_milli')::double precision,0)
        +COALESCE((s.score->'stage_summary'->>'total_rem_sleep_time_milli')::double precision,0))/3600000.0 END,
      (s.score->'stage_summary'->>'total_in_bed_time_milli')::double precision/3600000.0,
      (s.score->>'sleep_performance_percentage')::double precision,
      (s.score->>'sleep_consistency_percentage')::double precision,
      (s.score->>'sleep_efficiency_percentage')::double precision,
      (s.score->>'respiratory_rate')::double precision,
      (s.score->'stage_summary'->>'total_rem_sleep_time_milli')::double precision/3600000.0,
      (s.score->'stage_summary'->>'total_slow_wave_sleep_time_milli')::double precision/3600000.0,
      (s.score->'stage_summary'->>'total_light_sleep_time_milli')::double precision/3600000.0,
      (s.score->'stage_summary'->>'total_awake_time_milli')::double precision/3600000.0,
      (s.score->'stage_summary'->>'disturbance_count')::integer,
      COALESCE(w.workout_count,0), w.workout_total_strain, w.workout_max_strain,
      w.workout_total_duration_hours, w.workout_sports,
      GREATEST(c.updated_at, r.updated_at, s.updated_at, w.updated_at)
    FROM whoop_cycles c
    LEFT JOIN whoop_recoveries r ON r.cycle_id=c.id
    LEFT JOIN main_sleep s ON s.cycle_id=c.id
    LEFT JOIN workouts w ON w.local_date=DATE(c.start_time + COALESCE(c.timezone_offset,'+00:00')::interval)
    WHERE c.start_time IS NOT NULL
    ORDER BY c.start_time;
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            cur.execute("SELECT COUNT(*) AS n, MIN(metric_date) AS oldest, MAX(metric_date) AS newest FROM whoop_daily_metrics")
            x=cur.fetchone()
    return {"daily_records":x["n"],"oldest":str(x["oldest"]) if x["oldest"] else None,"newest":str(x["newest"]) if x["newest"] else None}

def daily_metrics(limit=14):
    init_analytics()
    limit=max(1,min(int(limit),90))
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT metric_date,recovery_score,resting_heart_rate,hrv_rmssd_milli,
                cycle_strain,cycle_calories,sleep_duration_hours,sleep_performance_percentage,
                sleep_consistency_percentage,sleep_efficiency_percentage,respiratory_rate,
                workout_count,workout_total_strain,workout_total_duration_hours,workout_sports
                FROM whoop_daily_metrics ORDER BY metric_date DESC LIMIT %s""",(limit,))
            rows=cur.fetchall()
    return rows

def validate_daily_metrics():
    init_analytics()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT COUNT(*) AS days,
              COUNT(recovery_score) AS recovery_days,
              COUNT(hrv_rmssd_milli) AS hrv_days,
              COUNT(sleep_duration_hours) AS sleep_days,
              COUNT(cycle_strain) AS strain_days,
              SUM(workout_count) AS workouts_mapped,
              MIN(metric_date) AS oldest, MAX(metric_date) AS newest
              FROM whoop_daily_metrics""")
            summary=cur.fetchone()
            cur.execute("""SELECT metric_date,recovery_score,resting_heart_rate,hrv_rmssd_milli,
              sleep_duration_hours,sleep_performance_percentage,sleep_consistency_percentage,
              cycle_strain,cycle_calories,workout_count,workout_total_strain
              FROM whoop_daily_metrics ORDER BY metric_date DESC LIMIT 7""")
            latest=cur.fetchall()
    return {"summary":summary,"latest_7_days":latest}
