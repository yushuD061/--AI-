from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import database, state
from app.main import app
from app.services import phase2


@pytest.fixture()
def phase2_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    sales = tmp_path / "sales.sqlite"
    db = sqlite3.connect(sales)
    db.executescript("""
      CREATE TABLE stores_clean (store_id TEXT PRIMARY KEY, store_name TEXT, category TEXT, district TEXT);
      CREATE TABLE sales_clean (order_id TEXT, date_clean TEXT, store_id TEXT, product_id TEXT,
        product_name TEXT, product_category TEXT, qty_clean NUMERIC, amount_clean NUMERIC);
      INSERT INTO stores_clean VALUES ('S01','一店','轻食','上海'), ('S02','二店','轻食','上海');
    """)
    rows = []
    # Five effective days in each period; P1 has ten historical orders.
    for offset in range(10):
        day = (date(2026, 5, 1) + timedelta(days=offset)).isoformat()
        amount = 10 if offset < 5 else 5
        for order in range(10):
            rows.append((f"O{offset}-{order}", day, "S01", "P01", "商品一", "主食", 1, amount))
        if offset < 5:
            rows.append((f"P2-{offset}", day, "S01", "P02", "商品二", "主食", 1, 20))
    db.executemany("INSERT INTO sales_clean VALUES (?,?,?,?,?,?,?,?)", rows); db.commit(); db.close()
    monkeypatch.setattr(database, "DATABASE_PATH", sales)
    monkeypatch.setattr(state, "APP_DB", tmp_path / "app.sqlite")
    return TestClient(app)


def test_compare_and_ranking_are_recomputable(phase2_db: TestClient):
    response = phase2_db.get("/api/v1/dashboard/compare", params={
        "current_start_date": "2026-05-06", "current_end_date": "2026-05-10"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["previous_period"] == {"start_date": "2026-05-01", "end_date": "2026-05-05", "store_id": None}
    assert data["metrics"]["net_revenue"]["current"] == 250.0
    assert data["metrics"]["net_revenue"]["previous"] == 600.0
    assert data["metrics"]["net_revenue"]["change_rate"] == pytest.approx(-0.583333)
    ranking = phase2_db.get("/api/v1/dashboard/store-ranking", params={
        "current_start_date": "2026-05-06", "current_end_date": "2026-05-10", "metric": "net_revenue", "limit": 1})
    assert ranking.status_code == 200 and ranking.json()["data"]["data"][0]["store_id"] == "S01"


def test_product_mix_and_decline_id_read_state(phase2_db: TestClient):
    params = {"current_start_date": "2026-05-06", "current_end_date": "2026-05-10"}
    mix = phase2_db.get("/api/v1/dashboard/product-mix", params=params).json()["data"]["data"]
    assert mix[0]["product_id"] == "P01" and mix[0]["revenue_share"] > 0
    alerts = phase2_db.get("/api/v1/alerts/product-decline", params=params).json()["data"]
    drop = next(item for item in alerts if item["trigger"] == "revenue_drop")
    assert drop["severity"] == "critical" and drop["is_read"] is False
    assert phase2_db.patch(f"/api/v1/alerts/{drop['alert_id']}/read", json={"is_read": True}).status_code == 200
    again = phase2_db.get("/api/v1/alerts/product-decline", params=params).json()["data"]
    assert next(item for item in again if item["alert_id"] == drop["alert_id"])["is_read"] is True


def test_compare_validation_and_unknown_store(phase2_db: TestClient):
    assert phase2_db.get("/api/v1/dashboard/compare", params={"current_start_date": "2026-05-06", "previous_start_date": "2026-05-01"}).status_code == 422
    assert phase2_db.get("/api/v1/dashboard/store-ranking", params={"current_start_date": "2026-05-06", "current_end_date": "2026-05-10", "metric": "bad"}).status_code == 422
    assert phase2_db.get("/api/v1/dashboard/store-diagnosis/S99", params={"current_start_date": "2026-05-06", "current_end_date": "2026-05-10"}).status_code == 422
