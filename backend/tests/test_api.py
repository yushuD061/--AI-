from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import database
from app.main import app


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    path = tmp_path / "sales_clean.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE stores_clean (store_id TEXT PRIMARY KEY, store_name TEXT, category TEXT, district TEXT);
        CREATE TABLE sales_clean (
          order_id TEXT, date_clean TEXT, store_id TEXT, product_id TEXT, product_name TEXT,
          product_category TEXT, qty_clean NUMERIC, amount_clean NUMERIC
        );
        INSERT INTO stores_clean VALUES ('S01', '门店一', '轻食', '上海');
        INSERT INTO stores_clean VALUES ('S02', '门店二', '主食', '上海');
        INSERT INTO sales_clean VALUES ('O1', '2026-05-01', 'S01', 'P01', '商品一', '主食', 2, 20);
        INSERT INTO sales_clean VALUES ('O1', '2026-05-01', 'S01', 'P02', '商品二', '主食', 1, 10);
        INSERT INTO sales_clean VALUES ('O2', '2026-05-03', 'S02', 'P01', '商品一', '主食', -1, -5);
        INSERT INTO sales_clean VALUES ('O3', '2026-05-03', 'S02', 'P01', '商品一', '主食', 1, 5);
    """)
    connection.commit()
    connection.close()
    monkeypatch.setattr(database, "DATABASE_PATH", path)
    with TestClient(app) as test_client:
        yield test_client


def test_health_and_filters(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "available"}
    payload = client.get("/api/v1/filters").json()
    assert payload["filters"]["start_date"] == "2026-05-01"
    assert [store["store_id"] for store in payload["data"]["stores"]] == ["S01", "S02"]


def test_summary_and_daily_fill_zero(client: TestClient) -> None:
    summary = client.get("/api/v1/dashboard/summary", params={"start_date": "2026-05-01", "end_date": "2026-05-03"}).json()["data"]
    assert summary == {"net_revenue": 30.0, "order_count": 3, "average_order_value": 10.0}
    daily = client.get("/api/v1/dashboard/daily", params={"start_date": "2026-05-01", "end_date": "2026-05-03"}).json()["data"]
    assert [point["date"] for point in daily] == ["2026-05-01", "2026-05-02", "2026-05-03"]
    assert daily[1] == {"date": "2026-05-02", "net_revenue": 0.0, "order_count": 0, "average_order_value": 0.0}
    assert daily[2]["net_revenue"] == 0.0


def test_store_filter_and_top_products(client: TestClient) -> None:
    summary = client.get("/api/v1/dashboard/summary", params={"store_id": "S02"}).json()["data"]
    assert summary["net_revenue"] == 0.0
    products = client.get("/api/v1/dashboard/top-products", params={"limit": 1}).json()["data"]
    assert len(products) == 1
    assert products[0]["product_id"] == "P01"
    assert products[0]["net_revenue"] == 20.0


@pytest.mark.parametrize("params", [
    {"start_date": "2026/05/01"},
    {"start_date": "2026-05-04", "end_date": "2026-05-01"},
    {"store_id": "S99"},
    {"limit": 51},
])
def test_invalid_parameters_return_422(client: TestClient, params: dict[str, str]) -> None:
    response = client.get("/api/v1/dashboard/daily", params=params) if "limit" not in params else client.get("/api/v1/dashboard/top-products", params=params)
    assert response.status_code == 422
