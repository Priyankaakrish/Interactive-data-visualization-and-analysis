"""Watermark-based incremental loading.

A full refresh re-reads 1,067,371 rows and rebuilds the warehouse in ~138
seconds. That is fine nightly and wasteful hourly. This module loads only rows
newer than the highest invoice date already in the fact table and merges them
on the line key, so a re-run of the same window is a no-op rather than a
duplicate.

    python -m orchestration.incremental            # load new rows only
    python -m orchestration.incremental --dry-run  # report what would load
"""
from __future__ import annotations

import argparse
import logging
import time
from dataclasses import dataclass
from datetime import datetime

import pandas as pd
import sqlalchemy as sa
from src.config import load_config

from src import clean, db, ingest, validate

log = logging.getLogger("incremental")

FACT = "core.fact_sales"
STAGING = "core._stg_fact_sales"


@dataclass
class LoadResult:
    watermark: datetime | None
    candidate_rows: int
    new_rows: int
    inserted: int
    updated: int
    duration_s: float

    def render(self) -> str:
        wm = self.watermark.isoformat(sep=" ") if self.watermark else "none (first load)"
        return (f"  watermark        {wm}\n"
                f"  candidate rows   {self.candidate_rows:,}\n"
                f"  newer than mark  {self.new_rows:,}\n"
                f"  inserted         {self.inserted:,}\n"
                f"  updated          {self.updated:,}\n"
                f"  duration         {self.duration_s:.1f}s")


def read_watermark(engine: sa.Engine) -> datetime | None:
    """Highest invoice date already loaded, or None on an empty warehouse."""
    frame = db.fetch(engine, f"""
        SELECT MAX(d.full_date) AS watermark
        FROM   {FACT} AS f
        INNER  JOIN core.dim_date AS d ON d.date_key = f.date_key
    """)
    if frame.empty or pd.isna(frame["watermark"].iloc[0]):
        return None
    return pd.to_datetime(frame["watermark"].iloc[0]).to_pydatetime()


def merge(engine: sa.Engine, frame: pd.DataFrame) -> tuple[int, int]:
    """Stage the new rows then merge them on the line key.

    UPSERT rather than INSERT because a corrected invoice may be re-delivered
    with the same line key and a different value. Appending would leave both
    versions in the fact table and silently inflate revenue.
    """
    if frame.empty:
        return 0, 0

    before = db.fetch(engine, f"SELECT COUNT(*) AS n FROM {FACT}")["n"].iloc[0]

    with engine.begin() as conn:
        conn.execute(sa.text(f"DROP TABLE IF EXISTS {STAGING}"))
        conn.execute(sa.text(f"CREATE TABLE {STAGING} (LIKE {FACT} INCLUDING DEFAULTS)"))

    frame.to_sql(STAGING.split(".")[1], engine, schema="core",
                 if_exists="append", index=False, method="multi", chunksize=5_000)

    cols = list(frame.columns)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "line_key")
    with engine.begin() as conn:
        conn.execute(sa.text(
            f"INSERT INTO {FACT} ({', '.join(cols)}) "
            f"SELECT {', '.join(cols)} FROM {STAGING} "
            f"ON CONFLICT (line_key) DO UPDATE SET {updates}"))
        conn.execute(sa.text(f"DROP TABLE IF EXISTS {STAGING}"))

    after = db.fetch(engine, f"SELECT COUNT(*) AS n FROM {FACT}")["n"].iloc[0]
    inserted = int(after - before)
    return inserted, int(len(frame) - inserted)


def run(config_path: str | None = None, dry_run: bool = False) -> LoadResult:
    started = time.time()
    cfg = load_config(config_path)

    raw, _provenance = ingest.load_transactions(cfg)
    clog, quarantine = clean.CleansingLog(), clean.Quarantine()
    cleaned = clean.clean_transactions(raw, cfg, clog, quarantine)

    with db.connect(cfg) as engine:
        watermark = read_watermark(engine)
        date_col = "invoice_date" if "invoice_date" in cleaned.columns else "full_date"
        if watermark is not None:
            fresh = cleaned[pd.to_datetime(cleaned[date_col]) > watermark]
        else:
            fresh = cleaned

        log.info("watermark=%s candidates=%d fresh=%d",
                 watermark, len(cleaned), len(fresh))

        if dry_run:
            return LoadResult(watermark, len(cleaned), len(fresh), 0, 0,
                              time.time() - started)

        inserted = updated = 0
        if not fresh.empty:
            # Validation and the fact payload both need the star, not raw rows.
            star = clean.build_star(fresh)

            results = validate.run_validation(
                star,
                fail_on_error=False,
                price_bounds=tuple(cfg.validation.get("price_bounds", (0.001, 10000.0))),
                iqr_multiplier=cfg.validation.get("outlier_iqr_multiplier", 3.0),
            )
            errors = results[(results["severity"] == "ERROR") & (~results["passed"])]
            if len(errors):
                raise RuntimeError(
                    f"incremental load aborted: {len(errors)} ERROR checks failed\n"
                    + errors[["check_name", "failed_rows"]].to_string(index=False))

            fact = star["fact_sales"]
            fact_cols = [c["name"] for c in sa.inspect(engine).get_columns(
                "fact_sales", schema="core")]
            payload = fact[[c for c in fact_cols if c in fact.columns]].copy()
            inserted, updated = merge(engine, payload)

    return LoadResult(watermark, len(cleaned), len(fresh), inserted, updated,
                      time.time() - started)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    ap = argparse.ArgumentParser(description="Incremental load for fact_sales")
    ap.add_argument("--config", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    result = run(args.config, dry_run=args.dry_run)
    print("\n  INCREMENTAL LOAD" + ("  (dry run)" if args.dry_run else ""))
    print(result.render())


if __name__ == "__main__":
    main()
