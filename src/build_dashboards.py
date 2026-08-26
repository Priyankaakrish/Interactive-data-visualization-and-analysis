"""Dashboard composition - stage 5.

Two deliverables, both assembled from the same chart library and both reading
only from the PostgreSQL analytics views:

* the business dashboard - what happened, for the people who run the shop
* the monitoring dashboard - whether the numbers can be trusted, for whoever
  owns the pipeline

Keeping them separate matters. A stakeholder should not have to scroll past
row-count drift to find revenue, and an engineer should not have to hunt
through revenue to find a failed load.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import viz_library as viz
from .analytics import abc_summary, cohort_matrix, segment_summary
from .config import Config
from .dashboard import Dashboard


# --------------------------------------------------------------------------
def build_business_dashboard(cfg: Config, data: dict[str, pd.DataFrame],
                             provenance: dict) -> Path:  # noqa: F821
    theme = cfg.theme
    d = Dashboard(
        "Online Retail II - Business Intelligence",
        f"UK online gift retailer &middot; {provenance.get('date_min'):%b %Y} to "
        f"{provenance.get('date_max'):%b %Y} &middot; served from PostgreSQL",
        theme,
    )

    monthly = data["sales_monthly"].sort_values("month_start")
    products = data["product_performance"]
    countries = data["country_performance"]
    rfm = data["customer_rfm"]
    cohorts = data["cohort_retention"]
    returns = data["returns_monthly"].sort_values("month_start")
    basket = data["basket"]
    cards = data["executive_summary"].sort_values("sort_order")

    # ------------------------------------------------------------ Executive
    card_frame = cards.rename(columns={"metric": "Metric", "value": "Value",
                                       "format": "Format"})[["Metric", "Value", "Format"]]
    card_frame["Comparison"] = np.nan
    d.add("Executive", viz.kpi_card_row(card_frame, theme, columns=5))

    d.add("Executive", viz.trend_line(
        monthly, "month_start", "gross_revenue", theme,
        rolling="revenue_rolling_3m",
        title="Monthly revenue with 3-month rolling average"), "w8")

    region_mix = (countries.groupby("region", as_index=False)["gross_revenue"].sum()
                           .sort_values("gross_revenue", ascending=False))
    d.add("Executive", viz.donut(
        region_mix, "region", "gross_revenue", theme,
        title="Revenue by region", centre_label="gross revenue"), "w4")

    gross = float(monthly["gross_revenue"].sum())
    ret = float(abs(monthly["returns_value"].fillna(0).sum()))
    svc = float(monthly["service_revenue"].fillna(0).sum())
    d.add("Executive", viz.waterfall(
        ["Product sales", "Returns", "Postage & fees", "Net revenue"],
        [gross, -ret, svc, gross - ret + svc], theme,
        measures=["relative", "relative", "relative", "total"],
        title="Gross to net revenue bridge"), "w6")

    d.add("Executive", viz.trend_line(
        monthly, "month_start", "avg_order_value", theme,
        title="Average order value"), "w6")

    d.add_note("Executive",
               f"<b>Read:</b> {viz.fmt_currency(gross)} of product sales, less "
               f"{viz.fmt_currency(ret)} of credit notes "
               f"({ret / gross:.1%} return rate), gives "
               f"{viz.fmt_currency(gross - ret + svc)} net. Cancellations are "
               "netted off rather than deleted - dropping them is the most common "
               "error made with this dataset and would overstate revenue by "
               f"{ret / (gross - ret):.1%}.")

    # -------------------------------------------------------------- Product
    top = products.nlargest(20, "gross_revenue")
    d.add("Product", viz.pareto(
        top, "description", "gross_revenue", theme,
        cumulative="cumulative_revenue_share", top_n=20,
        title="Pareto - top 20 products by revenue"))

    abc = abc_summary(products)
    d.add("Product", viz.grouped_bar(
        abc, "abc_class", "revenue", theme, title="Revenue by ABC class"), "w4")

    scatter = products[(products["units_sold"] > 0) & (products["gross_revenue"] > 0)]
    d.add("Product", viz.scatter_bubble(
        scatter.nlargest(400, "gross_revenue"), "gross_revenue", "return_rate_pct",
        "units_sold", theme, hover_name="description",
        title="Return rate vs revenue (bubble = units sold)",
        y_format="number"), "w8")

    d.add("Product", viz.data_table(
        top.head(12)[["description", "gross_revenue", "units_sold",
                      "order_count", "return_rate_pct", "abc_class"]],
        theme, title="Top products",
        formats={"gross_revenue": "currency", "units_sold": "integer",
                 "order_count": "integer"}), "w6")
    d.add("Product", viz.data_table(
        abc, theme, title="ABC concentration",
        formats={"revenue": "currency", "products": "integer",
                 "units": "integer"}), "w6")

    a_row = abc[abc["abc_class"] == "A"]
    if not a_row.empty:
        d.add_note("Product",
                   f"<b>Read:</b> class A is {a_row['product_share_pct'].iloc[0]:.0f}% "
                   f"of the catalogue and {a_row['revenue_share_pct'].iloc[0]:.0f}% of "
                   "revenue. Those SKUs justify guaranteed availability; the C tail "
                   "carries listing and storage cost against very little return.")

    # -------------------------------------------------------------- Country
    intl = countries[~countries["is_domestic"]].nlargest(15, "gross_revenue")
    d.add("Country", viz.grouped_bar(
        countries.nlargest(12, "gross_revenue"), "country", "gross_revenue", theme,
        horizontal=True, title="Revenue by country (all markets)"), "w6")
    d.add("Country", viz.grouped_bar(
        intl, "country", "gross_revenue", theme, horizontal=True,
        title="Export markets only (UK excluded)"), "w6")
    d.add("Country", viz.scatter_bubble(
        countries[countries["order_count"] > 5],
        "gross_revenue", "avg_order_value", "customer_count", theme,
        hover_name="country", colour="region",
        title="Order value vs revenue by country", y_format="number"), "w8")
    d.add("Country", viz.data_table(
        countries.nlargest(12, "gross_revenue")[
            ["country", "region", "gross_revenue", "order_count",
             "avg_order_value", "return_rate_pct"]],
        theme, title="Country detail",
        formats={"gross_revenue": "currency", "order_count": "integer",
                 "avg_order_value": "currency"}), "w4")

    dom = countries[countries["is_domestic"]]["gross_revenue"].sum()
    tot = countries["gross_revenue"].sum()
    d.add_note("Country",
               f"<b>Read:</b> the UK is {dom / tot:.0%} of revenue, so every "
               "blended average is effectively a UK average. Export markets are "
               "worth reading on their own axis, which is why they get a second "
               "chart rather than sharing one where the UK bar flattens everything.")

    # ------------------------------------------------------------- Customer
    if not rfm.empty:
        segments = segment_summary(rfm)
        d.add("Customer", viz.grouped_bar(
            segments, "segment", "revenue", theme, horizontal=True,
            title="Revenue by RFM segment"), "w6")
        d.add("Customer", viz.donut(
            segments, "segment", "customers", theme,
            title="Customers by segment", centre_label="customers"), "w6")
        d.add("Customer", viz.heatmap(
            rfm, "f_score", "r_score", "customer_key", theme, agg="count",
            title="RFM grid - recency vs frequency (cell = customers)"), "w6")
        d.add("Customer", viz.data_table(
            segments, theme, title="Segment economics",
            formats={"revenue": "currency", "customers": "integer",
                     "avg_order_value": "currency",
                     "avg_recency_days": "integer"}), "w6")

        top_seg = segments.iloc[0]
        at_risk = segments[segments["segment"].str.startswith("At Risk")]
        risk_rev = float(at_risk["revenue"].sum()) if not at_risk.empty else 0.0
        d.add_note("Customer",
                   f"<b>Read:</b> <b>{top_seg['segment']}</b> holds "
                   f"{top_seg['revenue_share_pct']:.0f}% of revenue across "
                   f"{int(top_seg['customers']):,} customers. The at-risk segments "
                   f"represent {viz.fmt_currency(risk_rev)} of historic spend with no "
                   "recent orders - the cheapest revenue in the business to win back, "
                   "because these buyers are already qualified.")

    # --------------------------------------------------------------- Cohort
    if not cohorts.empty:
        horizon = cfg.kpi.get("cohort_horizon_months", 12)
        matrix = cohort_matrix(cohorts, horizon)
        long = matrix.reset_index().melt(
            id_vars="cohort_month", var_name="months_since_first",
            value_name="retention_pct").dropna()
        d.add("Cohort", viz.heatmap(
            long, "months_since_first", "cohort_month", "retention_pct", theme,
            agg="first", title="Cohort retention (% of cohort ordering again)",
            value_format="integer"))

        curve = (cohorts[cohorts["months_since_first"].between(0, horizon)]
                 .groupby("months_since_first", as_index=False)["retention_pct"]
                 .mean())
        d.add("Cohort", viz.trend_line(
            curve, "months_since_first", "retention_pct", theme,
            title="Average retention curve", y_format="number"), "w6")

        sizes = (cohorts[cohorts["months_since_first"] == 0]
                 .groupby("cohort_month", as_index=False)["cohort_customers"].max())
        d.add("Cohort", viz.grouped_bar(
            sizes, "cohort_month", "cohort_customers", theme,
            value_format="integer", title="New customers acquired per month"), "w6")

        m1 = curve[curve["months_since_first"] == 1]["retention_pct"]
        if not m1.empty:
            d.add_note("Cohort",
                       f"<b>Read:</b> about {m1.iloc[0]:.0f}% of a cohort orders again "
                       "in the following month. Because this is a wholesale-heavy "
                       "business, retention matters more than acquisition: a cohort "
                       "that survives its first quarter tends to keep reordering.")

    # -------------------------------------------------------------- Returns
    if not returns.empty:
        d.add("Returns", viz.trend_line(
            returns, "month_start", "return_rate_pct", theme,
            title="Return rate by month (%)", y_format="number"), "w6")
        d.add("Returns", viz.grouped_bar(
            returns, "year_month", "returns_value", theme,
            title="Value of credit notes by month"), "w6")
        worst = products[products["units_sold"] > 50].nlargest(12, "return_rate_pct")
        d.add("Returns", viz.grouped_bar(
            worst, "description", "return_rate_pct", theme, horizontal=True,
            value_format="integer",
            title="Highest return rates (products with >50 units sold)"), "w6")
        d.add("Returns", viz.data_table(
            worst[["description", "units_sold", "units_returned",
                   "return_rate_pct", "gross_revenue"]],
            theme, title="Return-rate detail",
            formats={"units_sold": "integer", "units_returned": "integer",
                     "gross_revenue": "currency"}), "w6")
        d.add_note("Returns",
                   "<b>Read:</b> return rate is filtered to products with meaningful "
                   "volume. A single returned unit on a product that sold twice is a "
                   "50% return rate and tells you nothing - the volume filter is what "
                   "makes this chart actionable rather than noisy.")

    # --------------------------------------------------------------- Basket
    if not basket.empty:
        real = basket[~basket["is_cancellation"]]
        d.add("Basket", viz.trend_line(
            real.groupby("year_month", as_index=False)["basket_value"].mean()
                .rename(columns={"basket_value": "avg_basket_value"}),
            "year_month", "avg_basket_value", theme,
            title="Average basket value by month"), "w6")
        dist = real[real["line_count"] <= 60]
        d.add("Basket", viz.grouped_bar(
            dist.groupby("line_count", as_index=False)["invoice_no"].count()
                .rename(columns={"invoice_no": "invoices"}).head(30),
            "line_count", "invoices", theme, value_format="integer",
            title="Lines per invoice"), "w6")
        d.add("Basket", viz.data_table(
            real.nlargest(12, "basket_value")[
                ["invoice_no", "invoice_date", "country", "line_count",
                 "total_units", "basket_value"]],
            theme, title="Largest baskets",
            formats={"basket_value": "currency", "line_count": "integer",
                     "total_units": "integer"}))

    return d.render(cfg.path("reports") / "retail_dashboard.html")


# --------------------------------------------------------------------------
def build_monitoring_dashboard(cfg: Config, runs: pd.DataFrame,
                               dq_latest: pd.DataFrame, dq_history: pd.DataFrame,
                               drift: pd.DataFrame, cleansing: pd.DataFrame,
                               health_row: pd.DataFrame,
                               freshness_row: pd.DataFrame) -> Path:  # noqa: F821
    theme = cfg.theme
    status = (health_row["health_status"].iloc[0]
              if not health_row.empty else "UNKNOWN")

    d = Dashboard(
        "Pipeline Monitoring - Online Retail II",
        f"Current status: <b>{status}</b> &middot; "
        "run history, data quality and volume drift, read from PostgreSQL",
        theme,
    )

    # --------------------------------------------------------------- Health
    if not runs.empty:
        last = runs.iloc[0]
        cards = pd.DataFrame([
            {"Metric": "Rows Ingested", "Value": last["rows_ingested"],
             "Format": "integer", "Comparison": np.nan},
            {"Metric": "Rows Loaded", "Value": last["rows_loaded"],
             "Format": "integer", "Comparison": np.nan},
            {"Metric": "Quarantined", "Value": last["rows_quarantined"],
             "Format": "integer", "Comparison": np.nan},
            {"Metric": "Checks Run", "Value": last["checks_run"],
             "Format": "integer", "Comparison": np.nan},
            {"Metric": "Checks Failed", "Value": last["checks_failed"],
             "Format": "integer", "Comparison": np.nan},
            {"Metric": "Pass Rate", "Value": (last["check_pass_rate_pct"] or 0) / 100,
             "Format": "percent", "Comparison": np.nan},
            {"Metric": "Duration (s)", "Value": last["duration_seconds"],
             "Format": "integer", "Comparison": np.nan},
            {"Metric": "Quarantine Rate", "Value": (last["quarantine_rate_pct"] or 0) / 100,
             "Format": "percent", "Comparison": np.nan},
        ])
        d.add("Health", viz.kpi_card_row(cards, theme, columns=4))

        d.add("Health", viz.data_table(
            runs.head(12)[["run_id", "started_at", "status", "duration_seconds",
                           "rows_ingested", "rows_loaded", "rows_quarantined",
                           "checks_failed"]],
            theme, title="Recent pipeline runs",
            formats={"rows_ingested": "integer", "rows_loaded": "integer",
                     "rows_quarantined": "integer"}))

        if len(runs) > 1:
            hist = runs.sort_values("started_at")
            d.add("Health", viz.trend_line(
                hist, "started_at", "duration_seconds", theme,
                title="Run duration over time (seconds)", y_format="number"), "w6")
            d.add("Health", viz.trend_line(
                hist, "started_at", "rows_loaded", theme,
                title="Rows loaded per run", y_format="number"), "w6")

    if not freshness_row.empty:
        f = freshness_row.iloc[0]
        d.add_note("Health",
                   f"<b>Freshness:</b> last successful run "
                   f"{f['hours_since_run']:.1f} hours ago; the newest transaction in "
                   f"the warehouse is dated {f['latest_data_date']}. This dataset is a "
                   "fixed historical extract, so data age is expected - the number "
                   "that matters operationally is time since the last successful run.")

    # --------------------------------------------------------- Data quality
    if not dq_latest.empty:
        by_cat = (dq_latest.groupby("check_category", as_index=False)
                  .agg(checks=("check_name", "count"),
                       passed=("passed", "sum"),
                       failed_rows=("failed_rows", "sum")))
        by_cat["pass_rate_pct"] = (100 * by_cat["passed"] / by_cat["checks"]).round(1)

        d.add("Data Quality", viz.grouped_bar(
            by_cat, "check_category", "pass_rate_pct", theme,
            value_format="integer", title="Pass rate by category (%)"), "w6")
        d.add("Data Quality", viz.grouped_bar(
            dq_latest.groupby("severity", as_index=False)
                     .agg(failed=("passed", lambda s: int((~s).sum()))),
            "severity", "failed", theme, value_format="integer",
            title="Failed checks by severity"), "w6")
        d.add("Data Quality", viz.data_table(
            dq_latest[["check_category", "check_name", "severity", "failed_rows",
                       "total_rows", "fail_rate_pct", "passed"]]
            .sort_values(["passed", "severity"]),
            theme, title="Latest check results", max_rows=26))

        if not dq_history.empty and dq_history["run_id"].nunique() > 1:
            d.add("Data Quality", viz.trend_line(
                dq_history.groupby("started_at", as_index=False)["pass_rate_pct"].mean(),
                "started_at", "pass_rate_pct", theme,
                title="Data-quality pass rate over time (%)", y_format="number"))

        d.add_note("Data Quality",
                   "<b>Note:</b> validation runs <i>before</i> the load, so PostgreSQL "
                   "never receives a row that fails an ERROR-severity rule. These "
                   "results are written to monitoring.dq_result precisely because the "
                   "database itself will never contain the evidence.")

    # ----------------------------------------------------------- Volume drift
    if not drift.empty:
        d.add("Volume Drift", viz.grouped_bar(
            drift, "table_name", "rows_loaded", theme, value_format="integer",
            title="Rows loaded by table (this run)"), "w6")
        plot = drift.dropna(subset=["drift_pct"])
        if not plot.empty:
            d.add("Volume Drift", viz.grouped_bar(
                plot, "table_name", "drift_pct", theme, value_format="integer",
                title="Change vs previous run (%)"), "w6")
        d.add("Volume Drift", viz.data_table(
            drift, theme, title="Drift detail",
            formats={"rows_loaded": "integer", "prev_rows_loaded": "integer",
                     "row_delta": "integer"}))
        d.add_note("Volume Drift",
                   f"<b>Read:</b> tables are flagged INVESTIGATE when volume moves "
                   f"more than {cfg.monitoring.get('row_count_drift_pct', 20)}% against "
                   "the previous successful run. On a first run everything reads "
                   "BASELINE - drift only becomes meaningful once there is history "
                   "to compare against.")

    # -------------------------------------------------------------- Lineage
    if not cleansing.empty:
        acted = cleansing[cleansing["RowsAffected"] > 0]
        d.add("Cleansing Trail", viz.grouped_bar(
            acted[acted["Decision"] == "quarantined"],
            "Step", "RowsAffected", theme, horizontal=True, value_format="integer",
            title="Rows quarantined by reason"), "w6")
        d.add("Cleansing Trail", viz.donut(
            acted.groupby("Decision", as_index=False)["RowsAffected"].sum(),
            "Decision", "RowsAffected", theme,
            title="Cleaning decisions by type", centre_label="rows touched"), "w6")
        d.add("Cleansing Trail", viz.data_table(
            acted, theme, title="Full cleansing audit trail", max_rows=25))
        d.add_note("Cleansing Trail",
                   "<b>Read:</b> every removed row is quarantined with a reason and "
                   "written to data/processed/quarantine.csv, never silently dropped. "
                   "'Kept' decisions matter just as much: cancellations and guest "
                   "checkouts stay in the fact table because they are real events.")

    return d.render(cfg.path("reports") / "monitoring_dashboard.html")
