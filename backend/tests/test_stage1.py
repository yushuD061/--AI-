from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import database, state
from app.main import app
from app.services import alerts, quality


def make_sales_db(path: Path) -> None:
    db = sqlite3.connect(path)
    db.executescript("""
      CREATE TABLE stores_clean (store_id TEXT PRIMARY KEY, store_name TEXT, category TEXT, district TEXT);
      CREATE TABLE sales_clean (order_id TEXT, date_clean TEXT, store_id TEXT, product_id TEXT,
        product_name TEXT, product_category TEXT, qty_clean NUMERIC, amount_clean NUMERIC);
      INSERT INTO stores_clean VALUES ('S01','门店一','轻食','上海'), ('S02','门店二','轻食','上海');
    """)
    rows = []
    start = datetime(2026, 5, 1)
    for offset in range(10):
        day = (start + timedelta(days=offset)).date().isoformat()
        amounts = [10.0] * 10
        if offset == 7:
            amounts = [5.0] * 10
        if offset == 8:
            amounts = [-10.0] * 3 + [10.0] * 7
        for order, amount in enumerate(amounts):
            rows.append((f"O{offset}-{order}", day, "S01", "P01", "商品一", "主食", 1, amount))
        if offset == 0:
            rows.append(("S02-1", day, "S02", "P02", "商品二", "主食", 1, 20))
    db.executemany("INSERT INTO sales_clean VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    db.commit(); db.close()


@pytest.fixture()
def stage1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    sales = tmp_path / "sales.sqlite"; make_sales_db(sales)
    app_db = tmp_path / "app.sqlite"
    report = tmp_path / "cleaning_report.json"
    manifest = tmp_path / "import_manifest.json"
    raw = tmp_path / "raw"; raw.mkdir(); (raw / "sales.csv").write_text("x", encoding="utf-8")
    payload = {"raw_rows": {"sales": 100}, "valid_rows": 90, "isolated_rows": 5,
               "issues_by_type": {"MISSING_VALUE": 2, "DUPLICATE_ROW": 3},
               "invalid_store_fk": 1, "invalid_product_fk": 1,
               "date_min": "2026-05-01", "date_max": "2026-05-10"}
    report.write_text(json.dumps(payload), encoding="utf-8")
    manifest.write_text(json.dumps({"files": []}), encoding="utf-8")
    monkeypatch.setattr(database, "DATABASE_PATH", sales)
    monkeypatch.setattr(state, "APP_DB", app_db)
    monkeypatch.setattr(quality, "REPORT_PATH", report)
    monkeypatch.setattr(quality, "MANIFEST_PATH", manifest)
    monkeypatch.setattr(quality, "RAW_DIR", raw)
    return sales, app_db, report, payload


def test_quality_thresholds_and_failed_run(stage1) -> None:
    sales, app_db, report, payload = stage1
    now = datetime.now(timezone.utc)
    exactly_24 = (now - timedelta(hours=24)).timestamp()
    os.utime(sales, (exactly_24, exactly_24))
    assert quality.get_quality(now)["status"] == "healthy"
    thirty_hours = (now - timedelta(hours=30)).timestamp()
    os.utime(sales, (thirty_hours, thirty_hours))
    result = quality.get_quality(now)
    assert result["status"] == "warning"
    assert result["metrics"]["isolation_rate"] == .05
    exactly_48 = (now - timedelta(hours=48)).timestamp(); os.utime(sales, (exactly_48, exactly_48))
    assert quality.get_quality(now)["status"] == "warning"
    old = (now - timedelta(hours=49)).timestamp(); os.utime(sales, (old, old))
    assert quality.get_quality(now)["status"] == "critical"
    os.utime(sales, (now.timestamp(), now.timestamp()))
    payload["isolated_rows"] = 15; report.write_text(json.dumps(payload), encoding="utf-8")
    assert quality.get_quality(now)["status"] == "warning"
    payload["isolated_rows"] = 16; report.write_text(json.dumps(payload), encoding="utf-8")
    assert quality.get_quality(now)["status"] == "critical"
    state.record_quality_run("failed", error_message="清洗失败", path=app_db)
    assert quality.get_quality(now)["status"] == "critical"
    assert quality.get_runs(1)[0]["status"] == "failed"
    report.unlink()
    assert any(check["name"] == "artifacts" and check["status"] == "critical" for check in quality.get_quality(now)["checks"])


def test_alert_rules_are_deterministic_and_filterable(stage1) -> None:
    filters = alerts.Filters(start_date=datetime(2026, 5, 8).date(), end_date=datetime(2026, 5, 10).date())
    first = alerts.get_alerts(filters, limit=500)
    second = alerts.get_alerts(filters, limit=500)
    assert [item["alert_id"] for item in first] == [item["alert_id"] for item in second]
    assert any(item["type"] == "net_revenue_drop" and item["severity"] == "critical" for item in first)
    assert any(item["type"] == "refund_concentration" and item["severity"] == "warning" for item in first)
    assert any(item["type"] == "store_no_sales" and item["store_id"] == "S02" for item in first)
    assert all(item["severity"] == "critical" for item in alerts.get_alerts(filters, severity="critical", limit=500))


def test_alert_read_api_persists_and_validates(stage1) -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/alerts", params={"start_date": "2026-05-08", "end_date": "2026-05-10", "limit": 500})
        assert response.status_code == 200
        alert = response.json()["data"][0]
        marked = client.patch(f"/api/v1/alerts/{alert['alert_id']}/read", json={"is_read": True})
        assert marked.status_code == 200 and marked.json()["data"]["is_read"] is True
        unread = client.get("/api/v1/alerts", params={"start_date": "2026-05-08", "end_date": "2026-05-10", "is_read": "false", "limit": 500}).json()["data"]
        assert alert["alert_id"] not in {item["alert_id"] for item in unread}
        assert client.patch(f"/api/v1/alerts/{alert['alert_id']}/read", json={"is_read": False}).json()["data"]["is_read"] is False
        assert client.patch("/api/v1/alerts/a_missing/read", json={"is_read": True}).status_code == 404
        assert client.get("/api/v1/alerts", params={"severity": "urgent"}).status_code == 422
