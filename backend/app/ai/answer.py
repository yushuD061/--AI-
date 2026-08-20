from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from .models import FactSet, QueryPlan


def template_answer(plan: QueryPlan, facts: FactSet) -> str:
    if facts.value is None and not facts.rows:
        return "当前筛选条件下没有找到可用销售记录。"
    value = facts.value or 0
    start, end = facts.filters.get("start_date", ""), facts.filters.get("end_date", "")
    if facts.intent == "compare_period":
        row = facts.rows[0]
        previous_start, previous_end = facts.filters.get("previous_start_date", ""), facts.filters.get("previous_end_date", "")
        label = {"net_revenue": "净营业额", "order_count": "订单数", "average_order_value": "平均客单价"}.get(facts.metric, facts.metric)
        prefix = "¥" if facts.metric in {"net_revenue", "average_order_value"} else ""
        rate = "无法计算" if row["change_rate"] is None else f"{row['change_rate'] * 100:.2f}%"
        return f"{start} 至 {end} 的{label}为 {prefix}{row['current']:,.2f}；{previous_start} 至 {previous_end} 为 {prefix}{row['previous']:,.2f}，绝对变化 {prefix}{row['absolute_change']:,.2f}，变化率 {rate}。"
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


def _normalize_number(value: object) -> str:
    try:
        number = Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return str(value)
    return format(number.normalize(), "f")


def extract_business_numbers(content: str) -> list[str]:
    text = re.sub(r"20\d{2}[-/]\d{1,2}(?:[-/]\d{1,2})?", "", content)
    text = re.sub(r"20\d{2}\s*年(?:\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?)?", "", text)
    text = re.sub(r"\b[A-Za-z]+\d+\b", "", text)
    return [_normalize_number(value) for value in re.findall(r"(?<![\d.])(-?\d[\d,]*(?:\.\d+)?)(?![\d.])", text)]


def expected_business_numbers(facts: FactSet) -> set[str]:
    expected: set[str] = set()
    if facts.value is not None:
        expected.add(_normalize_number(facts.value))
    for row in facts.rows:
        for key in ("quantity", "order_count", "net_revenue", "average_order_value", "start_average_order_value", "end_average_order_value", "current", "previous", "absolute_change", "change_rate"):
            if row.get(key) is not None:
                expected.add(_normalize_number(row[key]))
                if key == "change_rate":
                    expected.add(_normalize_number(float(row[key]) * 100))
    if facts.intent == "daily_trend":
        expected.add(_normalize_number(len(facts.rows)))
    return expected


def validate_answer(content: str, facts: FactSet) -> bool:
    expected = expected_business_numbers(facts)
    return all(number in expected for number in extract_business_numbers(content))
