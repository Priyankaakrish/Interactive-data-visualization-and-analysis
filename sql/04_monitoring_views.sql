/* =============================================================================
   04_monitoring_views.sql
   The monitoring layer - the last box in the architecture and the one that
   answers "can I trust what the dashboard is showing me right now?"

   Four questions, four views:
     vw_run_history   did the last run succeed, and how long did it take?
     vw_freshness     how old is the data behind the report?
     vw_dq_trend      is data quality improving or degrading over time?
     vw_row_drift     did the volume move more than expected since last run?
   ========================================================================== */

/* ------------------------------------------------------------ run history */
CREATE OR REPLACE VIEW monitoring.vw_run_history AS
SELECT
    r.run_id,
    r.started_at,
    r.finished_at,
    r.duration_seconds,
    r.status,
    r.source_name,
    r.is_real_dataset,
    r.rows_ingested,
    r.rows_loaded,
    r.rows_quarantined,
    ROUND(100.0 * r.rows_quarantined / NULLIF(r.rows_ingested, 0), 3)
        AS quarantine_rate_pct,
    r.checks_run,
    r.checks_failed,
    ROUND(100.0 * (r.checks_run - r.checks_failed)
          / NULLIF(r.checks_run, 0), 1)               AS check_pass_rate_pct,
    r.data_min_date,
    r.data_max_date,
    r.error_message,
    ROW_NUMBER() OVER (ORDER BY r.started_at DESC)    AS runs_ago
FROM monitoring.pipeline_run AS r;

/* -------------------------------------------------------------- freshness */
CREATE OR REPLACE VIEW monitoring.vw_freshness AS
WITH latest AS (
    SELECT *
    FROM monitoring.pipeline_run
    WHERE status = 'SUCCESS'
    ORDER BY started_at DESC
    LIMIT 1
)
SELECT
    l.run_id,
    l.started_at                                        AS last_successful_run,
    EXTRACT(EPOCH FROM (now() - l.started_at)) / 3600.0 AS hours_since_run,
    l.data_max_date                                     AS latest_data_date,
    (CURRENT_DATE - l.data_max_date)                    AS data_age_days,
    /* The published dataset is a fixed historical extract, so "stale" here
       means the pipeline has not run recently, not that the retailer stopped
       trading. Both are worth surfacing, for different reasons. */
    CASE
        WHEN l.run_id IS NULL                                    THEN 'NO SUCCESSFUL RUN'
        WHEN EXTRACT(EPOCH FROM (now() - l.started_at)) > 86400  THEN 'PIPELINE STALE'
        ELSE 'CURRENT'
    END AS pipeline_status
FROM latest AS l;

/* ------------------------------------------------------ data-quality trend */
CREATE OR REPLACE VIEW monitoring.vw_dq_trend AS
SELECT
    d.run_id,
    r.started_at,
    d.check_category,
    COUNT(*)                                          AS checks,
    COUNT(*) FILTER (WHERE d.passed)                  AS passed,
    COUNT(*) FILTER (WHERE NOT d.passed)              AS failed,
    COUNT(*) FILTER (WHERE NOT d.passed AND d.severity = 'ERROR') AS critical_failed,
    SUM(d.failed_rows)                                AS failed_rows,
    ROUND(100.0 * COUNT(*) FILTER (WHERE d.passed) / NULLIF(COUNT(*), 0), 1)
        AS pass_rate_pct
FROM monitoring.dq_result       AS d
INNER JOIN monitoring.pipeline_run AS r ON r.run_id = d.run_id
GROUP BY d.run_id, r.started_at, d.check_category;

/* ---------------------------------------------------- latest DQ scorecard */
CREATE OR REPLACE VIEW monitoring.vw_dq_latest AS
WITH latest AS (
    SELECT run_id FROM monitoring.pipeline_run
    ORDER BY started_at DESC LIMIT 1
)
SELECT d.*
FROM monitoring.dq_result AS d
INNER JOIN latest AS l ON l.run_id = d.run_id;

/* ------------------------------------------------------ row-count drift */
CREATE OR REPLACE VIEW monitoring.vw_row_drift AS
WITH ordered AS (
    SELECT
        t.table_name,
        t.run_id,
        r.started_at,
        t.rows_loaded,
        LAG(t.rows_loaded) OVER (PARTITION BY t.table_name ORDER BY r.started_at)
            AS prev_rows_loaded
    FROM monitoring.table_load     AS t
    INNER JOIN monitoring.pipeline_run AS r ON r.run_id = t.run_id
    WHERE r.status = 'SUCCESS'
)
SELECT
    o.*,
    o.rows_loaded - o.prev_rows_loaded AS row_delta,
    ROUND(100.0 * (o.rows_loaded - o.prev_rows_loaded)
          / NULLIF(o.prev_rows_loaded, 0), 2) AS drift_pct,
    CASE
        WHEN o.prev_rows_loaded IS NULL THEN 'BASELINE'
        WHEN ABS(100.0 * (o.rows_loaded - o.prev_rows_loaded)
                 / NULLIF(o.prev_rows_loaded, 0)) > 20 THEN 'INVESTIGATE'
        ELSE 'NORMAL'
    END AS drift_status
FROM ordered AS o;

/* ------------------------------------------- single-row health indicator */
CREATE OR REPLACE VIEW monitoring.vw_health AS
WITH last_run AS (
    SELECT * FROM monitoring.pipeline_run ORDER BY started_at DESC LIMIT 1
),
drift AS (
    SELECT COUNT(*) AS investigate_count
    FROM monitoring.vw_row_drift
    WHERE drift_status = 'INVESTIGATE'
      AND run_id = (SELECT run_id FROM last_run)
)
SELECT
    lr.run_id,
    lr.status                                   AS last_run_status,
    lr.started_at                               AS last_run_at,
    lr.duration_seconds,
    lr.checks_run,
    lr.checks_failed,
    d.investigate_count                         AS tables_with_drift,
    CASE
        WHEN lr.status <> 'SUCCESS'      THEN 'RED - last run failed'
        WHEN lr.checks_failed > 0
             AND EXISTS (SELECT 1 FROM monitoring.vw_dq_latest
                         WHERE NOT passed AND severity = 'ERROR')
                                         THEN 'RED - critical data-quality failure'
        WHEN d.investigate_count > 0     THEN 'AMBER - unexpected volume change'
        WHEN lr.checks_failed > 0        THEN 'AMBER - warnings present'
        ELSE                                  'GREEN - all checks passed'
    END AS health_status
FROM last_run AS lr CROSS JOIN drift AS d;
