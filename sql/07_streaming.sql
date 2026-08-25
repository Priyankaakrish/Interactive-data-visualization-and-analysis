-- ============================================================================
-- 07_streaming.sql - schema for the real-time ingestion path
--
-- The batch warehouse (core.*) is the system of record. This schema holds the
-- streaming side: raw events as they arrive, and windowed aggregates written
-- by the Spark consumer. Keeping them apart means a streaming outage can never
-- corrupt a reconciled batch figure.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS stream;

-- ---------------------------------------------------------------- raw events
-- Append-only landing table. One row per Kafka message actually processed.
CREATE TABLE IF NOT EXISTS stream.event_log (
    event_id        BIGSERIAL PRIMARY KEY,
    invoice_no      TEXT        NOT NULL,
    stock_code      TEXT        NOT NULL,
    description     TEXT,
    quantity        INTEGER     NOT NULL,
    unit_price      NUMERIC(18,6) NOT NULL,
    line_revenue    NUMERIC(18,6) NOT NULL,
    customer_id     TEXT,
    country         TEXT        NOT NULL,
    is_cancellation BOOLEAN     NOT NULL DEFAULT FALSE,
    event_time      TIMESTAMP   NOT NULL,
    ingested_at     TIMESTAMP   NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_event_log_time    ON stream.event_log (event_time);
CREATE INDEX IF NOT EXISTS ix_event_log_country ON stream.event_log (country);

-- ------------------------------------------------------- windowed aggregates
-- Written by Spark via foreachBatch. The window bounds are part of the key so
-- a re-delivered micro-batch overwrites rather than double-counts.
CREATE TABLE IF NOT EXISTS stream.live_kpi (
    window_start    TIMESTAMP   NOT NULL,
    window_end      TIMESTAMP   NOT NULL,
    gross_revenue   NUMERIC(18,2) NOT NULL DEFAULT 0,
    returns_value   NUMERIC(18,2) NOT NULL DEFAULT 0,
    net_revenue     NUMERIC(18,2) NOT NULL DEFAULT 0,
    order_count     INTEGER     NOT NULL DEFAULT 0,
    line_count      INTEGER     NOT NULL DEFAULT 0,
    units_sold      BIGINT      NOT NULL DEFAULT 0,
    updated_at      TIMESTAMP   NOT NULL DEFAULT now(),
    PRIMARY KEY (window_start, window_end)
);

CREATE TABLE IF NOT EXISTS stream.live_country (
    window_start    TIMESTAMP   NOT NULL,
    window_end      TIMESTAMP   NOT NULL,
    country         TEXT        NOT NULL,
    gross_revenue   NUMERIC(18,2) NOT NULL DEFAULT 0,
    line_count      INTEGER     NOT NULL DEFAULT 0,
    updated_at      TIMESTAMP   NOT NULL DEFAULT now(),
    PRIMARY KEY (window_start, window_end, country)
);

-- ------------------------------------------------------------ consumer state
-- Lets the API report whether the stream is live without querying Kafka.
CREATE TABLE IF NOT EXISTS stream.consumer_state (
    consumer_name   TEXT PRIMARY KEY,
    last_batch_id   BIGINT,
    last_event_time TIMESTAMP,
    last_seen_at    TIMESTAMP NOT NULL DEFAULT now(),
    rows_processed  BIGINT NOT NULL DEFAULT 0
);

-- ------------------------------------------------------------------- rollups
CREATE OR REPLACE VIEW stream.vw_live_summary AS
SELECT
    COALESCE(SUM(gross_revenue), 0)                      AS gross_revenue,
    COALESCE(SUM(returns_value), 0)                      AS returns_value,
    COALESCE(SUM(net_revenue),   0)                      AS net_revenue,
    COALESCE(SUM(order_count),   0)                      AS order_count,
    COALESCE(SUM(line_count),    0)                      AS line_count,
    COALESCE(SUM(units_sold),    0)                      AS units_sold,
    CASE WHEN COALESCE(SUM(gross_revenue), 0) = 0 THEN 0
         ELSE ROUND(SUM(returns_value) / SUM(gross_revenue) * 100, 2)
    END                                                  AS return_rate_pct,
    MIN(window_start)                                    AS first_window,
    MAX(window_end)                                      AS last_window
FROM stream.live_kpi;

CREATE OR REPLACE VIEW stream.vw_live_recent AS
SELECT window_start, window_end, gross_revenue, net_revenue,
       returns_value, order_count, line_count, units_sold
FROM   stream.live_kpi
ORDER  BY window_start DESC
LIMIT  60;
