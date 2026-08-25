/* =============================================================================
   02_constraints.sql
   Keys, constraints and indexes applied AFTER the Python load.

   The load writes with pandas.to_sql, which creates untyped, unconstrained
   tables. Adding the constraints here means the database independently
   re-checks what the Python validation layer asserted: if a foreign key fails
   at this point, the two layers disagree and the run should not be trusted.
   ========================================================================== */

/* ------------------------------------------------------------ primary keys */
ALTER TABLE core.dim_date     ADD CONSTRAINT pk_dim_date     PRIMARY KEY (date_key);
ALTER TABLE core.dim_product  ADD CONSTRAINT pk_dim_product  PRIMARY KEY (product_key);
ALTER TABLE core.dim_customer ADD CONSTRAINT pk_dim_customer PRIMARY KEY (customer_key);
ALTER TABLE core.dim_country  ADD CONSTRAINT pk_dim_country  PRIMARY KEY (country_key);
ALTER TABLE core.fact_sales   ADD CONSTRAINT pk_fact_sales   PRIMARY KEY (invoice_line_key);

/* ------------------------------------------------------- natural-key unique */
ALTER TABLE core.dim_product  ADD CONSTRAINT uq_dim_product_stock_code
    UNIQUE (stock_code);
ALTER TABLE core.dim_customer ADD CONSTRAINT uq_dim_customer_natural
    UNIQUE (customer_id);
ALTER TABLE core.dim_country  ADD CONSTRAINT uq_dim_country_natural
    UNIQUE (country);

/* ------------------------------------------------------------ foreign keys */
ALTER TABLE core.fact_sales ADD CONSTRAINT fk_fact_date
    FOREIGN KEY (date_key)    REFERENCES core.dim_date(date_key);
ALTER TABLE core.fact_sales ADD CONSTRAINT fk_fact_product
    FOREIGN KEY (product_key) REFERENCES core.dim_product(product_key);
ALTER TABLE core.fact_sales ADD CONSTRAINT fk_fact_country
    FOREIGN KEY (country_key) REFERENCES core.dim_country(country_key);
/* customer_key is nullable by design - guest checkouts have no customer. */
ALTER TABLE core.fact_sales ADD CONSTRAINT fk_fact_customer
    FOREIGN KEY (customer_key) REFERENCES core.dim_customer(customer_key);

/* ------------------------------------------------------------- check rules */
ALTER TABLE core.fact_sales ADD CONSTRAINT ck_fact_quantity_nonzero
    CHECK (quantity <> 0);
ALTER TABLE core.fact_sales ADD CONSTRAINT ck_fact_price_positive
    CHECK (unit_price > 0 OR is_service_line);
ALTER TABLE core.fact_sales ADD CONSTRAINT ck_fact_revenue_consistent
    CHECK (abs(line_revenue - (quantity * unit_price)) <= 0.01);
/* A credit note must reverse value on product lines. Service lines are exempt:
   the real dataset posts a manual adjustment (stock code M, +GBP 373.57) onto
   credit invoice C496350, which is ordinary bookkeeping rather than a corrupt
   row. This constraint and the Python rule of the same name must agree - when
   they did not, this is the constraint that caught it. */
ALTER TABLE core.fact_sales ADD CONSTRAINT ck_fact_cancellation_sign
    CHECK (NOT is_cancellation OR line_revenue <= 0 OR is_service_line);

/* ---------------------------------------------------------------- indexes */
CREATE INDEX ix_fact_date_key    ON core.fact_sales (date_key);
CREATE INDEX ix_fact_product_key ON core.fact_sales (product_key);
CREATE INDEX ix_fact_customer    ON core.fact_sales (customer_key)
    WHERE customer_key IS NOT NULL;
CREATE INDEX ix_fact_country     ON core.fact_sales (country_key);
CREATE INDEX ix_fact_invoice     ON core.fact_sales (invoice_no);
/* Most analysis excludes cancellations and service lines; index the common path. */
CREATE INDEX ix_fact_net_sales   ON core.fact_sales (date_key, product_key)
    WHERE NOT is_cancellation AND NOT is_service_line;

CREATE INDEX ix_dim_date_month   ON core.dim_date (year_month);
CREATE INDEX ix_dim_cust_cohort  ON core.dim_customer (cohort_month);

ANALYZE core.fact_sales;
ANALYZE core.dim_product;
ANALYZE core.dim_customer;
