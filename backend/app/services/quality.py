from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import database, state

PROJECT_ROOT = Path(__file__).resolve().parents[3]
REPORT_PATH = PROJECT_ROOT / "data" / "reports" / "cleaning_report.json"
MANIFEST_PATH = PROJECT_ROOT / "data" / "reports" / "import_manifest.json"
RAW_DIR = PROJECT_ROOT / "data" / "raw"

RANK = {"healthy": 0, "warning": 1, "critical": 2}


def _iso_mtime(path: Path) -> str | None:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat() if path.exists() else None


def _read(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _status(checks: list[dict[str, Any]]) -> str:
    return max((item["status"] for item in checks), key=lambda value: RANK[value], default="healthy")


def _missing_amount_count(report: dict[str, Any] | None) -> int:
    try:
        db = database.connect()
        try:
            row = db.execute(
                "SELECT COUNT(*) FROM quarantine WHERE field_name = 'amount' AND error_type = 'MISSING_VALUE'"
            ).fetchone()
            return int(row[0])
        finally:
            db.close()
    except sqlite3.Error:
        return int((report or {}).get("issues_by_type", {}).get("MISSING_VALUE", 0))


def get_runs(limit: int = 20) -> list[dict[str, Any]]:
    runs = state.list_quality_runs(limit)
    if runs:
        return runs
    report = _read(REPORT_PATH)
    completed_at = _iso_mtime(database.DATABASE_PATH)
    if not report or not completed_at:
        return []
    return [{"run_id": "legacy_current", "status": "success", "completed_at": completed_at,
             "report": report, "error_message": None}]


def get_quality(now: datetime | None = None) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    report = _read(REPORT_PATH)
    manifest = _read(MANIFEST_PATH)
    runs = get_runs(1)
    latest = runs[0] if runs else None
    database_updated_at = _iso_mtime(database.DATABASE_PATH)
    raw_times = [_iso_mtime(path) for path in RAW_DIR.glob("*.csv")] if RAW_DIR.exists() else []
    raw_updated_at = max((value for value in raw_times if value), default=None)
    cleaned_at = latest["completed_at"] if latest and latest["status"] == "success" else database_updated_at

    checks: list[dict[str, Any]] = []
    missing = [name for name, present in {
        "sales_clean.sqlite": database.DATABASE_PATH.exists(),
        "cleaning_report.json": report is not None,
        "import_manifest.json": manifest is not None,
    }.items() if not present]
    checks.append({"name": "artifacts", "status": "critical" if missing else "healthy",
                   "message": "缺少必要产物: " + ", ".join(missing) if missing else "必要数据产物完整"})

    freshness_hours: float | None = None
    if cleaned_at:
        parsed = datetime.fromisoformat(cleaned_at.replace("Z", "+00:00"))
        freshness_hours = max(0.0, (current - parsed.astimezone(timezone.utc)).total_seconds() / 3600)
    freshness_status = "critical" if freshness_hours is None or freshness_hours > 48 else "warning" if freshness_hours > 24 else "healthy"
    checks.append({"name": "freshness", "status": freshness_status,
                   "message": "没有可用清洗时间" if freshness_hours is None else f"距最近成功清洗 {freshness_hours:.1f} 小时"})

    raw_rows = int((report or {}).get("raw_rows", {}).get("sales", 0))
    isolated = int((report or {}).get("isolated_rows", 0))
    isolation_rate = isolated / raw_rows if raw_rows else None
    isolation_status = "critical" if isolation_rate is None or isolation_rate > .15 else "warning" if isolation_rate > .05 else "healthy"
    checks.append({"name": "isolation_rate", "status": isolation_status,
                   "message": "无法计算隔离率" if isolation_rate is None else f"隔离率 {isolation_rate:.2%}"})
    if latest and latest["status"] == "failed":
        checks.append({"name": "last_run", "status": "critical", "message": latest["error_message"] or "最近清洗失败"})

    metrics = {
        "raw_rows": raw_rows, "valid_rows": int((report or {}).get("valid_rows", 0)),
        "isolated_rows": isolated, "isolation_rate": isolation_rate,
        "missing_amount_count": _missing_amount_count(report),
        "invalid_store_fk": int((report or {}).get("invalid_store_fk", 0)),
        "invalid_product_fk": int((report or {}).get("invalid_product_fk", 0)),
        "duplicate_rows": int((report or {}).get("issues_by_type", {}).get("DUPLICATE_ROW", 0)),
        "date_min": (report or {}).get("date_min"), "date_max": (report or {}).get("date_max"),
        "latest_sales_date": (report or {}).get("date_max"),
    }
    return {"status": _status(checks), "raw_updated_at": raw_updated_at,
            "cleaned_at": cleaned_at, "database_updated_at": database_updated_at,
            "metrics": metrics, "checks": checks}
