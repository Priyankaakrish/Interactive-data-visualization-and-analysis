# Online Retail II â€” BI Platform

## Architecture

![Pipeline](docs/pipeline_flow.png)


## Architecture



An end-to-end business intelligence pipeline on the UCI **Online Retail II**
dataset: 1,067,371 real transactions from a UK online gift retailer, December
2009 to December 2011.

Pandas cleans and validates, PostgreSQL stores and aggregates, a reusable chart
library and Power BI present, and a monitoring layer reports whether any of it
can be trusted.

```
Dataset â†’ Python/Pandas â†’ Cleaning & Validation â†’ PostgreSQL â†’ SQL Analytics
                                                       â†“
                                    Visualization Library + Power BI/Tableau
                                                       â†“
                                              Monitoring Dashboard
```

## Quick start

```bash
pip install -r requirements.txt
python tools/generate_sample_retail.py    # or drop the real file in data/raw/
python -m src.run_pipeline                # full pipeline, ~30s
pytest -q                                 # 28 tests
```

No database installation required â€” `database.mode: embedded` in `config.yaml`
starts a self-contained PostgreSQL 16 via `pgserver`. To use your own server,
set `mode: external` and point `dsn` at it, or export `DATABASE_URL`.

**Outputs** land in `outputs/`: an interactive business dashboard, a monitoring
dashboard, and a formatted Excel workbook. `data/processed/` holds every
analytics view as CSV, which Power BI and Tableau can read as a folder source.

## Using the real dataset

Download `online_retail_II.xlsx` (43.5 MB) from
[UCI](https://archive.ics.uci.edu/dataset/502/online+retail+ii) and drop it into
`data/raw/`. The pipeline detects it automatically â€” both worksheets are read
and stacked, and nothing else changes.

Or fetch it programmatically with the maintainers' own package:

```python
from ucimlrepo import fetch_ucirepo
fetch_ucirepo(id=502).data.original.to_csv("data/raw/online_retail_II.csv", index=False)
```

Until then, `tools/generate_sample_retail.py` produces a schema-identical
extract that reproduces the dataset's actual defect profile â€” cancellations,
service codes, guest checkouts, duplicates, junk descriptions, decimal slips â€”
so the cleaning and validation layers get a real workout on a fresh clone.

## What makes this more than a chart exercise

**Revenue is defined three ways, on purpose.** This dataset records cancellations
as `C`-prefix invoices with negative quantities. Most published analyses simply
delete them and report gross revenue as though it were net. Here
`gross_revenue`, `returns_value`, `net_revenue` and `service_revenue` are
separate, named, and computed once in SQL â€” a roughly 2% difference that no
chart can quietly get wrong.

**Cleaning makes decisions, not deletions.** Guest checkouts (~22% of rows) are
kept for revenue and excluded from RFM. Postage and fees are flagged, not
dropped. Warehouse annotations are quarantined *with a reason* to
`data/processed/quarantine.csv`. Every decision is logged to
`monitoring.cleansing_log`.

**Validation gates the database.** 24 rules run before the load. An
ERROR-severity failure raises and the load never happens, so PostgreSQL only
ever contains data that passed. Constraints are then applied after the load so
the database independently re-checks what Python asserted â€” if they disagree,
the run is wrong.

**The pipeline reports on itself.** Because validation runs before the load, the
database would otherwise hold no evidence of what was caught. The `monitoring`
schema fixes that: run history, quality trend, row-count drift and a
RED/AMBER/GREEN health view, surfaced on their own dashboard.

## Layout

```
sql/     01 schemas Â· 02 constraints Â· 03 analytics views Â· 04 monitoring views
src/     config ingest clean validate db analytics monitoring
         viz_library dashboard build_dashboards export run_pipeline
tools/   generate_sample_retail.py
powerbi/ measures.dax Â· MODEL.md
docs/    ARCHITECTURE.md
tests/   test_pipeline.py
data/    raw/ (input) Â· processed/ (validated views, BI folder source)
outputs/ retail_dashboard.html Â· monitoring_dashboard.html Â· Retail_KPI_Workbook.xlsx
```

## Dashboards

**Business** â€” Executive, Product, Country, Customer, Cohort, Returns, Basket.
Revenue trend and gross-to-net bridge, Pareto and ABC concentration, export
markets charted separately from the UK (which is ~90% of revenue and flattens
everything it shares an axis with), RFM segments, and a cohort retention matrix.

**Monitoring** â€” Health, Data Quality, Volume Drift, Cleansing Trail. Run
history and duration, pass rates by category and severity, drift against the
previous successful run, and the full audit trail of what cleaning did.

## Configuration

`config.yaml` controls the source path, database mode and DSN, schema names,
cleaning rules (service codes, junk-description patterns, whether to keep guest
checkouts), validation thresholds, monitoring tolerances, KPI parameters and the
visual theme. No code edits are needed to repoint, retune or restyle.

## A note on scope

Online Retail II contains no cost of goods, so there is no margin analysis here
and none has been fabricated. Profitability is approached through returns,
basket economics and product concentration â€” all real, all derivable from the
source. See `docs/ARCHITECTURE.md` for the reasoning.

---

Data: Chen, D. (2012). *Online Retail II* [Dataset]. UCI Machine Learning
Repository. <https://doi.org/10.24432/C5CG6D> â€” CC BY 4.0.

Update README.md with corrections. Do not invent anything; use exactly what I give you.

1) In section 14, change "Both call the same underlying functions" to
   "Both call into orchestration/incremental.py".

2) Add a new subsection 14.1 "What running it actually proved":
   The scheduling machinery is verified — the DAG fired on its own hourly
   interval, max_active_runs=1 held a manual trigger behind the scheduled run,
   the task retried with exponential backoff, downstream tasks stayed blocked on
   upstream failure, and pipeline_summary still reported via TriggerRule.ALL_DONE.

   orchestration/incremental.py had never been executed before that. Running it
   under a real scheduler surfaced five defects, as a table:
   - ingest.load(cfg) -> no such function; it is load_transactions(cfg) returning a tuple
   - clean.run(raw, cfg) -> no such function; clean_transactions(df, cfg, clog, quarantine)
   - validate.run(fresh, cfg) -> no such function; run_validation(tables: dict, ...) takes the star, not a frame
   - errors[["check_name","failing_rows"]] -> column is failed_rows; the error path would itself have raised
   - inserted/updated unbound -> UnboundLocalError on any empty delta

   Close with: a code path that is imported but never invoked is not tested by
   anything, and "the DAG parses" is not "the DAG works".

3) Add subsection 14.2 "The embedded-database conflict":
   config.yaml runs the batch pipeline against an embedded PostgreSQL started
   in-process by pgserver (ADR-001). That server is a Windows process bound to a
   local socket, so an Airflow task inside WSL cannot reach it, and installing
   pgserver inside Linux would only create a second empty warehouse.

   The orchestrated path therefore uses config.airflow.yaml pointing at the
   Dockerised PostgreSQL on 55432. The cost is two warehouses holding separate
   copies of the same data with nothing keeping them in sync. In production this
   collapses back to one managed database (section 21).

4) In the ADR table, append to ADR-001's driver:
   "cost: unreachable from containerised orchestration, see 14.2"

5) In section 22 limitations, add three bullets:
   - Two warehouses: batch writes to embedded, orchestrated path writes to Docker,
     nothing syncs them. Consequence of ADR-001, not an oversight.
   - build_star surrogate keys on a subset are unverified. The incremental merge
     builds a star from post-watermark rows only. If generated dimension keys
     don't align with existing warehouse keys, the merge inserts wrong keys
     SILENTLY - it will not error. Needs before/after row-count and revenue
     reconciliation before the incremental path can be trusted.
   - Airflow runs on SQLite with SequentialExecutor. Fine for demonstrating
     scheduling; not a topology for parallel task execution.

Then show me a diff before writing.