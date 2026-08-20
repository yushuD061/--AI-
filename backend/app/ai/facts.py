from __future__ import annotations

from datetime import date
from typing import Any

from ..database import connect
from ..services.analytics import get_daily, get_summary, resolve_filters
from ..services import phase2
from .models import FactSet, QueryPlan


def _resolve_dates(plan: QueryPlan) -> tuple[date, date]:
    filters = resolve_filters(plan.start_date, plan.end_date, plan.store_id)
    return filters.start_date, filters.end_date


def execute_plan(plan: QueryPlan) -> FactSet:
    if plan.intent == "compare_period":
        current, previous = phase2.resolve_periods(plan.start_date, plan.end_date,
                                                   plan.previous_start_date, plan.previous_end_date,
                                                   plan.store_id)
        result = phase2.compare(current, previous)
        metric = plan.metric if plan.metric in {"net_revenue", "order_count", "average_order_value", "quantity"} else "net_revenue"
        row = {"metric": metric, **result["metrics"][metric]}
        return FactSet(intent=plan.intent, metric=metric, value=row["current"],
                       unit="count" if metric in {"order_count", "quantity"} else "CNY",
                       filters={"start_date": current.start_date.isoformat(), "end_date": current.end_date.isoformat(),
                                "previous_start_date": previous.start_date.isoformat(),
                                "previous_end_date": previous.end_date.isoformat(), "store_id": plan.store_id},
                       rows=[row])
    start, end = _resolve_dates(plan)
    filters = resolve_filters(start, end, plan.store_id)
    filter_clauses = ["date_clean >= ?", "date_clean <= ?"]
    params: list[Any] = [start.isoformat(), end.isoformat()]
    if plan.store_id:
        filter_clauses.append("store_id = ?"); params.append(plan.store_id)
    elif filters.allowed_store_ids is not None:
        filter_clauses.append(f"store_id IN ({','.join('?' for _ in filters.allowed_store_ids)})")
        params.extend(filters.allowed_store_ids)
    where = " AND ".join(filter_clauses)
    db = connect()
    try:
        if plan.product_name:
            filter_clauses.append("product_name = ?"); params.append(plan.product_name)
        if plan.category:
            filter_clauses.append("product_category = ?"); params.append(plan.category)
        where = " AND ".join(filter_clauses)
        if plan.intent in {"daily_trend", "aov_trend"}:
            rows = [dict(row) for row in get_daily(filters)]
            if plan.intent == "aov_trend":
                value = rows[-1]["average_order_value"] if rows else 0
                rows = [{"start_average_order_value": rows[0]["average_order_value"] if rows else 0, "end_average_order_value": value}]
            return FactSet(intent=plan.intent, metric=plan.metric, value=rows[-1].get("average_order_value") if rows and plan.intent == "daily_trend" else None, filters={"start_date": start.isoformat(), "end_date": end.isoformat(), "store_id": plan.store_id, "product_name": plan.product_name, "category": plan.category}, rows=rows)
        if plan.intent in {"top_products", "store_revenue", "category_revenue"}:
            if plan.intent == "top_products":
                group = "product_id, product_name, product_category"
                select = "product_id, product_name AS name, product_category AS category"
            elif plan.intent == "store_revenue":
                group = "store_id, store_name"
                select = "store_id, store_name AS name"
            else:
                group = "product_category"
                select = "product_category AS name"
            rows = [dict(row) for row in db.execute(f"SELECT {select}, SUM(amount_clean) AS net_revenue, COUNT(DISTINCT order_id) AS order_count FROM sales_clean WHERE {where} GROUP BY {group} ORDER BY net_revenue DESC, name ASC LIMIT 10", params)]
            return FactSet(intent=plan.intent, metric="net_revenue", value=rows[0]["net_revenue"] if rows else None, filters={"start_date": start.isoformat(), "end_date": end.isoformat(), "store_id": plan.store_id, "product_name": plan.product_name, "category": plan.category}, rows=rows)
        if plan.intent == "product_revenue":
            row = db.execute(f"SELECT SUM(amount_clean) AS net_revenue, SUM(qty_clean) AS quantity, COUNT(DISTINCT order_id) AS order_count FROM sales_clean WHERE {where}", params).fetchone()
            rows = [dict(row)] if row and row["net_revenue"] is not None else []
            return FactSet(intent=plan.intent, metric="net_revenue", value=rows[0]["net_revenue"] if rows else None, filters={"start_date": start.isoformat(), "end_date": end.isoformat(), "store_id": plan.store_id, "product_name": plan.product_name, "category": plan.category}, rows=rows)
        if plan.product_name or plan.category:
            row = db.execute(f"""SELECT COALESCE(SUM(amount_clean), 0) AS net_revenue,
              COUNT(DISTINCT order_id) AS order_count FROM sales_clean WHERE {where}""", params).fetchone()
            revenue = float(row["net_revenue"] or 0)
            order_count = int(row["order_count"] or 0)
            summary_data = {
                "net_revenue": revenue,
                "order_count": order_count,
                "average_order_value": round(revenue / order_count, 2) if order_count else 0,
            }
        else:
            summary_data = get_summary(filters).model_dump()
        if plan.intent == "orders_by_period":
            return FactSet(intent=plan.intent, metric="order_count", value=summary_data["order_count"], filters={"start_date": start.isoformat(), "end_date": end.isoformat(), "store_id": plan.store_id, "product_name": plan.product_name, "category": plan.category}, rows=[summary_data])
        if plan.intent in {"aov_by_period", "aov_trend"}:
            return FactSet(intent=plan.intent, metric="average_order_value", value=summary_data["average_order_value"], filters={"start_date": start.isoformat(), "end_date": end.isoformat(), "store_id": plan.store_id, "product_name": plan.product_name, "category": plan.category}, rows=[summary_data])
        return FactSet(intent=plan.intent, metric="net_revenue", value=summary_data["net_revenue"], filters={"start_date": start.isoformat(), "end_date": end.isoformat(), "store_id": plan.store_id, "product_name": plan.product_name, "category": plan.category}, rows=[summary_data])
    finally:
        db.close()
