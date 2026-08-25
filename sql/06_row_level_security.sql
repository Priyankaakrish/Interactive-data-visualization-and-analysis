/* =============================================================================
   06_row_level_security.sql
   Row-level security, enforced by PostgreSQL.

   A regional analyst may query the warehouse directly, but must only ever see
   their own market. Not "the report filters it out" - the database refuses to
   return the rows.

   THREE BUGS THIS FILE EXISTS TO AVOID, each of which produced a policy that
   looked correct and was not:

   1. A per-row policy function never returns. USING (fn(country_key)) is
      evaluated once per row, each call joining three tables. On 1,026,355 rows
      the query hung. The fix is a policy expression independent of the row: a
      set-returning function evaluated ONCE per statement as an InitPlan, so
      the per-row cost becomes a hashed IN. Never -> 250 ms.

   2. SECURITY DEFINER hides the caller. Inside such a function current_user is
      the function OWNER, so every analyst resolved to "postgres" and saw
      nothing. The caller's role must be passed IN as an argument.

   3. EXECUTE is granted to PUBLIC by default. Revoking a function from one
      role changes nothing. Until it was revoked from PUBLIC, any analyst could
      call set_current_user('cfo@...') and read every market.

   EXECUTION ORDER: last, after the analytics views exist.
   ========================================================================== */

CREATE SCHEMA IF NOT EXISTS security;

CREATE TABLE IF NOT EXISTS security.user_access (
    user_email   TEXT PRIMARY KEY,
    display_name TEXT,
    job_title    TEXT,
    access_scope TEXT NOT NULL CHECK (access_scope IN ('GLOBAL','REGION','COUNTRY')),
    region_name  TEXT,
    country      TEXT,
    is_active    BOOLEAN NOT NULL DEFAULT TRUE,
    db_role      TEXT,
    granted_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_scope_qualifier CHECK (
        (access_scope='GLOBAL'  AND region_name IS NULL AND country IS NULL)
     OR (access_scope='REGION'  AND region_name IS NOT NULL)
     OR (access_scope='COUNTRY' AND country IS NOT NULL))
);

TRUNCATE security.user_access;
INSERT INTO security.user_access
 (user_email,display_name,job_title,access_scope,region_name,country,db_role) VALUES
 ('cfo@retailco.com','Priya Sharma','Chief Financial Officer','GLOBAL',NULL,NULL,'bi_cfo'),
 ('head.analytics@retailco.com','Alex Doyle','Head of Analytics','GLOBAL',NULL,NULL,NULL),
 ('uk.analyst@retailco.com','Sam Whitfield','UK Market Analyst','COUNTRY',NULL,'United Kingdom','bi_uk'),
 ('eu.analyst@retailco.com','Lena Fischer','Europe Market Analyst','REGION','Europe',NULL,'bi_eu'),
 ('export.analyst@retailco.com','Rui Almeida','Export Market Analyst','REGION','Rest of World',NULL,NULL),
 ('eire.analyst@retailco.com','Niamh Byrne','EIRE Account Manager','COUNTRY',NULL,'EIRE',NULL);

/* Identity: (1) the database login, unspoofable. (2) the session context, but
   ONLY for the pooled application role. Takes the role as an ARGUMENT - bug 2. */
CREATE OR REPLACE FUNCTION security.resolve_email(p_db_role TEXT)
RETURNS TEXT LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = security, pg_catalog, pg_temp
AS $$
    SELECT COALESCE(
        (SELECT ua.user_email FROM security.user_access AS ua
          WHERE ua.db_role = p_db_role AND ua.is_active LIMIT 1),
        CASE WHEN pg_has_role(p_db_role,'bi_app','MEMBER')
             THEN NULLIF(current_setting('app.current_user_email', TRUE),'') END,
        '')
$$;

CREATE OR REPLACE FUNCTION security.current_email()
RETURNS TEXT LANGUAGE sql STABLE
AS $$ SELECT security.resolve_email(current_user::text) $$;

CREATE OR REPLACE FUNCTION security.set_current_user(p_email TEXT)
RETURNS TEXT LANGUAGE plpgsql AS $$
BEGIN
    PERFORM set_config('app.current_user_email', COALESCE(p_email,''), FALSE);
    RETURN security.current_email();
END $$;

/* Set-returning, no row reference: evaluated once per statement - bug 1. */
CREATE OR REPLACE FUNCTION security.visible_country_keys(p_db_role TEXT)
RETURNS SETOF BIGINT LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = core, security, pg_catalog, pg_temp
AS $$
    SELECT c.country_key::bigint
    FROM security.user_access        AS ua
    CROSS JOIN core.dim_country_snow AS c
    INNER JOIN core.dim_region       AS r ON r.region_key = c.region_key
    WHERE ua.user_email = security.resolve_email(p_db_role)
      AND ua.is_active
      AND ( ua.access_scope='GLOBAL'
         OR (ua.access_scope='REGION'  AND ua.region_name = r.region_name)
         OR (ua.access_scope='COUNTRY' AND ua.country     = c.country) )
$$;

ALTER TABLE core.fact_sales ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.fact_sales NO FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS p_fact_sales_country_scope ON core.fact_sales;
CREATE POLICY p_fact_sales_country_scope ON core.fact_sales
    FOR SELECT TO PUBLIC
    USING (country_key IN (SELECT security.visible_country_keys(current_user::text)));

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='bi_reader') THEN
        CREATE ROLE bi_reader NOLOGIN; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='bi_etl') THEN
        CREATE ROLE bi_etl NOLOGIN BYPASSRLS; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='bi_app') THEN
        CREATE ROLE bi_app NOLOGIN; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='bi_uk')  THEN
        CREATE ROLE bi_uk  NOLOGIN IN ROLE bi_reader; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='bi_eu')  THEN
        CREATE ROLE bi_eu  NOLOGIN IN ROLE bi_reader; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='bi_cfo') THEN
        CREATE ROLE bi_cfo NOLOGIN IN ROLE bi_reader; END IF;
END $$;

/* Revoke from PUBLIC FIRST - bug 3. Without this every grant below is
   decorative, because PostgreSQL already gave EXECUTE to everyone. */
REVOKE EXECUTE ON FUNCTION security.set_current_user(TEXT)     FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION security.resolve_email(TEXT)        FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION security.visible_country_keys(TEXT) FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA security                    FROM PUBLIC;

GRANT USAGE ON SCHEMA core, analytics, monitoring TO bi_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA core, analytics, monitoring TO bi_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA analytics GRANT SELECT ON TABLES TO bi_reader;

GRANT USAGE ON SCHEMA security TO bi_reader, bi_app;
GRANT EXECUTE ON FUNCTION security.current_email()            TO bi_reader, bi_app;
GRANT EXECUTE ON FUNCTION security.resolve_email(TEXT)        TO bi_reader, bi_app;
GRANT EXECUTE ON FUNCTION security.visible_country_keys(TEXT) TO bi_reader, bi_app;
GRANT EXECUTE ON FUNCTION security.set_current_user(TEXT)     TO bi_app;
GRANT SELECT ON ALL TABLES IN SCHEMA core, analytics, monitoring TO bi_app;

GRANT USAGE ON SCHEMA core, analytics, monitoring, security TO bi_etl;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA core, monitoring TO bi_etl;

/* A view executes with its OWNER's privileges by default, so RLS on the base
   table is evaluated as the owner and the caller's policy is ignored. Without
   these lines every policy above is bypassed by anyone reading analytics. */
ALTER VIEW analytics.vw_sales_enriched          SET (security_invoker = true);
ALTER VIEW analytics.vw_kpi_sales_monthly       SET (security_invoker = true);
ALTER VIEW analytics.vw_kpi_product_performance SET (security_invoker = true);
ALTER VIEW analytics.vw_kpi_country_performance SET (security_invoker = true);
ALTER VIEW analytics.vw_kpi_customer_rfm        SET (security_invoker = true);
ALTER VIEW analytics.vw_kpi_cohort_retention    SET (security_invoker = true);
ALTER VIEW analytics.vw_kpi_returns_monthly     SET (security_invoker = true);
ALTER VIEW analytics.vw_kpi_basket              SET (security_invoker = true);
ALTER VIEW analytics.vw_kpi_executive_summary   SET (security_invoker = true);

CREATE OR REPLACE VIEW security.vw_access_matrix AS
SELECT ua.user_email, ua.display_name, ua.job_title, ua.access_scope,
       COALESCE(ua.region_name, ua.country, 'all markets') AS scope_value,
       ua.is_active,
       (SELECT COUNT(*) FROM core.dim_country_snow c
          JOIN core.dim_region r ON r.region_key = c.region_key
         WHERE ua.access_scope='GLOBAL'
            OR (ua.access_scope='REGION'  AND r.region_name = ua.region_name)
            OR (ua.access_scope='COUNTRY' AND c.country     = ua.country)
       ) AS countries_visible
FROM security.user_access AS ua;