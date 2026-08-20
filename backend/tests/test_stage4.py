from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import database, state
from app.main import app
from app.services import quality


@pytest.fixture()
def secured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    sales = tmp_path / "sales.sqlite"
    db = sqlite3.connect(sales)
    db.executescript("""
      CREATE TABLE stores_clean (store_id TEXT PRIMARY KEY, store_name TEXT, category TEXT, district TEXT);
      CREATE TABLE sales_clean (order_id TEXT, date_clean TEXT, store_id TEXT, product_id TEXT, product_name TEXT, product_category TEXT, qty_clean NUMERIC, amount_clean NUMERIC);
      INSERT INTO stores_clean VALUES ('S01','一店','轻食','上海'),('S02','二店','主食','上海');
      INSERT INTO sales_clean VALUES ('A','2026-05-01','S01','P1','商品一','主食',1,10),('B','2026-05-01','S02','P2','商品二','主食',1,20);
    """)
    db.commit(); db.close()
    monkeypatch.setattr(database, "DATABASE_PATH", sales)
    monkeypatch.setattr(state, "APP_DB", tmp_path / "app.sqlite")
    monkeypatch.setattr(quality, "get_quality", lambda: {"status":"healthy","cleaned_at":"2026-05-02T00:00:00Z","metrics":{},"checks":[]})
    with TestClient(app) as client:
        yield client


def token(client: TestClient, username: str, password: str) -> str:
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["data"]["access_token"]


def test_role_scope_and_protected_actions(secured: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    assert secured.get("/api/v1/filters").status_code == 401
    manager = token(secured, "manager", "manager-demo")
    readonly = token(secured, "readonly", "readonly-demo")
    data = secured.get("/api/v1/filters", headers={"Authorization": f"Bearer {manager}"}).json()["data"]
    assert [item["store_id"] for item in data["stores"]] == ["S01"]
    assert secured.get("/api/v1/dashboard/summary", params={"store_id":"S02"}, headers={"Authorization": f"Bearer {manager}"}).status_code == 403
    assert secured.patch("/api/v1/ai/config", json={"provider":"deepseek","model":"x","base_url":"https://x.example","timeout_seconds":10}, headers={"Authorization": f"Bearer {readonly}"}).status_code == 403
    assert secured.post("/api/v1/reports/daily", json={"report_date":"2026-05-01","store_id":"S01"}, headers={"Authorization": f"Bearer {readonly}"}).status_code == 403


def test_audit_excludes_secrets_and_records_actions(secured: TestClient):
    admin = token(secured, "admin", "admin-demo")
    headers = {"Authorization": f"Bearer {admin}", "X-Request-ID":"req-test"}
    assert secured.get("/api/v1/data-quality", headers=headers).status_code == 200
    result = secured.get("/api/v1/audit/events", headers=headers)
    assert result.status_code == 200
    assert any(item["action"] == "quality_view" and item["request_id"] == "req-test" for item in result.json()["data"])
    assert all("password" not in str(item).lower() and "api_key" not in str(item).lower() for item in result.json()["data"])
