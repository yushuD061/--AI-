"""Deterministic CSV cleaning pipeline for the store sales dataset."""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
import sqlite3
import sys
import uuid
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable

EXPECTED_HEADERS = {
    "sales.csv": ["order_id", "date", "store_id", "product_id", "qty", "amount", "payment"],
    "stores.csv": ["store_id", "store_name", "category", "district"],
    "products.csv": ["product_id", "product_name", "product_category", "unit_price"],
}
PAYMENTS = {"微信", "支付宝", "会员储值", "银行卡", "现金"}
AS_OF_DATE = date(2026, 8, 19)
MONEY_QUANTUM = Decimal("0.01")


def record_quality_run(root: Path, status: str, report: dict[str, Any] | None = None,
                       error_message: str | None = None) -> None:
    path = root / "data" / "app" / "app.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    try:
        db.execute("""CREATE TABLE IF NOT EXISTS quality_runs (
          run_id TEXT PRIMARY KEY, status TEXT NOT NULL, completed_at TEXT NOT NULL,
          report_json TEXT, error_message TEXT
        )""")
        db.execute("INSERT INTO quality_runs VALUES (?, ?, ?, ?, ?)", (
            f"qr_{uuid.uuid4().hex[:16]}", status, datetime.now(timezone.utc).isoformat(),
            json.dumps(report, ensure_ascii=False) if report else None, error_message,
        ))
        db.commit()
    finally:
        db.close()


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = "".join(ch for ch in value.strip() if ch.isprintable())
    return value or None


def clean_id(value: str | None) -> str | None:
    value = clean_text(value)
    return value.upper() if value else None


def parse_date(value: str | None) -> tuple[str | None, str | None]:
    raw = clean_text(value)
    if not raw:
        return None, "MISSING_VALUE"
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            parsed = datetime.strptime(raw, fmt).date()
            if parsed > AS_OF_DATE:
                return None, "FUTURE_DATE"
            return parsed.isoformat(), None
        except ValueError:
            continue
    return None, "INVALID_DATE"


def parse_decimal(value: str | None, *, money: bool = False) -> Decimal | None:
    raw = clean_text(value)
    if not raw:
        return None
    normalized = raw.replace("¥", "").replace("￥", "").replace(",", "").strip()
    try:
        number = Decimal(normalized)
    except InvalidOperation:
        return None
    return number.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP) if money else number


def read_csv(path: Path, expected: list[str]) -> tuple[list[dict[str, str]], list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != expected:
            raise ValueError(f"{path.name}: expected header {expected}, got {reader.fieldnames}")
        rows: list[dict[str, str]] = []
        malformed: list[str] = []
        for line_number, row in enumerate(reader, start=2):
            if None in row:
                malformed.append(f"{path.name}:{line_number}")
                continue
            rows.append(row)
    return rows, malformed


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def reset_output_dirs(root: Path) -> tuple[Path, Path, Path, Path]:
    raw = root / "data" / "raw"
    clean = root / "data" / "clean"
    quarantine = root / "data" / "quarantine"
    reports = root / "data" / "reports"
    for directory in (clean, quarantine, reports):
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True, exist_ok=True)
    raw.mkdir(parents=True, exist_ok=True)
    return raw, clean, quarantine, reports


def add_issue(issues: list[dict[str, Any]], source_file: str, source_row: int | None,
              record_key: str, field_name: str, raw_value: Any, error_type: str,
              message: str, severity: str) -> None:
    issues.append({
        "source_file": source_file, "source_row": source_row, "record_key": record_key,
        "field_name": field_name, "raw_value": "" if raw_value is None else str(raw_value),
        "error_type": error_type, "error_message": message, "severity": severity,
    })


def decimal_json(value: Decimal) -> float:
    return float(value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP))


def clean_dataset(root: Path) -> dict[str, Any]:
    source_dir = root / "data"
    raw_dir, clean_dir, quarantine_dir, reports_dir = reset_output_dirs(root)
    source_paths = {name: source_dir / name for name in EXPECTED_HEADERS}
    for name, path in source_paths.items():
        if not path.exists():
            raise FileNotFoundError(f"missing input file: {path}")
        shutil.copy2(path, raw_dir / name)

    manifest = {
        "as_of_date": AS_OF_DATE.isoformat(),
        "files": [{"file": name, "sha256": sha256(path), "bytes": path.stat().st_size}
                  for name, path in source_paths.items()],
    }
    (reports_dir / "import_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    issues: list[dict[str, Any]] = []
    counts = Counter()
    stores_rows, malformed_stores = read_csv(source_paths["stores.csv"], EXPECTED_HEADERS["stores.csv"])
    products_rows, malformed_products = read_csv(source_paths["products.csv"], EXPECTED_HEADERS["products.csv"])
    sales_rows, malformed_sales = read_csv(source_paths["sales.csv"], EXPECTED_HEADERS["sales.csv"])
    for item in malformed_stores + malformed_products + malformed_sales:
        add_issue(issues, item.split(":")[0], int(item.split(":")[1]), "", "", "", "MALFORMED_CSV", "row has an incorrect number of columns", "ERROR")
        counts["isolated_rows"] += 1

    stores: dict[str, tuple[str, str, str]] = {}
    products: dict[str, tuple[str, str, Decimal]] = {}
    for source_row, row in enumerate(stores_rows, start=2):
        sid = clean_id(row["store_id"])
        values = (clean_text(row["store_name"]), clean_text(row["category"]), clean_text(row["district"]))
        if not sid or any(value is None for value in values):
            add_issue(issues, "stores.csv", source_row, sid or "", "", "", "MISSING_VALUE", "store key or dimension value is missing", "ERROR")
            counts["isolated_rows"] += 1
        elif sid in stores:
            if stores[sid] != values:
                add_issue(issues, "stores.csv", source_row, sid, "store_id", sid, "CONFLICTING_DIMENSION", "store_id has conflicting dimension values", "ERROR")
                counts["isolated_rows"] += 1
            else:
                counts["deduplicated_rows"] += 1
        else:
            stores[sid] = values  # type: ignore[assignment]

    for source_row, row in enumerate(products_rows, start=2):
        pid = clean_id(row["product_id"])
        price = parse_decimal(row["unit_price"], money=True)
        values = (clean_text(row["product_name"]), clean_text(row["product_category"]), price)
        if not pid or values[0] is None or values[1] is None or price is None or price <= 0:
            add_issue(issues, "products.csv", source_row, pid or "", "unit_price", row["unit_price"], "INVALID_NUMBER", "product dimension is incomplete or unit_price is not positive", "ERROR")
            counts["isolated_rows"] += 1
        elif pid in products:
            if products[pid] != values:
                add_issue(issues, "products.csv", source_row, pid, "product_id", pid, "CONFLICTING_DIMENSION", "product_id has conflicting dimension values", "ERROR")
                counts["isolated_rows"] += 1
            else:
                counts["deduplicated_rows"] += 1
        else:
            products[pid] = values  # type: ignore[assignment]

    valid_sales: list[dict[str, Any]] = []
    seen_exact: set[tuple[Any, ...]] = set()
    seen_candidate: set[tuple[Any, ...]] = set()
    for source_row, row in enumerate(sales_rows, start=2):
        order_id = clean_text(row["order_id"])
        store_id = clean_id(row["store_id"])
        product_id = clean_id(row["product_id"])
        payment = clean_text(row["payment"])
        date_clean, date_error = parse_date(row["date"])
        qty = parse_decimal(row["qty"])
        amount = parse_decimal(row["amount"], money=True)
        exact_key = tuple(clean_text(row.get(field)) for field in EXPECTED_HEADERS["sales.csv"])
        candidate_key = (order_id, product_id, date_clean or clean_text(row["date"]), store_id, qty, amount, payment)
        record_key = order_id or f"row-{source_row}"
        if exact_key in seen_exact:
            add_issue(issues, "sales.csv", source_row, record_key, "", "", "DUPLICATE_ROW", "exact duplicate sales row removed", "INFO")
            counts["deduplicated_rows"] += 1
            continue
        seen_exact.add(exact_key)
        if candidate_key in seen_candidate:
            add_issue(issues, "sales.csv", source_row, record_key, "", "", "DUPLICATE_ROW", "duplicate candidate retained for review", "WARNING")
        seen_candidate.add(candidate_key)

        if date_error:
            add_issue(issues, "sales.csv", source_row, record_key, "date", row["date"], date_error, "date is missing, invalid, or in the future", "ERROR")
        if not order_id or not store_id or not product_id or not payment:
            add_issue(issues, "sales.csv", source_row, record_key, "", "", "MISSING_VALUE", "required sales field is missing", "ERROR")
        if qty is None:
            add_issue(issues, "sales.csv", source_row, record_key, "qty", row["qty"], "INVALID_NUMBER", "qty is not numeric", "ERROR")
        elif qty == 0:
            add_issue(issues, "sales.csv", source_row, record_key, "qty", row["qty"], "ZERO_QTY", "qty cannot be zero", "ERROR")
        elif qty < 0:
            add_issue(issues, "sales.csv", source_row, record_key, "qty", row["qty"], "NEGATIVE_QTY", "negative quantity retained as refund candidate", "WARNING")
            counts["negative_qty"] += 1
        if amount is None:
            add_issue(issues, "sales.csv", source_row, record_key, "amount", row["amount"], "MISSING_VALUE", "amount is missing or invalid", "ERROR")
        elif amount < 0:
            add_issue(issues, "sales.csv", source_row, record_key, "amount", row["amount"], "NEGATIVE_AMOUNT", "negative amount retained as refund candidate", "WARNING")
            counts["negative_amount"] += 1
        if store_id not in stores:
            add_issue(issues, "sales.csv", source_row, record_key, "store_id", row["store_id"], "INVALID_STORE_FK", "store_id does not exist in stores", "ERROR")
            counts["invalid_store_fk"] += 1
        if product_id not in products:
            add_issue(issues, "sales.csv", source_row, record_key, "product_id", row["product_id"], "INVALID_PRODUCT_FK", "product_id does not exist in products", "ERROR")
            counts["invalid_product_fk"] += 1
        if payment and payment not in PAYMENTS:
            add_issue(issues, "sales.csv", source_row, record_key, "payment", payment, "UNKNOWN_PAYMENT", "payment method is outside the known enumeration", "WARNING")
        if date_error or not order_id or not store_id or not product_id or not payment or qty is None or qty == 0 or amount is None or store_id not in stores or product_id not in products:
            counts["isolated_rows"] += 1
            continue
        store_name, store_category, district = stores[store_id]
        product_name, product_category, unit_price = products[product_id]
        if abs(amount - (qty * unit_price)) > Decimal("0.01"):
            add_issue(issues, "sales.csv", source_row, record_key, "amount", row["amount"], "AMOUNT_PRICE_MISMATCH", "amount differs from qty multiplied by unit_price; retained", "WARNING")
        valid_sales.append({
            "source_row": source_row, "order_id": order_id, "date_raw": clean_text(row["date"]), "date_clean": date_clean,
            "store_id": store_id, "store_name": store_name, "store_category": store_category, "district": district,
            "product_id": product_id, "product_name": product_name, "product_category": product_category,
            "qty_raw": clean_text(row["qty"]), "qty_clean": str(qty), "amount_raw": clean_text(row["amount"]),
            "amount_clean": decimal_json(amount), "payment": payment, "unit_price": decimal_json(unit_price),
        })

    db_path = clean_dir / "sales_clean.sqlite"
    db = sqlite3.connect(db_path)
    try:
        db.executescript("""
        CREATE TABLE stores_clean (store_id TEXT PRIMARY KEY, store_name TEXT NOT NULL, category TEXT NOT NULL, district TEXT NOT NULL);
        CREATE TABLE products_clean (product_id TEXT PRIMARY KEY, product_name TEXT NOT NULL, product_category TEXT NOT NULL, unit_price NUMERIC NOT NULL);
        CREATE TABLE sales_clean (source_row INTEGER, order_id TEXT, date_raw TEXT, date_clean TEXT, store_id TEXT, store_name TEXT, store_category TEXT, district TEXT, product_id TEXT, product_name TEXT, product_category TEXT, qty_raw TEXT, qty_clean NUMERIC, amount_raw TEXT, amount_clean NUMERIC, payment TEXT, unit_price NUMERIC);
        CREATE TABLE quarantine (source_file TEXT, source_row INTEGER, record_key TEXT, field_name TEXT, raw_value TEXT, error_type TEXT, error_message TEXT, severity TEXT);
        CREATE TABLE cleaning_runs (as_of_date TEXT, raw_sales_rows INTEGER, valid_sales_rows INTEGER, isolated_rows INTEGER, deduplicated_rows INTEGER);
        """)
        db.executemany("INSERT INTO stores_clean VALUES (?, ?, ?, ?)", [(key, *values) for key, values in stores.items()])
        db.executemany("INSERT INTO products_clean VALUES (?, ?, ?, ?)", [(key, values[0], values[1], str(values[2])) for key, values in products.items()])
        sales_columns = ["source_row", "order_id", "date_raw", "date_clean", "store_id", "store_name", "store_category", "district", "product_id", "product_name", "product_category", "qty_raw", "qty_clean", "amount_raw", "amount_clean", "payment", "unit_price"]
        db.executemany(f"INSERT INTO sales_clean ({','.join(sales_columns)}) VALUES ({','.join('?' for _ in sales_columns)})", [[row[column] for column in sales_columns] for row in valid_sales])
        db.executemany("INSERT INTO quarantine VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [[issue[key] for key in ("source_file", "source_row", "record_key", "field_name", "raw_value", "error_type", "error_message", "severity")] for issue in issues])
        net_revenue = sum((Decimal(str(row["amount_clean"])) for row in valid_sales), Decimal("0"))
        order_count = len({row["order_id"] for row in valid_sales})
        db.execute("INSERT INTO cleaning_runs VALUES (?, ?, ?, ?, ?)", (AS_OF_DATE.isoformat(), len(sales_rows), len(valid_sales), counts["isolated_rows"], counts["deduplicated_rows"]))
        db.commit()
    finally:
        db.close()

    quarantine_path = quarantine_dir / "quarantine.csv"
    issue_fields = ["source_file", "source_row", "record_key", "field_name", "raw_value", "error_type", "error_message", "severity"]
    with quarantine_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=issue_fields)
        writer.writeheader()
        writer.writerows(issues)

    net_revenue = sum((Decimal(str(row["amount_clean"])) for row in valid_sales), Decimal("0"))
    order_count = len({row["order_id"] for row in valid_sales})
    report = {
        "as_of_date": AS_OF_DATE.isoformat(), "raw_rows": {"sales": len(sales_rows), "stores": len(stores_rows), "products": len(products_rows)},
        "valid_rows": len(valid_sales), "isolated_rows": counts["isolated_rows"], "deduplicated_rows": counts["deduplicated_rows"],
        "auto_corrected_rows": 0, "missing_value_count": sum(1 for issue in issues if issue["error_type"] == "MISSING_VALUE"),
        "invalid_store_fk": counts["invalid_store_fk"], "invalid_product_fk": counts["invalid_product_fk"],
        "negative_qty": counts["negative_qty"], "negative_amount": counts["negative_amount"],
        "date_min": min((row["date_clean"] for row in valid_sales), default=None), "date_max": max((row["date_clean"] for row in valid_sales), default=None),
        "net_revenue": decimal_json(net_revenue), "order_count": order_count,
        "average_order_value": decimal_json(net_revenue / order_count) if order_count else 0.0,
        "issues_by_type": dict(sorted(Counter(issue["error_type"] for issue in issues).items())),
        "issues_by_severity": dict(sorted(Counter(issue["severity"] for issue in issues).items())),
    }
    (reports_dir / "cleaning_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record_quality_run(root, "success", report)
    return report


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    try:
        report = clean_dataset(root)
    except (OSError, ValueError, csv.Error) as exc:
        record_quality_run(root, "failed", error_message=str(exc))
        print(f"清洗失败: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
