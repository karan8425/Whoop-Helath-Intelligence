from db import get_conn

METRICS = {
    "recovery_score": "recovery_score",
    "hrv_rmssd_milli": "hrv_rmssd_milli",
    "resting_heart_rate": "resting_heart_rate",
    "sleep_duration_hours": "sleep_duration_hours",
    "sleep_performance_percentage": "sleep_performance_percentage",
    "sleep_consistency_percentage": "sleep_consistency_percentage",
    "cycle_strain": "cycle_strain",
    "workout_count": "workout_count",
}

WINDOWS = (7, 14, 30, 90)

DDL = """
CREATE TABLE IF NOT EXISTS whoop_daily_baselines (
    metric_date DATE NOT NULL,
    metric_name TEXT NOT NULL,
    current_value DOUBLE PRECISION,
    baseline_7 DOUBLE PRECISION,
    n_7 INTEGER,
    pct_vs_7 DOUBLE PRECISION,
    baseline_14 DOUBLE PRECISION,
    n_14 INTEGER,
    pct_vs_14 DOUBLE PRECISION,
    baseline_30 DOUBLE PRECISION,
    n_30 INTEGER,
    pct_vs_30 DOUBLE PRECISION,
    baseline_90 DOUBLE PRECISION,
    n_90 INTEGER,
    pct_vs_90 DOUBLE PRECISION,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (metric_date, metric_name)
)
"""

def init_baselines():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)

def rebuild_baselines():
    init_baselines()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE whoop_daily_baselines")

            for metric_name, column_name in METRICS.items():
                # Explicit calendar-day windows. Because metric_date is a DATE and the
                # daily table contains one row per calendar day, RANGE makes the
                # statistical definition independent of physical row position.
                sql = f"""
                WITH rolling AS (
                  SELECT
                    metric_date,
                    {column_name}::double precision AS current_value,

                    AVG({column_name}::double precision) OVER (
                      ORDER BY metric_date
                      RANGE BETWEEN INTERVAL '7 days' PRECEDING AND INTERVAL '1 day' PRECEDING
                    ) AS baseline_7,
                    COUNT({column_name}) OVER (
                      ORDER BY metric_date
                      RANGE BETWEEN INTERVAL '7 days' PRECEDING AND INTERVAL '1 day' PRECEDING
                    )::int AS n_7,

                    AVG({column_name}::double precision) OVER (
                      ORDER BY metric_date
                      RANGE BETWEEN INTERVAL '14 days' PRECEDING AND INTERVAL '1 day' PRECEDING
                    ) AS baseline_14,
                    COUNT({column_name}) OVER (
                      ORDER BY metric_date
                      RANGE BETWEEN INTERVAL '14 days' PRECEDING AND INTERVAL '1 day' PRECEDING
                    )::int AS n_14,

                    AVG({column_name}::double precision) OVER (
                      ORDER BY metric_date
                      RANGE BETWEEN INTERVAL '30 days' PRECEDING AND INTERVAL '1 day' PRECEDING
                    ) AS baseline_30,
                    COUNT({column_name}) OVER (
                      ORDER BY metric_date
                      RANGE BETWEEN INTERVAL '30 days' PRECEDING AND INTERVAL '1 day' PRECEDING
                    )::int AS n_30,

                    AVG({column_name}::double precision) OVER (
                      ORDER BY metric_date
                      RANGE BETWEEN INTERVAL '90 days' PRECEDING AND INTERVAL '1 day' PRECEDING
                    ) AS baseline_90,
                    COUNT({column_name}) OVER (
                      ORDER BY metric_date
                      RANGE BETWEEN INTERVAL '90 days' PRECEDING AND INTERVAL '1 day' PRECEDING
                    )::int AS n_90
                  FROM whoop_daily_metrics
                )
                INSERT INTO whoop_daily_baselines (
                  metric_date, metric_name, current_value,
                  baseline_7, n_7, pct_vs_7,
                  baseline_14, n_14, pct_vs_14,
                  baseline_30, n_30, pct_vs_30,
                  baseline_90, n_90, pct_vs_90
                )
                SELECT
                  metric_date, %s, current_value,
                  baseline_7, n_7,
                  CASE WHEN current_value IS NULL OR baseline_7 IS NULL OR baseline_7=0
                    THEN NULL ELSE ((current_value-baseline_7)/baseline_7)*100.0 END,
                  baseline_14, n_14,
                  CASE WHEN current_value IS NULL OR baseline_14 IS NULL OR baseline_14=0
                    THEN NULL ELSE ((current_value-baseline_14)/baseline_14)*100.0 END,
                  baseline_30, n_30,
                  CASE WHEN current_value IS NULL OR baseline_30 IS NULL OR baseline_30=0
                    THEN NULL ELSE ((current_value-baseline_30)/baseline_30)*100.0 END,
                  baseline_90, n_90,
                  CASE WHEN current_value IS NULL OR baseline_90 IS NULL OR baseline_90=0
                    THEN NULL ELSE ((current_value-baseline_90)/baseline_90)*100.0 END
                FROM rolling
                """
                cur.execute(sql, (metric_name,))

            cur.execute("""
                SELECT COUNT(*) AS baseline_rows,
                       COUNT(DISTINCT metric_date) AS dates,
                       COUNT(DISTINCT metric_name) AS metrics,
                       MIN(metric_date) AS oldest,
                       MAX(metric_date) AS newest
                FROM whoop_daily_baselines
            """)
            x = cur.fetchone()

    return {
        "baseline_rows": x["baseline_rows"],
        "dates": x["dates"],
        "metrics": x["metrics"],
        "oldest": x["oldest"].isoformat() if x["oldest"] else None,
        "newest": x["newest"].isoformat() if x["newest"] else None,
        "baseline_windows": list(WINDOWS),
        "window_definition": "preceding calendar days; current day excluded",
        "missing_values_treated_as_zero": False,
    }

def _with_coverage(row):
    d = dict(row)
    for w in WINDOWS:
        n = d.get(f"n_{w}") or 0
        d[f"coverage_{w}_percentage"] = round((n / w) * 100.0, 1)
    return d

def latest_baselines():
    init_baselines()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(metric_date) AS latest_date FROM whoop_daily_baselines")
            latest = cur.fetchone()["latest_date"]
            if latest is None:
                return {"metric_date": None, "metrics": []}
            cur.execute("""
                SELECT metric_name,current_value,
                       baseline_7,n_7,pct_vs_7,
                       baseline_14,n_14,pct_vs_14,
                       baseline_30,n_30,pct_vs_30,
                       baseline_90,n_90,pct_vs_90
                FROM whoop_daily_baselines
                WHERE metric_date=%s
                ORDER BY metric_name
            """,(latest,))
            rows = [_with_coverage(r) for r in cur.fetchall()]
    return {"metric_date": latest.isoformat(), "metrics": rows}

def validate_baselines():
    init_baselines()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM whoop_daily_metrics")
            days = cur.fetchone()["n"]
            cur.execute("""
                SELECT COUNT(*) AS rows, COUNT(DISTINCT metric_date) AS dates,
                       COUNT(DISTINCT metric_name) AS metrics
                FROM whoop_daily_baselines
            """)
            s = cur.fetchone()
            cur.execute("""
                SELECT metric_name,
                       COUNT(*) FILTER (WHERE current_value IS NOT NULL) AS current_observations,
                       MAX(n_7) AS max_n_7, MAX(n_14) AS max_n_14,
                       MAX(n_30) AS max_n_30, MAX(n_90) AS max_n_90
                FROM whoop_daily_baselines
                GROUP BY metric_name ORDER BY metric_name
            """)
            coverage = cur.fetchall()
            cur.execute("""
                SELECT metric_name,metric_date,current_value,
                       baseline_7,n_7,pct_vs_7,
                       baseline_14,n_14,pct_vs_14,
                       baseline_30,n_30,pct_vs_30,
                       baseline_90,n_90,pct_vs_90
                FROM whoop_daily_baselines
                WHERE metric_date=(SELECT MAX(metric_date) FROM whoop_daily_baselines)
                ORDER BY metric_name
            """)
            latest = [_with_coverage(r) for r in cur.fetchall()]

    expected = days * len(METRICS)
    return {
        "daily_metric_days": days,
        "configured_metrics": len(METRICS),
        "expected_baseline_rows": expected,
        "actual_baseline_rows": s["rows"],
        "row_count_matches": s["rows"] == expected,
        "window_definition": "explicit calendar-day ranges",
        "current_day_excluded": True,
        "missing_physiological_values_excluded": True,
        "zero_workout_days_retained_as_real_zero": True,
        "metric_coverage": coverage,
        "latest_date_baselines": latest,
    }

def metric_history(metric_name, limit=30):
    init_baselines()
    if metric_name not in METRICS:
        raise ValueError("Invalid metric. Choose one of: " + ", ".join(sorted(METRICS)))
    limit=max(1,min(int(limit),365))
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT metric_date,current_value,
                       baseline_7,n_7,pct_vs_7,
                       baseline_14,n_14,pct_vs_14,
                       baseline_30,n_30,pct_vs_30,
                       baseline_90,n_90,pct_vs_90
                FROM whoop_daily_baselines
                WHERE metric_name=%s ORDER BY metric_date DESC LIMIT %s
            """,(metric_name,limit))
            rows=[_with_coverage(r) for r in cur.fetchall()]
    return {"metric_name":metric_name,"records":rows}
