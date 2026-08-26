"""Airflow DAG for the retail BI pipeline.

Runs the incremental load, refreshes the published extracts, checks the
monitoring gate and evaluates the business-rule alerts. Prefect remains in the
repo as an alternative runner; this is the scheduled path.

    airflow dags test retail_bi_incremental 2011-12-09
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.utils.trigger_rule import TriggerRule

# The DAG file usually lives outside the project tree, so make the project
# importable rather than relying on it being pip-installed.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

log = logging.getLogger(__name__)

DEFAULT_ARGS = {
    "owner": "data-engineering",
    "depends_on_past": False,
    # Retries matter here because the failure modes are transient: an embedded
    # PostgreSQL still starting, a locked file, a container mid-restart.
    "retries": 3,
    "retry_delay": timedelta(seconds=30),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=5),
    "execution_timeout": timedelta(minutes=30),
}


def task_incremental_load(**context) -> dict:
    """Load only rows newer than the warehouse watermark."""
    from orchestration.incremental import run

    result = run(os.environ.get("RETAIL_CONFIG"))
    payload = {
        "candidate_rows": result.candidate_rows,
        "new_rows": result.new_rows,
        "inserted": result.inserted,
        "updated": result.updated,
        "duration_s": round(result.duration_s, 1),
    }
    log.info("incremental load: %s", payload)
    context["ti"].xcom_push(key="load", value=payload)
    return payload


def task_refresh_extracts(**context) -> int:
    """Re-export the CSVs the API and Power BI read."""
    import pandas as pd

    from src import db, export
    from src.config import load_config

    cfg = load_config(None)
    wanted = ["executive_summary", "sales_monthly", "product_performance",
              "country_performance", "customer_rfm", "cohort_retention",
              "returns_monthly", "basket"]

    frames: dict[str, pd.DataFrame] = {}
    with db.connect(cfg) as engine:
        for view in wanted:
            try:
                frames[view] = db.fetch_view(engine, "analytics", view)
            except Exception as exc:
                log.warning("view analytics.%s unavailable: %s", view, exc)

    if not frames:
        raise RuntimeError("no analytics views could be read - is the warehouse loaded?")

    processed = cfg.raw.get("paths", {}).get("processed", "data/processed")
    written = export.write_csvs(frames, PROJECT_ROOT / processed)
    log.info("refreshed %d extracts", len(written))
    return len(written)


def task_check_monitoring(**context) -> dict:
    """Read the data-quality gate. Raises only on RED."""
    import pandas as pd

    from src import db
    from src.config import load_config

    cfg = load_config(None)

    def first(engine, *statements) -> pd.DataFrame:
        for sql in statements:
            try:
                return db.fetch(engine, sql)
            except Exception:
                continue
        return pd.DataFrame()

    with db.connect(cfg) as engine:
        checks = first(engine,
                       "SELECT severity, COUNT(*) AS n FROM monitoring.dq_results "
                       "WHERE NOT passed GROUP BY severity",
                       "SELECT severity, COUNT(*) AS n FROM core.dq_results "
                       "WHERE NOT passed GROUP BY severity")

    failing = ({str(r["severity"]): int(r["n"]) for _, r in checks.iterrows()}
               if not checks.empty else {})
    status = "RED" if failing.get("ERROR") else ("AMBER" if failing else "GREEN")

    payload = {"status": status, "failing": failing}
    log.info("monitoring: %s", payload)
    context["ti"].xcom_push(key="monitoring", value=payload)

    if status == "RED":
        raise RuntimeError(f"data quality gate is RED: {failing}")
    return payload


def task_business_alerts(**context) -> dict:
    """Evaluate the commercial alert rules and dispatch whatever fires."""
    from src.business_alerts import run, summary_table

    groups = run(os.environ.get("RETAIL_CONFIG"))
    if not groups:
        log.info("no business alerts firing")
        return {"fired": 0}

    table = summary_table(groups)
    log.info("business alerts:\n%s", table.to_string(index=False))
    return {"fired": int(table["count"].sum()),
            "rules": table["alert_key"].tolist()}


def task_pipeline_summary(**context) -> None:
    """Always runs. Reports whatever the upstream tasks managed to produce."""
    ti = context["ti"]
    load = ti.xcom_pull(task_ids="incremental_load", key="load") or {}
    health = ti.xcom_pull(task_ids="check_monitoring", key="monitoring") or {}

    log.info("run summary | new_rows=%s inserted=%s updated=%s status=%s",
             load.get("new_rows"), load.get("inserted"),
             load.get("updated"), health.get("status", "unknown"))


def on_failure(context) -> None:
    """Route Airflow task failures through the project's own alert sinks."""
    from src.alerts import notify

    ti = context.get("task_instance")
    notify(
        level="RED",
        title=f"Airflow task failed: {getattr(ti, 'task_id', 'unknown')}",
        body=(f"DAG: {getattr(ti, 'dag_id', 'unknown')}\n"
              f"Run: {context.get('run_id')}\n"
              f"Try: {getattr(ti, 'try_number', '?')}\n"
              f"Error: {context.get('exception')}"),
    )


with DAG(
    dag_id="retail_bi_incremental",
    description="Incremental load, extract refresh, quality gate and business alerts",
    default_args=DEFAULT_ARGS,
    start_date=datetime(2024, 1, 1),
    schedule="0 * * * *",
    catchup=False,
    max_active_runs=1,
    tags=["retail", "bi", "incremental"],
    on_failure_callback=on_failure,
) as dag:

    incremental_load = PythonOperator(
        task_id="incremental_load",
        python_callable=task_incremental_load,
    )

    refresh_extracts = PythonOperator(
        task_id="refresh_extracts",
        python_callable=task_refresh_extracts,
        retries=2,
    )

    check_monitoring = PythonOperator(
        task_id="check_monitoring",
        python_callable=task_check_monitoring,
        retries=1,
    )

    business_alerts = PythonOperator(
        task_id="business_alerts",
        python_callable=task_business_alerts,
        retries=1,
        trigger_rule=TriggerRule.ALL_SUCCESS,
    )

    pipeline_summary = PythonOperator(
        task_id="pipeline_summary",
        python_callable=task_pipeline_summary,
        trigger_rule=TriggerRule.ALL_DONE,
    )

    incremental_load >> refresh_extracts >> check_monitoring >> business_alerts
    [incremental_load, check_monitoring, business_alerts] >> pipeline_summary
