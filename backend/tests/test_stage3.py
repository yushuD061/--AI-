from __future__ import annotations

import csv
import io
import json
import sqlite3
import zipfile
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import database, state
from app.main import app
from app.services import quality


@pytest.fixture()
def report_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    sales = tmp_path / "sales.sqlite"
    db = sqlite3.connect(sales)
    db.executescript("""
      CREATE TABLE stores_clean (store_id TEXT PRIMARY KEY, store_name TEXT, category TEXT, district TEXT);
      CREATE TABLE sales_clean (order_id TEXT, date_clean TEXT, store_id TEXT, product_id TEXT,
        product_name TEXT, product_category TEXT, qty_clean NUMERIC, amount_clean NUMERIC);
      INSERT INTO stores_clean VALUES ('S01','一店','轻食','上海'), ('S02','二店','主食','上海');
      INSERT INTO sales_clean VALUES
        ('A1','2026-05-01','S01','P01','商品一','主食',1,100),
        ('A2','2026-05-01','S02','P02','商品二','小食',1,50),
        ('B1','2026-05-02','S01','P01','商品一','主食',2,120),
        ('B2','2026-05-02','S01','P02','商品二','小食',1,30),
        ('B3','2026-05-02','S02','P02','商品二','小食',1,80);
    """)
    db.commit(); db.close()
    monkeypatch.setattr(database, "DATABASE_PATH", sales)
    monkeypatch.setattr(state, "APP_DB", tmp_path / "app.sqlite")
    healthy = {"status":"healthy", "cleaned_at":"2026-05-03T00:00:00+00:00",
               "database_updated_at":"2026-05-03T00:00:00+00:00", "raw_updated_at":None,
               "metrics":{}, "checks":[]}
    monkeypatch.setattr(quality, "get_quality", lambda: healthy)
    with TestClient(app) as client:
        yield client, sales, healthy


def test_daily_report_versions_are_immutable_and_recomputable(report_client):
    client, sales, _ = report_client
    first = client.post("/api/v1/reports/daily", json={"report_date":"2026-05-02"})
    assert first.status_code == 201
    report = first.json()["data"]
    assert report["version"] == 1
    assert report["summary"] == {"net_revenue":230.0,"order_count":3,"average_order_value":76.67}
    assert report["previous_date"] == "2026-05-01"
    assert report["best_store"]["store_id"] == "S01"
    assert report["best_product"]["product_id"] == "P01"
    before = client.get(f"/api/v1/reports/daily/{report['report_id']}").json()["data"]
    db = sqlite3.connect(sales); db.execute("UPDATE sales_clean SET amount_clean=999 WHERE order_id='B1'"); db.commit(); db.close()
    second = client.post("/api/v1/reports/daily", json={"report_date":"2026-05-02"}).json()["data"]
    assert second["version"] == 2 and second["report_id"] != report["report_id"]
    assert client.get(f"/api/v1/reports/daily/{report['report_id']}").json()["data"] == before
    versions = client.get("/api/v1/reports/daily", params={"report_date":"2026-05-02"}).json()["data"]
    assert [item["version"] for item in versions] == [2,1]


def test_exports_contain_snapshot_metadata_and_sections(report_client):
    client, _, _ = report_client
    report = client.post("/api/v1/reports/daily", json={"report_date":"2026-05-02","store_id":"S01"}).json()["data"]
    csv_response = client.get("/api/v1/reports/daily/export", params={"report_id":report["report_id"],"format":"csv"})
    assert csv_response.status_code == 200 and csv_response.content.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(csv_response.content.decode("utf-8-sig"))))
    assert {row[0] for row in rows[1:]} >= {"metadata","kpi","comparison","store_ranking","product_mix"}
    xlsx = client.get("/api/v1/reports/daily/export", params={"report_id":report["report_id"],"format":"xlsx"})
    with zipfile.ZipFile(io.BytesIO(xlsx.content)) as archive:
        workbook = archive.read("xl/workbook.xml").decode("utf-8")
        assert all(name in workbook for name in ("元数据","日报摘要","门店排名","商品结构","异常列表"))
        assert len([name for name in archive.namelist() if name.startswith("xl/worksheets/sheet")]) == 5
    pdf = client.get("/api/v1/reports/daily/export", params={"report_id":report["report_id"],"format":"pdf"})
    assert pdf.content.startswith(b"%PDF-1.4") and b"STSong-Light" in pdf.content
    assert "moneki_daily_2026-05-02_v1" in pdf.headers["content-disposition"]


def test_critical_quality_and_invalid_inputs(report_client, monkeypatch: pytest.MonkeyPatch):
    client, _, healthy = report_client
    critical = {**healthy, "status":"critical"}
    monkeypatch.setattr(quality, "get_quality", lambda: critical)
    blocked = client.post("/api/v1/reports/daily", json={"report_date":"2026-05-02"})
    assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "DATA_QUALITY_CRITICAL"
    assert client.get("/api/v1/reports/daily").json()["data"] == []
    assert client.post("/api/v1/reports/daily", json={"report_date":"2026-05-10"}).status_code == 422
    assert client.get("/api/v1/reports/daily/missing").status_code == 404
    assert client.get("/api/v1/reports/daily/export", params={"report_id":"missing","format":"csv"}).status_code == 404
    warning = {**healthy, "status":"warning"}
    monkeypatch.setattr(quality, "get_quality", lambda: warning)
    created = client.post("/api/v1/reports/daily", json={"report_date":"2026-05-02"})
    assert created.status_code == 201 and created.json()["data"]["quality_status"] == "warning"


def test_version_allocation_is_concurrency_safe(report_client):
    client, _, _ = report_client
    base = client.post("/api/v1/reports/daily", json={"report_date":"2026-05-02"}).json()["data"]
    # Use a different scope so the concurrent writers start at version 1.
    base["store_id"] = "CONCURRENT"
    def save(index: int) -> int:
        payload = deepcopy(base); payload["report_id"] = f"concurrent-{index}"; payload.pop("version", None)
        return state.save_daily_report(payload)["version"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        versions = list(pool.map(save, (1, 2)))
    assert sorted(versions) == [1, 2]
