from __future__ import annotations

import json
import re
from calendar import monthrange
from datetime import date

from ..database import connect
from .models import QueryPlan
from .config import get_config

INTENTS = {"revenue_by_period", "orders_by_period", "aov_by_period", "daily_trend", "product_revenue", "top_products", "store_revenue", "category_revenue", "aov_trend", "follow_up"}


def _month_range(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, monthrange(year, month)[1])


def _dates(question: str) -> tuple[date | None, date | None]:
    year_match = re.search(r"(20\d{2})年", question)
    year = int(year_match.group(1)) if year_match else 2026
    month_match = re.search(r"(\d{1,2})月", question)
    if month_match:
        month = int(month_match.group(1))
        if 1 <= month <= 12:
            return _month_range(year, month)
    chinese_month = re.search(r"([一二三四五六七八九十])月", question)
    if chinese_month:
        months = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
        return _month_range(year, months[chinese_month.group(1)])
    dates = re.findall(r"20\d{2}[-/]\d{1,2}[-/]\d{1,2}", question)
    if len(dates) >= 2:
        return date.fromisoformat(dates[0].replace("/", "-")), date.fromisoformat(dates[1].replace("/", "-"))
    return None, None


def _known_name(question: str, column: str, table: str) -> str | None:
    db = connect()
    try:
        values = [row[0] for row in db.execute(f"SELECT DISTINCT {column} FROM {table} WHERE {column} IS NOT NULL")]
    finally:
        db.close()
    return next((value for value in sorted(values, key=len, reverse=True) if value.lower() in question.lower()), None)


def local_parse(question: str, previous: dict | None = None) -> QueryPlan:
    start, end = _dates(question)
    lower = question.lower()
    product = _known_name(question, "product_name", "sales_clean")
    category = _known_name(question, "product_category", "sales_clean")
    store = _known_name(question, "store_id", "stores_clean")
    if "那" in question and previous:
        # A newly stated month/date replaces the entire prior period.
        if start is None or end is None:
            start = previous.get("start_date")
            end = previous.get("end_date")
        if start is None and end is None:
            start, end = _dates(question.replace("那", ""))
        intent = "follow_up"
    elif product and any(token in question for token in ("多少钱", "营业额", "销售额", "卖了")):
        intent = "product_revenue"
    elif any(token in question for token in ("哪个商品", "商品卖得最好", "top")):
        intent = "top_products"
    elif any(token in question for token in ("哪个门店", "门店营业额")):
        intent = "store_revenue"
    elif any(token in question for token in ("哪个品类", "品类营业额")):
        intent = "category_revenue"
    elif any(token in question for token in ("客单价涨", "客单价跌", "客单价趋势")):
        intent = "aov_trend"
    elif "客单价" in question:
        intent = "aov_by_period"
    elif "订单" in question:
        intent = "orders_by_period"
    elif any(token in question for token in ("每天", "趋势")):
        intent = "daily_trend"
    elif any(token in question for token in ("营业额", "销售额", "收入")):
        intent = "revenue_by_period"
    else:
        raise ValueError("unsupported")
    if intent == "follow_up" and previous:
        base = QueryPlan(**previous)
        base.start_date = date.fromisoformat(start) if isinstance(start, str) else start
        base.end_date = date.fromisoformat(end) if isinstance(end, str) else end
        base.intent = previous.get("intent", "revenue_by_period")
        base.confidence = 0.95
        return base
    return QueryPlan(intent=intent, metric="average_order_value" if intent in {"aov_by_period", "aov_trend"} else "net_revenue", dimensions=["product"] if product else [], start_date=start, end_date=end, product_name=product, store_id=store, category=category, confidence=0.9)


def parse_question(question: str, previous: dict | None = None) -> tuple[QueryPlan, str]:
    if get_config().api_key and get_config().model:
        try:
            return langchain_parse(question, previous), "langchain"
        except Exception:
            pass
    try:
        return local_parse(question, previous), "local"
    except ValueError:
        raise


def langchain_parse(question: str, previous: dict | None = None) -> QueryPlan:
    """Use LangChain for intent extraction only; facts still come from SQLite."""
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_openai import ChatOpenAI

    config = get_config()
    llm = ChatOpenAI(model=config.model, api_key=config.api_key, base_url=config.base_url, temperature=0, timeout=config.timeout_seconds)
    structured = llm.with_structured_output(QueryPlan)
    prompt = ChatPromptTemplate.from_messages([
        ("system", "你是销售数据问答的意图解析器。只输出结构化查询计划，不要回答问题。intent 必须是 revenue_by_period、orders_by_period、aov_by_period、daily_trend、product_revenue、top_products、store_revenue、category_revenue、aov_trend 之一。不要生成 SQL，不要编造商品或门店 ID。"),
        ("human", "问题：{question}\n上一次查询计划：{previous}"),
    ])
    result = (prompt | structured).invoke({"question": question, "previous": previous or "无"})
    plan = result if isinstance(result, QueryPlan) else QueryPlan.model_validate(result)
    if plan.intent not in INTENTS - {"follow_up"}:
        raise ValueError("unsupported")
    return plan
