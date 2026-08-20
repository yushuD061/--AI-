from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

APP_DB = Path(__file__).resolve().parents[2] / "data" / "app" / "app.sqlite"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(path: Path | None = None) -> sqlite3.Connection:
    target = path or APP_DB
    target.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(target)
    db.row_factory = sqlite3.Row
    db.executescript("""
      CREATE TABLE IF NOT EXISTS quality_runs (
        run_id TEXT PRIMARY KEY, status TEXT NOT NULL, completed_at TEXT NOT NULL,
        report_json TEXT, error_message TEXT
      );
      CREATE TABLE IF NOT EXISTS alert_states (
        alert_id TEXT PRIMARY KEY, is_read INTEGER NOT NULL, updated_at TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS alert_catalog (
        alert_id TEXT PRIMARY KEY, observed_at TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS daily_reports (
        report_id TEXT PRIMARY KEY, report_date TEXT NOT NULL, store_id TEXT NOT NULL,
        version INTEGER NOT NULL, generated_at TEXT NOT NULL, data_version TEXT NOT NULL,
        quality_status TEXT NOT NULL, snapshot_json TEXT NOT NULL,
        UNIQUE(report_date, store_id, version)
      );
      CREATE TABLE IF NOT EXISTS users (
        user_id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
        role TEXT NOT NULL, display_name TEXT NOT NULL, active INTEGER NOT NULL, created_at TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS user_store_scope (
        user_id TEXT NOT NULL, store_id TEXT NOT NULL, PRIMARY KEY(user_id, store_id)
      );
      CREATE TABLE IF NOT EXISTS auth_sessions (
        token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL, revoked_at TEXT
      );
      CREATE TABLE IF NOT EXISTS alert_user_states (
        user_id TEXT NOT NULL, alert_id TEXT NOT NULL, is_read INTEGER NOT NULL,
        updated_at TEXT NOT NULL, PRIMARY KEY(user_id, alert_id)
      );
      CREATE TABLE IF NOT EXISTS audit_events (
        event_id TEXT PRIMARY KEY, user_id TEXT, role TEXT NOT NULL, action TEXT NOT NULL,
        resource TEXT NOT NULL, filters_json TEXT NOT NULL, request_id TEXT NOT NULL,
        created_at TEXT NOT NULL, result TEXT NOT NULL
      );
    """)
    db.commit()
    return db


def record_quality_run(status: str, report: dict[str, Any] | None = None,
                       error_message: str | None = None, path: Path | None = None,
                       completed_at: str | None = None) -> dict[str, Any]:
    run = {
        "run_id": f"qr_{uuid.uuid4().hex[:16]}",
        "status": status,
        "completed_at": completed_at or _now(),
        "report": report,
        "error_message": error_message,
    }
    db = connect(path)
    try:
        db.execute(
            "INSERT INTO quality_runs VALUES (?, ?, ?, ?, ?)",
            (run["run_id"], status, run["completed_at"],
             json.dumps(report, ensure_ascii=False) if report else None, error_message),
        )
        db.commit()
        return run
    finally:
        db.close()


def list_quality_runs(limit: int = 20) -> list[dict[str, Any]]:
    db = connect()
    try:
        rows = db.execute(
            "SELECT * FROM quality_runs ORDER BY completed_at DESC, rowid DESC LIMIT ?", (limit,)
        ).fetchall()
        return [{
            "run_id": row["run_id"], "status": row["status"],
            "completed_at": row["completed_at"],
            "report": json.loads(row["report_json"]) if row["report_json"] else None,
            "error_message": row["error_message"],
        } for row in rows]
    finally:
        db.close()


def read_states(alert_ids: list[str], user_id: str = "legacy") -> dict[str, bool]:
    if not alert_ids:
        return {}
    db = connect()
    try:
        placeholders = ",".join("?" for _ in alert_ids)
        rows = db.execute(
            f"SELECT alert_id, is_read FROM alert_user_states WHERE user_id = ? AND alert_id IN ({placeholders})", [user_id, *alert_ids]
        ).fetchall()
        return {row["alert_id"]: bool(row["is_read"]) for row in rows}
    finally:
        db.close()


def set_alert_read(alert_id: str, is_read: bool, user_id: str = "legacy") -> None:
    db = connect()
    try:
        db.execute(
            "INSERT INTO alert_user_states VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, alert_id) DO UPDATE SET is_read=excluded.is_read, updated_at=excluded.updated_at",
            (user_id, alert_id, int(is_read), _now()),
        )
        db.commit()
    finally:
        db.close()


def register_alerts(alert_ids: list[str]) -> None:
    if not alert_ids:
        return
    db = connect()
    try:
        db.executemany(
            "INSERT INTO alert_catalog VALUES (?, ?) ON CONFLICT(alert_id) DO NOTHING",
            [(alert_id, _now()) for alert_id in alert_ids],
        )
        db.commit()
    finally:
        db.close()


def alert_registered(alert_id: str) -> bool:
    db = connect()
    try:
        return db.execute("SELECT 1 FROM alert_catalog WHERE alert_id = ?", (alert_id,)).fetchone() is not None
    finally:
        db.close()


def save_daily_report(report: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    db = connect(path)
    try:
        scope_key = report.get("scope_key") if report["store_id"] == "ALL" else report["store_id"]
        scope_key = scope_key or report["store_id"]
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM daily_reports WHERE report_date = ? AND store_id = ?",
            (report["report_date"], scope_key),
        ).fetchone()
        report["version"] = int(row[0])
        db.execute(
            "INSERT INTO daily_reports VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (report["report_id"], report["report_date"], scope_key, report["version"],
             report["generated_at"], report["data_version"], report["quality_status"],
             json.dumps(report, ensure_ascii=False)),
        )
        db.commit()
        return report
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def list_daily_reports(report_date: str | None = None, store_id: str | None = None,
                       limit: int = 20) -> list[dict[str, Any]]:
    clauses, params = [], []
    if report_date:
        clauses.append("report_date = ?"); params.append(report_date)
    if store_id:
        clauses.append("store_id = ?"); params.append(store_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    db = connect()
    try:
        rows = db.execute(f"SELECT report_id, report_date, store_id, version, generated_at, data_version, quality_status, snapshot_json FROM daily_reports {where} ORDER BY report_date DESC, version DESC LIMIT ?", [*params, limit]).fetchall()
        result = []
        for row in rows:
            snapshot = json.loads(row["snapshot_json"])
            result.append({key: snapshot.get(key, row[key] if key in row.keys() else None) for key in ("report_id", "report_date", "store_id", "version", "generated_at", "data_version", "quality_status", "scope_store_ids")})
        return result
    finally:
        db.close()


def get_daily_report(report_id: str) -> dict[str, Any] | None:
    db = connect()
    try:
        row = db.execute("SELECT snapshot_json FROM daily_reports WHERE report_id = ?", (report_id,)).fetchone()
        return json.loads(row[0]) if row else None
    finally:
        db.close()


def record_audit(event: dict[str, Any]) -> None:
    db = connect()
    try:
        db.execute("INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (
            event["event_id"], event["user_id"], event["role"], event["action"], event["resource"],
            json.dumps(event["filters"], ensure_ascii=False), event["request_id"], event["created_at"], event["result"],
        ))
        db.commit()
    finally:
        db.close()


def list_audit(limit: int = 100, user_id: str | None = None, action: str | None = None) -> list[dict[str, Any]]:
    clauses, params = [], []
    if user_id: clauses.append("user_id = ?"); params.append(user_id)
    if action: clauses.append("action = ?"); params.append(action)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    db = connect()
    try:
        rows = db.execute(f"SELECT * FROM audit_events {where} ORDER BY created_at DESC, rowid DESC LIMIT ?", [*params, limit]).fetchall()
        return [{**dict(row), "filters": json.loads(row["filters_json"])} for row in rows]
    finally:
        db.close()
