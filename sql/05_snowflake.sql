/* =============================================================================
   05_snowflake.sql
   Normalise the star dimensions into a snowflake.

   A star schema is faster for analytics. But a denormalised dimension stores
   the same attribute thousands of times, and anything stored thousands of
   times will eventually disagree with itself. "region" lived in 43 country
   rows and transitively in 5,941 customer rows.

     core       normalised snowflake  - one row per attribute, FK-enforced
     analytics  star-shaped views     - flattened, identical column contract

   Power BI never sees the snowflake; it reads the views, which did not change.

   EXECUTION ORDER: after 02_constraints.sql, before 03_analytics_views.sql.
   ========================================================================== */

/* ---------------------------------------------------------- GEOGRAPHY ---- */
CREATE TABLE IF NOT EXISTS core.dim_region (
    region_key   SMALLINT PRIMARY KEY,
    region_name  TEXT NOT NULL UNIQUE,
    is_domestic  BOOLEAN NOT NULL
);
TRUNCATE core.dim_region CASCADE;
INSERT INTO core.dim_region (region_key, region_name, is_domestic)
SELECT ROW_NUMBER() OVER (ORDER BY region), region, BOOL_OR(is_domestic)
FROM core.dim_country GROUP BY region;

CREATE TABLE IF NOT EXISTS core.dim_country_snow (
    country_key INTEGER PRIMARY KEY,
    country     TEXT NOT NULL UNIQUE,
    region_key  SMALLINT NOT NULL REFERENCES core.dim_region(region_key)
);
TRUNCATE core.dim_country_snow CASCADE;
INSERT INTO core.dim_country_snow
SELECT c.country_key, c.country, r.region_key
FROM core.dim_country AS c
INNER JOIN core.dim_region AS r ON r.region_name = c.region;

/* ------------------------------------------------------------- COHORT ---- */
CREATE TABLE IF NOT EXISTS core.dim_cohort (
    cohort_key     INTEGER PRIMARY KEY,
    cohort_month   TEXT NOT NULL UNIQUE,
    cohort_year    SMALLINT NOT NULL,
    cohort_quarter SMALLINT NOT NULL
);
TRUNCATE core.dim_cohort CASCADE;
INSERT INTO core.dim_cohort
SELECT ROW_NUMBER() OVER (ORDER BY cohort_month), cohort_month,
       SPLIT_PART(cohort_month,'-',1)::smallint,
       CEIL(SPLIT_PART(cohort_month,'-',2)::numeric / 3)::smallint
FROM (SELECT DISTINCT cohort_month FROM core.dim_customer
      WHERE cohort_month IS NOT NULL) AS c;

/* ----------------------------------------------------------- CUSTOMER ---- */
CREATE TABLE IF NOT EXISTS core.dim_customer_snow (
    customer_key   INTEGER PRIMARY KEY,
    customer_id    TEXT NOT NULL UNIQUE,
    country_key    INTEGER NOT NULL REFERENCES core.dim_country_snow(country_key),
    cohort_key     INTEGER REFERENCES core.dim_cohort(cohort_key),
    first_purchase DATE,
    last_purchase  DATE,
    countries_seen SMALLINT
);
TRUNCATE core.dim_customer_snow CASCADE;
INSERT INTO core.dim_customer_snow
SELECT c.customer_key, c.customer_id, g.country_key, h.cohort_key,
       c.first_purchase::date, c.last_purchase::date, c.countries_seen
FROM core.dim_customer AS c
INNER JOIN core.dim_country_snow AS g ON g.country      = c.country
LEFT  JOIN core.dim_cohort       AS h ON h.cohort_month = c.cohort_month;

/* --------------------------------------------------------------- DATE ---- */
CREATE TABLE IF NOT EXISTS core.dim_year (
    year_key SMALLINT PRIMARY KEY,
    year_num SMALLINT NOT NULL UNIQUE
);
TRUNCATE core.dim_year CASCADE;
INSERT INTO core.dim_year SELECT DISTINCT year, year FROM core.dim_date;

CREATE TABLE IF NOT EXISTS core.dim_month (
    month_key    INTEGER PRIMARY KEY,
    year_month   TEXT NOT NULL UNIQUE,
    year_key     SMALLINT NOT NULL REFERENCES core.dim_year(year_key),
    month_number SMALLINT NOT NULL,
    month_name   TEXT NOT NULL,
    quarter      SMALLINT NOT NULL,
    month_start  DATE NOT NULL
);
TRUNCATE core.dim_month CASCADE;
INSERT INTO core.dim_month
SELECT DISTINCT (year*100 + month_number), year_month, year, month_number,
       month_name, quarter, month_start::date
FROM core.dim_date;

CREATE TABLE IF NOT EXISTS core.dim_date_snow (
    date_key     INTEGER PRIMARY KEY,
    full_date    DATE NOT NULL UNIQUE,
    month_key    INTEGER NOT NULL REFERENCES core.dim_month(month_key),
    day_of_week  SMALLINT NOT NULL,
    day_name     TEXT NOT NULL,
    week_of_year SMALLINT NOT NULL,
    is_weekend   BOOLEAN NOT NULL
);
TRUNCATE core.dim_date_snow CASCADE;
INSERT INTO core.dim_date_snow
SELECT date_key, full_date::date, (year*100 + month_number),
       day_of_week, day_name, week_of_year, is_weekend
FROM core.dim_date;

/* ------------------------------------------------------------ PRODUCT ---- */
CREATE TABLE IF NOT EXISTS core.dim_product_type (
    product_type_key SMALLINT PRIMARY KEY,
    type_name        TEXT NOT NULL UNIQUE,
    is_service       BOOLEAN NOT NULL,
    description      TEXT
);
TRUNCATE core.dim_product_type CASCADE;
INSERT INTO core.dim_product_type VALUES
 (1,'Product',        FALSE,'A sellable item of gift-ware'),
 (2,'Service charge', TRUE ,'Postage, carriage, bank charges, manual adjustments'),
 (3,'Gift voucher',   TRUE ,'Dotcomgiftshop gift vouchers');

CREATE TABLE IF NOT EXISTS core.dim_product_snow (
    product_key          INTEGER PRIMARY KEY,
    stock_code           TEXT NOT NULL UNIQUE,
    description          TEXT,
    product_type_key     SMALLINT NOT NULL REFERENCES core.dim_product_type(product_type_key),
    description_variants SMALLINT,
    avg_unit_price       NUMERIC(12,2),
    first_sold           DATE,
    last_sold            DATE
);
TRUNCATE core.dim_product_snow CASCADE;
INSERT INTO core.dim_product_snow
SELECT p.product_key, p.stock_code, p.description,
       CASE WHEN p.stock_code LIKE 'GIFT%' THEN 3
            WHEN p.is_service_line          THEN 2
            ELSE 1 END,
       p.description_variants, p.avg_unit_price,
       p.first_sold::date, p.last_sold::date
FROM core.dim_product AS p;

/* ---------------------------------------------------------- FACT REWIRE -- */
/* Adding these proves the decomposition lost nothing: if one surrogate key
   failed to resolve through the new hierarchy, this fails loudly. */
ALTER TABLE core.fact_sales DROP CONSTRAINT IF EXISTS fk_fact_product_snow;
ALTER TABLE core.fact_sales DROP CONSTRAINT IF EXISTS fk_fact_customer_snow;
ALTER TABLE core.fact_sales DROP CONSTRAINT IF EXISTS fk_fact_country_snow;
ALTER TABLE core.fact_sales DROP CONSTRAINT IF EXISTS fk_fact_date_snow;

ALTER TABLE core.fact_sales ADD CONSTRAINT fk_fact_product_snow
    FOREIGN KEY (product_key)  REFERENCES core.dim_product_snow(product_key);
ALTER TABLE core.fact_sales ADD CONSTRAINT fk_fact_customer_snow
    FOREIGN KEY (customer_key) REFERENCES core.dim_customer_snow(customer_key);
ALTER TABLE core.fact_sales ADD CONSTRAINT fk_fact_country_snow
    FOREIGN KEY (country_key)  REFERENCES core.dim_country_snow(country_key);
ALTER TABLE core.fact_sales ADD CONSTRAINT fk_fact_date_snow
    FOREIGN KEY (date_key)     REFERENCES core.dim_date_snow(date_key);

CREATE INDEX IF NOT EXISTS ix_country_snow_region   ON core.dim_country_snow (region_key);
CREATE INDEX IF NOT EXISTS ix_customer_snow_country ON core.dim_customer_snow (country_key);
CREATE INDEX IF NOT EXISTS ix_date_snow_month       ON core.dim_date_snow (month_key);
CREATE INDEX IF NOT EXISTS ix_month_year            ON core.dim_month (year_key);

ANALYZE core.dim_region;
ANALYZE core.dim_country_snow;
ANALYZE core.dim_customer_snow;
ANALYZE core.dim_date_snow;
ANALYZE core.dim_product_snow;

/* ------------------------------------------- flattened star, for the BI --- */
CREATE OR REPLACE VIEW core.vw_dim_customer_flat AS
SELECT cs.customer_key, cs.customer_id, g.country, r.region_name AS region,
       r.is_domestic, h.cohort_month, h.cohort_year, h.cohort_quarter,
       cs.first_purchase, cs.last_purchase, cs.countries_seen
FROM core.dim_customer_snow      AS cs
INNER JOIN core.dim_country_snow AS g ON g.country_key = cs.country_key
INNER JOIN core.dim_region       AS r ON r.region_key  = g.region_key
LEFT  JOIN core.dim_cohort       AS h ON h.cohort_key  = cs.cohort_key;

CREATE OR REPLACE VIEW core.vw_dim_date_flat AS
SELECT d.date_key, d.full_date, y.year_num AS year, m.quarter, m.month_number,
       m.month_name, m.year_month, m.month_start, d.day_of_week, d.day_name,
       d.week_of_year, d.is_weekend
FROM core.dim_date_snow   AS d
INNER JOIN core.dim_month AS m ON m.month_key = d.month_key
INNER JOIN core.dim_year  AS y ON y.year_key  = m.year_key;

CREATE OR REPLACE VIEW core.vw_dim_product_flat AS
SELECT p.product_key, p.stock_code, p.description, t.type_name AS product_type,
       t.is_service AS is_service_line, NOT t.is_service AS is_product,
       p.description_variants, p.avg_unit_price, p.first_sold, p.last_sold
FROM core.dim_product_snow       AS p
INNER JOIN core.dim_product_type AS t ON t.product_type_key = p.product_type_key;

CREATE OR REPLACE VIEW core.vw_dim_country_flat AS
SELECT g.country_key, g.country, r.region_name AS region, r.is_domestic
FROM core.dim_country_snow AS g
INNER JOIN core.dim_region AS r ON r.region_key = g.region_key;