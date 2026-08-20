from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .models import AssistantMessage, Conversation

APP_DB = Path(__file__).resolve().parents[3] / "data" / "app" / "app.sqlite"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    APP_DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(APP_DB)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript("""
      CREATE TABLE IF NOT EXISTS ai_conversations (
        conversation_id TEXT PRIMARY KEY, title TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS ai_messages (
        message_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
        role TEXT NOT NULL, content TEXT NOT NULL, query_plan_json TEXT,
        facts_json TEXT, status TEXT NOT NULL, created_at TEXT NOT NULL,
        FOREIGN KEY (conversation_id) REFERENCES ai_conversations(conversation_id) ON DELETE CASCADE
      );
    """)
    db.commit()
    return db


def create_conversation(title: str = "新对话") -> Conversation:
    conversation_id = f"c_{uuid.uuid4().hex[:12]}"
    now = _now()
    db = connect()
    try:
        db.execute("INSERT INTO ai_conversations VALUES (?, ?, ?, ?)", (conversation_id, title.strip() or "新对话", now, now))
        db.commit()
        return Conversation(conversation_id=conversation_id, title=title.strip() or "新对话", message_count=0, created_at=now, updated_at=now)
    finally:
        db.close()


def list_conversations() -> list[Conversation]:
    db = connect()
    try:
        rows = db.execute("""SELECT c.*, COUNT(m.message_id) AS message_count FROM ai_conversations c
          LEFT JOIN ai_messages m ON m.conversation_id = c.conversation_id
          GROUP BY c.conversation_id ORDER BY c.updated_at DESC""").fetchall()
        return [Conversation(**dict(row)) for row in rows]
    finally:
        db.close()


def conversation_exists(conversation_id: str) -> bool:
    db = connect()
    try:
        return db.execute("SELECT 1 FROM ai_conversations WHERE conversation_id = ?", (conversation_id,)).fetchone() is not None
    finally:
        db.close()


def delete_conversation(conversation_id: str) -> None:
    db = connect()
    try:
        cursor = db.execute("DELETE FROM ai_conversations WHERE conversation_id = ?", (conversation_id,))
        db.commit()
        if cursor.rowcount == 0:
            raise LookupError("对话不存在")
    finally:
        db.close()


def add_message(conversation_id: str, role: str, content: str, status: str, query_plan: dict | None = None, facts: dict | None = None) -> AssistantMessage:
    message_id = f"m_{uuid.uuid4().hex[:12]}"
    created_at = _now()
    db = connect()
    try:
        db.execute("INSERT INTO ai_messages VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (message_id, conversation_id, role, content, json.dumps(query_plan, ensure_ascii=False, default=str) if query_plan else None, json.dumps(facts, ensure_ascii=False, default=str) if facts else None, status, created_at))
        db.execute("UPDATE ai_conversations SET updated_at = ? WHERE conversation_id = ?", (created_at, conversation_id))
        db.commit()
        return _message_model({"message_id": message_id, "role": role, "content": content, "status": status, "facts_json": json.dumps(facts, ensure_ascii=False, default=str) if facts else None, "query_plan_json": json.dumps(query_plan, ensure_ascii=False, default=str) if query_plan else None, "created_at": created_at})
    finally:
        db.close()


def get_messages(conversation_id: str) -> list[AssistantMessage]:
    db = connect()
    try:
        if not conversation_exists(conversation_id):
            raise LookupError("对话不存在")
        rows = db.execute("SELECT * FROM ai_messages WHERE conversation_id = ? ORDER BY created_at, rowid", (conversation_id,)).fetchall()
        result = []
        for row in rows:
            result.append(_message_model(row))
        return result
    finally:
        db.close()


def _message_model(row: sqlite3.Row | dict) -> AssistantMessage:
    plan = json.loads(row["query_plan_json"]) if row["query_plan_json"] else None
    facts = json.loads(row["facts_json"]) if row["facts_json"] else None
    context = None
    target = None
    if plan:
        context = {key: plan.get(key) for key in ("operation", "changed_fields", "inherited_fields", "previous_message_id")}
    if facts:
        filters = facts.get("filters", {})
        target = {
            "start_date": filters.get("start_date"),
            "end_date": filters.get("end_date"),
            "store_id": filters.get("store_id"),
            "metric": facts.get("metric", "net_revenue"),
            "view": "products" if facts.get("intent") == "top_products" else "trend",
        }
    return AssistantMessage(message_id=row["message_id"], role=row["role"], content=row["content"], status=row["status"], facts=facts, query_plan=plan, context=context, dashboard_target=target, created_at=row["created_at"])


def last_query_plan(conversation_id: str) -> dict | None:
    db = connect()
    try:
        row = db.execute("""SELECT message_id, query_plan_json FROM ai_messages
          WHERE conversation_id = ? AND role = 'assistant' AND query_plan_json IS NOT NULL
            AND facts_json IS NOT NULL AND status IN ('answered', 'answered_local', 'provider_error')
          ORDER BY created_at DESC, rowid DESC LIMIT 1""", (conversation_id,)).fetchone()
        if not row:
            return None
        plan = json.loads(row["query_plan_json"])
        plan["previous_message_id"] = row["message_id"]
        return plan
    finally:
        db.close()
