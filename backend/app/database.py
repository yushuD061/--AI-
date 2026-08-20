from __future__ import annotations

import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATABASE_PATH = PROJECT_ROOT / "data" / "clean" / "sales_clean.sqlite"


def database_available() -> bool:
    return DATABASE_PATH.is_file()


def connect() -> sqlite3.Connection:
    if not database_available():
        raise FileNotFoundError(
            f"清洗数据库不存在: {DATABASE_PATH}，请先运行 python scripts/clean_data.py"
        )
    connection = sqlite3.connect(f"file:{DATABASE_PATH.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection
