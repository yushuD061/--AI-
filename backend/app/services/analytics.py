from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from ..database import connect
from ..schemas import DailyPoint, FilterData, Filters, ProductPerformance, Store, Summary

MONEY_QUANTUM = Decimal("0.01")


def _money(value: Any) -> float:
    return float(Decimal(str(value or 0)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP))


def _date_bounds(connection: sqlite3.Connection) -> tuple[date, date]:
    row = connection.execute("SELECT MIN(date_clean), MAX(date_clean) FROM sales_clean").fetchone()
    if not row or not row[0] or not row[1]:
        raise ValueError("sales_clean 中没有可用日期")
    return date.fromisoformat(row[0]), date.fromisoformat(row[1])


def resolve_filters(start_date: date | None, end_date: date | None, store_id: str | None) -> Filters:
    connection = connect()
    try:
        default_start, default_end = _date_bounds(connection)
        resolved_start = start_date or default_start
        resolved_end = end_date or default_end
        if resolved_start > resolved_end:
            raise ValueError("start_date 不能晚于 end_date")
        if store_id is not None:
            exists = connection.execute("SELECT 1 FROM stores_clean WHERE store_id = ?", (store_id,)).fetchone()
            if exists is None:
                raise LookupError(f"未知门店: {store_id}")
        return Filters(start_date=resolved_start, end_date=resolved_end, store_id=store_id)
    finally:
        connection.close()


def filter_sql(filters: Filters) -> tuple[str, dict[str, Any]]:
    clauses = ["date_clean >= :start_date", "date_clean <= :end_date"]
    params: dict[str, Any] = {"start_date": filters.start_date.isoformat(), "end_date": filters.end_date.isoformat()}
    if filters.store_id is not None:
        clauses.append("store_id = :store_id")
        params["store_id"] = filters.store_id
    return " AND ".join(clauses), params


def get_filters() -> FilterData:
    connection = connect()
    try:
        date_min, date_max = _date_bounds(connection)
        stores = [Store(**dict(row)) for row in connection.execute(
            "SELECT store_id, store_name, district FROM stores_clean ORDER BY store_id"
        )]
        return FilterData(date_min=date_min, date_max=date_max, stores=stores)
    finally:
        connection.close()


def get_summary(filters: Filters) -> Summary:
    where, params = filter_sql(filters)
    connection = connect()
    try:
        row = connection.execute(
            f"SELECT COALESCE(SUM(amount_clean), 0) AS net_revenue, "
            f"COUNT(DISTINCT order_id) AS order_count FROM sales_clean WHERE {where}", params
        ).fetchone()
        revenue = _money(row["net_revenue"])
        orders = int(row["order_count"])
        return Summary(net_revenue=revenue, order_count=orders, average_order_value=_money(revenue / orders if orders else 0))
    finally:
        connection.close()


def get_daily(filters: Filters) -> list[DailyPoint]:
    where, params = filter_sql(filters)
    connection = connect()
    try:
        rows = connection.execute(
            f"SELECT date_clean AS date, COALESCE(SUM(amount_clean), 0) AS net_revenue, "
            f"COUNT(DISTINCT order_id) AS order_count FROM sales_clean WHERE {where} GROUP BY date_clean ORDER BY date_clean",
            params,
        ).fetchall()
    finally:
        connection.close()
    by_date = {row["date"]: row for row in rows}
    points: list[DailyPoint] = []
    cursor = filters.start_date
    while cursor <= filters.end_date:
        key = cursor.isoformat()
        row = by_date.get(key)
        revenue = _money(row["net_revenue"] if row else 0)
        orders = int(row["order_count"] if row else 0)
        points.append(DailyPoint(date=cursor, net_revenue=revenue, order_count=orders, average_order_value=_money(revenue / orders if orders else 0)))
        cursor += timedelta(days=1)
    return points


def get_top_products(filters: Filters, limit: int) -> list[ProductPerformance]:
    where, params = filter_sql(filters)
    params["limit"] = limit
    connection = connect()
    try:
        rows = connection.execute(
            f"SELECT product_id, product_name, product_category, COALESCE(SUM(amount_clean), 0) AS net_revenue, "
            f"COALESCE(SUM(qty_clean), 0) AS quantity, COUNT(DISTINCT order_id) AS order_count "
            f"FROM sales_clean WHERE {where} GROUP BY product_id, product_name, product_category "
            f"ORDER BY net_revenue DESC, product_id ASC LIMIT :limit", params
        ).fetchall()
    finally:
        connection.close()
    return [ProductPerformance(rank=index, product_id=row["product_id"], product_name=row["product_name"], product_category=row["product_category"], net_revenue=_money(row["net_revenue"]), quantity=float(row["quantity"] or 0), order_count=int(row["order_count"])) for index, row in enumerate(rows, start=1)]
