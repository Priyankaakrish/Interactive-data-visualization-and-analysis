-- ============================================================================
-- 08_business_alerts.sql - business-rule alerting over the batch warehouse
--
-- These are commercial alerts, not pipeline-health alerts. The monitoring gate
-- answers "did the load work". These answer "is something wrong with the
-- business".
--
-- Every view returns a uniform shape - alert_key, severity, subject, detail and
-- the numbers behind it - so one evaluator can dispatch all of them without
-- knowing what each rule means.
--
-- HONEST SCOPE NOTE. Online Retail II contains no inventory levels and no cost
-- of goods. A true stockout alert needs on-hand stock, which this source does
-- not have. vw_alert_demand_surge is therefore a *demand-side proxy*: it flags
-- products selling far faster than their own trailing rate, which is the input
-- to a stockout calculation, not the calculation itself. It is labelled as such
-- rather than dressed up as something it is not.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS alerting;

-- Config lives in a table rather than hard-coded in each view, so a threshold
-- can be tuned without a migration.
CREATE TABLE IF NOT EXISTS alerting.thresholds (
    alert_key       TEXT PRIMARY KEY,
    threshold       NUMERIC       NOT NULL,
    lookback_days   INTEGER       NOT NULL DEFAULT 28,
    min_baseline    NUMERIC       NOT NULL DEFAULT 0,
    severity        TEXT          NOT NULL DEFAULT 'AMBER',
    is_active       BOOLEAN       NOT NULL DEFAULT TRUE,
    description     TEXT
);

INSERT INTO alerting.thresholds
    (alert_key, threshold, lookback_days, min_baseline, severity, description)
VALUES
    ('revenue_drop',  -20.0, 28,  5000, 'RED',
     'Week-on-week revenue decline worse than this percentage'),
    ('return_spike',    2.0, 90,   500, 'RED',
     'Return rate above trailing mean by this many standard deviations'),
    ('dead_stock',     45.0, 180,  250, 'AMBER',
     'Days since last sale for a previously active product'),
    ('demand_surge',    3.0, 56,    20, 'AMBER',
     'Recent units per day as a multiple of the trailing rate'),
    ('churn_risk',      3.0, 365, 1000, 'AMBER',
     'Days since last order as a multiple of the customer usual gap')
ON CONFLICT (alert_key) DO NOTHING;

-- The most recent date in the fact table. Alerts are evaluated relative to the
-- data, not to wall-clock time - the source is historical, and an alert that
-- silently goes quiet because "today" is years after the last invoice would be
-- worse than useless.
CREATE OR REPLACE VIEW alerting.vw_as_of AS
SELECT MAX(d.full_date)::date AS as_of_date
FROM   core.fact_sales    AS f
INNER  JOIN core.dim_date AS d ON d.date_key = f.date_key;

-- ------------------------------------------------------------ revenue drop
CREATE OR REPLACE VIEW alerting.vw_alert_revenue_drop AS
WITH cfg AS (
    SELECT threshold, min_baseline, severity
    FROM   alerting.thresholds WHERE alert_key = 'revenue_drop' AND is_active
),
asof AS (SELECT as_of_date FROM alerting.vw_as_of),
weekly AS (
    SELECT date_trunc('week', d.full_date)::date AS week_start,
           SUM(CASE WHEN NOT f.is_cancellation THEN f.line_revenue ELSE 0 END) AS revenue
    FROM   core.fact_sales    AS f
    INNER  JOIN core.dim_date AS d ON d.date_key = f.date_key
    GROUP  BY 1
),
paired AS (
    SELECT week_start, revenue,
           LAG(revenue) OVER (ORDER BY week_start) AS prev_revenue
    FROM   weekly
)
SELECT 'revenue_drop'                                   AS alert_key,
       cfg.severity                                     AS severity,
       'Weekly revenue fell ' || ROUND(ABS(
           (p.revenue - p.prev_revenue) / NULLIF(p.prev_revenue, 0) * 100), 1)
           || chr(37) || ' week on week'                AS subject,
       'Week of ' || p.week_start || ': '
           || ROUND(p.revenue, 2) || ' vs ' || ROUND(p.prev_revenue, 2)
           || ' the week before'                        AS detail,
       p.week_start                                     AS event_date,
       ROUND(p.revenue, 2)                              AS observed,
       ROUND(p.prev_revenue, 2)                         AS baseline,
       ROUND((p.revenue - p.prev_revenue)
             / NULLIF(p.prev_revenue, 0) * 100, 2)      AS change_pct
FROM   paired AS p
CROSS  JOIN cfg
CROSS  JOIN asof
WHERE  p.prev_revenue >= cfg.min_baseline
  AND  p.week_start > asof.as_of_date - INTERVAL '1 year'
  AND  (p.revenue - p.prev_revenue) / NULLIF(p.prev_revenue, 0) * 100 <= cfg.threshold
ORDER  BY p.week_start DESC;

-- ------------------------------------------------------------ return spike
CREATE OR REPLACE VIEW alerting.vw_alert_return_spike AS
WITH cfg AS (
    SELECT threshold, lookback_days, min_baseline, severity
    FROM   alerting.thresholds WHERE alert_key = 'return_spike' AND is_active
),
asof AS (SELECT as_of_date FROM alerting.vw_as_of),
daily AS (
    SELECT d.full_date::date AS day,
           SUM(CASE WHEN NOT f.is_cancellation THEN f.line_revenue ELSE 0 END) AS gross,
           SUM(CASE WHEN f.is_cancellation THEN ABS(f.line_revenue) ELSE 0 END) AS returns
    FROM   core.fact_sales    AS f
    INNER  JOIN core.dim_date AS d ON d.date_key = f.date_key
    GROUP  BY 1
),
rated AS (
    SELECT day, gross, returns,
           CASE WHEN gross > 0 THEN returns / gross * 100 ELSE 0 END AS return_pct
    FROM   daily
),
stats AS (
    SELECT AVG(return_pct) AS mean_pct, STDDEV_POP(return_pct) AS sd_pct
    FROM   rated AS r CROSS JOIN cfg CROSS JOIN asof
    WHERE  r.day > asof.as_of_date - (cfg.lookback_days || ' days')::interval
      AND  r.gross >= cfg.min_baseline
)
SELECT 'return_spike'                                   AS alert_key,
       cfg.severity                                     AS severity,
       'Return rate ' || ROUND(r.return_pct, 1) || chr(37)
           || ' on ' || r.day                           AS subject,
       'Trailing mean ' || ROUND(s.mean_pct, 1) || chr(37) || ', sd '
           || ROUND(s.sd_pct, 1) || chr(37) || ' over '
           || cfg.lookback_days || ' days'               AS detail,
       r.day                                            AS event_date,
       ROUND(r.return_pct, 2)                           AS observed,
       ROUND(s.mean_pct, 2)                             AS baseline,
       ROUND((r.return_pct - s.mean_pct)
             / NULLIF(s.sd_pct, 0), 2)                  AS change_pct
FROM   rated AS r
CROSS  JOIN cfg
CROSS  JOIN asof
CROSS  JOIN stats AS s
WHERE  r.gross >= cfg.min_baseline
  AND  r.day > asof.as_of_date - (cfg.lookback_days || ' days')::interval
  AND  s.sd_pct > 0
  AND  (r.return_pct - s.mean_pct) / s.sd_pct >= cfg.threshold
ORDER  BY r.day DESC;

-- -------------------------------------------------------------- dead stock
CREATE OR REPLACE VIEW alerting.vw_alert_dead_stock AS
WITH cfg AS (
    SELECT threshold, lookback_days, min_baseline, severity
    FROM   alerting.thresholds WHERE alert_key = 'dead_stock' AND is_active
),
asof AS (SELECT as_of_date FROM alerting.vw_as_of),
per_product AS (
    SELECT p.stock_code,
           MAX(p.description)                AS description,
           MAX(d.full_date)::date            AS last_sold,
           SUM(CASE WHEN NOT f.is_cancellation THEN f.line_revenue ELSE 0 END) AS lifetime_revenue
    FROM   core.fact_sales       AS f
    INNER  JOIN core.dim_date    AS d ON d.date_key    = f.date_key
    INNER  JOIN core.dim_product AS p ON p.product_key = f.product_key
    GROUP  BY p.stock_code
)
SELECT 'dead_stock'                                     AS alert_key,
       cfg.severity                                     AS severity,
       'No sale of ' || pp.stock_code || ' for '
           || (asof.as_of_date - pp.last_sold) || ' days' AS subject,
       COALESCE(pp.description, '(no description)')
           || ' - lifetime revenue '
           || ROUND(pp.lifetime_revenue, 2)             AS detail,
       pp.last_sold                                     AS event_date,
       (asof.as_of_date - pp.last_sold)::numeric        AS observed,
       cfg.threshold                                    AS baseline,
       ROUND(pp.lifetime_revenue, 2)                    AS change_pct
FROM   per_product AS pp
CROSS  JOIN cfg
CROSS  JOIN asof
WHERE  pp.lifetime_revenue >= cfg.min_baseline
  AND  (asof.as_of_date - pp.last_sold) >= cfg.threshold
ORDER  BY pp.lifetime_revenue DESC;

-- ------------------------------------------------------------ demand surge
-- Stockout-risk proxy. See the scope note at the top of this file: without
-- inventory levels this measures demand acceleration, not cover.
CREATE OR REPLACE VIEW alerting.vw_alert_demand_surge AS
WITH cfg AS (
    SELECT threshold, lookback_days, min_baseline, severity
    FROM   alerting.thresholds WHERE alert_key = 'demand_surge' AND is_active
),
asof AS (SELECT as_of_date FROM alerting.vw_as_of),
recent AS (
    SELECT p.stock_code,
           MAX(p.description) AS description,
           SUM(f.quantity)::numeric / 7.0 AS units_per_day
    FROM   core.fact_sales       AS f
    INNER  JOIN core.dim_date    AS d ON d.date_key    = f.date_key
    INNER  JOIN core.dim_product AS p ON p.product_key = f.product_key
    CROSS  JOIN asof
    WHERE  NOT f.is_cancellation
      AND  d.full_date >  asof.as_of_date - INTERVAL '7 days'
    GROUP  BY p.stock_code
),
baseline_rate AS (
    SELECT p.stock_code,
           SUM(f.quantity)::numeric / NULLIF(cfg.lookback_days, 0) AS units_per_day
    FROM   core.fact_sales       AS f
    INNER  JOIN core.dim_date    AS d ON d.date_key    = f.date_key
    INNER  JOIN core.dim_product AS p ON p.product_key = f.product_key
    CROSS  JOIN cfg
    CROSS  JOIN asof
    WHERE  NOT f.is_cancellation
      AND  d.full_date <= asof.as_of_date - INTERVAL '7 days'
      AND  d.full_date >  asof.as_of_date - (cfg.lookback_days || ' days')::interval
    GROUP  BY p.stock_code, cfg.lookback_days
)
SELECT 'demand_surge'                                   AS alert_key,
       cfg.severity                                     AS severity,
       'Demand for ' || r.stock_code || ' is '
           || ROUND(r.units_per_day / NULLIF(t.units_per_day, 0), 1)
           || 'x its trailing rate'                     AS subject,
       COALESCE(r.description, '(no description)')
           || ' - ' || ROUND(r.units_per_day, 1)
           || ' units/day vs ' || ROUND(t.units_per_day, 1)
           || ' trailing. Demand-side proxy: no inventory data in source.' AS detail,
       (SELECT as_of_date FROM asof)                    AS event_date,
       ROUND(r.units_per_day, 2)                        AS observed,
       ROUND(t.units_per_day, 2)                        AS baseline,
       ROUND(r.units_per_day
             / NULLIF(t.units_per_day, 0), 2)           AS change_pct
FROM   recent AS r
INNER  JOIN baseline_rate AS t ON t.stock_code = r.stock_code
CROSS  JOIN cfg
WHERE  t.units_per_day > 0
  AND  r.units_per_day >= cfg.min_baseline
  AND  r.units_per_day / t.units_per_day >= cfg.threshold
ORDER  BY r.units_per_day / t.units_per_day DESC;

-- ------------------------------------------------------------- churn risk
CREATE OR REPLACE VIEW alerting.vw_alert_churn_risk AS
WITH cfg AS (
    SELECT threshold, lookback_days, min_baseline, severity
    FROM   alerting.thresholds WHERE alert_key = 'churn_risk' AND is_active
),
asof AS (SELECT as_of_date FROM alerting.vw_as_of),
orders AS (
    SELECT c.customer_id,
           d.full_date::date AS order_date,
           SUM(CASE WHEN NOT f.is_cancellation THEN f.line_revenue ELSE 0 END) AS revenue
    FROM   core.fact_sales        AS f
    INNER  JOIN core.dim_date     AS d ON d.date_key     = f.date_key
    INNER  JOIN core.dim_customer AS c ON c.customer_key = f.customer_key
    WHERE  c.customer_id IS NOT NULL
    GROUP  BY c.customer_id, d.full_date
),
gaps AS (
    SELECT customer_id, order_date, revenue,
           order_date - LAG(order_date)
               OVER (PARTITION BY customer_id ORDER BY order_date) AS gap_days
    FROM   orders
),
profile AS (
    SELECT customer_id,
           AVG(gap_days)::numeric      AS typical_gap,
           COUNT(*)                    AS order_count,
           SUM(revenue)                AS lifetime_revenue,
           MAX(order_date)             AS last_order
    FROM   gaps
    GROUP  BY customer_id
    HAVING COUNT(*) >= 3 AND AVG(gap_days) > 0
)
SELECT 'churn_risk'                                     AS alert_key,
       cfg.severity                                     AS severity,
       'Customer ' || pr.customer_id || ' silent for '
           || (asof.as_of_date - pr.last_order) || ' days' AS subject,
       'Usual gap ' || ROUND(pr.typical_gap, 1) || ' days across '
           || pr.order_count || ' orders, lifetime revenue '
           || ROUND(pr.lifetime_revenue, 2)             AS detail,
       pr.last_order                                    AS event_date,
       (asof.as_of_date - pr.last_order)::numeric       AS observed,
       ROUND(pr.typical_gap, 2)                         AS baseline,
       ROUND((asof.as_of_date - pr.last_order)
             / NULLIF(pr.typical_gap, 0), 2)            AS change_pct
FROM   profile AS pr
CROSS  JOIN cfg
CROSS  JOIN asof
WHERE  pr.lifetime_revenue >= cfg.min_baseline
  AND  (asof.as_of_date - pr.last_order) / pr.typical_gap >= cfg.threshold
ORDER  BY pr.lifetime_revenue DESC;

-- ----------------------------------------------------------------- rollup
CREATE OR REPLACE VIEW alerting.vw_all_alerts AS
SELECT * FROM alerting.vw_alert_revenue_drop
UNION ALL SELECT * FROM alerting.vw_alert_return_spike
UNION ALL SELECT * FROM alerting.vw_alert_dead_stock
UNION ALL SELECT * FROM alerting.vw_alert_demand_surge
UNION ALL SELECT * FROM alerting.vw_alert_churn_risk;

CREATE OR REPLACE VIEW alerting.vw_alert_summary AS
SELECT alert_key,
       severity,
       COUNT(*)          AS alert_count,
       MAX(event_date)   AS most_recent
FROM   alerting.vw_all_alerts
GROUP  BY alert_key, severity
ORDER  BY CASE severity WHEN 'RED' THEN 1 WHEN 'AMBER' THEN 2 ELSE 3 END,
          alert_count DESC;
