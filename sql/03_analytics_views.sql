/* =============================================================================
   03_analytics_views.sql
   The analytics contract. Power BI, Tableau and the Python chart library all
   read from here and nowhere else.

   Revenue is defined once, in one place, because this dataset makes it easy to
   define three different "revenues" by accident:

     gross_revenue    product sales only, cancellations excluded
     returns_value    the negative value of credit notes (C-prefix invoices)
     net_revenue      gross + returns  <- the number the business actually earned
     service_revenue  postage, fees and vouchers, reported separately

   Every downstream visual uses these names, so a chart can never quietly show
   gross where the table shows net.
   ========================================================================== */

/* ---------------------------------------------------- base enriched fact */
CREATE OR REPLACE VIEW analytics.vw_sales_enriched AS
SELECT
    f.invoice_line_key,
    f.invoice_no,
    f.invoice_date,
    d.date_key,
    d.full_date,
    d.year,
    d.quarter,
    d.month_number,
    d.month_name,
    d.year_month,
    d.month_start,
    d.day_name,
    d.is_weekend,
    p.product_key,
    p.stock_code,
    p.description,
    c.customer_key,
    c.customer_id,
    c.cohort_month,
    g.country_key,
    g.country,
    g.region,
    g.is_domestic,
    f.quantity,
    f.unit_price,
    f.line_revenue,
    f.is_cancellation,
    f.is_service_line,
    f.is_product_line,
    f.has_customer
FROM core.fact_sales                  AS f
INNER JOIN core.vw_dim_date_flat      AS d ON d.date_key     = f.date_key
INNER JOIN core.vw_dim_product_flat   AS p ON p.product_key  = f.product_key
INNER JOIN core.vw_dim_country_flat   AS g ON g.country_key  = f.country_key
LEFT  JOIN core.vw_dim_customer_flat  AS c ON c.customer_key = f.customer_key;

COMMENT ON VIEW analytics.vw_sales_enriched IS
    'Sales fact joined to every dimension. Grain: one row per invoice line.';

/* -------------------------------------------------- monthly sales trend */
CREATE OR REPLACE VIEW analytics.vw_kpi_sales_monthly AS
WITH monthly AS (
    SELECT
        year_month,
        month_start,
        year,
        SUM(line_revenue) FILTER (WHERE NOT is_cancellation AND is_product_line)
            AS gross_revenue,
        SUM(line_revenue) FILTER (WHERE is_cancellation)
            AS returns_value,
        SUM(line_revenue) FILTER (WHERE NOT is_cancellation AND is_service_line)
            AS service_revenue,
        SUM(quantity)     FILTER (WHERE NOT is_cancellation AND is_product_line)
            AS units_sold,
        SUM(ABS(quantity)) FILTER (WHERE is_cancellation)
            AS units_returned,
        COUNT(DISTINCT invoice_no) FILTER (WHERE NOT is_cancellation)
            AS order_count,
        COUNT(DISTINCT invoice_no) FILTER (WHERE is_cancellation)
            AS credit_note_count,
        COUNT(DISTINCT customer_key)                     AS active_customers,
        COUNT(DISTINCT product_key) FILTER (WHERE is_product_line)
            AS products_sold
    FROM analytics.vw_sales_enriched
    GROUP BY year_month, month_start, year
),
enriched AS (
    SELECT
        m.*,
        COALESCE(m.gross_revenue, 0) + COALESCE(m.returns_value, 0) AS net_revenue,
        LAG(m.gross_revenue)     OVER (ORDER BY m.month_start) AS prev_month_revenue,
        LAG(m.gross_revenue, 12) OVER (ORDER BY m.month_start) AS sply_revenue,
        AVG(m.gross_revenue) OVER (ORDER BY m.month_start
                                   ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)
            AS revenue_rolling_3m
    FROM monthly AS m
)
SELECT
    e.*,
    ROUND(100.0 * (e.gross_revenue - e.prev_month_revenue)
          / NULLIF(e.prev_month_revenue, 0), 2)               AS mom_growth_pct,
    ROUND(100.0 * (e.gross_revenue - e.sply_revenue)
          / NULLIF(e.sply_revenue, 0), 2)                     AS yoy_growth_pct,
    ROUND(e.gross_revenue / NULLIF(e.order_count, 0), 2)      AS avg_order_value,
    ROUND(e.units_sold::numeric / NULLIF(e.order_count, 0), 2) AS avg_units_per_order,
    ROUND(100.0 * ABS(COALESCE(e.returns_value, 0))
          / NULLIF(e.gross_revenue, 0), 2)                    AS return_rate_pct
FROM enriched AS e;

/* ------------------------------------ product performance with ABC class */
CREATE OR REPLACE VIEW analytics.vw_kpi_product_performance AS
WITH totals AS (
    SELECT
        product_key,
        stock_code,
        description,
        SUM(line_revenue) FILTER (WHERE NOT is_cancellation) AS gross_revenue,
        SUM(line_revenue) FILTER (WHERE is_cancellation)     AS returns_value,
        SUM(quantity)     FILTER (WHERE NOT is_cancellation) AS units_sold,
        SUM(ABS(quantity)) FILTER (WHERE is_cancellation)    AS units_returned,
        COUNT(DISTINCT invoice_no) FILTER (WHERE NOT is_cancellation) AS order_count,
        COUNT(DISTINCT customer_key)                         AS customer_count,
        AVG(unit_price)   FILTER (WHERE NOT is_cancellation) AS avg_unit_price
    FROM analytics.vw_sales_enriched
    WHERE is_product_line
    GROUP BY product_key, stock_code, description
),
ranked AS (
    SELECT
        t.*,
        COALESCE(t.gross_revenue, 0) + COALESCE(t.returns_value, 0) AS net_revenue,
        SUM(t.gross_revenue) OVER ()                                AS total_revenue,
        SUM(t.gross_revenue) OVER (ORDER BY t.gross_revenue DESC
                                   ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
            AS running_revenue,
        ROW_NUMBER() OVER (ORDER BY t.gross_revenue DESC)           AS revenue_rank
    FROM totals AS t
)
SELECT
    r.product_key,
    r.stock_code,
    r.description,
    r.gross_revenue,
    r.returns_value,
    r.net_revenue,
    r.units_sold,
    r.units_returned,
    r.order_count,
    r.customer_count,
    ROUND(r.avg_unit_price, 2)                                       AS avg_unit_price,
    r.revenue_rank,
    ROUND(r.gross_revenue / NULLIF(r.total_revenue, 0), 6)           AS revenue_share,
    ROUND(r.running_revenue / NULLIF(r.total_revenue, 0), 6)         AS cumulative_revenue_share,
    ROUND(100.0 * COALESCE(r.units_returned, 0)
          / NULLIF(r.units_sold, 0), 2)                              AS return_rate_pct,
    CASE
        WHEN r.running_revenue / NULLIF(r.total_revenue, 0) <= 0.80 THEN 'A'
        WHEN r.running_revenue / NULLIF(r.total_revenue, 0) <= 0.95 THEN 'B'
        ELSE 'C'
    END                                                              AS abc_class
FROM ranked AS r;

/* --------------------------------------------------- country performance */
CREATE OR REPLACE VIEW analytics.vw_kpi_country_performance AS
SELECT
    country_key,
    country,
    region,
    is_domestic,
    SUM(line_revenue) FILTER (WHERE NOT is_cancellation AND is_product_line)
        AS gross_revenue,
    SUM(line_revenue) FILTER (WHERE is_cancellation)      AS returns_value,
    COALESCE(SUM(line_revenue) FILTER (WHERE NOT is_cancellation AND is_product_line), 0)
        + COALESCE(SUM(line_revenue) FILTER (WHERE is_cancellation), 0)
        AS net_revenue,
    SUM(quantity) FILTER (WHERE NOT is_cancellation AND is_product_line)
        AS units_sold,
    COUNT(DISTINCT invoice_no)   FILTER (WHERE NOT is_cancellation) AS order_count,
    COUNT(DISTINCT customer_key)                                    AS customer_count,
    COUNT(DISTINCT product_key)  FILTER (WHERE is_product_line)     AS products_sold,
    ROUND(SUM(line_revenue) FILTER (WHERE NOT is_cancellation AND is_product_line)
          / NULLIF(COUNT(DISTINCT invoice_no)
                   FILTER (WHERE NOT is_cancellation), 0), 2)       AS avg_order_value,
    ROUND(SUM(line_revenue) FILTER (WHERE NOT is_cancellation AND is_product_line)
          / NULLIF(COUNT(DISTINCT customer_key), 0), 2)             AS revenue_per_customer,
    ROUND(100.0 * ABS(COALESCE(SUM(line_revenue) FILTER (WHERE is_cancellation), 0))
          / NULLIF(SUM(line_revenue)
                   FILTER (WHERE NOT is_cancellation AND is_product_line), 0), 2)
        AS return_rate_pct
FROM analytics.vw_sales_enriched
GROUP BY country_key, country, region, is_domestic;

/* ------------------------------------------------ customer RFM segments */
CREATE OR REPLACE VIEW analytics.vw_kpi_customer_rfm AS
WITH bounds AS (
    SELECT MAX(full_date)::date AS as_of_date FROM analytics.vw_sales_enriched
),
base AS (
    SELECT
        s.customer_key,
        s.customer_id,
        s.country,
        s.cohort_month,
        MAX(s.full_date)::date                             AS last_purchase,
        MIN(s.full_date)::date                             AS first_purchase,
        /* date - date yields an integer day count; timestamp - timestamp would
           yield an interval, which is not a number any chart can plot. */
        ((SELECT as_of_date FROM bounds) - MAX(s.full_date)::date)::int
                                                           AS recency_days,
        COUNT(DISTINCT s.invoice_no) FILTER (WHERE NOT s.is_cancellation)
            AS frequency,
        COALESCE(SUM(s.line_revenue) FILTER (WHERE NOT s.is_cancellation), 0)
            + COALESCE(SUM(s.line_revenue) FILTER (WHERE s.is_cancellation), 0)
            AS monetary,
        SUM(s.quantity) FILTER (WHERE NOT s.is_cancellation) AS units_bought,
        COUNT(DISTINCT s.product_key)                        AS distinct_products
    FROM analytics.vw_sales_enriched AS s
    WHERE s.customer_key IS NOT NULL
    GROUP BY s.customer_key, s.customer_id, s.country, s.cohort_month
),
scored AS (
    SELECT
        b.*,
        NTILE(5) OVER (ORDER BY b.recency_days DESC) AS r_score,  -- recent = high
        NTILE(5) OVER (ORDER BY b.frequency)         AS f_score,
        NTILE(5) OVER (ORDER BY b.monetary)          AS m_score
    FROM base AS b
    WHERE b.monetary > 0
)
SELECT
    s.*,
    ROUND(s.monetary / NULLIF(s.frequency, 0), 2) AS avg_order_value,
    (s.r_score::text || s.f_score::text || s.m_score::text) AS rfm_cell,
    CASE
        WHEN s.r_score >= 4 AND s.f_score >= 4 AND s.m_score >= 4 THEN 'Champions'
        WHEN s.r_score >= 4 AND s.f_score >= 3                    THEN 'Loyal'
        WHEN s.r_score >= 4 AND s.f_score <= 2                    THEN 'New / Promising'
        WHEN s.r_score <= 2 AND s.m_score >= 4                    THEN 'At Risk - High Value'
        WHEN s.r_score <= 2 AND s.f_score >= 3                    THEN 'At Risk'
        WHEN s.r_score <= 2                                       THEN 'Hibernating'
        ELSE                                                           'Needs Attention'
    END AS segment
FROM scored AS s;

/* ------------------------------------------------- cohort retention grid */
CREATE OR REPLACE VIEW analytics.vw_kpi_cohort_retention AS
WITH activity AS (
    SELECT DISTINCT
        s.customer_key,
        s.cohort_month,
        s.year_month AS activity_month,
        (DATE_PART('year',  s.month_start) - DATE_PART('year',  TO_DATE(s.cohort_month, 'YYYY-MM'))) * 12
        + (DATE_PART('month', s.month_start) - DATE_PART('month', TO_DATE(s.cohort_month, 'YYYY-MM')))
            AS months_since_first
    FROM analytics.vw_sales_enriched AS s
    WHERE s.customer_key IS NOT NULL
      AND NOT s.is_cancellation
),
cohort_size AS (
    SELECT cohort_month, COUNT(DISTINCT customer_key) AS cohort_customers
    FROM activity
    WHERE months_since_first = 0
    GROUP BY cohort_month
),
retained AS (
    SELECT
        a.cohort_month,
        a.months_since_first::int AS months_since_first,
        COUNT(DISTINCT a.customer_key) AS active_customers
    FROM activity AS a
    WHERE a.months_since_first >= 0
    GROUP BY a.cohort_month, a.months_since_first
)
SELECT
    r.cohort_month,
    r.months_since_first,
    c.cohort_customers,
    r.active_customers,
    ROUND(100.0 * r.active_customers / NULLIF(c.cohort_customers, 0), 2)
        AS retention_pct
FROM retained AS r
INNER JOIN cohort_size AS c ON c.cohort_month = r.cohort_month;

/* ------------------------------------------------------ returns analysis */
CREATE OR REPLACE VIEW analytics.vw_kpi_returns_monthly AS
SELECT
    year_month,
    month_start,
    SUM(line_revenue) FILTER (WHERE NOT is_cancellation AND is_product_line)
        AS gross_revenue,
    ABS(COALESCE(SUM(line_revenue) FILTER (WHERE is_cancellation), 0))
        AS returns_value,
    COUNT(DISTINCT invoice_no) FILTER (WHERE is_cancellation) AS credit_notes,
    COUNT(DISTINCT customer_key) FILTER (WHERE is_cancellation)
        AS customers_returning,
    ROUND(100.0 * ABS(COALESCE(SUM(line_revenue) FILTER (WHERE is_cancellation), 0))
          / NULLIF(SUM(line_revenue)
                   FILTER (WHERE NOT is_cancellation AND is_product_line), 0), 2)
        AS return_rate_pct
FROM analytics.vw_sales_enriched
GROUP BY year_month, month_start;

/* -------------------------------------------------------- basket metrics */
CREATE OR REPLACE VIEW analytics.vw_kpi_basket AS
SELECT
    invoice_no,
    MIN(full_date)                AS invoice_date,
    MIN(year_month)               AS year_month,
    MAX(country)                  AS country,
    MAX(customer_key)             AS customer_key,
    BOOL_OR(is_cancellation)      AS is_cancellation,
    COUNT(*)                      AS line_count,
    COUNT(DISTINCT product_key)   AS distinct_products,
    SUM(quantity)                 AS total_units,
    ROUND(SUM(line_revenue), 2)   AS basket_value
FROM analytics.vw_sales_enriched
GROUP BY invoice_no;

/* ------------------------------------------------------ executive rollup */
CREATE OR REPLACE VIEW analytics.vw_kpi_executive_summary AS
WITH totals AS (
    SELECT
        COALESCE(SUM(line_revenue) FILTER (WHERE NOT is_cancellation AND is_product_line), 0)
            AS gross_revenue,
        COALESCE(SUM(line_revenue) FILTER (WHERE is_cancellation), 0)
            AS returns_value,
        COALESCE(SUM(line_revenue) FILTER (WHERE NOT is_cancellation AND is_service_line), 0)
            AS service_revenue,
        COUNT(DISTINCT invoice_no) FILTER (WHERE NOT is_cancellation) AS order_count,
        COUNT(DISTINCT customer_key)                                  AS customer_count,
        COUNT(DISTINCT product_key) FILTER (WHERE is_product_line)    AS product_count,
        SUM(quantity) FILTER (WHERE NOT is_cancellation AND is_product_line)
            AS units_sold,
        COUNT(*) FILTER (WHERE NOT has_customer)                      AS guest_lines,
        COUNT(*)                                                      AS total_lines,
        MIN(full_date) AS first_date,
        MAX(full_date) AS last_date
    FROM analytics.vw_sales_enriched
)
SELECT 'Net Revenue'        AS metric, 1 AS sort_order,
       ROUND(gross_revenue + returns_value, 2) AS value, 'currency' AS format FROM totals
UNION ALL SELECT 'Gross Revenue', 2, ROUND(gross_revenue, 2), 'currency' FROM totals
UNION ALL SELECT 'Returns Value', 3, ROUND(ABS(returns_value), 2), 'currency' FROM totals
UNION ALL SELECT 'Return Rate', 4,
       ROUND(ABS(returns_value) / NULLIF(gross_revenue, 0), 4), 'percent' FROM totals
UNION ALL SELECT 'Orders', 5, order_count, 'integer' FROM totals
UNION ALL SELECT 'Avg Order Value', 6,
       ROUND(gross_revenue / NULLIF(order_count, 0), 2), 'currency' FROM totals
UNION ALL SELECT 'Identified Customers', 7, customer_count, 'integer' FROM totals
UNION ALL SELECT 'Products Sold', 8, product_count, 'integer' FROM totals
UNION ALL SELECT 'Units Sold', 9, units_sold, 'integer' FROM totals
UNION ALL SELECT 'Guest Checkout Share', 10,
       ROUND(guest_lines::numeric / NULLIF(total_lines, 0), 4), 'percent' FROM totals;
