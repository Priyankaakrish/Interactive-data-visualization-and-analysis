"""Headline figures and the monthly series."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..models import CountryRow, KpiSummary, MonthlyPoint
from ..store import store
from ..util import kv_lookup, num, pick, records

router = APIRouter(prefix="/kpi", tags=["kpi"])

_ALIASES = {
    "gross_revenue": ("Gross Revenue", "gross_revenue"),
    "net_revenue": ("Net Revenue", "net_revenue"),
    "returns_value": ("Returns Value", "returns_value"),
    "return_rate_pct": ("Return Rate", "return_rate_pct"),
    "orders": ("Orders", "orders"),
    "avg_order_value": ("Avg Order Value", "avg_order_value"),
    "units_sold": ("Units Sold", "units_sold"),
    "products_sold": ("Products Sold", "products_sold"),
    "identified_customers": ("Identified Customers", "identified_customers"),
    "guest_checkout_share_pct": ("Guest Checkout Share", "guest_checkout_share_pct"),
}


@router.get("/summary", response_model=KpiSummary, summary="Headline KPIs")
def summary() -> KpiSummary:
    """Return the ten executive figures from the published extract."""
    try:
        frame = store.first("executive_summary")
    except KeyError:
        raise HTTPException(404, "executive_summary extract not loaded") from None

    key_col = pick(frame, "metric", "kpi", "name")
    val_col = pick(frame, "value", "amount", "figure")

    out = {}
    if key_col and val_col:
        for field, names in _ALIASES.items():
            out[field] = kv_lookup(frame, key_col, val_col, names[0])
    else:
        for field, names in _ALIASES.items():
            out[field] = num(frame, *names)

    for field in ("orders", "units_sold", "products_sold", "identified_customers"):
        out[field] = int(out.get(field, 0))
    return KpiSummary(**out)


@router.get("/monthly", response_model=list[MonthlyPoint], summary="Monthly revenue series")
def monthly() -> list[MonthlyPoint]:
    try:
        frame = store.first("sales_monthly")
    except KeyError:
        raise HTTPException(404, "sales_monthly extract not loaded") from None

    rows = records(frame, {
        "month": ("month", "month_label", "year_month", "full_month"),
        "gross_revenue": ("gross_revenue", "revenue"),
        "net_revenue": ("net_revenue",),
        "orders": ("orders", "order_count"),
        "return_rate_pct": ("return_rate_pct", "return_rate"),
    })
    for r in rows:
        r["month"] = str(r["month"])
        r["orders"] = int(r["orders"] or 0)
        r["gross_revenue"] = float(r["gross_revenue"] or 0)
        r["net_revenue"] = float(r["net_revenue"] or r["gross_revenue"] or 0)
    return [MonthlyPoint(**r) for r in rows]


@router.get("/countries", response_model=list[CountryRow], summary="Revenue by country")
def countries(limit: int = Query(43, ge=1, le=250)) -> list[CountryRow]:
    try:
        frame = store.first("country_performance")
    except KeyError:
        raise HTTPException(404, "country_performance extract not loaded") from None

    rev = pick(frame, "gross_revenue", "revenue")
    if rev:
        frame = frame.sort_values(rev, ascending=False)

    rows = records(frame, {
        "country": ("country", "country_name"),
        "gross_revenue": ("gross_revenue", "revenue"),
        "orders": ("orders", "order_count"),
        "return_rate_pct": ("return_rate_pct", "return_rate"),
    }, limit=limit)
    for r in rows:
        r["country"] = str(r["country"])
        r["orders"] = int(r["orders"] or 0)
        r["gross_revenue"] = float(r["gross_revenue"] or 0)
    return [CountryRow(**r) for r in rows]
