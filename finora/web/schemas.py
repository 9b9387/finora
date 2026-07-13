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


class StrategyModel(BaseModel):
    name: str
    kind: str
    stage: str
    capital_fraction: float
    params: dict


class StrategyListResponse(BaseModel):
    strategies: list[StrategyModel]


class RunBacktestRequest(BaseModel):
    name: str
    symbol: str | None = None  # overrides params.symbol for technical kinds
    start: str | None = None  # ISO dates
    end: str | None = None
    cost_bps: float = 15.0


class BacktestMetrics(BaseModel):
    # floats are None when the artifact stored a non-finite value (e.g. the
    # sharpe of a constant return series)
    total_return: float | None
    annualized_return: float | None
    annualized_vol: float | None
    sharpe: float | None
    max_drawdown: float | None
    calmar: float | None
    n_days: int


class BacktestSummary(BaseModel):
    id: str  # artifact directory name, e.g. rsi_spy_20260709
    name: str
    stamp: str  # last return date, YYYYMMDD (from the directory name)
    kind: str | None
    start: str | None
    end: str | None
    cost_bps: float | None
    metrics: BacktestMetrics


class BacktestList(BaseModel):
    runs: list[BacktestSummary]


class EquityPoint(BaseModel):
    date: str
    ret: float
    equity: float
    drawdown: float


class BacktestDetail(BaseModel):
    id: str
    name: str
    metrics: BacktestMetrics
    config: dict
    points: list[EquityPoint]
    trades: list[dict] | None = None


class RunBacktestResponse(BaseModel):
    id: str  # artifact directory name — /api/backtests/{id} serves the detail
    name: str
    metrics: BacktestMetrics
