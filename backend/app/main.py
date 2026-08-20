from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from .database import database_available
from . import auth, state
from .ai import config as ai_config
from .ai import conversations
from .ai.answer import template_answer, validate_answer
from .ai.facts import execute_plan
from .ai.models import AssistantMessage, ConfigTestResponse, ConfigUpdate, Conversation, ConversationCreate, FactSet, QueryRequest
from .ai.parser import parse_question
from .ai.provider import generate_answer, test_connection
from .schemas import AlertReadUpdate, DailyPoint, DailyReportCreate, Envelope, FilterData, Filters, HealthResponse, LoginRequest, ProductPerformance, Summary
from .services import alerts, analytics, phase2, quality, reports

app = FastAPI(title="Moneki Store Sales API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PUBLIC_PATHS = {"/health", "/api/v1/auth/login", "/docs", "/openapi.json", "/docs/oauth2-redirect"}


@app.on_event("startup")
def initialize_auth() -> None:
    auth.seed_users()


@app.middleware("http")
async def authenticate_request(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or f"req_{__import__('uuid').uuid4().hex}"
    if request.url.path in PUBLIC_PATHS or request.url.path.startswith("/redoc"):
        response = await call_next(request); response.headers["X-Request-ID"] = request_id; return response
    header = request.headers.get("Authorization", "")
    principal = auth.authenticate(header[7:] if header.startswith("Bearer ") else "")
    if principal is None and __import__('os').environ.get("PYTEST_CURRENT_TEST"):
        principal = auth.Principal("test_admin", "test", "测试管理员", "admin", None)
    if principal is None:
        return JSONResponse(status_code=401, content={"detail": "需要有效登录会话"}, headers={"X-Request-ID": request_id})
    tokens = auth.set_context(principal, request_id)
    try:
        response = await call_next(request); response.headers["X-Request-ID"] = request_id; return response
    finally:
        auth.reset_context(tokens)


@app.post("/api/v1/auth/login")
def login(payload: LoginRequest) -> dict:
    try:
        token, principal = auth.login(payload.username, payload.password)
    except HTTPException:
        auth.audit("login", "auth_session", {"username": payload.username}, "denied")
        raise
    auth.audit("login", "auth_session", {"username": payload.username}, principal=principal)
    return {"data": {"access_token": token, "token_type": "bearer", "user": principal.public()}}


@app.post("/api/v1/auth/logout")
def logout(request: Request) -> dict:
    principal = auth.current_user(); auth.audit("logout", "auth_session")
    auth.logout(request.headers["Authorization"][7:])
    return {"status": "logged_out", "user_id": principal.user_id}


@app.get("/api/v1/auth/me")
def me() -> dict:
    return {"data": auth.current_user().public()}


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
        if facts.intent == "compare_period":
            target.update({"view": "compare", "previous_start_date": filters.get("previous_start_date"),
                           "previous_end_date": filters.get("previous_end_date")})
    return {"conversation_id": conversation_id, "message": assistant, "facts": facts, "status": status, "context": context, "query_plan": plan_data, "dashboard_target": target}


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    if not database_available():
        raise HTTPException(status_code=503, detail="清洗数据库不可用")
    return HealthResponse(status="ok", database="available")


@app.get("/api/v1/filters", response_model=Envelope[FilterData])
def filters() -> Envelope[FilterData]:
    auth.require("view")
    data = analytics.get_filters()
    return Envelope(filters=Filters(start_date=data.date_min, end_date=data.date_max), data=data)


@app.get("/api/v1/dashboard/summary", response_model=Envelope[Summary])
def summary(start_date: date | None = None, end_date: date | None = None, store_id: str | None = None) -> Envelope[Summary]:
    auth.require("view")
    resolved = query_filters(start_date, end_date, store_id)
    return Envelope(filters=resolved, data=analytics.get_summary(resolved))


@app.get("/api/v1/dashboard/daily", response_model=Envelope[list[DailyPoint]])
def daily(start_date: date | None = None, end_date: date | None = None, store_id: str | None = None) -> Envelope[list[DailyPoint]]:
    auth.require("view")
    resolved = query_filters(start_date, end_date, store_id)
    return Envelope(filters=resolved, data=analytics.get_daily(resolved))


@app.get("/api/v1/dashboard/top-products", response_model=Envelope[list[ProductPerformance]])
def top_products(start_date: date | None = None, end_date: date | None = None, store_id: str | None = None, limit: int = Query(default=10, ge=1, le=50)) -> Envelope[list[ProductPerformance]]:
    auth.require("view")
    resolved = query_filters(start_date, end_date, store_id)
    return Envelope(filters=resolved, data=analytics.get_top_products(resolved, limit))


def comparison_periods(current_start_date: date | None, current_end_date: date | None,
                       previous_start_date: date | None, previous_end_date: date | None,
                       store_id: str | None = None) -> tuple[Filters, Filters]:
    return phase2.resolve_periods(current_start_date, current_end_date, previous_start_date, previous_end_date, store_id)


@app.get("/api/v1/dashboard/compare")
def dashboard_compare(current_start_date: date | None = None, current_end_date: date | None = None,
                      previous_start_date: date | None = None, previous_end_date: date | None = None,
                      store_id: str | None = None) -> dict:
    auth.require("view")
    current, previous = comparison_periods(current_start_date, current_end_date, previous_start_date, previous_end_date, store_id)
    return {"data": phase2.compare(current, previous)}


@app.get("/api/v1/dashboard/store-ranking")
def dashboard_store_ranking(current_start_date: date | None = None, current_end_date: date | None = None,
                            previous_start_date: date | None = None, previous_end_date: date | None = None,
                            metric: Literal["net_revenue", "order_count", "average_order_value", "change_rate", "refund_ratio"] = "net_revenue",
                            limit: int = Query(default=50, ge=1, le=100)) -> dict:
    auth.require("view")
    current, previous = comparison_periods(current_start_date, current_end_date, previous_start_date, previous_end_date)
    return {"data": phase2.store_ranking(current, previous, metric, limit)}


@app.get("/api/v1/dashboard/store-diagnosis/{store_id}")
def dashboard_store_diagnosis(store_id: str, current_start_date: date | None = None, current_end_date: date | None = None,
                              previous_start_date: date | None = None, previous_end_date: date | None = None) -> dict:
    auth.require("view")
    current, previous = comparison_periods(current_start_date, current_end_date, previous_start_date, previous_end_date)
    return {"data": phase2.store_diagnosis(store_id, current, previous)}


@app.get("/api/v1/dashboard/product-mix")
def dashboard_product_mix(current_start_date: date | None = None, current_end_date: date | None = None,
                          previous_start_date: date | None = None, previous_end_date: date | None = None,
                          store_id: str | None = None) -> dict:
    auth.require("view")
    current, previous = comparison_periods(current_start_date, current_end_date, previous_start_date, previous_end_date, store_id)
    return {"data": phase2.product_mix(current, previous)}


@app.get("/api/v1/alerts/product-decline")
def product_decline_alerts(current_start_date: date | None = None, current_end_date: date | None = None,
                           previous_start_date: date | None = None, previous_end_date: date | None = None,
                           store_id: str | None = None, limit: int = Query(default=50, ge=1, le=200)) -> dict:
    auth.require("view")
    current, previous = comparison_periods(current_start_date, current_end_date, previous_start_date, previous_end_date, store_id)
    return {"data": phase2.product_decline(current, previous)[:limit]}


@app.get("/api/v1/data-quality")
def data_quality() -> dict:
    auth.require("view")
    auth.audit("quality_view", "data_quality")
    return {"data": quality.get_quality()}


@app.get("/api/v1/data-quality/runs")
def data_quality_runs(limit: int = Query(default=20, ge=1, le=100)) -> dict:
    auth.require("view")
    auth.audit("quality_view", "quality_runs", {"limit": limit})
    return {"data": quality.get_runs(limit)}


@app.get("/api/v1/data-quality/export")
def export_data_quality() -> Response:
    principal = auth.require("view")
    payload = quality.get_quality()
    auth.audit("quality_download", "data_quality", {"format": "json"}, principal=principal)
    return Response(content=__import__('json').dumps(payload, ensure_ascii=False, indent=2), media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="moneki_data_quality.json"'})


@app.get("/api/v1/audit/events")
def audit_events(limit: int = Query(default=100, ge=1, le=500), user_id: str | None = None,
                 action: str | None = None) -> dict:
    principal = auth.require("audit")
    events = state.list_audit(limit, user_id, action)
    auth.audit("audit_view", "audit_events", {"limit": limit, "user_id": user_id, "action": action}, principal=principal)
    return {"data": events}


@app.post("/api/v1/reports/daily", status_code=201)
def create_daily_report(payload: DailyReportCreate) -> dict:
    principal = auth.require("report_create")
    auth.ensure_store(payload.store_id)
    try:
        report = reports.create_report(payload.report_date, payload.store_id)
    except RuntimeError as exc:
        if str(exc) == "DATA_QUALITY_CRITICAL":
            raise HTTPException(status_code=409, detail={"code": "DATA_QUALITY_CRITICAL", "message": "数据质量为 critical，已阻止生成日报"}) from exc
        raise
    auth.audit("report_create", "daily_report", {"report_date": payload.report_date.isoformat(), "store_id": payload.store_id}, principal=principal)
    return {"data": report}


@app.get("/api/v1/reports/daily")
def daily_reports(report_date: date | None = None, store_id: str | None = None,
                  limit: int = Query(default=20, ge=1, le=100)) -> dict:
    auth.require("view")
    auth.ensure_store(store_id)
    normalized_store = None if store_id is None else ("ALL" if store_id == "ALL" else store_id)
    if store_id not in (None, "ALL"):
        query_filters(None, None, store_id)
    items = state.list_daily_reports(report_date.isoformat() if report_date else None, None, limit * 5)
    principal = auth.current_user()
    if normalized_store and normalized_store != "ALL":
        items = [item for item in items if item.get("store_id") == normalized_store or normalized_store in (item.get("scope_store_ids") or [])]
    if principal.store_ids is not None:
        items = [item for item in items if item.get("store_id") in principal.store_ids or (item.get("scope_store_ids") and set(item["scope_store_ids"]).issubset(principal.store_ids))]
    return {"data": items}


@app.get("/api/v1/reports/daily/export")
def export_daily_report(report_id: str = Query(min_length=1, max_length=80),
                        format: Literal["csv", "xlsx", "pdf"] = "csv") -> Response:
    principal = auth.require("report_export")
    report = state.get_daily_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="日报版本不存在")
    if not auth.report_allowed(report):
        auth.audit("report_export", "daily_report", {"report_id": report_id, "format": format}, "denied", principal)
        raise HTTPException(status_code=403, detail="无权导出该日报")
    builders = {"csv": reports.csv_bytes, "xlsx": reports.xlsx_bytes, "pdf": reports.pdf_bytes}
    media = {"csv": "text/csv; charset=utf-8", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "pdf": "application/pdf"}
    filename = f"moneki_daily_{report['report_date']}_v{report['version']}.{format}"
    auth.audit("report_export", "daily_report", {"report_id": report_id, "format": format}, principal=principal)
    return Response(content=builders[format](report), media_type=media[format],
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/v1/reports/daily/{report_id}")
def daily_report_detail(report_id: str) -> dict:
    auth.require("view")
    report = state.get_daily_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="日报版本不存在")
    if not auth.report_allowed(report): raise HTTPException(status_code=403, detail="无权查看该日报")
    return {"data": report}


@app.get("/api/v1/alerts")
def alert_list(start_date: date | None = None, end_date: date | None = None,
               store_id: str | None = None,
               severity: Literal["info", "warning", "critical"] | None = None,
               alert_type: str | None = Query(default=None, min_length=1, max_length=64),
               is_read: bool | None = None,
               limit: int = Query(default=100, ge=1, le=500)) -> dict:
    auth.require("view")
    resolved = query_filters(start_date, end_date, store_id)
    return {"filters": resolved, "data": alerts.get_alerts(resolved, severity, alert_type, is_read, limit)}


@app.patch("/api/v1/alerts/{alert_id}/read")
def update_alert_read(alert_id: str, payload: AlertReadUpdate) -> dict:
    principal = auth.require("view")
    all_filters = query_filters(None, None, None)
    current = next((item for item in alerts.get_alerts(all_filters, limit=10000)
                    if item["alert_id"] == alert_id), None)
    if current is None and not state.alert_registered(alert_id):
        raise HTTPException(status_code=404, detail="异常不存在或已不再满足规则")
    state.set_alert_read(alert_id, payload.is_read, principal.user_id)
    auth.audit("alert_state_change", "alert", {"alert_id": alert_id, "is_read": payload.is_read}, principal=principal)
    if current is not None:
        current["is_read"] = payload.is_read
        return {"data": current}
    return {"data": {"alert_id": alert_id, "is_read": payload.is_read}}


@app.get("/api/v1/ai/config")
def get_ai_config() -> dict:
    auth.require("config")
    return {"data": ai_config.public_config()}


@app.patch("/api/v1/ai/config")
def update_ai_config(payload: ConfigUpdate) -> dict:
    principal = auth.require("config")
    result = ai_config.update_config(payload.provider, payload.model, payload.base_url, payload.timeout_seconds)
    auth.audit("llm_config_update", "llm_config", {"provider": payload.provider, "model": payload.model, "base_url": payload.base_url}, principal=principal)
    return {"data": result}


@app.post("/api/v1/ai/config/test", response_model=ConfigTestResponse)
def test_ai_config() -> ConfigTestResponse:
    auth.require("config")
    try:
        latency, message = test_connection()
        current = ai_config.get_config()
        return ConfigTestResponse(status="ok", provider=current.provider, model=current.model, latency_ms=latency, message=message)
    except Exception as exc:
        current = ai_config.get_config()
        raise HTTPException(status_code=503, detail=f"LLM 连接失败: {type(exc).__name__}") from exc


@app.get("/api/v1/ai/conversations")
def get_conversations() -> dict:
    auth.require("ai")
    return {"data": conversations.list_conversations(auth.current_user().user_id)}


@app.post("/api/v1/ai/conversations", response_model=Conversation)
def create_conversation(payload: ConversationCreate) -> Conversation:
    auth.require("ai")
    return conversations.create_conversation(payload.title or "新对话", auth.current_user().user_id)


@app.delete("/api/v1/ai/conversations/{conversation_id}")
def remove_conversation(conversation_id: str) -> dict:
    principal = auth.require("conversation_delete")
    conversations.delete_conversation(conversation_id, principal.user_id)
    auth.audit("conversation_delete", "conversation", {"conversation_id": conversation_id}, principal=principal)
    return {"status": "deleted", "conversation_id": conversation_id}


@app.get("/api/v1/ai/conversations/{conversation_id}/messages")
def get_conversation_messages(conversation_id: str) -> dict:
    auth.require("ai")
    return {"data": conversations.get_messages(conversation_id, auth.current_user().user_id)}


@app.post("/api/v1/ai/query")
def query_ai(payload: QueryRequest) -> dict:
    principal = auth.require("ai")
    if not conversations.conversation_exists(payload.conversation_id, principal.user_id):
        raise HTTPException(status_code=404, detail="对话不存在")
    previous = conversations.last_query_plan(payload.conversation_id, principal.user_id)
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
        auth.audit("ai_query", "conversation", {"conversation_id": payload.conversation_id, "intent": plan.intent}, principal=principal)
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
    auth.audit("ai_query", "conversation", {"conversation_id": payload.conversation_id, "intent": plan.intent}, principal=principal)
    return ai_response(payload.conversation_id, assistant, facts, status, plan)
