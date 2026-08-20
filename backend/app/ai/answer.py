from __future__ import annotations

import re

from .models import FactSet, QueryPlan


def template_answer(plan: QueryPlan, facts: FactSet) -> str:
    if facts.value is None and not facts.rows:
        return "当前筛选条件下没有找到可用销售记录。"
    value = facts.value or 0
    start, end = facts.filters.get("start_date", ""), facts.filters.get("end_date", "")
    if facts.intent == "product_revenue":
        row = facts.rows[0]
        return f"{facts.filters.get('product_name', '该商品')} 在 {start[:7]} 的净营业额为 ¥{value:,.2f}，共售出 {row.get('quantity', 0):,.0f} 份，涉及 {row.get('order_count', 0):,} 个订单。"
    if facts.intent == "orders_by_period":
        return f"{start} 至 {end} 共 {value:,.0f} 个订单。"
    if facts.intent in {"aov_by_period", "aov_trend"}:
        return f"{start} 至 {end} 的平均客单价为 ¥{value:,.2f}。"
    if facts.intent == "daily_trend":
        return f"{start} 至 {end} 共 {len(facts.rows)} 天，日营业额趋势已整理完成。"
    if facts.intent in {"top_products", "store_revenue", "category_revenue"}:
        first = facts.rows[0]
        return f"最高的是 {first.get('name', '当前第一名')}，净营业额为 ¥{first.get('net_revenue', 0):,.2f}。"
    return f"{start} 至 {end} 的净营业额为 ¥{value:,.2f}。"


def validate_answer(content: str, facts: FactSet) -> bool:
    expected = []
    if facts.value is not None:
        expected.append(f"{facts.value:,.2f}")
    for row in facts.rows:
        for key in ("quantity", "order_count", "net_revenue"):
            if row.get(key) is not None:
                expected.append(f"{float(row[key]):,.0f}" if key != "net_revenue" else f"{float(row[key]):,.2f}")
    numbers = re.findall(r"(?<!\d)(\d[\d,]*(?:\.\d+)?)(?!\d)", content)
    return not expected or all(any(number.replace(",", "") == candidate.replace(",", "") for candidate in expected) for number in numbers if "." in number or "," in number)
