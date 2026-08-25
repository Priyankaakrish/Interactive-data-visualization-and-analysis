"""Customer value and segmentation."""
from __future__ import annotations

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from ..models import CustomerRfm, SegmentCount
from ..store import store
from ..util import pick, records

router = APIRouter(prefix="/customers", tags=["customers"])

_MAP = {
    "customer_id": ("customer_id", "customer"),
    "recency_days": ("recency_days", "recency"),
    "frequency": ("frequency", "orders"),
    "monetary": ("monetary", "monetary_value", "gross_revenue"),
    "r_score": ("r_score", "recency_score"),
    "f_score": ("f_score", "frequency_score"),
    "m_score": ("m_score", "monetary_score"),
    "segment": ("segment", "rfm_segment"),
}


def _frame() -> pd.DataFrame:
    try:
        return store.first("customer_rfm")
    except KeyError:
        raise HTTPException(404, "customer_rfm extract not loaded") from None


def _clean(rows: list[dict]) -> list[dict]:
    for r in rows:
        r["customer_id"] = str(r["customer_id"])
        r["recency_days"] = int(r["recency_days"] or 0)
        r["frequency"] = int(r["frequency"] or 0)
        r["monetary"] = float(r["monetary"] or 0)
        for s in ("r_score", "f_score", "m_score"):
            r[s] = int(r[s]) if r[s] is not None else None
        r["segment"] = str(r["segment"]) if r["segment"] is not None else None
    return rows


@router.get("/segments", response_model=list[SegmentCount], summary="RFM segment sizes")
def segments() -> list[SegmentCount]:
    frame = _frame()
    seg = pick(frame, "segment", "rfm_segment")
    mon = pick(frame, "monetary", "monetary_value", "gross_revenue")
    if seg is None:
        raise HTTPException(500, "extract has no segment column")

    grouped = frame.groupby(seg, dropna=False).agg(
        customers=(seg, "size"),
        monetary=(mon, "sum") if mon else (seg, "size"))
    grouped = grouped.reset_index().sort_values("monetary", ascending=False)

    return [SegmentCount(segment=str(r[seg]), customers=int(r["customers"]),
                         monetary=float(r["monetary"]))
            for _, r in grouped.iterrows()]


@router.get("/top", response_model=list[CustomerRfm], summary="Highest-value customers")
def top(n: int = Query(20, ge=1, le=500),
        segment: str | None = Query(None)) -> list[CustomerRfm]:
    frame = _frame()

    seg = pick(frame, "segment", "rfm_segment")
    if segment and seg:
        frame = frame[frame[seg].astype(str).str.lower() == segment.lower()]

    mon = pick(frame, "monetary", "monetary_value", "gross_revenue")
    if mon:
        frame = frame.sort_values(mon, ascending=False)

    return [CustomerRfm(**r) for r in _clean(records(frame, _MAP, limit=n))]


@router.get("/{customer_id}/rfm", response_model=CustomerRfm, summary="One customer's RFM")
def one(customer_id: str) -> CustomerRfm:
    frame = _frame()
    col = pick(frame, "customer_id", "customer")
    if col is None:
        raise HTTPException(500, "extract has no customer id column")

    hit = frame[frame[col].astype(str) == str(customer_id)]
    if hit.empty:
        raise HTTPException(404, f"customer {customer_id} not found")
    return CustomerRfm(**_clean(records(hit, _MAP, limit=1))[0])
