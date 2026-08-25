"""API contract tests. No warehouse and no Kafka required."""
from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def client(extract_dir, monkeypatch):
    monkeypatch.setenv("EXTRACT_DIR", str(extract_dir))
    monkeypatch.setenv("STREAM_ENABLED", "0")

    import importlib

    from api import settings as settings_module
    importlib.reload(settings_module)
    from api import main as main_module
    importlib.reload(main_module)

    with TestClient(main_module.app) as c:
        yield c


def test_health_reports_loaded_extracts(client) -> None:
    body = client.get("/api/v1/health").json()
    assert body["status"] == "ok"
    assert body["extracts_loaded"] == 5
    assert body["stream_connected"] is False


def test_kpi_summary_matches_the_extract(client) -> None:
    body = client.get("/api/v1/kpi/summary").json()
    assert body["gross_revenue"] == pytest.approx(19_464_639.76)
    assert body["orders"] == 40_122
    assert body["return_rate_pct"] == pytest.approx(6.7)


def test_monthly_series_is_ordered_and_typed(client) -> None:
    body = client.get("/api/v1/kpi/monthly").json()
    assert len(body) == 2
    assert isinstance(body[0]["orders"], int)


def test_top_products_respects_limit(client) -> None:
    body = client.get("/api/v1/products/top?n=2").json()
    assert len(body) == 2
    assert body[0]["gross_revenue"] >= body[1]["gross_revenue"]


def test_product_filter_by_abc_class(client) -> None:
    body = client.get("/api/v1/products/top?abc=B").json()
    assert all(r["abc_class"] == "B" for r in body)


def test_unknown_product_returns_404(client) -> None:
    assert client.get("/api/v1/products/DOES-NOT-EXIST").status_code == 404


def test_customer_segments_aggregate(client) -> None:
    body = client.get("/api/v1/customers/segments").json()
    assert {r["segment"] for r in body} == {"Champions", "Loyal", "Lost"}
    assert body[0]["segment"] == "Champions", "ordered by monetary value"


def test_single_customer_lookup(client) -> None:
    body = client.get("/api/v1/customers/14646/rfm").json()
    assert body["frequency"] == 77
    assert body["segment"] == "Champions"


def test_live_degrades_when_stream_is_down(client) -> None:
    body = client.get("/api/v1/live/summary").json()
    assert body["streaming"] is False
    assert body["gross_revenue"] == 0.0


def test_openapi_document_is_generated(client) -> None:
    spec = client.get("/openapi.json").json()
    assert "/api/v1/kpi/summary" in spec["paths"]
