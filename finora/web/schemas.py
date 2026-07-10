"""Response models for the data API. Dates travel as YYYY-MM-DD strings."""
from __future__ import annotations

from pydantic import BaseModel


class SymbolSummary(BaseModel):
    symbol: str
    rows: int
    first_date: str
    last_date: str
    fresh: bool


class StoreOverview(BaseModel):
    total_rows: int
    symbol_count: int
    last_completed_session: str
    store_size_bytes: int
    symbols: list[SymbolSummary]


class SymbolList(BaseModel):
    symbols: list[str]


class Bar(BaseModel):
    date: str
    open: float | None
    high: float | None
    low: float | None
    close: float
    volume: float | None
    factor: float
    dividend: float
    split_ratio: float


class BarsResponse(BaseModel):
    symbol: str
    count: int
    bars: list[Bar]


class EventModel(BaseModel):
    date: str
    kind: str  # "split" | "dividend"
    close: float
    dividend: float | None = None
    split_ratio: float | None = None


class EventsResponse(BaseModel):
    symbol: str
    events: list[EventModel]


class QualityThresholds(BaseModel):
    max_missing_run_days: int
    max_abs_daily_return: float
    min_price: float


class QualityIssueModel(BaseModel):
    symbol: str
    kind: str
    detail: str
    date: str | None


class QualityResponse(BaseModel):
    checked_symbols: int
    thresholds: QualityThresholds
    issues: list[QualityIssueModel]
    generated_at: str


class SnapshotInfo(BaseModel):
    date: str
    symbol_count: int


class SnapshotList(BaseModel):
    snapshots: list[SnapshotInfo]


class SnapshotDetail(BaseModel):
    date: str
    symbols: list[str]


class UniverseDiff(BaseModel):
    from_date: str
    to_date: str
    added: list[str]
    removed: list[str]
    unchanged_count: int
