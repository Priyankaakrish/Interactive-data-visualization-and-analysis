"""Monitoring - the final layer of the architecture.

Every pipeline execution opens a run record before it does any work and closes
it on the way out, whether it succeeded or failed. That ordering matters: a run
that crashes mid-load still leaves a FAILED row behind, so a silent failure is
impossible to confuse with "never ran".

What gets persisted to PostgreSQL:

    monitoring.pipeline_run   one row per execution: timing, volumes, status
    monitoring.table_load     rows written per table, for drift detection
    monitoring.dq_result      every validation result, for trending
    monitoring.cleansing_log  what cleaning decided, and why

Because validation happens before the load, these tables are the *only* record
of the defects that were caught. Without them the evidence would exist solely
in a console log that nobody reads twice.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd
import sqlalchemy as sa
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)


def new_run_id() -> str:
    """Sortable, human-readable run identifier."""
    return f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"


@dataclass
class RunRecord:
    """In-flight state for one pipeline execution."""

    run_id: str
    started_at: datetime
    status: str = "RUNNING"
    source_name: str | None = None
    source_bytes: int | None = None
    is_real_dataset: bool | None = None
    rows_ingested: int = 0
    rows_loaded: int = 0
    rows_quarantined: int = 0
    checks_run: int = 0
    checks_failed: int = 0
    error_message: str | None = None
    data_min_date: object = None
    data_max_date: object = None
    finished_at: datetime | None = None
    duration_seconds: float | None = None
    table_loads: list[dict] = field(default_factory=list)

    def finish(self, status: str, error: str | None = None) -> None:
        self.finished_at = datetime.now(timezone.utc)
        self.duration_seconds = round(
            (self.finished_at - self.started_at).total_seconds(), 2
        )
        self.status = status
        self.error_message = error

    def to_row(self) -> dict:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": self.duration_seconds,
            "status": self.status,
            "source_name": self.source_name,
            "source_bytes": self.source_bytes,
            "is_real_dataset": self.is_real_dataset,
            "rows_ingested": self.rows_ingested,
            "rows_loaded": self.rows_loaded,
            "rows_quarantined": self.rows_quarantined,
            "checks_run": self.checks_run,
            "checks_failed": self.checks_failed,
            "error_message": self.error_message,
            "data_min_date": self.data_min_date,
            "data_max_date": self.data_max_date,
        }


# --------------------------------------------------------------------------
def open_run(engine: Engine, record: RunRecord) -> None:
    """Insert the RUNNING row before any work begins."""
    with engine.begin() as conn:
        conn.execute(sa.text("""
            INSERT INTO monitoring.pipeline_run
                (run_id, started_at, status, source_name, source_bytes,
                 is_real_dataset)
            VALUES (:run_id, :started_at, :status, :source_name, :source_bytes,
                    :is_real_dataset)
            ON CONFLICT (run_id) DO NOTHING
        """), {
            "run_id": record.run_id,
            "started_at": record.started_at,
            "status": "RUNNING",
            "source_name": record.source_name,
            "source_bytes": record.source_bytes,
            "is_real_dataset": record.is_real_dataset,
        })


def close_run(engine: Engine, record: RunRecord) -> None:
    """Update the run row with the final outcome."""
    with engine.begin() as conn:
        conn.execute(sa.text("""
            UPDATE monitoring.pipeline_run SET
                finished_at      = :finished_at,
                duration_seconds = :duration_seconds,
                status           = :status,
                source_name      = :source_name,
                source_bytes     = :source_bytes,
                is_real_dataset  = :is_real_dataset,
                rows_ingested    = :rows_ingested,
                rows_loaded      = :rows_loaded,
                rows_quarantined = :rows_quarantined,
                checks_run       = :checks_run,
                checks_failed    = :checks_failed,
                error_message    = :error_message,
                data_min_date    = :data_min_date,
                data_max_date    = :data_max_date
            WHERE run_id = :run_id
        """), record.to_row())
    log.info("run %s closed: %s", record.run_id, record.status)


def record_table_loads(engine: Engine, run_id: str, manifest: pd.DataFrame) -> None:
    if manifest.empty:
        return
    rows = [
        {"run_id": run_id, "table_name": r.table_name,
         "rows_loaded": int(r.rows_loaded), "columns": int(r.columns)}
        for r in manifest.itertuples()
    ]
    with engine.begin() as conn:
        conn.execute(sa.text("""
            INSERT INTO monitoring.table_load
                (run_id, table_name, rows_loaded, columns)
            VALUES (:run_id, :table_name, :rows_loaded, :columns)
            ON CONFLICT (run_id, table_name) DO UPDATE
                SET rows_loaded = EXCLUDED.rows_loaded
        """), rows)


def record_dq_results(engine: Engine, run_id: str, results: pd.DataFrame) -> None:
    if results.empty:
        return
    df = results.copy()
    df["run_id"] = run_id
    cols = ["run_id", "run_ts", "check_name", "check_category", "target_table",
            "severity", "failed_rows", "total_rows", "fail_rate_pct", "passed",
            "details"]
    df[cols].to_sql("dq_result", engine, schema="monitoring",
                    if_exists="append", index=False, method="multi", chunksize=500)


def record_cleansing_log(engine: Engine, run_id: str, log_df: pd.DataFrame) -> None:
    if log_df.empty:
        return
    df = log_df.reset_index(drop=True).copy()
    df.insert(0, "step_order", df.index + 1)
    df.insert(0, "run_id", run_id)
    df = df.rename(columns={"Step": "step", "RowsAffected": "rows_affected",
                            "Decision": "decision", "Note": "note"})
    df[["run_id", "step_order", "step", "rows_affected", "decision", "note"]].to_sql(
        "cleansing_log", engine, schema="monitoring",
        if_exists="append", index=False, method="multi",
    )


# --------------------------------------------------------------------------
def evaluate_drift(engine: Engine, run_id: str, threshold_pct: float) -> pd.DataFrame:
    """Compare this run's volumes with the previous successful run."""
    df = pd.read_sql(sa.text("""
        SELECT table_name, rows_loaded, prev_rows_loaded, row_delta,
               drift_pct, drift_status
        FROM monitoring.vw_row_drift
        WHERE run_id = :run_id
        ORDER BY table_name
    """), engine, params={"run_id": run_id})
    if df.empty:
        return df
    # PostgreSQL NUMERIC arrives as Decimal, and drift_pct is NULL on the first
    # run of a table - coerce both into plain floats before comparing.
    for col in ("rows_loaded", "prev_rows_loaded", "row_delta", "drift_pct"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["exceeds_threshold"] = df["drift_pct"].abs().fillna(0) > threshold_pct
    return df


def health(engine: Engine) -> pd.DataFrame:
    return pd.read_sql("SELECT * FROM monitoring.vw_health", engine)


def run_history(engine: Engine, limit: int = 20) -> pd.DataFrame:
    return pd.read_sql(sa.text("""
        SELECT * FROM monitoring.vw_run_history
        ORDER BY started_at DESC LIMIT :lim
    """), engine, params={"lim": limit})


def dq_trend(engine: Engine) -> pd.DataFrame:
    return pd.read_sql(
        "SELECT * FROM monitoring.vw_dq_trend ORDER BY started_at, check_category",
        engine,
    )


def freshness(engine: Engine) -> pd.DataFrame:
    return pd.read_sql("SELECT * FROM monitoring.vw_freshness", engine)
