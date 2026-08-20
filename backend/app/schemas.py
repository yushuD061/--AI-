from __future__ import annotations

from datetime import date
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class Store(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    store_id: str
    store_name: str
    district: str


class Filters(BaseModel):
    start_date: date
    end_date: date
    store_id: str | None = None
    allowed_store_ids: list[str] | None = Field(default=None, exclude=True)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class Envelope(BaseModel, Generic[T]):
    filters: Filters
    data: T


class FilterData(BaseModel):
    date_min: date
    date_max: date
    stores: list[Store]


class Summary(BaseModel):
    net_revenue: float
    order_count: int
    average_order_value: float


class DailyPoint(BaseModel):
    date: date
    net_revenue: float
    order_count: int
    average_order_value: float


class ProductPerformance(BaseModel):
    rank: int
    product_id: str
    product_name: str
    product_category: str
    net_revenue: float
    quantity: float
    order_count: int


class HealthResponse(BaseModel):
    status: str
    database: str


class AlertReadUpdate(BaseModel):
    is_read: bool


class DailyReportCreate(BaseModel):
    report_date: date
    store_id: str | None = None
