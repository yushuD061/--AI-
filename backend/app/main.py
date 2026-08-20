from __future__ import annotations

from datetime import date

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .database import database_available
from .schemas import DailyPoint, Envelope, FilterData, Filters, HealthResponse, ProductPerformance, Summary
from .services import analytics

app = FastAPI(title="Moneki Store Sales API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["GET"],
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
