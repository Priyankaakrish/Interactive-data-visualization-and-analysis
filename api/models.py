"""Pydantic v2 response models.

Every endpoint declares its shape here rather than returning bare dicts, so the
generated OpenAPI document is accurate and clients get a real contract.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class KpiSummary(BaseModel):
    gross_revenue: float = Field(..., description="Revenue before returns, GBP")
    net_revenue: float = Field(..., description="Revenue after returns are netted off, GBP")
    returns_value: float
    return_rate_pct: float
    orders: int
    avg_order_value: float
    units_sold: int
    products_sold: int
    identified_customers: int
    guest_checkout_share_pct: float


class MonthlyPoint(BaseModel):
    month: str
    gross_revenue: float
    net_revenue: float
    orders: int
    return_rate_pct: float | None = None


class ProductRow(BaseModel):
    stock_code: str
    description: str | None = None
    gross_revenue: float
    units_sold: int
    orders: int
    revenue_share_pct: float | None = None
    abc_class: str | None = None


class CountryRow(BaseModel):
    country: str
    gross_revenue: float
    orders: int
    return_rate_pct: float | None = None


class CustomerRfm(BaseModel):
    customer_id: str
    recency_days: int
    frequency: int
    monetary: float
    r_score: int | None = None
    f_score: int | None = None
    m_score: int | None = None
    segment: str | None = None


class SegmentCount(BaseModel):
    segment: str
    customers: int
    monetary: float


class LiveWindow(BaseModel):
    window_start: datetime
    window_end: datetime
    gross_revenue: float
    net_revenue: float
    returns_value: float
    order_count: int
    line_count: int
    units_sold: int


class LiveSummary(BaseModel):
    streaming: bool = Field(..., description="False when the stream database is unreachable")
    gross_revenue: float = 0.0
    net_revenue: float = 0.0
    returns_value: float = 0.0
    return_rate_pct: float = 0.0
    order_count: int = 0
    line_count: int = 0
    units_sold: int = 0
    first_window: datetime | None = None
    last_window: datetime | None = None
    lag_seconds: float | None = Field(
        None, description="Seconds between the newest window and now")


class DatasetInfo(BaseModel):
    name: str
    rows: int
    columns: list[str]


class HealthResponse(BaseModel):
    status: str
    version: str
    extracts_loaded: int
    datasets: list[DatasetInfo]
    stream_connected: bool
    uptime_seconds: float
