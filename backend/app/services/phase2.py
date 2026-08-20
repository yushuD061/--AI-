from __future__ import annotations

import hashlib
from datetime import date, timedelta
from typing import Any, Literal

from .. import auth, state
from ..database import connect
from ..schemas import Filters
from .analytics import resolve_filters

Metric = Literal["net_revenue", "order_count", "average_order_value", "change_rate", "refund_ratio"]
RULE_VERSION = "product-decline-v1"


def _period_payload(filters: Filters) -> dict[str, Any]:
    return filters.model_dump(mode="json")


def _effective_dates(start: date | None = None, end: date | None = None) -> list[date]:
    clauses, params = [], []
    if start is not None:
        clauses.append("date_clean >= ?"); params.append(start.isoformat())
    if end is not None:
        clauses.append("date_clean <= ?"); params.append(end.isoformat())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    db = connect()
    try:
        return [date.fromisoformat(row[0]) for row in db.execute(
            f"SELECT DISTINCT date_clean FROM sales_clean {where} ORDER BY date_clean", params)]
    finally:
        db.close()


def _previous_period(start: date, end: date, explicit_start: date | None, explicit_end: date | None) -> tuple[date, date]:
    if (explicit_start is None) != (explicit_end is None):
        raise ValueError("previous_start_date 和 previous_end_date 必须同时提供")
    if explicit_start is not None and explicit_end is not None:
        if explicit_start > explicit_end:
            raise ValueError("上一周期开始日期不能晚于结束日期")
        return explicit_start, explicit_end
    current_dates = _effective_dates(start, end)
    required = len(current_dates) or max(1, (end - start).days + 1)
    before = _effective_dates(end=start - timedelta(days=1))
    if len(before) < required:
        fallback_end = start - timedelta(days=1)
        return fallback_end - timedelta(days=required - 1), fallback_end
    selected = before[-required:]
    return selected[0], selected[-1]


def resolve_periods(current_start: date | None, current_end: date | None, previous_start: date | None,
                    previous_end: date | None, store_id: str | None = None) -> tuple[Filters, Filters]:
    current = resolve_filters(current_start, current_end, store_id)
    old_start, old_end = _previous_period(current.start_date, current.end_date, previous_start, previous_end)
    return current, resolve_filters(old_start, old_end, store_id)


def _where(filters: Filters) -> tuple[str, list[Any]]:
    clauses, params = ["date_clean >= ?", "date_clean <= ?"], [filters.start_date.isoformat(), filters.end_date.isoformat()]
    if filters.store_id:
        clauses.append("store_id = ?"); params.append(filters.store_id)
    elif filters.allowed_store_ids is not None:
        clauses.append(f"store_id IN ({','.join('?' for _ in filters.allowed_store_ids)})")
        params.extend(filters.allowed_store_ids)
    return " AND ".join(clauses), params


def _change(current: float | int, previous: float | int) -> dict[str, float | int | None]:
    absolute = current - previous
    return {"current": current, "previous": previous, "absolute_change": round(absolute, 2) if isinstance(absolute, float) else absolute,
            "change_rate": round(absolute / abs(previous), 6) if previous else None}


def _aggregate(filters: Filters) -> dict[str, float | int]:
    where, params = _where(filters); db = connect()
    try:
        row = db.execute(f"""SELECT COALESCE(SUM(amount_clean),0) revenue, COUNT(DISTINCT order_id) orders,
          COALESCE(SUM(qty_clean),0) quantity, COALESCE(SUM(CASE WHEN amount_clean < 0 THEN -amount_clean ELSE 0 END),0) refunds,
          COALESCE(SUM(ABS(amount_clean)),0) absolute_flow FROM sales_clean WHERE {where}""", params).fetchone()
    finally: db.close()
    revenue, orders = round(float(row["revenue"] or 0), 2), int(row["orders"] or 0)
    return {"net_revenue": revenue, "order_count": orders, "average_order_value": round(revenue / orders, 2) if orders else 0.0,
            "quantity": float(row["quantity"] or 0), "refund_amount": round(float(row["refunds"] or 0), 2),
            "refund_ratio": round(float(row["refunds"] or 0) / float(row["absolute_flow"]), 6) if row["absolute_flow"] else 0.0}


def _daily(filters: Filters) -> list[dict[str, Any]]:
    where, params = _where(filters); db = connect()
    try:
        rows = db.execute(f"SELECT date_clean AS date, COALESCE(SUM(amount_clean),0) net_revenue, COUNT(DISTINCT order_id) order_count, COALESCE(SUM(qty_clean),0) quantity FROM sales_clean WHERE {where} GROUP BY date_clean ORDER BY date_clean", params).fetchall()
    finally: db.close()
    return [{**dict(row), "net_revenue": round(float(row["net_revenue"]), 2), "quantity": float(row["quantity"])} for row in rows]


def compare(current: Filters, previous: Filters) -> dict[str, Any]:
    now, old = _aggregate(current), _aggregate(previous)
    return {"current_period": _period_payload(current), "previous_period": _period_payload(previous),
            "metrics": {key: _change(now[key], old[key]) for key in ("net_revenue", "order_count", "average_order_value", "quantity")},
            "daily": {"current": _daily(current), "previous": _daily(previous)}}


def _store_values(filters: Filters) -> dict[str, dict[str, Any]]:
    where, params = _where(filters); db = connect()
    try:
        rows = db.execute(f"SELECT store_id, COALESCE(SUM(amount_clean),0) net_revenue, COUNT(DISTINCT order_id) order_count, COALESCE(SUM(CASE WHEN amount_clean < 0 THEN -amount_clean ELSE 0 END),0) refund_amount, COALESCE(SUM(ABS(amount_clean)),0) absolute_flow FROM sales_clean WHERE {where} GROUP BY store_id", params).fetchall()
    finally: db.close()
    result = {}
    for row in rows:
        revenue, orders = float(row["net_revenue"] or 0), int(row["order_count"] or 0)
        result[row["store_id"]] = {"net_revenue": round(revenue, 2), "order_count": orders, "average_order_value": round(revenue / orders, 2) if orders else 0.0, "refund_ratio": round(float(row["refund_amount"] or 0) / float(row["absolute_flow"]), 6) if row["absolute_flow"] else 0.0}
    return result


def store_ranking(current: Filters, previous: Filters, metric: Metric = "net_revenue", limit: int = 50) -> dict[str, Any]:
    now, old = _store_values(current), _store_values(previous); db = connect()
    try:
        if current.allowed_store_ids is None:
            store_rows = db.execute("SELECT store_id, store_name, district FROM stores_clean ORDER BY store_id")
        else:
            placeholders = ",".join("?" for _ in current.allowed_store_ids)
            store_rows = db.execute(f"SELECT store_id, store_name, district FROM stores_clean WHERE store_id IN ({placeholders}) ORDER BY store_id", current.allowed_store_ids)
        stores = [dict(row) for row in store_rows]
    finally: db.close()
    empty = {"net_revenue": 0.0, "order_count": 0, "average_order_value": 0.0, "refund_ratio": 0.0}; rows = []
    for store in stores:
        cv, pv = now.get(store["store_id"], empty), old.get(store["store_id"], empty)
        change = _change(cv["net_revenue"], pv["net_revenue"])["change_rate"]
        value = change if metric == "change_rate" else cv[metric]; previous_value = pv["net_revenue"] if metric == "change_rate" else pv[metric]
        rows.append({**store, **cv, "value": value, "previous_value": previous_value, "change_rate": change, "stable_sort_key": store["store_id"]})
    rows.sort(key=lambda row: (row["value"] is None, -(float(row["value"] or 0)), row["store_id"]))
    for rank, row in enumerate(rows, 1): row["rank"] = rank
    return {"metric": metric, "current_period": _period_payload(current), "previous_period": _period_payload(previous), "data": rows[:limit]}


def store_diagnosis(store_id: str, current: Filters, previous: Filters) -> dict[str, Any]:
    if current.allowed_store_ids is not None and store_id not in current.allowed_store_ids:
        raise LookupError(f"未知门店: {store_id}")
    scoped_current = Filters(start_date=current.start_date, end_date=current.end_date, store_id=store_id,
                             allowed_store_ids=current.allowed_store_ids)
    scoped_previous = Filters(start_date=previous.start_date, end_date=previous.end_date, store_id=store_id,
                              allowed_store_ids=previous.allowed_store_ids)
    ranking = store_ranking(current, previous, "net_revenue", 100)["data"]
    store = next((item for item in ranking if item["store_id"] == store_id), None)
    if store is None:
        raise LookupError(f"未知门店: {store_id}")
    current_products, previous_products = _product_values(scoped_current), _product_values(scoped_previous)
    products = []
    for product_id, item in current_products.items():
        prior = previous_products.get(product_id, {"net_revenue": 0.0})
        products.append({"product_id": product_id, "product_name": item["product_name"],
                        "net_revenue": item["net_revenue"], "order_count": item["order_count"],
                        "previous_revenue": prior["net_revenue"],
                        "change_rate": _change(item["net_revenue"], prior["net_revenue"])["change_rate"]
                        if prior["net_revenue"] else None})
    products.sort(key=lambda item: (-item["net_revenue"], item["product_id"]))
    declining = sorted([item for item in products if item["change_rate"] is not None and item["change_rate"] < 0],
                       key=lambda item: (item["change_rate"], item["product_id"]))[:3]
    return {"store": store, "rankings": {"net_revenue": store["rank"],
            "order_count": next((item["rank"] for item in store_ranking(current, previous, "order_count", 100)["data"] if item["store_id"] == store_id), None),
            "average_order_value": next((item["rank"] for item in store_ranking(current, previous, "average_order_value", 100)["data"] if item["store_id"] == store_id), None)},
            "changes": {"net_revenue": store["change_rate"]}, "top_products": products[:3],
            "declining_products": declining, "current_period": _period_payload(current),
            "previous_period": _period_payload(previous)}


def _product_values(filters: Filters) -> dict[str, dict[str, Any]]:
    where, params = _where(filters); db = connect()
    try: rows = db.execute(f"SELECT product_id, MAX(product_name) product_name, MAX(product_category) product_category, COALESCE(SUM(amount_clean),0) net_revenue, COALESCE(SUM(qty_clean),0) quantity, COUNT(DISTINCT order_id) order_count, COALESCE(SUM(CASE WHEN amount_clean < 0 THEN -amount_clean ELSE 0 END),0) refund_amount FROM sales_clean WHERE {where} GROUP BY product_id", params).fetchall()
    finally: db.close()
    return {row["product_id"]: {**dict(row), "net_revenue": round(float(row["net_revenue"]), 2), "quantity": float(row["quantity"]), "order_count": int(row["order_count"]), "refund_amount": round(float(row["refund_amount"]), 2)} for row in rows}


def _store_distribution(filters: Filters) -> dict[str, list[dict[str, Any]]]:
    where, params = _where(filters); db = connect()
    try: rows = db.execute(f"SELECT product_id, store_id, store_id AS store_name, COALESCE(SUM(amount_clean),0) net_revenue, COALESCE(SUM(qty_clean),0) quantity FROM sales_clean WHERE {where} GROUP BY product_id, store_id ORDER BY product_id, net_revenue DESC, store_id", params).fetchall()
    finally: db.close()
    result: dict[str, list[dict[str, Any]]] = {}
    for row in rows: result.setdefault(row["product_id"], []).append({"store_id": row["store_id"], "store_name": row["store_name"], "net_revenue": round(float(row["net_revenue"]), 2), "quantity": float(row["quantity"])})
    return result


def product_mix(current: Filters, previous: Filters) -> dict[str, Any]:
    now, old = _product_values(current), _product_values(previous); current_ids = sorted(now, key=lambda key: (-now[key]["net_revenue"], key)); previous_ids = sorted(old, key=lambda key: (-old[key]["net_revenue"], key)); previous_ranks = {key: index for index, key in enumerate(previous_ids, 1)}; revenue_total = sum(item["net_revenue"] for item in now.values()); quantity_total = sum(item["quantity"] for item in now.values()); distributions = _store_distribution(current); rows = []
    for rank, product_id in enumerate(current_ids, 1):
        item, prior = now[product_id], old.get(product_id); previous_rank = previous_ranks.get(product_id)
        rows.append({**item, "rank": rank, "revenue_share": round(item["net_revenue"] / revenue_total, 6) if revenue_total else 0.0, "quantity_share": round(item["quantity"] / quantity_total, 6) if quantity_total else 0.0, "previous_rank": previous_rank, "rank_change": previous_rank - rank if previous_rank is not None else None, "is_new_top_10": previous_rank is None and rank <= 10, "previous_revenue": prior["net_revenue"] if prior else 0.0, "previous_order_count": prior["order_count"] if prior else 0, "change_rate": _change(item["net_revenue"], prior["net_revenue"])["change_rate"] if prior else None, "store_distribution": distributions.get(product_id, [])})
    return {"current_period": _period_payload(current), "previous_period": _period_payload(previous), "data": rows}


def _alert_id(rule: str, current: Filters, product_id: str) -> str:
    raw = "|".join((RULE_VERSION, rule, current.start_date.isoformat(), current.end_date.isoformat(), current.store_id or "ALL", product_id))
    return f"pd_{hashlib.sha256(raw.encode()).hexdigest()[:20]}"


def _trailing_no_sales_days(current: Filters, product_id: str) -> int:
    dates = _effective_dates(current.start_date, current.end_date); where, params = _where(current); params.append(product_id); db = connect()
    try: sold = {date.fromisoformat(row[0]) for row in db.execute(f"SELECT DISTINCT date_clean FROM sales_clean WHERE {where} AND product_id = ? AND amount_clean > 0", params)}
    finally: db.close()
    count = 0
    for day in reversed(dates):
        if day in sold: break
        count += 1
    return count


def product_decline(current: Filters, previous: Filters) -> list[dict[str, Any]]:
    mix = product_mix(current, previous); old = _product_values(previous); current_map = {item["product_id"]: item for item in mix["data"]}; result = []
    for product_id, prior in old.items():
        if prior["order_count"] < 10: continue
        item = current_map.get(product_id); actual = item["net_revenue"] if item else 0.0; rate = _change(actual, prior["net_revenue"])["change_rate"]; no_sales = _trailing_no_sales_days(current, product_id); rules = []
        if rate is not None and rate <= -.3: rules.append(("revenue_drop", "critical" if rate <= -.5 else "warning"))
        if no_sales >= 3: rules.append(("consecutive_no_sales", "critical" if no_sales >= 7 else "warning" if no_sales >= 5 else "info"))
        for trigger, severity in rules:
            alert_id = _alert_id(trigger, current, product_id)
            result.append({"alert_id": alert_id, "type": "product_decline", "rule_version": RULE_VERSION, "severity": severity, "product_id": product_id, "product_name": prior["product_name"], "store_id": current.store_id, "actual_period": _period_payload(current), "baseline_period": _period_payload(previous), "actual_value": actual, "baseline_value": prior["net_revenue"], "change_rate": rate, "sample_size": prior["order_count"], "trigger": trigger, "consecutive_no_sales_days": no_sales if trigger == "consecutive_no_sales" else 0, "dashboard_target": {"start_date": current.start_date.isoformat(), "end_date": current.end_date.isoformat(), "previous_start_date": previous.start_date.isoformat(), "previous_end_date": previous.end_date.isoformat(), "store_id": current.store_id, "product_id": product_id, "view": "products"}})
    state.register_alerts([item["alert_id"] for item in result]); states = state.read_states([item["alert_id"] for item in result], auth.state_user_id())
    for item in result: item["is_read"] = states.get(item["alert_id"], False)
    order = {"critical": 0, "warning": 1, "info": 2}; result.sort(key=lambda item: (order[item["severity"]], item["product_id"], item["trigger"]))
    return result
