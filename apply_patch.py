from pathlib import Path
rp = Path("src/run_pipeline.py"); av = Path("sql/03_analytics_views.sql")
for f in (rp, av):
    if not f.exists(): raise SystemExit(f"Not found: {f}")
src = rp.read_text(encoding="utf-8"); changed = []

a1 = 'db.run_sql_file(engine, SQL_DIR / "02_constraints.sql")'
if "05_snowflake.sql" in src:
    print("1. snowflake already wired - skipped")
elif a1 in src:
    i = src.index(a1); eol = src.index("\n", i)
    for _ in range(2):
        nxt = src.find("\n", eol + 1)
        if nxt != -1 and "print(" in src[eol:nxt]: eol = nxt
    block = (
        '\n            db.run_sql_file(engine, SQL_DIR / "05_snowflake.sql")'
        '\n            snow = db.fetch(engine, """'
        "\n                SELECT 'dim_region' AS table_name, COUNT(*) AS rows FROM core.dim_region"
        "\n                UNION ALL SELECT 'dim_country_snow',  COUNT(*) FROM core.dim_country_snow"
        "\n                UNION ALL SELECT 'dim_cohort',        COUNT(*) FROM core.dim_cohort"
        "\n                UNION ALL SELECT 'dim_customer_snow', COUNT(*) FROM core.dim_customer_snow"
        "\n                UNION ALL SELECT 'dim_year',          COUNT(*) FROM core.dim_year"
        "\n                UNION ALL SELECT 'dim_month',         COUNT(*) FROM core.dim_month"
        "\n                UNION ALL SELECT 'dim_date_snow',     COUNT(*) FROM core.dim_date_snow"
        "\n                UNION ALL SELECT 'dim_product_type',  COUNT(*) FROM core.dim_product_type"
        "\n                UNION ALL SELECT 'dim_product_snow',  COUNT(*) FROM core.dim_product_snow"
        '\n                ORDER BY table_name""")'
        '\n            print("\\n  snowflake dimensions built:")'
        '\n            print(snow.to_string(index=False))')
    src = src[:eol] + block + src[eol:]; changed.append("snowflake")
else: print("1. WARNING: anchor not found")

a2 = 'db.run_sql_file(engine, SQL_DIR / "04_monitoring_views.sql")'
if "06_row_level_security.sql" in src:
    print("2. RLS already wired - skipped")
elif a2 in src:
    src = src.replace(a2, a2 +
        '\n            db.run_sql_file(engine, SQL_DIR / "06_row_level_security.sql")'
        '\n            print("  row-level security policies applied")')
    changed.append("RLS")
else: print("2. WARNING: anchor not found")

a3 = 'frames["dq_results"] = results'
if 'frames["user_access"]' in src:
    print("3. user_access already wired - skipped")
elif a3 in src:
    src = src.replace(a3, a3 +
        '\n            frames["user_access"] = db.fetch(engine, """'
        "\n                SELECT user_email, display_name, job_title, access_scope,"
        "\n                       region_name, country, is_active"
        '\n                FROM security.user_access ORDER BY user_email""")')
    changed.append("user_access")
else: print("3. WARNING: anchor not found")

if changed: rp.write_text(src, encoding="utf-8")

v = av.read_text(encoding="utf-8")
old = ("FROM core.fact_sales        AS f\n"
       "INNER JOIN core.dim_date    AS d ON d.date_key    = f.date_key\n"
       "INNER JOIN core.dim_product AS p ON p.product_key = f.product_key\n"
       "INNER JOIN core.dim_country AS g ON g.country_key = f.country_key\n"
       "LEFT  JOIN core.dim_customer AS c ON c.customer_key = f.customer_key;")
new = ("FROM core.fact_sales                  AS f\n"
       "INNER JOIN core.vw_dim_date_flat      AS d ON d.date_key     = f.date_key\n"
       "INNER JOIN core.vw_dim_product_flat   AS p ON p.product_key  = f.product_key\n"
       "INNER JOIN core.vw_dim_country_flat   AS g ON g.country_key  = f.country_key\n"
       "LEFT  JOIN core.vw_dim_customer_flat  AS c ON c.customer_key = f.customer_key;")
if "vw_dim_date_flat" in v: print("4. views already point at snowflake - skipped")
elif old in v: av.write_text(v.replace(old, new), encoding="utf-8"); changed.append("view joins")
else: print("4. WARNING: join block not found")

print("applied:", ", ".join(changed) if changed else "nothing")
import py_compile; py_compile.compile(str(rp), doraise=True)
print("run_pipeline.py compiles cleanly")
