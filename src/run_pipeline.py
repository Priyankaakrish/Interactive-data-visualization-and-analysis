"""End-to-end pipeline.

    python -m src.run_pipeline [--config config.yaml] [--no-dashboards]

Stages, matching the project architecture one for one:

    1. Dataset                    read the UCI workbook or the sample extract
    2. Python / Pandas            profile what arrived
    3. Cleaning & Validation      conform, quarantine, then gate on ERROR rules
    4. PostgreSQL                 load the star schema, apply keys and indexes
    5. SQL Analytics              build KPI views, read them back
    6. Visualization library      business dashboard, Excel workbook, CSVs
    7. Monitoring dashboard       run history, quality trend, volume drift

A run record is opened before any work starts and closed on the way out, so a
crash leaves a FAILED row rather than silence.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import analytics, db, export, monitoring
from . import viz_library as viz
from .build_dashboards import build_business_dashboard, build_monitoring_dashboard
from .clean import CleansingLog, Quarantine, build_star, clean_transactions
from .config import PROJECT_ROOT, load_config
from .ingest import load_transactions, profile
from .validate import ValidationError, run_validation, scorecard

log = logging.getLogger("pipeline")
SQL_DIR = PROJECT_ROOT / "sql"


def _banner(step: str) -> None:
    print(f"\n{'=' * 78}\n  {step}\n{'=' * 78}")


def run(config_path: str | None = None, build_dash: bool = True) -> dict:
    t0 = time.time()
    logging.basicConfig(level=logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    cfg = load_config(config_path)
    theme = cfg.theme
    viz.register_template(theme)
    viz.set_currency(cfg.currency)

    core = cfg.schema("core")
    analytics_schema = cfg.schema("analytics")

    with db.connect(cfg) as engine:
        print(f"  PostgreSQL: {db.server_version(engine)}")

        # Schemas and monitoring tables must exist before a run can be recorded.
        db.run_sql_file(engine, SQL_DIR / "01_schemas.sql")

        record = monitoring.RunRecord(
            run_id=monitoring.new_run_id(),
            started_at=datetime.now(timezone.utc),
        )
        monitoring.open_run(engine, record)
        print(f"  run_id: {record.run_id}")

        try:
            # ---------------------------------------------- 1-2. INGEST -----
            _banner("1-2. DATASET -> PYTHON / PANDAS")
            raw, provenance = load_transactions(cfg)
            record.source_name = str(provenance["source"])
            record.source_bytes = int(provenance["bytes"])
            record.is_real_dataset = bool(provenance["is_real_dataset"])
            record.rows_ingested = len(raw)
            record.data_min_date = pd.Timestamp(provenance["date_min"]).date()
            record.data_max_date = pd.Timestamp(provenance["date_max"]).date()

            print(f"  source: {record.source_name} "
                  f"({record.source_bytes / 1e6:.1f} MB, "
                  f"{'real UCI dataset' if record.is_real_dataset else 'generated sample'})")
            print(f"  {len(raw):,} rows, {provenance['date_min']:%Y-%m-%d} to "
                  f"{provenance['date_max']:%Y-%m-%d}\n")
            print(profile(raw).to_string(index=False))

            # ------------------------------------ 3. CLEAN AND VALIDATE -----
            _banner("3. DATA CLEANING & VALIDATION")
            clog, quarantine = CleansingLog(), Quarantine()
            clean = clean_transactions(raw, cfg, clog, quarantine)
            star = build_star(clean)

            actions = clog.to_frame()
            print(actions[actions.RowsAffected > 0].to_string(index=False))

            q_frame = quarantine.to_frame()
            record.rows_quarantined = len(q_frame)
            print(f"\n  quarantined {len(q_frame):,} rows; "
                  f"{len(clean):,} retained for analysis")

            print("\n  validating before load - ERROR failures abort the run")
            results = run_validation(
                star,
                fail_on_error=cfg.validation.get("fail_on_error", True),
                price_bounds=tuple(cfg.validation.get("price_bounds", (0.001, 10000.0))),
                iqr_multiplier=cfg.validation.get("outlier_iqr_multiplier", 3.0),
                max_quantity=cfg.validation.get("max_line_quantity", 80000),
                run_id=record.run_id,
            )
            record.checks_run = len(results)
            record.checks_failed = int((~results["passed"]).sum())
            print(scorecard(results).to_string(index=False))
            failing = results[~results["passed"]]
            print(f"\n  {record.checks_run - record.checks_failed}/{record.checks_run} "
                  f"checks passed"
                  + (f"; WARN only: {', '.join(failing['check_name'])}"
                     if len(failing) else ""))

            # ------------------------------------------- 4. POSTGRESQL ------
            _banner("4. POSTGRESQL")
            with engine.begin() as conn:
                import sqlalchemy as sa
                conn.execute(sa.text(f"DROP SCHEMA IF EXISTS {core} CASCADE"))
                conn.execute(sa.text(f"CREATE SCHEMA {core}"))

            manifest = db.load_star(engine, star, core,
                                    cfg.db.get("load_chunksize", 20000))
            record.rows_loaded = int(manifest["rows_loaded"].sum())
            print(manifest.to_string(index=False))

            db.run_sql_file(engine, SQL_DIR / "02_constraints.sql")
            print("\n  keys, foreign keys, check constraints and indexes applied")
            print("  (the database re-verifies independently what Python asserted)")
            db.run_sql_file(engine, SQL_DIR / "05_snowflake.sql")
            snow = db.fetch(engine, """
                SELECT 'dim_region' AS table_name, COUNT(*) AS rows FROM core.dim_region
                UNION ALL SELECT 'dim_country_snow',  COUNT(*) FROM core.dim_country_snow
                UNION ALL SELECT 'dim_cohort',        COUNT(*) FROM core.dim_cohort
                UNION ALL SELECT 'dim_customer_snow', COUNT(*) FROM core.dim_customer_snow
                UNION ALL SELECT 'dim_year',          COUNT(*) FROM core.dim_year
                UNION ALL SELECT 'dim_month',         COUNT(*) FROM core.dim_month
                UNION ALL SELECT 'dim_date_snow',     COUNT(*) FROM core.dim_date_snow
                UNION ALL SELECT 'dim_product_type',  COUNT(*) FROM core.dim_product_type
                UNION ALL SELECT 'dim_product_snow',  COUNT(*) FROM core.dim_product_snow
                ORDER BY table_name""")
            print("\n  snowflake dimensions built:")
            print(snow.to_string(index=False))

            # --------------------------------------- 5. SQL ANALYTICS -------
            _banner("5. SQL ANALYTICS")
            db.run_sql_file(engine, SQL_DIR / "03_analytics_views.sql")
            db.run_sql_file(engine, SQL_DIR / "04_monitoring_views.sql")
            db.run_sql_file(engine, SQL_DIR / "06_row_level_security.sql")
            print("  row-level security policies applied")

            data = analytics.load_all(engine, analytics_schema)
            for key, frame in data.items():
                print(f"  {key:<24} {len(frame):>8,} rows")

            cards = data["executive_summary"].sort_values("sort_order")
            print()
            for r in cards.itertuples():
                print(f"  {r.metric:<24} {viz.format_value(r.value, r.format):>14}")

            # ------------------------- persist monitoring facts -------------
            monitoring.record_table_loads(engine, record.run_id, manifest)
            monitoring.record_dq_results(engine, record.run_id, results)
            monitoring.record_cleansing_log(engine, record.run_id, actions)

            # ------------------------------------------ 6. PUBLISH ----------
            _banner("6. VISUALIZATION LIBRARY -> BI OUTPUTS")
            processed = cfg.path("processed")
            frames = {k: v for k, v in data.items()}
            frames["dq_results"] = results
            frames["user_access"] = db.fetch(engine, """
                SELECT user_email, display_name, job_title, access_scope,
                       region_name, country, is_active
                FROM security.user_access ORDER BY user_email""")
            frames["cleansing_log"] = actions
            if not q_frame.empty:
                frames["quarantine"] = q_frame
            export.write_csvs(frames, processed)
            print(f"  {len(frames)} CSVs -> {processed.name}/ "
                  "(Power BI / Tableau folder source)")

            workbook = export.write_workbook(
                {
                    "Executive Summary": cards,
                    "Monthly Trend": data["sales_monthly"],
                    "Product Performance": data["product_performance"].head(500),
                    "Country Performance": data["country_performance"],
                    "Customer Segments": analytics.segment_summary(data["customer_rfm"]),
                    "Top Customers": data["customer_rfm"].nlargest(300, "monetary"),
                    "Cohort Retention": data["cohort_retention"],
                    "Returns": data["returns_monthly"],
                    "Data Quality": results,
                    "Cleansing Log": actions,
                },
                cfg.path("reports") / "Retail_KPI_Workbook.xlsx", theme,
            )
            print(f"  Excel workbook          -> {workbook.name}")

            business_path = monitoring_path = None
            if build_dash:
                business_path = build_business_dashboard(cfg, data, provenance)
                print(f"  Business dashboard      -> {business_path.name}")

            # ----------------------------------------- 7. MONITORING --------
            _banner("7. MONITORING")
            record.finish("SUCCESS")
            monitoring.close_run(engine, record)

            drift = monitoring.evaluate_drift(
                engine, record.run_id,
                cfg.monitoring.get("row_count_drift_pct", 20.0))
            health = monitoring.health(engine)
            fresh = monitoring.freshness(engine)
            runs = monitoring.run_history(engine)
            dq_hist = monitoring.dq_trend(engine)
            dq_latest = db.fetch(engine, "SELECT * FROM monitoring.vw_dq_latest")

            print(f"  status:    {health['health_status'].iloc[0]}")
            print(f"  duration:  {record.duration_seconds}s")
            print(f"  runs on record: {len(runs)}")
            if not drift.empty:
                print("\n" + drift[["table_name", "rows_loaded", "prev_rows_loaded",
                                    "drift_pct", "drift_status"]].to_string(index=False))

            if build_dash:
                monitoring_path = build_monitoring_dashboard(
                    cfg, runs, dq_latest, dq_hist, drift, actions, health, fresh)
                print(f"\n  Monitoring dashboard    -> {monitoring_path.name}")

            print(f"\n  Completed in {time.time() - t0:.1f}s")
            return {
                "run_id": record.run_id, "star": star, "data": data,
                "validation": results, "drift": drift, "health": health,
                "business_dashboard": business_path,
                "monitoring_dashboard": monitoring_path,
                "workbook": workbook,
            }

        except ValidationError as exc:
            print(f"\n  LOAD ABORTED - {exc}")
            record.finish("FAILED", str(exc))
            monitoring.close_run(engine, record)
            raise
        except Exception as exc:                       # pragma: no cover
            print(f"\n  PIPELINE FAILED - {type(exc).__name__}: {exc}")
            record.finish("FAILED", f"{type(exc).__name__}: {exc}")
            monitoring.close_run(engine, record)
            raise


def main() -> int:
    ap = argparse.ArgumentParser(description="Online Retail II BI pipeline")
    ap.add_argument("--config", default=None)
    ap.add_argument("--no-dashboards", action="store_true")
    args = ap.parse_args()
    try:
        run(args.config, build_dash=not args.no_dashboards)
    except ValidationError:
        return 2
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
