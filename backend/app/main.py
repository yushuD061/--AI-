from __future__ import annotations

from datetime import date

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .database import database_available
from .ai import config as ai_config
from .ai import conversations
from .ai.answer import template_answer, validate_answer
from .ai.facts import execute_plan
from .ai.models import AssistantMessage, ConfigTestResponse, ConfigUpdate, Conversation, ConversationCreate, FactSet, QueryRequest
from .ai.parser import parse_question
from .ai.provider import generate_answer, test_connection
from .schemas import DailyPoint, Envelope, FilterData, Filters, HealthResponse, ProductPerformance, Summary
from .services import analytics

app = FastAPI(title="Moneki Store Sales API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(FileNotFoundError)
async def database_not_found(_: Request, exc: FileNotFoundError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(ValueError)
async def invalid_value(_: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(LookupError)
async def invalid_lookup(_: Request, exc: LookupError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


def query_filters(start_date: date | None, end_date: date | None, store_id: str | None) -> Filters:
    return analytics.resolve_filters(start_date, end_date, store_id)


def ai_response(conversation_id: str, assistant: AssistantMessage, facts: FactSet | None, status: str, plan=None) -> dict:
    plan_data = plan.model_dump(mode="json") if plan else None
    context = None
    target = None
    if plan_data:
        context = {key: plan_data.get(key) for key in ("operation", "changed_fields", "inherited_fields", "previous_message_id")}
    if facts:
        filters = facts.filters
        target = {
            "start_date": filters.get("start_date"),
            "end_date": filters.get("end_date"),
            "store_id": filters.get("store_id"),
            "metric": facts.metric,
            "view": "products" if facts.intent == "top_products" else "trend",
        }
    return {"conversation_id": conversation_id, "message": assistant, "facts": facts, "status": status, "context": context, "query_plan": plan_data, "dashboard_target": target}


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    if not database_available():
        raise HTTPException(status_code=503, detail="清洗数据库不可用")
    return HealthResponse(status="ok", database="available")


@app.get("/api/v1/filters", response_model=Envelope[FilterData])
def filters() -> Envelope[FilterData]:
    data = analytics.get_filters()
    return Envelope(filters=Filters(start_date=data.date_min, end_date=data.date_max), data=data)


@app.get("/api/v1/dashboard/summary", response_model=Envelope[Summary])
def summary(start_date: date | None = None, end_date: date | None = None, store_id: str | None = None) -> Envelope[Summary]:
    resolved = query_filters(start_date, end_date, store_id)
    return Envelope(filters=resolved, data=analytics.get_summary(resolved))


@app.get("/api/v1/dashboard/daily", response_model=Envelope[list[DailyPoint]])
def daily(start_date: date | None = None, end_date: date | None = None, store_id: str | None = None) -> Envelope[list[DailyPoint]]:
    resolved = query_filters(start_date, end_date, store_id)
    return Envelope(filters=resolved, data=analytics.get_daily(resolved))


@app.get("/api/v1/dashboard/top-products", response_model=Envelope[list[ProductPerformance]])
def top_products(start_date: date | None = None, end_date: date | None = None, store_id: str | None = None, limit: int = Query(default=10, ge=1, le=50)) -> Envelope[list[ProductPerformance]]:
    resolved = query_filters(start_date, end_date, store_id)
    return Envelope(filters=resolved, data=analytics.get_top_products(resolved, limit))


@app.get("/api/v1/ai/config")
def get_ai_config() -> dict:
    return {"data": ai_config.public_config()}


@app.patch("/api/v1/ai/config")
def update_ai_config(payload: ConfigUpdate) -> dict:
    return {"data": ai_config.update_config(payload.provider, payload.model, payload.base_url, payload.timeout_seconds)}


@app.post("/api/v1/ai/config/test", response_model=ConfigTestResponse)
def test_ai_config() -> ConfigTestResponse:
    try:
        latency, message = test_connection()
        current = ai_config.get_config()
        return ConfigTestResponse(status="ok", provider=current.provider, model=current.model, latency_ms=latency, message=message)
    except Exception as exc:
        current = ai_config.get_config()
        raise HTTPException(status_code=503, detail=f"LLM 连接失败: {type(exc).__name__}") from exc


@app.get("/api/v1/ai/conversations")
def get_conversations() -> dict:
    return {"data": conversations.list_conversations()}


@app.post("/api/v1/ai/conversations", response_model=Conversation)
def create_conversation(payload: ConversationCreate) -> Conversation:
    return conversations.create_conversation(payload.title or "新对话")


@app.delete("/api/v1/ai/conversations/{conversation_id}")
def remove_conversation(conversation_id: str) -> dict:
    conversations.delete_conversation(conversation_id)
    return {"status": "deleted", "conversation_id": conversation_id}


@app.get("/api/v1/ai/conversations/{conversation_id}/messages")
def get_conversation_messages(conversation_id: str) -> dict:
    return {"data": conversations.get_messages(conversation_id)}


@app.post("/api/v1/ai/query")
def query_ai(payload: QueryRequest) -> dict:
    if not conversations.conversation_exists(payload.conversation_id):
        raise HTTPException(status_code=404, detail="对话不存在")
    previous = conversations.last_query_plan(payload.conversation_id)
    try:
        plan, mode = parse_question(payload.question, previous)
    except ValueError:
        conversations.add_message(payload.conversation_id, "user", payload.question, "unsupported")
        assistant = conversations.add_message(payload.conversation_id, "assistant", "这个问题暂不在当前数据问答范围内，我可以回答营业额、订单数、客单价、商品、门店和品类问题。", "unsupported")
        return ai_response(payload.conversation_id, assistant, None, "unsupported")
    if plan.confidence < 0.75:
        conversations.add_message(payload.conversation_id, "user", payload.question, "clarification_required")
        assistant = conversations.add_message(payload.conversation_id, "assistant", "请补充明确的日期、商品或门店，我才能查询准确数据。", "clarification_required", plan.model_dump())
        return ai_response(payload.conversation_id, assistant, None, "clarification_required", plan)
    conversations.add_message(payload.conversation_id, "user", payload.question, "pending")
    try:
        facts = execute_plan(plan)
    except (LookupError, ValueError):
        facts = None
    if facts is None or (facts.value is None and not facts.rows):
        content = "当前问题没有匹配到销售记录，请检查商品、门店或日期。"
        assistant = conversations.add_message(payload.conversation_id, "assistant", content, "no_data", plan.model_dump(), facts.model_dump() if facts else None)
        return ai_response(payload.conversation_id, assistant, facts, "no_data", plan)
    status = "answered_local"
    content = template_answer(plan, facts)
    try:
        generated = generate_answer(payload.question, facts)
        if validate_answer(generated, facts):
            content = generated
            status = "answered"
    except Exception:
        status = "provider_error"
    assistant = conversations.add_message(payload.conversation_id, "assistant", content, status, plan.model_dump(), facts.model_dump())
    return ai_response(payload.conversation_id, assistant, facts, status, plan)
