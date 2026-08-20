from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import database
from app.ai import config, conversations
from app.ai.answer import extract_business_numbers
from app.main import app


REPORT_PATH = Path(__file__).resolve().parents[2] / "data" / "reports" / "ai_answer_verification.json"


@pytest.fixture()
def truth_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    sales_path = tmp_path / "sales_clean.sqlite"
    db = sqlite3.connect(sales_path)
    db.executescript("""
      CREATE TABLE stores_clean (store_id TEXT PRIMARY KEY, store_name TEXT, category TEXT, district TEXT);
      CREATE TABLE sales_clean (order_id TEXT, date_clean TEXT, store_id TEXT, product_id TEXT,
        product_name TEXT, product_category TEXT, qty_clean NUMERIC, amount_clean NUMERIC);
      INSERT INTO stores_clean VALUES ('S01', 'Makai Poke', '轻食', '上海');
      INSERT INTO stores_clean VALUES ('S02', 'Super Souper', '轻食', '上海');
      INSERT INTO sales_clean VALUES ('M1', '2026-05-03', 'S01', 'P01', '牛肉poke', '主食', 1, 40);
      INSERT INTO sales_clean VALUES ('M2', '2026-05-04', 'S01', 'P02', '三文鱼poke', '主食', 1, 30);
      INSERT INTO sales_clean VALUES ('J1', '2026-06-01', 'S01', 'P01', '牛肉poke', '主食', 2, 50);
      INSERT INTO sales_clean VALUES ('J2', '2026-06-02', 'S01', 'P01', '牛肉poke', '主食', 1, 25);
      INSERT INTO sales_clean VALUES ('J3', '2026-06-02', 'S01', 'P02', '三文鱼poke', '主食', 1, 38);
      INSERT INTO sales_clean VALUES ('J4', '2026-06-03', 'S02', 'P01', '牛肉poke', '主食', 1, 20);
      INSERT INTO sales_clean VALUES ('J5', '2026-06-04', 'S01', 'P01', '牛肉poke', '主食', -1, -10);
    """)
    db.commit(); db.close()
    monkeypatch.setattr(database, "DATABASE_PATH", sales_path)
    monkeypatch.setattr(conversations, "APP_DB", tmp_path / "app.sqlite")
    monkeypatch.setattr(config, "_config", config.LLMConfig("deepseek", "", "https://api.deepseek.com", "", 30))
    with TestClient(app) as client:
        yield client, sales_path, monkeypatch


def direct_truth(path: Path, start: str, end: str, product: str | None = None, store: str | None = None) -> dict:
    clauses = ["date_clean >= ?", "date_clean <= ?"]
    params: list[object] = [start, end]
    if product:
        clauses.append("product_name = ?"); params.append(product)
    if store:
        clauses.append("store_id = ?"); params.append(store)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    try:
        row = db.execute(f"""SELECT COALESCE(SUM(amount_clean), 0) AS net_revenue,
          COUNT(DISTINCT order_id) AS order_count FROM sales_clean WHERE {' AND '.join(clauses)}""", params).fetchone()
        revenue, orders = float(row["net_revenue"]), int(row["order_count"])
        return {"net_revenue": revenue, "order_count": orders, "average_order_value": round(revenue / orders, 2) if orders else 0}
    finally:
        db.close()


def ask(client: TestClient, conversation_id: str, question: str) -> tuple[dict, int]:
    started = time.perf_counter()
    response = client.post("/api/v1/ai/query", json={"conversation_id": conversation_id, "question": question})
    assert response.status_code == 200
    return response.json(), round((time.perf_counter() - started) * 1000)


def test_context_chain_matches_independent_sql_and_writes_report(truth_client) -> None:
    client, sales_path, _ = truth_client
    conversation_id = client.post("/api/v1/ai/conversations", json={}).json()["conversation_id"]
    cases = [
        ("beef_june", "牛肉poke 六月卖了多少钱？", "2026-06-01", "2026-06-30", "牛肉poke", None, "net_revenue"),
        ("beef_may", "那五月呢？", "2026-05-01", "2026-05-31", "牛肉poke", None, "net_revenue"),
        ("salmon_may", "换成三文鱼poke呢？", "2026-05-01", "2026-05-31", "三文鱼poke", None, "net_revenue"),
        ("salmon_s01", "S01 门店呢？", "2026-05-01", "2026-05-31", "三文鱼poke", "S01", "net_revenue"),
        ("salmon_s01_orders", "订单数呢？", "2026-05-01", "2026-05-31", "三文鱼poke", "S01", "order_count"),
    ]
    report = []
    history: list[str] = []
    for case_id, question, start, end, product, store, metric in cases:
        expected = direct_truth(sales_path, start, end, product, store)
        result, duration = ask(client, conversation_id, question)
        assert result["facts"]["value"] == expected[metric]
        assert result["query_plan"]["product_name"] == product
        assert result["query_plan"]["store_id"] == store
        assert result["dashboard_target"]["start_date"] == start
        assert result["dashboard_target"]["end_date"] == end
        assert result["dashboard_target"]["store_id"] == store
        assert result["context"]["previous_message_id"] is not None or case_id == "beef_june"
        numbers = extract_business_numbers(result["message"]["content"])
        report.append({
            "case_id": case_id, "question": question, "conversation_history": history.copy(),
            "query_plan": result["query_plan"], "expected_database_facts": expected,
            "actual_facts": result["facts"], "final_answer": result["message"]["content"],
            "numbers_in_answer": numbers, "consistency_passed": True,
            "fallback_used": result["status"] != "answered", "duration_ms": duration,
        })
        history.append(question)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps({"consistency_rate": 1.0, "passed": len(report), "total": len(report), "cases": report}, ensure_ascii=False, indent=2), encoding="utf-8")
    assert json.loads(REPORT_PATH.read_text(encoding="utf-8"))["consistency_rate"] == 1.0


def test_follow_up_without_context_requires_clarification(truth_client) -> None:
    client, _, _ = truth_client
    conversation_id = client.post("/api/v1/ai/conversations", json={}).json()["conversation_id"]
    result, _ = ask(client, conversation_id, "那五月呢？")
    assert result["status"] == "clarification_required"
    assert result["facts"] is None


def test_wrong_model_number_is_replaced_by_verified_template(truth_client) -> None:
    client, _, monkeypatch = truth_client
    monkeypatch.setattr("app.main.generate_answer", lambda _question, _facts: "净营业额为 ¥999,999.00。")
    conversation_id = client.post("/api/v1/ai/conversations", json={}).json()["conversation_id"]
    result, _ = ask(client, conversation_id, "牛肉poke 六月卖了多少钱？")
    assert result["status"] == "answered_local"
    assert "999,999" not in result["message"]["content"]
    assert result["facts"]["value"] == 85.0


def test_unsupported_and_missing_product_do_not_invent_numbers(truth_client) -> None:
    client, _, _ = truth_client
    conversation_id = client.post("/api/v1/ai/conversations", json={}).json()["conversation_id"]
    unsupported, _ = ask(client, conversation_id, "六月利润是多少？")
    missing, _ = ask(client, conversation_id, "榴莲poke 六月卖了多少钱？")
    assert unsupported["status"] == "unsupported" and unsupported["facts"] is None
    assert missing["status"] == "no_data"
    assert missing["facts"]["value"] is None and missing["facts"]["rows"] == []
    assert extract_business_numbers(unsupported["message"]["content"]) == []
    assert extract_business_numbers(missing["message"]["content"]) == []
