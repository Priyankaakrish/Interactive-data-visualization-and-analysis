# BI layer: Power BI and Tableau

Both tools connect to the same place — the `analytics` schema in PostgreSQL.
Neither should ever read `core` directly, and certainly not the raw file. The
schema separation is the contract: `core` can be dropped and rebuilt on every
run, while the views in `analytics` keep the same names and columns, so a
refresh cannot silently reshape a report.

## Connecting

**Power BI Desktop.** Get Data → PostgreSQL database. Server `localhost:5432`,
database `retail_bi`. Choose **Import** rather than DirectQuery: this is a fixed
historical extract of about a million rows, it compresses well in VertiPaq, and
import gives you the full DAX surface with no query folding surprises.

Select these objects and nothing else:

| Object | Role in the model |
|---|---|
| `analytics.vw_sales_enriched` | fact, one row per invoice line |
| `core.dim_date` | date dimension — mark as date table |
| `core.dim_product` | product |
| `core.dim_customer` | customer, carries `cohort_month` |
| `core.dim_country` | country and region rollup |
| `analytics.vw_kpi_cohort_retention` | pre-computed retention grid |
| `monitoring.vw_dq_latest` | data-quality results |
| `monitoring.vw_run_history` | pipeline run history |

**Tableau.** Connect → PostgreSQL, then drag `vw_sales_enriched` in as the
primary table and add the dimensions as related tables on the surrogate keys.
Tableau's relationships behave like Power BI's, so the same star holds. Use an
extract, not a live connection, for the same reason.

**Offline alternative.** If a reviewer has no database, `data/processed/` holds
the same views as CSVs. Get Data → Folder points at it and the model builds
identically.

## Relationships

| From | To | Cardinality | Direction | Active |
|---|---|---|---|---|
| `dim_date[date_key]` | `fact[date_key]` | 1:* | single | yes |
| `dim_product[product_key]` | `fact[product_key]` | 1:* | single | yes |
| `dim_country[country_key]` | `fact[country_key]` | 1:* | single | yes |
| `dim_customer[customer_key]` | `fact[customer_key]` | 1:* | single | yes |
| `dim_date[full_date]` | `dim_customer[first_purchase]` | 1:* | single | no |

`customer_key` is deliberately nullable — around a fifth of lines are guest
checkouts with no customer at all. Power BI will surface these under a blank
row on `dim_customer`; leave it visible rather than filtering it out, because
that blank is roughly 20% of revenue and hiding it makes every per-customer
figure quietly wrong.

The inactive relationship on `first_purchase` exists so `New Customers` can be
written with `USERELATIONSHIP` instead of duplicating the date dimension.

Mark `dim_date` as the date table (Table tools → Mark as date table →
`full_date`). Nothing in the time-intelligence section works correctly without
it, and the failure is silent.

## Measures

Load `measures.dax` into a columnless `_Measures` table. It is grouped into
revenue, volume, time intelligence, Pareto, customer, cohort, geography, data
quality, targets, and field parameters.

The section worth reading before building anything: **revenue has three
definitions here**, and they differ by about 2%.

- `Gross Revenue` — product sales, cancellations excluded
- `Returns Value` — credit notes, already negative
- `Net Revenue` — the two added together, and the number to put on an exec page
- `Service Revenue` — postage, carriage and fees, reported separately

Most published analyses of this dataset quietly delete the C-prefix invoices
and report gross as if it were net. Naming all three and using them explicitly
is the difference between a report that is right and one that merely looks
right.

Hide from report view: every `*_key` column, and the raw numeric columns on the
fact that already have a measure equivalent. If a column should never be dragged
onto a visual directly, it should not be visible.

## Report pages

Six pages, matching the generated HTML dashboard exactly, so the two artefacts
cannot drift apart.

**Executive** — KPI cards (net revenue, gross, returns, return rate, orders,
AOV), monthly revenue line with a three-month rolling average, revenue mix by
region, and the gross-to-net waterfall. Drill-through to Product and Country.

**Product** — Pareto combo chart, ABC class summary, return rate versus revenue
scatter sized by volume, and a top-products table with conditional formatting
driven by `KPI Colour Returns`.

**Country** — revenue bars for all markets and a second chart for exports only.
The UK is roughly 90% of revenue, so a single chart with the UK bar on it
compresses every other country into nothing; two charts is not redundancy, it
is the only way the export markets are legible.

**Customer** — RFM segment bars, customer counts by segment, the 5×5 RFM matrix,
and segment economics. "At Risk – High Value" is the intended call to action.

**Cohort** — the retention heat matrix from the SQL view, the average retention
curve, and monthly acquisition volumes.

**Data Quality** — pass rate, failures by category and severity, rows
quarantined, and the run history table. This page is the evidence for the other
five.

## Interactivity

Slicers synced across pages for date, country, region and ABC class.
Cross-filtering left on within a page, switched off between the KPI card strip
and detail visuals so the cards always show page-level context.

Drill-through configured Executive → Product (on product) and Executive →
Country (on country), each with a back button and a `Dynamic Title` measure so
the target page states what it was filtered to.

Report-page tooltips (240×180) showing a mini trend, units and return rate for
whatever product or country is hovered.

Field parameters drive `Dynamic Measure`, so one chart can be repointed at
revenue, units, orders or customers from a slicer — which keeps the page count
down without narrowing what the report can answer.

## Refresh

The pipeline owns the refresh order, and the order is the point:

1. `python -m src.run_pipeline` — validates, loads `core`, rebuilds `analytics`
2. The dataset refresh runs only if the pipeline exited 0

Wire it that way in the gateway or the orchestrator. A failed validation exits
non-zero and must stop the refresh, otherwise the report republishes on stale
data while the monitoring page reports a failure — the worst of both worlds.

Because `core` is dropped and recreated each run, use import mode with a full
refresh. If you move to an incremental warehouse load later, add
`RangeStart`/`RangeEnd` parameters on `date_key` and switch to incremental
refresh with a 12-month window.
