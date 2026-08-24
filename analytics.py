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
    has_cycle BOOLEAN NOT NULL DEFAULT FALSE,
    has_recovery BOOLEAN NOT NULL DEFAULT FALSE,
    has_sleep BOOLEAN NOT NULL DEFAULT FALSE,
    has_workout BOOLEAN NOT NULL DEFAULT FALSE,
    source_updated_at TIMESTAMPTZ,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)"""

def init_analytics():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)
            # Safe additive migration for existing Phase 3A table.
            cur.execute("ALTER TABLE whoop_daily_metrics ADD COLUMN IF NOT EXISTS has_cycle BOOLEAN NOT NULL DEFAULT FALSE")
            cur.execute("ALTER TABLE whoop_daily_metrics ADD COLUMN IF NOT EXISTS has_recovery BOOLEAN NOT NULL DEFAULT FALSE")
            cur.execute("ALTER TABLE whoop_daily_metrics ADD COLUMN IF NOT EXISTS has_sleep BOOLEAN NOT NULL DEFAULT FALSE")
            cur.execute("ALTER TABLE whoop_daily_metrics ADD COLUMN IF NOT EXISTS has_workout BOOLEAN NOT NULL DEFAULT FALSE")

def rebuild_daily_metrics():
    init_analytics()

    sql = """
    TRUNCATE TABLE whoop_daily_metrics;

    WITH cycle_prepared AS (
      SELECT
        c.*,
        CASE
          WHEN c.timezone_offset IS NULL OR c.timezone_offset = '' OR c.timezone_offset = 'Z'
            THEN INTERVAL '0 seconds'
          ELSE c.timezone_offset::interval
        END AS tz_interval,
        DATE(
          c.start_time +
          CASE
            WHEN c.timezone_offset IS NULL OR c.timezone_offset = '' OR c.timezone_offset = 'Z'
              THEN INTERVAL '0 seconds'
            ELSE c.timezone_offset::interval
          END
        ) AS local_date
      FROM whoop_cycles c
      WHERE c.start_time IS NOT NULL
    ),
    cycle_ranked AS (
      SELECT
        cp.*,
        ROW_NUMBER() OVER (
          PARTITION BY cp.local_date
          ORDER BY cp.start_time DESC, cp.updated_at DESC NULLS LAST, cp.id DESC
        ) AS rn
      FROM cycle_prepared cp
    ),
    cycle_daily AS (
      SELECT *
      FROM cycle_ranked
      WHERE rn = 1
    ),
    main_sleep AS (
      SELECT DISTINCT ON (cycle_id)
        cycle_id,
        id AS sleep_id,
        start_time AS sleep_start,
        end_time AS sleep_end,
        score,
        updated_at
      FROM whoop_sleeps
      WHERE nap = FALSE
        AND score_state = 'SCORED'
      ORDER BY cycle_id, end_time DESC NULLS LAST
    ),
    workout_prepared AS (
      SELECT
        w.*,
        DATE(
          w.start_time +
          CASE
            WHEN w.timezone_offset IS NULL OR w.timezone_offset = '' OR w.timezone_offset = 'Z'
              THEN INTERVAL '0 seconds'
            ELSE w.timezone_offset::interval
          END
        ) AS local_date
      FROM whoop_workouts w
      WHERE w.start_time IS NOT NULL
    ),
    workouts AS (
      SELECT
        local_date,
        COUNT(*)::int AS workout_count,
        SUM(COALESCE(strain,0)) AS workout_total_strain,
        MAX(strain) AS workout_max_strain,
        SUM(EXTRACT(EPOCH FROM (end_time-start_time))/3600.0) AS workout_total_duration_hours,
        jsonb_agg(sport_name ORDER BY start_time) AS workout_sports,
        MAX(updated_at) AS updated_at
      FROM workout_prepared
      GROUP BY local_date
    ),
    date_bounds AS (
      SELECT
        LEAST(
          (SELECT MIN(local_date) FROM cycle_prepared),
          (SELECT MIN(local_date) FROM workout_prepared)
        ) AS min_date,
        GREATEST(
          (SELECT MAX(local_date) FROM cycle_prepared),
          (SELECT MAX(local_date) FROM workout_prepared)
        ) AS max_date
    ),
    calendar AS (
      SELECT generate_series(min_date, max_date, INTERVAL '1 day')::date AS metric_date
      FROM date_bounds
    )
    INSERT INTO whoop_daily_metrics (
      metric_date, cycle_id, recovery_score, resting_heart_rate, hrv_rmssd_milli,
      spo2_percentage, skin_temp_celsius, cycle_strain, cycle_kilojoule, cycle_calories,
      sleep_id, sleep_start, sleep_end, sleep_duration_hours, time_in_bed_hours,
      sleep_performance_percentage, sleep_consistency_percentage, sleep_efficiency_percentage,
      respiratory_rate, rem_sleep_hours, slow_wave_sleep_hours, light_sleep_hours, awake_hours,
      disturbance_count, workout_count, workout_total_strain, workout_max_strain,
      workout_total_duration_hours, workout_sports,
      has_cycle, has_recovery, has_sleep, has_workout, source_updated_at
    )
    SELECT
      cal.metric_date,
      c.id,
      r.recovery_score,
      r.resting_heart_rate,
      r.hrv_rmssd_milli,
      r.spo2_percentage,
      r.skin_temp_celsius,
      c.strain,
      c.kilojoule,
      CASE WHEN c.kilojoule IS NULL THEN NULL ELSE c.kilojoule / 4.184 END,
      s.sleep_id,
      s.sleep_start,
      s.sleep_end,
      CASE WHEN s.score IS NULL THEN NULL ELSE
        (
          COALESCE((s.score->'stage_summary'->>'total_light_sleep_time_milli')::double precision,0)
          + COALESCE((s.score->'stage_summary'->>'total_slow_wave_sleep_time_milli')::double precision,0)
          + COALESCE((s.score->'stage_summary'->>'total_rem_sleep_time_milli')::double precision,0)
        ) / 3600000.0
      END,
      (s.score->'stage_summary'->>'total_in_bed_time_milli')::double precision / 3600000.0,
      (s.score->>'sleep_performance_percentage')::double precision,
      (s.score->>'sleep_consistency_percentage')::double precision,
      (s.score->>'sleep_efficiency_percentage')::double precision,
      (s.score->>'respiratory_rate')::double precision,
      (s.score->'stage_summary'->>'total_rem_sleep_time_milli')::double precision / 3600000.0,
      (s.score->'stage_summary'->>'total_slow_wave_sleep_time_milli')::double precision / 3600000.0,
      (s.score->'stage_summary'->>'total_light_sleep_time_milli')::double precision / 3600000.0,
      (s.score->'stage_summary'->>'total_awake_time_milli')::double precision / 3600000.0,
      (s.score->'stage_summary'->>'disturbance_count')::integer,
      COALESCE(w.workout_count,0),
      w.workout_total_strain,
      w.workout_max_strain,
      w.workout_total_duration_hours,
      w.workout_sports,
      (c.id IS NOT NULL),
      (r.sleep_id IS NOT NULL),
      (s.sleep_id IS NOT NULL),
      (COALESCE(w.workout_count,0) > 0),
      GREATEST(c.updated_at, r.updated_at, s.updated_at, w.updated_at)
    FROM calendar cal
    LEFT JOIN cycle_daily c ON c.local_date = cal.metric_date
    LEFT JOIN whoop_recoveries r ON r.cycle_id = c.id
    LEFT JOIN main_sleep s ON s.cycle_id = c.id
    LEFT JOIN workouts w ON w.local_date = cal.metric_date
    ORDER BY cal.metric_date;
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            cur.execute("""
                SELECT
                  COUNT(*) AS days,
                  MIN(metric_date) AS oldest,
                  MAX(metric_date) AS newest,
                  COUNT(*) FILTER (WHERE has_cycle) AS cycle_days,
                  COUNT(*) FILTER (WHERE has_recovery) AS recovery_days,
                  COUNT(*) FILTER (WHERE has_sleep) AS sleep_days,
                  COUNT(*) FILTER (WHERE has_workout) AS workout_days,
                  COALESCE(SUM(workout_count),0) AS workouts_mapped
                FROM whoop_daily_metrics
            """)
            x = cur.fetchone()

    return {
        "calendar_days": x["days"],
        "oldest": str(x["oldest"]) if x["oldest"] else None,
        "newest": str(x["newest"]) if x["newest"] else None,
        "cycle_days": x["cycle_days"],
        "recovery_days": x["recovery_days"],
        "sleep_days": x["sleep_days"],
        "workout_days": x["workout_days"],
        "workouts_mapped": x["workouts_mapped"],
    }

def validate_data_integrity():
    init_analytics()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                  COUNT(*) AS calendar_days,
                  COUNT(*) FILTER (WHERE has_cycle) AS cycle_days,
                  COUNT(*) FILTER (WHERE NOT has_cycle) AS days_without_cycle,
                  COUNT(*) FILTER (WHERE has_recovery) AS recovery_days,
                  COUNT(*) FILTER (WHERE has_sleep) AS sleep_days,
                  COUNT(*) FILTER (WHERE has_workout) AS workout_days,
                  COALESCE(SUM(workout_count),0) AS workouts_mapped,
                  MIN(metric_date) AS oldest,
                  MAX(metric_date) AS newest
                FROM whoop_daily_metrics
            """)
            summary = cur.fetchone()

            cur.execute("SELECT COUNT(*) AS n FROM whoop_workouts")
            source_workouts = cur.fetchone()["n"]

            cur.execute("""
                SELECT metric_date, has_cycle, has_recovery, has_sleep, has_workout, workout_count
                FROM whoop_daily_metrics
                WHERE NOT has_cycle OR (has_workout AND NOT has_cycle)
                ORDER BY metric_date DESC
                LIMIT 30
            """)
            exception_days = cur.fetchall()

            cur.execute("""
                WITH cycle_prepared AS (
                  SELECT DATE(
                    start_time +
                    CASE
                      WHEN timezone_offset IS NULL OR timezone_offset = '' OR timezone_offset = 'Z'
                        THEN INTERVAL '0 seconds'
                      ELSE timezone_offset::interval
                    END
                  ) AS local_date
                  FROM whoop_cycles
                  WHERE start_time IS NOT NULL
                )
                SELECT COUNT(*) AS duplicate_cycle_dates
                FROM (
                  SELECT local_date
                  FROM cycle_prepared
                  GROUP BY local_date
                  HAVING COUNT(*) > 1
                ) x
            """)
            duplicate_cycle_dates = cur.fetchone()["duplicate_cycle_dates"]

    return {
        "summary": {
            **summary,
            "oldest": summary["oldest"].isoformat() if summary["oldest"] else None,
            "newest": summary["newest"].isoformat() if summary["newest"] else None,
            "source_workouts": source_workouts,
            "workout_mapping_difference": source_workouts - summary["workouts_mapped"],
            "duplicate_cycle_dates_in_source": duplicate_cycle_dates,
        },
        "exception_days": exception_days,
    }

def daily_metrics(limit=14):
    init_analytics()
    limit=max(1,min(int(limit),90))
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT metric_date,recovery_score,resting_heart_rate,hrv_rmssd_milli,
                       cycle_strain,cycle_calories,sleep_duration_hours,
                       sleep_performance_percentage,sleep_consistency_percentage,
                       sleep_efficiency_percentage,respiratory_rate,
                       workout_count,workout_total_strain,workout_total_duration_hours,
                       workout_sports,has_cycle,has_recovery,has_sleep,has_workout
                FROM whoop_daily_metrics
                ORDER BY metric_date DESC
                LIMIT %s
            """,(limit,))
            rows=cur.fetchall()
    return rows
