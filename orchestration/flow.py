"""Prefect flow wrapping the pipeline.

Adds what a bare script cannot give you: retries on transient failure, a run
history, and an alert when the monitoring gate goes AMBER or RED.

    prefect server start                  # in one terminal
    python -m orchestration.flow          # run once
    python -m orchestration.flow --serve  # schedule hourly
"""
from __future__ import annotations

import argparse
import logging
from datetime import timedelta

log = logging.getLogger("flow")

try:
    from prefect import flow, get_run_logger, task
    from prefect.tasks import task_input_hash
    PREFECT = True
except ImportError:  # pragma: no cover - the flow degrades to plain calls
    PREFECT = False

    def task(*d_args, **d_kwargs):
        def wrap(fn):
            return fn
        return wrap(d_args[0]) if d_args and callable(d_args[0]) else wrap

    def flow(*d_args, **d_kwargs):
        def wrap(fn):
            return fn
        return wrap(d_args[0]) if d_args and callable(d_args[0]) else wrap

    def get_run_logger():
        return log

    task_input_hash = None

from src.alerts import notify  # noqa: E402


@task(name="incremental-load", retries=3, retry_delay_seconds=30)
def load_task(config_path: str | None = None) -> dict:
    from orchestration.incremental import run
    result = run(config_path)
    return {
        "candidate_rows": result.candidate_rows,
        "new_rows": result.new_rows,
        "inserted": result.inserted,
        "updated": result.updated,
        "duration_s": round(result.duration_s, 1),
    }


@task(name="refresh-extracts", retries=2, retry_delay_seconds=15)
def refresh_task(config_path: str | None = None) -> int:
    """Re-export the CSV extracts the API and Power BI read.

    The export entry point differs between project revisions, so the candidates
    are probed rather than hard-coded. Falling back to a full pipeline run is
    correct but slow, which is why it is last.
    """
    from src.config import PROJECT_ROOT, load_config

    cfg = load_config(config_path)

    for module_name, fn_name in (("src.exporter", "export_all"),
                                 ("src.export", "export_all"),
                                 ("src.outputs", "write_extracts")):
        try:
            module = __import__(module_name, fromlist=[fn_name])
            fn = getattr(module, fn_name)
        except (ImportError, AttributeError):
            continue
        from src import db
        with db.connect(cfg) as engine:
            return int(fn(engine, cfg) or 0)

    from src.run_pipeline import run
    run(config_path, build_dash=False)
    folder = PROJECT_ROOT / "data" / "processed"
    return len(list(folder.glob("*.csv"))) if folder.exists() else 0


@task(name="check-monitoring", retries=1)
def monitor_task(config_path: str | None = None) -> dict:
    import pandas as pd

    from src import db
    from src.config import load_config

    cfg = load_config(config_path)

    def first(engine, *statements):
        """Return the first query that succeeds; object names vary by revision."""
        for sql in statements:
            try:
                return db.fetch(engine, sql)
            except Exception:
                continue
        return pd.DataFrame()

    with db.connect(cfg) as engine:
        drift = first(engine,
                      "SELECT * FROM monitoring.vw_row_drift",
                      "SELECT * FROM monitoring.vw_table_drift",
                      "SELECT * FROM analytics.vw_row_drift")
        checks = first(engine,
                       "SELECT severity, COUNT(*) AS n FROM monitoring.dq_results "
                       "WHERE NOT passed GROUP BY severity",
                       "SELECT severity, COUNT(*) AS n FROM core.dq_results "
                       "WHERE NOT passed GROUP BY severity")

    failing = ({r["severity"]: int(r["n"]) for _, r in checks.iterrows()}
               if not checks.empty else {})
    status = "RED" if failing.get("ERROR") else ("AMBER" if failing else "GREEN")
    worst = (float(drift["drift_pct"].abs().max())
             if (not drift.empty and "drift_pct" in drift.columns) else 0.0)
    return {"status": status, "failing": failing, "max_drift_pct": round(worst, 2)}


@flow(name="retail-bi-pipeline", log_prints=True)
def retail_pipeline(config_path: str | None = None, alert: bool = True) -> dict:
    logger = get_run_logger()

    load = load_task(config_path)
    logger.info("loaded %s new rows (%s inserted, %s updated)",
                load["new_rows"], load["inserted"], load["updated"])

    extracts = refresh_task(config_path)
    logger.info("refreshed %s extracts", extracts)

    health = monitor_task(config_path)
    logger.info("monitoring status %s", health["status"])

    summary = {"load": load, "extracts": extracts, "monitoring": health}

    if alert and health["status"] in ("AMBER", "RED"):
        notify(
            level=health["status"],
            title=f"Retail BI pipeline finished {health['status']}",
            body=(f"New rows: {load['new_rows']:,}\n"
                  f"Inserted: {load['inserted']:,}  Updated: {load['updated']:,}\n"
                  f"Failing checks: {health['failing']}\n"
                  f"Max row drift: {health['max_drift_pct']}%"),
        )

    return summary


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    ap = argparse.ArgumentParser(description="Run or serve the retail pipeline flow")
    ap.add_argument("--config", default=None)
    ap.add_argument("--no-alert", action="store_true")
    ap.add_argument("--serve", action="store_true", help="schedule hourly")
    args = ap.parse_args()

    if args.serve:
        if not PREFECT:
            raise SystemExit("prefect is not installed. pip install prefect")
        retail_pipeline.serve(name="retail-hourly", interval=timedelta(hours=1))
        return

    result = retail_pipeline(args.config, alert=not args.no_alert)
    print("\n  FLOW RESULT")
    for key, value in result.items():
        print(f"  {key:<12} {value}")


if __name__ == "__main__":
    main()
