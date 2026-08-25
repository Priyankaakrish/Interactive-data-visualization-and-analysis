/* =============================================================================
   01_schemas.sql
   Project : Online Retail II BI Platform
   Purpose : Create the layered schemas. Separating them is what lets the
             report bind to a stable contract while the load rewrites tables
             underneath it.

     core        star schema written by the Python load
     analytics   KPI views - the only layer BI tools should ever touch
     monitoring  pipeline runs, data-quality results, freshness

   Run first; safe to re-run.
   ========================================================================== */

CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS analytics;
CREATE SCHEMA IF NOT EXISTS monitoring;

COMMENT ON SCHEMA core       IS 'Star schema: conformed dimensions and the sales fact.';
COMMENT ON SCHEMA analytics  IS 'KPI views consumed by Power BI, Tableau and the Python chart library.';
COMMENT ON SCHEMA monitoring IS 'Pipeline run history, data-quality results and freshness.';

/* ---------------------------------------------------------------------------
   Monitoring tables. These are the only tables the pipeline appends to rather
   than replacing, because their whole value is the history.
   --------------------------------------------------------------------------- */
CREATE TABLE IF NOT EXISTS monitoring.pipeline_run (
    run_id              TEXT PRIMARY KEY,
    started_at          TIMESTAMPTZ  NOT NULL,
    finished_at         TIMESTAMPTZ,
    duration_seconds    NUMERIC(10,2),
    status              TEXT         NOT NULL,   -- RUNNING | SUCCESS | FAILED
    source_name         TEXT,
    source_bytes        BIGINT,
    is_real_dataset     BOOLEAN,
    rows_ingested       BIGINT,
    rows_loaded         BIGINT,
    rows_quarantined    BIGINT,
    checks_run          INTEGER,
    checks_failed       INTEGER,
    error_message       TEXT,
    data_min_date       DATE,
    data_max_date       DATE
);

CREATE TABLE IF NOT EXISTS monitoring.table_load (
    run_id        TEXT        NOT NULL REFERENCES monitoring.pipeline_run(run_id)
                              ON DELETE CASCADE,
    loaded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    table_name    TEXT        NOT NULL,
    rows_loaded   BIGINT      NOT NULL,
    columns       INTEGER,
    PRIMARY KEY (run_id, table_name)
);

CREATE TABLE IF NOT EXISTS monitoring.dq_result (
    dq_id           BIGSERIAL PRIMARY KEY,
    run_id          TEXT         NOT NULL REFERENCES monitoring.pipeline_run(run_id)
                                 ON DELETE CASCADE,
    run_ts          TIMESTAMPTZ  NOT NULL,
    check_name      TEXT         NOT NULL,
    check_category  TEXT         NOT NULL,
    target_table    TEXT         NOT NULL,
    severity        TEXT         NOT NULL,
    failed_rows     BIGINT       NOT NULL,
    total_rows      BIGINT       NOT NULL,
    fail_rate_pct   NUMERIC(9,4),
    passed          BOOLEAN      NOT NULL,
    details         TEXT
);

CREATE TABLE IF NOT EXISTS monitoring.cleansing_log (
    run_id         TEXT        NOT NULL REFERENCES monitoring.pipeline_run(run_id)
                               ON DELETE CASCADE,
    step_order     INTEGER     NOT NULL,
    step           TEXT        NOT NULL,
    rows_affected  BIGINT      NOT NULL,
    decision       TEXT        NOT NULL,
    note           TEXT,
    PRIMARY KEY (run_id, step_order)
);

CREATE INDEX IF NOT EXISTS ix_dq_result_run     ON monitoring.dq_result (run_id);
CREATE INDEX IF NOT EXISTS ix_dq_result_ts      ON monitoring.dq_result (run_ts DESC);
CREATE INDEX IF NOT EXISTS ix_pipeline_run_time ON monitoring.pipeline_run (started_at DESC);
