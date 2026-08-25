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
            cur.execute("ALTER TABLE whoop_daily_metrics ADD COLUMN IF NOT EXISTS has_cycle BOOLEAN NOT NULL DEFAULT FALSE")
            cur.execute("ALTER TABLE whoop_daily_metrics ADD COLUMN IF NOT EXISTS has_recovery BOOLEAN NOT NULL DEFAULT FALSE")
            cur.execute("ALTER TABLE whoop_daily_metrics ADD COLUMN IF NOT EXISTS has_sleep BOOLEAN NOT NULL DEFAULT FALSE")
            cur.execute("ALTER TABLE whoop_daily_metrics ADD COLUMN IF NOT EXISTS has_workout BOOLEAN NOT NULL DEFAULT FALSE")

def rebuild_daily_metrics():
    """
    Coaching-day rule:
    Recovery, HRV, RHR, overnight sleep and the linked WHOOP cycle are assigned
    to the LOCAL CALENDAR DATE ON WHICH THE MAIN SLEEP ENDS (wake date).

    This matches the day on which WHOOP presents the recovery score to the user.
    Workouts remain assigned to their own local activity date.
    """
    init_analytics()

    sql = """
    TRUNCATE TABLE whoop_daily_metrics;

    WITH sleep_prepared AS (
      SELECT
        s.*,
        CASE
          WHEN s.timezone_offset IS NULL OR s.timezone_offset = '' OR s.timezone_offset = 'Z'
            THEN INTERVAL '0 seconds'
          ELSE s.timezone_offset::interval
        END AS tz_interval,
        DATE(
          s.end_time +
          CASE
            WHEN s.timezone_offset IS NULL OR s.timezone_offset = '' OR s.timezone_offset = 'Z'
              THEN INTERVAL '0 seconds'
            ELSE s.timezone_offset::interval
          END
        ) AS wake_date
      FROM whoop_sleeps s
      WHERE s.nap = FALSE
        AND s.score_state = 'SCORED'
        AND s.end_time IS NOT NULL
    ),
    physiology_ranked AS (
      SELECT
        sp.wake_date AS metric_date,
        sp.id AS sleep_id,
        sp.cycle_id,
        sp.start_time AS sleep_start,
        sp.end_time AS sleep_end,
        sp.score AS sleep_score,
        sp.updated_at AS sleep_updated_at,

        c.created_at AS cycle_created_at,
        c.updated_at AS cycle_updated_at,
        c.start_time AS cycle_start,
        c.end_time AS cycle_end,
        c.timezone_offset AS cycle_timezone_offset,
        c.strain AS cycle_strain,
        c.kilojoule AS cycle_kilojoule,

        r.recovery_score,
        r.resting_heart_rate,
        r.hrv_rmssd_milli,
        r.spo2_percentage,
        r.skin_temp_celsius,
        r.updated_at AS recovery_updated_at,

        ROW_NUMBER() OVER (
          PARTITION BY sp.wake_date
          ORDER BY sp.end_time DESC,
                   r.updated_at DESC NULLS LAST,
                   sp.updated_at DESC NULLS LAST,
                   sp.id DESC
        ) AS rn
      FROM sleep_prepared sp
      LEFT JOIN whoop_cycles c
        ON c.id = sp.cycle_id
      LEFT JOIN whoop_recoveries r
        ON r.sleep_id = sp.id
       AND r.cycle_id = sp.cycle_id
    ),
    physiology_daily AS (
      SELECT *
      FROM physiology_ranked
      WHERE rn = 1
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
          (SELECT MIN(metric_date) FROM physiology_daily),
          (SELECT MIN(local_date) FROM workout_prepared)
        ) AS min_date,
        GREATEST(
          (SELECT MAX(metric_date) FROM physiology_daily),
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
      p.cycle_id,
      p.recovery_score,
      p.resting_heart_rate,
      p.hrv_rmssd_milli,
      p.spo2_percentage,
      p.skin_temp_celsius,
      p.cycle_strain,
      p.cycle_kilojoule,
      CASE WHEN p.cycle_kilojoule IS NULL THEN NULL ELSE p.cycle_kilojoule / 4.184 END,

      p.sleep_id,
      p.sleep_start,
      p.sleep_end,

      CASE WHEN p.sleep_score IS NULL THEN NULL ELSE
        (
          COALESCE((p.sleep_score->'stage_summary'->>'total_light_sleep_time_milli')::double precision,0)
          + COALESCE((p.sleep_score->'stage_summary'->>'total_slow_wave_sleep_time_milli')::double precision,0)
          + COALESCE((p.sleep_score->'stage_summary'->>'total_rem_sleep_time_milli')::double precision,0)
        ) / 3600000.0
      END,

      (p.sleep_score->'stage_summary'->>'total_in_bed_time_milli')::double precision / 3600000.0,
      (p.sleep_score->>'sleep_performance_percentage')::double precision,
      (p.sleep_score->>'sleep_consistency_percentage')::double precision,
      (p.sleep_score->>'sleep_efficiency_percentage')::double precision,
      (p.sleep_score->>'respiratory_rate')::double precision,
      (p.sleep_score->'stage_summary'->>'total_rem_sleep_time_milli')::double precision / 3600000.0,
      (p.sleep_score->'stage_summary'->>'total_slow_wave_sleep_time_milli')::double precision / 3600000.0,
      (p.sleep_score->'stage_summary'->>'total_light_sleep_time_milli')::double precision / 3600000.0,
      (p.sleep_score->'stage_summary'->>'total_awake_time_milli')::double precision / 3600000.0,
      (p.sleep_score->'stage_summary'->>'disturbance_count')::integer,

      COALESCE(w.workout_count,0),
      w.workout_total_strain,
      w.workout_max_strain,
      w.workout_total_duration_hours,
      w.workout_sports,

      (p.cycle_id IS NOT NULL),
      (p.recovery_score IS NOT NULL),
      (p.sleep_id IS NOT NULL),
      (COALESCE(w.workout_count,0) > 0),

      GREATEST(
        p.cycle_updated_at,
        p.recovery_updated_at,
        p.sleep_updated_at,
        w.updated_at
      )
    FROM calendar cal
    LEFT JOIN physiology_daily p
      ON p.metric_date = cal.metric_date
    LEFT JOIN workouts w
      ON w.local_date = cal.metric_date
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
        "physiology_date_rule": "local date of main non-nap sleep end (wake date)",
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
                WHERE NOT has_sleep OR (has_workout AND NOT has_sleep)
                ORDER BY metric_date DESC
                LIMIT 30
            """)
            exception_days = cur.fetchall()

    return {
        "summary": {
            **summary,
            "oldest": summary["oldest"].isoformat() if summary["oldest"] else None,
            "newest": summary["newest"].isoformat() if summary["newest"] else None,
            "source_workouts": source_workouts,
            "workout_mapping_difference": source_workouts - summary["workouts_mapped"],
            "physiology_date_rule": "local date of main non-nap sleep end (wake date)",
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
