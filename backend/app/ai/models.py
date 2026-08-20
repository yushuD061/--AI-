from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


Status = Literal["answered", "answered_local", "no_data", "unsupported", "clarification_required", "provider_error"]


class QueryPlan(BaseModel):
    intent: str
    metric: str = "net_revenue"
    dimensions: list[str] = Field(default_factory=list)
    start_date: date | None = None
    end_date: date | None = None
    product_name: str | None = None
    store_id: str | None = None
    category: str | None = None
    confidence: float = 1.0


class FactSet(BaseModel):
    intent: str
    metric: str
    value: float | None = None
    unit: str = "CNY"
    filters: dict[str, Any] = Field(default_factory=dict)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    source: str = "sales_clean.sqlite:sales_clean"


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=80)


class QueryRequest(BaseModel):
    conversation_id: str
    question: str = Field(min_length=1, max_length=1000)


class ConfigUpdate(BaseModel):
    provider: str = Field(min_length=1, max_length=40)
    model: str = Field(min_length=1, max_length=120)
    base_url: str = Field(min_length=8, max_length=300)
    timeout_seconds: int = Field(default=30, ge=5, le=120)


class ConfigTestResponse(BaseModel):
    status: str
    provider: str
    model: str
    latency_ms: int | None = None
    message: str


class AssistantMessage(BaseModel):
    message_id: str
    role: str
    content: str
    status: Status | str
    facts: dict[str, Any] | None = None
    created_at: datetime


class Conversation(BaseModel):
    conversation_id: str
    title: str
    message_count: int
    created_at: datetime
    updated_at: datetime
