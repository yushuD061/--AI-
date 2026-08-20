from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import date
from typing import Any

from .. import auth, database, state
from ..schemas import Filters

RULE_VERSION = "v1"
SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


def _severity(rate: float) -> str:
    return "critical" if abs(rate) >= .5 else "warning"


def _streak_severity(days: int) -> str:
    return "critical" if days >= 7 else "warning" if days >= 5 else "info"


def _id(kind: str, day: str, store_id: str | None, product_id: str | None) -> str:
    raw = "|".join((RULE_VERSION, kind, day, store_id or "", product_id or ""))
    return "a_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _alert(kind: str, severity: str, day: str, message: str, metric: str,
           actual: float, baseline: float | None, change: float | None, sample: int,
           store_id: str | None = None, product_id: str | None = None,
           product_name: str | None = None) -> dict[str, Any]:
    return {
        "alert_id": _id(kind, day, store_id, product_id), "type": kind,
        "severity": severity, "date": day, "store_id": store_id,
        "product_id": product_id, "product_name": product_name, "metric": metric,
        "actual_value": round(actual, 4),
        "baseline_value": round(baseline, 4) if baseline is not None else None,
        "change_rate": round(change, 4) if change is not None else None,
        "sample_size": sample, "message": message,
        "dashboard_target": {"start_date": day, "end_date": day, "store_id": store_id,
                             "product_id": product_id, "view": "products" if product_id else "trend"},
    }


def calculate(filters: Filters) -> list[dict[str, Any]]:
    db = database.connect()
    try:
        dates = [row[0] for row in db.execute("SELECT DISTINCT date_clean FROM sales_clean ORDER BY date_clean")]
        allowed = filters.allowed_store_ids
        scope_sql = "" if allowed is None else f" WHERE store_id IN ({','.join('?' for _ in allowed)})"
        scope_params = [] if allowed is None else allowed
        stores = [row[0] for row in db.execute(f"SELECT store_id FROM stores_clean{scope_sql} ORDER BY store_id", scope_params)]
        products = {row[0]: row[1] for row in db.execute(
            f"SELECT product_id, product_name FROM sales_clean{scope_sql} GROUP BY product_id, product_name ORDER BY product_id", scope_params
        )}
        rows = db.execute(
            f"SELECT date_clean, store_id, product_id, product_name, order_id, amount_clean, qty_clean FROM sales_clean{scope_sql}", scope_params
        ).fetchall()
    finally:
        db.close()

    daily: dict[tuple[str, str], dict[str, Any]] = defaultdict(lambda: {
        "revenue": 0.0, "orders": set(), "negative_orders": set(), "negative_amount": 0.0,
        "absolute": 0.0,
    })
    global_products: dict[str, set[str]] = defaultdict(set)
    store_products: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in rows:
        key = (row["date_clean"], row["store_id"])
        amount = float(row["amount_clean"] or 0)
        qty = float(row["qty_clean"] or 0)
        item = daily[key]
        item["revenue"] += amount
        item["orders"].add(row["order_id"])
        item["absolute"] += abs(amount)
        if amount < 0:
            item["negative_orders"].add(row["order_id"])
            item["negative_amount"] += abs(amount)
        if amount > 0 and qty > 0:
            global_products[row["date_clean"]].add(row["product_id"])
            store_products[(row["date_clean"], row["store_id"])].add(row["product_id"])

    selected_stores = [filters.store_id] if filters.store_id else stores
    in_range = lambda value: filters.start_date.isoformat() <= value <= filters.end_date.isoformat()
    result: list[dict[str, Any]] = []
    for index, day in enumerate(dates):
        if not in_range(day):
            continue
        prior = dates[max(0, index - 7):index]
        for store_id in selected_stores:
            actual = daily[(day, store_id)]
            order_count = len(actual["orders"])
            if len(prior) == 7 and order_count >= 10:
                previous = [daily[(value, store_id)] for value in prior]
                baseline_revenue = sum(item["revenue"] for item in previous) / 7
                baseline_orders = sum(len(item["orders"]) for item in previous) / 7
                actual_aov = actual["revenue"] / order_count
                prior_orders = sum(len(item["orders"]) for item in previous)
                baseline_aov = sum(item["revenue"] for item in previous) / prior_orders if prior_orders else 0
                values = [
                    ("revenue", "net_revenue", actual["revenue"], baseline_revenue, "营业额"),
                    ("orders", "order_count", float(order_count), baseline_orders, "订单数"),
                    ("aov", "average_order_value", actual_aov, baseline_aov, "客单价"),
                ]
                changes: dict[str, float] = {}
                for suffix, metric, value, baseline, label in values:
                    if baseline == 0:
                        continue
                    change = (value - baseline) / abs(baseline)
                    changes[suffix] = change
                    if abs(change) >= .3:
                        direction = "上升" if change > 0 else "下降"
                        result.append(_alert(
                            f"{metric}_{'rise' if change > 0 else 'drop'}", _severity(change), day,
                            f"{store_id} 当日{label}较近 7 日基线{direction} {abs(change):.2%}",
                            metric, value, baseline, change, order_count, store_id,
                        ))
                if changes.get("revenue", 0) * changes.get("orders", 0) < 0 and max(
                    abs(changes.get("revenue", 0)), abs(changes.get("orders", 0))
                ) >= .3:
                    magnitude = max(abs(changes["revenue"]), abs(changes["orders"]))
                    result.append(_alert("metric_divergence", _severity(magnitude), day,
                        f"{store_id} 营业额与订单数相对近 7 日基线变化方向相反", "divergence",
                        magnitude, 0.0, magnitude, order_count, store_id))
            negative_count = len(actual["negative_orders"])
            refund_rate = actual["negative_amount"] / actual["absolute"] if actual["absolute"] else 0
            if negative_count >= 3 and refund_rate >= .2:
                result.append(_alert("refund_concentration", "critical" if refund_rate >= .5 else "warning",
                    day, f"{store_id} 当日负金额占绝对流水 {refund_rate:.2%}", "refund_ratio",
                    refund_rate, .2, refund_rate - .2, negative_count, store_id))

    def add_streaks(entity_ids: list[str], activity: dict[Any, set[str]], store_id: str | None = None) -> None:
        for entity_id in entity_ids:
            seen = False
            streak = 0
            for day in dates:
                active = entity_id in activity[(day, store_id)] if store_id else entity_id in activity[day]
                if active:
                    seen, streak = True, 0
                elif seen:
                    streak += 1
                    if streak >= 3 and in_range(day):
                        kind = "store_no_sales" if entity_id in stores and store_id is None else "product_no_sales"
                        target_store = entity_id if kind == "store_no_sales" else store_id
                        product_id = None if kind == "store_no_sales" else entity_id
                        label = target_store if kind == "store_no_sales" else products.get(entity_id, entity_id)
                        result.append(_alert(kind, _streak_severity(streak), day,
                            f"{label} 已连续 {streak} 个有效营业日无销售", "no_sales_days",
                            float(streak), 3.0, None, streak, target_store, product_id,
                            products.get(product_id) if product_id else None))

    store_activity: dict[str, set[str]] = defaultdict(set)
    for (day, store_id), item in daily.items():
        if item["revenue"] > 0:
            store_activity[day].add(store_id)
    add_streaks(selected_stores, store_activity)
    if filters.store_id:
        add_streaks(list(products), store_products, filters.store_id)
    else:
        add_streaks(list(products), global_products)
    return result


def get_alerts(filters: Filters, severity: str | None = None, alert_type: str | None = None,
               is_read: bool | None = None, limit: int = 100) -> list[dict[str, Any]]:
    alerts = calculate(filters)
    states = state.read_states([item["alert_id"] for item in alerts], auth.state_user_id())
    for item in alerts:
        item["is_read"] = states.get(item["alert_id"], False)
    if severity:
        alerts = [item for item in alerts if item["severity"] == severity]
    if alert_type:
        alerts = [item for item in alerts if item["type"] == alert_type]
    if is_read is not None:
        alerts = [item for item in alerts if item["is_read"] is is_read]
    alerts.sort(key=lambda item: (SEVERITY_ORDER[item["severity"]], -date.fromisoformat(item["date"]).toordinal(), item["alert_id"]))
    return alerts[:limit]
