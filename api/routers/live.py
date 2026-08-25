"""Live figures from the streaming path.

These endpoints read the Spark output tables, not the batch warehouse. They are
deliberately separate from /kpi: the batch figures are reconciled and final, the
live figures are approximate and moving. Presenting them through the same
endpoint would invite someone to compare them and conclude one is wrong.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Query

from .. import db
from ..models import LiveSummary, LiveWindow

router = APIRouter(prefix="/live", tags=["live"])


@router.get("/summary", response_model=LiveSummary, summary="Live rolling KPIs")
def summary() -> LiveSummary:
    """Aggregate every window the consumer has written.

    Returns streaming=False rather than an error when the stream database is
    unreachable, so a dashboard polling this endpoint degrades quietly.
    """
    if not db.connected():
        return LiveSummary(streaming=False)

    frame = db.query("SELECT * FROM stream.vw_live_summary")
    if frame.empty:
        return LiveSummary(streaming=True)

    row = frame.iloc[0]
    last = row["last_window"]
    lag = None
    if last is not None and not isinstance(last, float):
        ref = last.to_pydatetime() if hasattr(last, "to_pydatetime") else last
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=timezone.utc)
        lag = round((datetime.now(timezone.utc) - ref).total_seconds(), 1)

    return LiveSummary(
        streaming=True,
        gross_revenue=float(row["gross_revenue"] or 0),
        net_revenue=float(row["net_revenue"] or 0),
        returns_value=float(row["returns_value"] or 0),
        return_rate_pct=float(row["return_rate_pct"] or 0),
        order_count=int(row["order_count"] or 0),
        line_count=int(row["line_count"] or 0),
        units_sold=int(row["units_sold"] or 0),
        first_window=row["first_window"],
        last_window=row["last_window"],
        lag_seconds=lag,
    )


@router.get("/windows", response_model=list[LiveWindow], summary="Recent windows")
def windows(limit: int = Query(30, ge=1, le=200)) -> list[LiveWindow]:
    if not db.connected():
        return []
    frame = db.query(
        "SELECT window_start, window_end, gross_revenue, net_revenue, returns_value, "
        "       order_count, line_count, units_sold "
        "FROM stream.live_kpi ORDER BY window_start DESC LIMIT :n", n=limit)
    return [LiveWindow(**r) for r in frame.to_dict("records")]


@router.get("/countries", summary="Live revenue by country")
def countries(limit: int = Query(15, ge=1, le=100)) -> list[dict]:
    if not db.connected():
        return []
    frame = db.query(
        "SELECT country, SUM(gross_revenue) AS gross_revenue, SUM(line_count) AS line_count "
        "FROM stream.live_country GROUP BY country "
        "ORDER BY gross_revenue DESC LIMIT :n", n=limit)
    return frame.to_dict("records")
