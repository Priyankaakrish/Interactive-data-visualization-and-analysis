"""Analytics reader - stage 4.

Thin wrapper that pulls each analytics view out of PostgreSQL. Keeping the
query text here, rather than scattered through the charting code, means the
set of things the BI layer depends on is a single readable list.
"""
from __future__ import annotations

import pandas as pd
from sqlalchemy.engine import Engine

# view name -> friendly key used everywhere downstream
VIEWS = {
    "vw_kpi_executive_summary":   "executive_summary",
    "vw_kpi_sales_monthly":       "sales_monthly",
    "vw_kpi_product_performance": "product_performance",
    "vw_kpi_country_performance": "country_performance",
    "vw_kpi_customer_rfm":        "customer_rfm",
    "vw_kpi_cohort_retention":    "cohort_retention",
    "vw_kpi_returns_monthly":     "returns_monthly",
    "vw_kpi_basket":              "basket",
}


def load_all(engine: Engine, schema: str = "analytics") -> dict[str, pd.DataFrame]:
    """Read every analytics view into a dict of DataFrames."""
    out: dict[str, pd.DataFrame] = {}
    for view, key in VIEWS.items():
        out[key] = pd.read_sql(f"SELECT * FROM {schema}.{view}", engine)
    return out


def segment_summary(rfm: pd.DataFrame) -> pd.DataFrame:
    """Segment-level economics, derived from the RFM view."""
    if rfm.empty:
        return pd.DataFrame()
    g = (rfm.groupby("segment", as_index=False)
            .agg(customers=("customer_key", "nunique"),
                 revenue=("monetary", "sum"),
                 avg_recency_days=("recency_days", "mean"),
                 avg_frequency=("frequency", "mean"),
                 avg_order_value=("avg_order_value", "mean"))
            .sort_values("revenue", ascending=False))
    g["revenue_share_pct"] = (100 * g["revenue"] / g["revenue"].sum()).round(1)
    g["avg_recency_days"] = g["avg_recency_days"].round(0)
    g["avg_frequency"] = g["avg_frequency"].round(1)
    return g.reset_index(drop=True)


def abc_summary(products: pd.DataFrame) -> pd.DataFrame:
    if products.empty:
        return pd.DataFrame()
    g = (products.groupby("abc_class", as_index=False)
                 .agg(products=("product_key", "count"),
                      revenue=("gross_revenue", "sum"),
                      units=("units_sold", "sum"))
                 .sort_values("abc_class"))
    g["product_share_pct"] = (100 * g["products"] / g["products"].sum()).round(1)
    g["revenue_share_pct"] = (100 * g["revenue"] / g["revenue"].sum()).round(1)
    return g


def cohort_matrix(cohorts: pd.DataFrame, horizon: int = 12) -> pd.DataFrame:
    """Pivot the cohort view into the familiar triangular retention grid."""
    if cohorts.empty:
        return pd.DataFrame()
    d = cohorts[cohorts["months_since_first"].between(0, horizon)]
    return d.pivot_table(index="cohort_month", columns="months_since_first",
                         values="retention_pct", aggfunc="first")
