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
    if not product and "卖了多少钱" in question:
        candidate = re.sub(r"(?:20\d{2}年)?[一二三四五六七八九十\d]{1,2}月", "", question.split("卖了多少钱", 1)[0])
        candidate = candidate.strip(" ，,？?的")
        if candidate:
            product = candidate
    follow_up = any(token in question for token in ("那", "换成", "改成", "呢")) and len(question) <= 30
    if follow_up and not previous:
        return QueryPlan(intent="follow_up", confidence=0.0, operation="inherit")
    if follow_up and previous:
        base = QueryPlan(**previous)
        old = base.model_dump()
        metric_changed = False
        if "订单" in question:
            base.intent, base.metric, metric_changed = "orders_by_period", "order_count", True
        elif "客单价" in question:
            base.intent, base.metric, metric_changed = "aov_by_period", "average_order_value", True
        elif any(token in question for token in ("营业额", "销售额", "收入")):
            base.intent, base.metric, metric_changed = "product_revenue" if (product or base.product_name) else "revenue_by_period", "net_revenue", True
        if start and end:
            base.start_date, base.end_date = start, end
        if product:
            base.product_name = product
        if store:
            base.store_id = store
        if category:
            base.category = category
        changed = []
        for field in ("intent", "metric", "start_date", "end_date", "product_name", "store_id", "category"):
            if old.get(field) != getattr(base, field):
                changed.append(field)
        if not changed and not metric_changed:
            base.confidence = 0.0
            return base
        base.operation = "inherit"
        base.changed_fields = changed
        base.inherited_fields = [field for field in ("intent", "metric", "start_date", "end_date", "product_name", "store_id", "category") if field not in changed and old.get(field)]
        base.previous_message_id = previous.get("previous_message_id")
        base.confidence = 0.95
        return base
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
    metric = "average_order_value" if intent in {"aov_by_period", "aov_trend"} else "order_count" if intent == "orders_by_period" else "net_revenue"
    plan = QueryPlan(intent=intent, metric=metric, dimensions=["product"] if product else [], start_date=start, end_date=end, product_name=product, store_id=store, category=category, confidence=0.9)
    plan.changed_fields = [field for field, value in (("start_date", start), ("end_date", end), ("product_name", product), ("store_id", store), ("category", category), ("metric", plan.metric), ("intent", plan.intent)) if value is not None]
    return plan


def parse_question(question: str, previous: dict | None = None) -> tuple[QueryPlan, str]:
    is_short_follow_up = any(token in question for token in ("那", "换成", "改成", "呢")) and len(question) <= 30
    if is_short_follow_up:
        return _merge_plan(local_parse(question, previous), previous), "local"
    if get_config().api_key and get_config().model:
        try:
            plan = langchain_parse(question, previous)
            return _merge_plan(plan, previous), "langchain"
        except Exception:
            pass
    try:
        return _merge_plan(local_parse(question, previous), previous), "local"
    except ValueError:
        raise


def _merge_plan(plan: QueryPlan, previous: dict | None) -> QueryPlan:
    """Merge only validated structured fields; never merge arbitrary model output."""
    if not previous:
        return plan
    prior = QueryPlan(**previous)
    if plan.operation == "inherit" or plan.intent == "follow_up":
        changed = set(plan.changed_fields)
        for field in ("intent", "metric", "dimensions", "start_date", "end_date", "product_name", "store_id", "category"):
            if field not in changed and getattr(plan, field) is None:
                setattr(plan, field, getattr(prior, field))
        plan.intent = prior.intent if plan.intent == "follow_up" else plan.intent
        plan.operation = "inherit"
        plan.inherited_fields = [field for field in ("intent", "metric", "dimensions", "start_date", "end_date", "product_name", "store_id", "category") if field not in changed and getattr(prior, field)]
        plan.previous_message_id = previous.get("previous_message_id")
    return plan


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
