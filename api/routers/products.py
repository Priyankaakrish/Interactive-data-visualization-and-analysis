"""Product performance and concentration."""
from __future__ import annotations

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from ..models import ProductRow
from ..store import store
from ..util import pick, records

router = APIRouter(prefix="/products", tags=["products"])


def _frame() -> pd.DataFrame:
    try:
        return store.first("product_performance")
    except KeyError:
        raise HTTPException(404, "product_performance extract not loaded") from None


@router.get("/top", response_model=list[ProductRow], summary="Top products by revenue")
def top(n: int = Query(20, ge=1, le=500),
        abc: str | None = Query(None, pattern="^[ABCabc]$")) -> list[ProductRow]:
    frame = _frame()

    cls = pick(frame, "abc_class", "abc", "pareto_class")
    if abc and cls:
        frame = frame[frame[cls].astype(str).str.upper() == abc.upper()]

    rev = pick(frame, "gross_revenue", "revenue")
    if rev:
        frame = frame.sort_values(rev, ascending=False)

    rows = records(frame, {
        "stock_code": ("stock_code", "product_code"),
        "description": ("description", "product_description"),
        "gross_revenue": ("gross_revenue", "revenue"),
        "units_sold": ("units_sold", "quantity"),
        "orders": ("orders", "order_count"),
        "revenue_share_pct": ("revenue_share_pct", "cumulative_share_pct", "revenue_share"),
        "abc_class": ("abc_class", "abc", "pareto_class"),
    }, limit=n)
    for r in rows:
        r["stock_code"] = str(r["stock_code"])
        r["units_sold"] = int(r["units_sold"] or 0)
        r["orders"] = int(r["orders"] or 0)
        r["gross_revenue"] = float(r["gross_revenue"] or 0)
    return [ProductRow(**r) for r in rows]


@router.get("/{stock_code}", response_model=ProductRow, summary="One product")
def one(stock_code: str) -> ProductRow:
    frame = _frame()
    code = pick(frame, "stock_code", "product_code")
    if code is None:
        raise HTTPException(500, "extract has no stock code column")

    hit = frame[frame[code].astype(str).str.upper() == stock_code.upper()]
    if hit.empty:
        raise HTTPException(404, f"stock code {stock_code} not found")

    rows = records(hit, {
        "stock_code": ("stock_code", "product_code"),
        "description": ("description",),
        "gross_revenue": ("gross_revenue", "revenue"),
        "units_sold": ("units_sold", "quantity"),
        "orders": ("orders", "order_count"),
        "revenue_share_pct": ("revenue_share_pct", "cumulative_share_pct"),
        "abc_class": ("abc_class", "abc"),
    }, limit=1)
    r = rows[0]
    r["stock_code"] = str(r["stock_code"])
    r["units_sold"] = int(r["units_sold"] or 0)
    r["orders"] = int(r["orders"] or 0)
    r["gross_revenue"] = float(r["gross_revenue"] or 0)
    return ProductRow(**r)
