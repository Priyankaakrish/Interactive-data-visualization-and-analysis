"""Fixtures. Everything here is synthetic so CI needs no 45 MB Excel file."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest


@pytest.fixture
def raw_frame() -> pd.DataFrame:
    """A small frame carrying one instance of every defect class."""
    return pd.DataFrame([
        # normal sale
        {"invoice_no": "489434", "stock_code": "85048", "description": "GLASS BALL",
         "quantity": 12, "unit_price": 6.95, "customer_id": "13085",
         "country": "United Kingdom", "invoice_date": "2009-12-01 07:45:00"},
        # exact duplicate of the row above
        {"invoice_no": "489434", "stock_code": "85048", "description": "GLASS BALL",
         "quantity": 12, "unit_price": 6.95, "customer_id": "13085",
         "country": "United Kingdom", "invoice_date": "2009-12-01 07:45:00"},
        # cancellation
        {"invoice_no": "C489435", "stock_code": "85048", "description": "GLASS BALL",
         "quantity": -12, "unit_price": 6.95, "customer_id": "13085",
         "country": "United Kingdom", "invoice_date": "2009-12-02 09:10:00"},
        # service line
        {"invoice_no": "489436", "stock_code": "POST", "description": "POSTAGE",
         "quantity": 1, "unit_price": 18.0, "customer_id": "13085",
         "country": "France", "invoice_date": "2009-12-02 10:00:00"},
        # warehouse annotation
        {"invoice_no": "489437", "stock_code": "22423", "description": "damaged",
         "quantity": 5, "unit_price": 2.5, "customer_id": None,
         "country": "United Kingdom", "invoice_date": "2009-12-03 11:00:00"},
        # zero price
        {"invoice_no": "489438", "stock_code": "22424", "description": "REGENCY TEACUP",
         "quantity": 3, "unit_price": 0.0, "customer_id": "13086",
         "country": "United Kingdom", "invoice_date": "2009-12-03 12:00:00"},
        # guest checkout
        {"invoice_no": "489439", "stock_code": "22425", "description": "JUMBO BAG",
         "quantity": 2, "unit_price": 4.25, "customer_id": None,
         "country": "Germany", "invoice_date": "2009-12-04 13:00:00"},
    ])


@pytest.fixture
def extract_dir(tmp_path: Path) -> Path:
    """A minimal extract folder the API can be pointed at."""
    folder = tmp_path / "processed"
    folder.mkdir()

    pd.DataFrame({
        "metric": ["Gross Revenue", "Net Revenue", "Returns Value", "Return Rate",
                   "Orders", "Avg Order Value", "Units Sold", "Products Sold",
                   "Identified Customers", "Guest Checkout Share"],
        "value": [19464639.76, 18200000.0, 1300000.0, 6.7,
                  40122, 485.0, 11104561, 4726, 5941, 22.3],
    }).to_csv(folder / "executive_summary.csv", index=False)

    pd.DataFrame({
        "month": ["2009-12", "2010-01"],
        "gross_revenue": [683000.0, 555000.0],
        "net_revenue": [660000.0, 540000.0],
        "orders": [1200, 980],
        "return_rate_pct": [3.4, 2.7],
    }).to_csv(folder / "sales_monthly.csv", index=False)

    pd.DataFrame({
        "stock_code": ["85048", "22423", "22425"],
        "description": ["GLASS BALL", "REGENCY CAKESTAND", "JUMBO BAG"],
        "gross_revenue": [164000.0, 132000.0, 98000.0],
        "units_sold": [23000, 13000, 45000],
        "orders": [1300, 1900, 2100],
        "abc_class": ["A", "A", "B"],
    }).to_csv(folder / "product_performance.csv", index=False)

    pd.DataFrame({
        "country": ["United Kingdom", "Netherlands", "EIRE"],
        "gross_revenue": [17000000.0, 560000.0, 480000.0],
        "orders": [37000, 200, 340],
        "return_rate_pct": [6.9, 1.1, 3.2],
    }).to_csv(folder / "country_performance.csv", index=False)

    pd.DataFrame({
        "customer_id": ["13085", "13086", "14646"],
        "recency_days": [12, 340, 3],
        "frequency": [24, 1, 77],
        "monetary": [12400.0, 89.0, 280000.0],
        "r_score": [5, 1, 5], "f_score": [4, 1, 5], "m_score": [4, 1, 5],
        "segment": ["Loyal", "Lost", "Champions"],
    }).to_csv(folder / "customer_rfm.csv", index=False)

    return folder
