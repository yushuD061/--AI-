from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import database
from app.ai import config, conversations
from app.main import app


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    sales_path = tmp_path / "sales_clean.sqlite"
    app_path = tmp_path / "app.sqlite"
    db = sqlite3.connect(sales_path)
    db.executescript("""
      CREATE TABLE stores_clean (store_id TEXT PRIMARY KEY, store_name TEXT, category TEXT, district TEXT);
      CREATE TABLE sales_clean (order_id TEXT, date_clean TEXT, store_id TEXT, product_id TEXT, product_name TEXT, product_category TEXT, qty_clean NUMERIC, amount_clean NUMERIC);
      INSERT INTO stores_clean VALUES ('S01', 'Makai Poke', '轻食', '上海');
      INSERT INTO sales_clean VALUES ('O1', '2026-06-01', 'S01', 'P01', '牛肉poke', '主食', 2, 50);
      INSERT INTO sales_clean VALUES ('O2', '2026-06-02', 'S01', 'P01', '牛肉poke', '主食', 1, 25);
      INSERT INTO sales_clean VALUES ('O3', '2026-06-02', 'S01', 'P02', '三文鱼poke', '主食', 1, 38);
    """)
    db.commit(); db.close()
    monkeypatch.setattr(database, "DATABASE_PATH", sales_path)
    monkeypatch.setattr(conversations, "APP_DB", app_path)
    monkeypatch.setattr(config, "_config", config.LLMConfig("deepseek", "", "https://api.deepseek.com", "super-secret-key", 30))
    with TestClient(app) as test_client:
        yield test_client


def test_config_masks_key_and_updates_runtime(client: TestClient) -> None:
    response = client.get("/api/v1/ai/config")
    assert response.status_code == 200
    assert "super-secret-key" not in response.text
    updated = client.patch("/api/v1/ai/config", json={"provider": "openai-compatible", "model": "test-model", "base_url": "https://example.com/v1", "timeout_seconds": 20})
    assert updated.status_code == 200
    assert updated.json()["data"]["source"] == "runtime"
    assert client.patch("/api/v1/ai/config", json={"provider": "x", "model": "m", "base_url": "bad", "timeout_seconds": 20}).status_code == 422


def test_local_question_uses_true_database_fact(client: TestClient) -> None:
    conversation = client.post("/api/v1/ai/conversations", json={}).json()
    response = client.post("/api/v1/ai/query", json={"conversation_id": conversation["conversation_id"], "question": "牛肉poke 六月卖了多少钱？"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"answered_local", "provider_error"}
    assert body["facts"]["value"] == 75.0
    assert "75.00" in body["message"]["content"]


def test_follow_up_and_delete_conversation(client: TestClient) -> None:
    conversation = client.post("/api/v1/ai/conversations", json={}).json()
    first = client.post("/api/v1/ai/query", json={"conversation_id": conversation["conversation_id"], "question": "六月营业额是多少？"})
    assert first.status_code == 200
    follow = client.post("/api/v1/ai/query", json={"conversation_id": conversation["conversation_id"], "question": "那五月呢？"})
    assert follow.status_code == 200
    assert follow.json()["facts"]["filters"]["start_date"] == "2026-05-01"
    assert client.delete(f"/api/v1/ai/conversations/{conversation['conversation_id']}").status_code == 200
    assert client.get(f"/api/v1/ai/conversations/{conversation['conversation_id']}/messages").status_code == 422


def test_unsupported_question_never_invents_facts(client: TestClient) -> None:
    conversation = client.post("/api/v1/ai/conversations", json={}).json()
    result = client.post("/api/v1/ai/query", json={"conversation_id": conversation["conversation_id"], "question": "这个月利润是多少？"}).json()
    assert result["status"] == "unsupported"
    assert result["facts"] is None
