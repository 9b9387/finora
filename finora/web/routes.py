"""Read-only API routes over the market store, universe snapshots, and
quality checks. Every handler gets a fresh in-memory MarketStore so the
server never holds the DuckDB file lock that `finora etl` needs."""
from __future__ import annotations

import math
from datetime import date
from pathlib import Path
from typing import Iterator

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from finora.core.config import Settings
from finora.core.models import utc_now
from finora.data.etl import last_completed_session
from finora.data.events import detect_adjustment_events
from finora.data.store import MarketStore, run_quality_checks
from finora.data.universe import normalize_symbol
from finora.web import schemas

router = APIRouter(prefix="/api")


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_store(request: Request) -> Iterator[MarketStore]:
    with MarketStore(request.app.state.settings.data, in_memory=True) as store:
        yield store


def _iso(value: object) -> str:
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def _opt_float(value: object) -> float | None:
    number = float(value)
    return None if math.isnan(number) else number


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/store/overview", response_model=schemas.StoreOverview)
def store_overview(
    settings: Settings = Depends(get_settings),
    store: MarketStore = Depends(get_store),
) -> schemas.StoreOverview:
    rows = store.conn.execute(
        "SELECT symbol, count(*) AS rows, min(date), max(date) FROM daily_bars "
        "WHERE symbol IS NOT NULL GROUP BY symbol ORDER BY symbol"
    ).fetchall()
    session = last_completed_session()
    summaries = [
        schemas.SymbolSummary(
            symbol=r[0],
            rows=int(r[1]),
            first_date=_iso(r[2]),
            last_date=_iso(r[3]),
            fresh=pd.Timestamp(r[3]).date() >= session,
        )
        for r in rows
    ]
    parquet_dir = settings.data.parquet_dir
    size = (
        sum(p.stat().st_size for p in parquet_dir.glob("symbol=*/*.parquet"))
        if parquet_dir.is_dir()
        else 0
    )
    return schemas.StoreOverview(
        total_rows=sum(s.rows for s in summaries),
        symbol_count=len(summaries),
        last_completed_session=session.isoformat(),
        store_size_bytes=size,
        symbols=summaries,
    )


@router.get("/symbols", response_model=schemas.SymbolList)
def list_symbols(store: MarketStore = Depends(get_store)) -> schemas.SymbolList:
    return schemas.SymbolList(symbols=store.symbols())


@router.get("/symbols/{symbol}/bars", response_model=schemas.BarsResponse)
def symbol_bars(
    symbol: str,
    start: date | None = None,
    end: date | None = None,
    store: MarketStore = Depends(get_store),
) -> schemas.BarsResponse:
    symbol = symbol.upper()
    if symbol not in store.symbols():
        raise HTTPException(status_code=404, detail=f"unknown symbol {symbol!r}")
    df = store.get_prices([symbol], start, end)
    bars = [
        schemas.Bar(
            date=_iso(row.date),
            open=_opt_float(row.open),
            high=_opt_float(row.high),
            low=_opt_float(row.low),
            close=float(row.close),
            volume=_opt_float(row.volume),
            factor=float(row.factor),
            dividend=float(row.dividend),
            split_ratio=float(row.split_ratio),
        )
        for row in df.itertuples(index=False)
    ]
    return schemas.BarsResponse(symbol=symbol, count=len(bars), bars=bars)


@router.get("/symbols/{symbol}/events", response_model=schemas.EventsResponse)
def symbol_events(
    symbol: str, store: MarketStore = Depends(get_store)
) -> schemas.EventsResponse:
    symbol = symbol.upper()
    df = store.get_prices([symbol])
    if df.empty:
        raise HTTPException(status_code=404, detail=f"unknown symbol {symbol!r}")
    events = [
        schemas.EventModel(
            date=event.date.isoformat(),
            kind=event.kind,
            close=event.close,
            dividend=event.dividend,
            split_ratio=event.split_ratio,
        )
        for event in detect_adjustment_events(df)
    ]
    return schemas.EventsResponse(symbol=symbol, events=events)


@router.get("/quality", response_model=schemas.QualityResponse)
def quality(
    symbol: list[str] | None = Query(default=None),
    settings: Settings = Depends(get_settings),
    store: MarketStore = Depends(get_store),
) -> schemas.QualityResponse:
    requested = [s.upper() for s in symbol] if symbol else None
    df = store.get_prices(requested)
    issues = run_quality_checks(df, settings.data.quality)
    quality_cfg = settings.data.quality
    return schemas.QualityResponse(
        checked_symbols=int(df["symbol"].nunique()) if not df.empty else 0,
        thresholds=schemas.QualityThresholds(
            max_missing_run_days=quality_cfg.max_missing_run_days,
            max_abs_daily_return=quality_cfg.max_abs_daily_return,
            min_price=quality_cfg.min_price,
        ),
        issues=[
            schemas.QualityIssueModel(
                symbol=i.symbol,
                kind=i.kind,
                detail=i.detail,
                date=i.date.isoformat() if i.date else None,
            )
            for i in issues
        ],
        generated_at=utc_now().isoformat(),
    )


# -- backtests ---------------------------------------------------------------


def _load_artifact_json(path: Path) -> dict | None:
    import json

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _artifact_summary(run_dir: Path) -> schemas.BacktestSummary | None:
    metrics = _load_artifact_json(run_dir / "metrics.json")
    config = _load_artifact_json(run_dir / "config.json") or {}
    if metrics is None:
        return None
    name, _, stamp = run_dir.name.rpartition("_")
    try:
        parsed = schemas.BacktestMetrics(**metrics)
    except Exception:
        return None
    return schemas.BacktestSummary(
        id=run_dir.name,
        name=config.get("name") or name or run_dir.name,
        stamp=stamp,
        kind=config.get("kind"),
        start=config.get("start"),
        end=config.get("end"),
        cost_bps=config.get("cost_bps"),
        metrics=parsed,
    )


@router.get("/backtests", response_model=schemas.BacktestList)
def list_backtests(settings: Settings = Depends(get_settings)) -> schemas.BacktestList:
    backtests_dir = settings.ops.backtests_dir
    if not backtests_dir.is_dir():
        return schemas.BacktestList(runs=[])
    runs = [
        summary
        for run_dir in backtests_dir.iterdir()
        if run_dir.is_dir() and (summary := _artifact_summary(run_dir)) is not None
    ]
    # newest stamp first, then name for stable ordering
    runs.sort(key=lambda r: (r.stamp, r.name), reverse=True)
    return schemas.BacktestList(runs=runs)


@router.get("/backtests/{run_id}", response_model=schemas.BacktestDetail)
def backtest_detail(
    run_id: str, settings: Settings = Depends(get_settings)
) -> schemas.BacktestDetail:
    backtests_dir = settings.ops.backtests_dir
    run_dir = (backtests_dir / run_id).resolve()
    if run_dir.parent != backtests_dir.resolve() or not run_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"unknown backtest {run_id!r}")
    summary = _artifact_summary(run_dir)
    if summary is None:
        raise HTTPException(status_code=404, detail=f"backtest {run_id!r} has no readable metrics")

    points: list[schemas.EquityPoint] = []
    returns_path = run_dir / "returns.csv"
    if returns_path.exists():
        frame = pd.read_csv(returns_path)
        if {"date", "return"}.issubset(frame.columns) and len(frame):
            returns = pd.to_numeric(frame["return"], errors="coerce").fillna(0.0)
            equity = (1.0 + returns).cumprod()
            drawdown = equity / equity.cummax() - 1.0
            points = [
                schemas.EquityPoint(
                    date=_iso(day),
                    ret=round(float(r), 8),
                    equity=round(float(e), 6),
                    drawdown=round(float(dd), 6),
                )
                for day, r, e, dd in zip(frame["date"], returns, equity, drawdown)
            ]

    config = _load_artifact_json(run_dir / "config.json") or {}
    trades = config.pop("trades", None)
    return schemas.BacktestDetail(
        id=run_dir.name,
        name=summary.name,
        metrics=summary.metrics,
        config=config,
        points=points,
        trades=trades if isinstance(trades, list) else None,
    )


# -- universe ----------------------------------------------------------------


def _read_snapshot(path: Path) -> list[str]:
    df = pd.read_csv(path)
    if "symbol" not in df.columns:
        raise ValueError(f"snapshot {path.name} has no 'symbol' column")
    return sorted({normalize_symbol(str(s)) for s in df["symbol"].dropna()})


def _snapshot_path(settings: Settings, snapshot_date: date) -> Path:
    return settings.data.universe_dir / f"{snapshot_date.isoformat()}.csv"


@router.get("/universe/snapshots", response_model=schemas.SnapshotList)
def universe_snapshots(
    settings: Settings = Depends(get_settings),
) -> schemas.SnapshotList:
    universe_dir = settings.data.universe_dir
    if not universe_dir.is_dir():
        return schemas.SnapshotList(snapshots=[])
    snapshots = []
    for path in sorted(universe_dir.glob("*.csv"), reverse=True):
        try:
            symbols = _read_snapshot(path)
        except Exception:
            continue  # unreadable file: not this endpoint's problem
        snapshots.append(
            schemas.SnapshotInfo(date=path.stem, symbol_count=len(symbols))
        )
    return schemas.SnapshotList(snapshots=snapshots)


@router.get("/universe/snapshots/{snapshot_date}", response_model=schemas.SnapshotDetail)
def universe_snapshot_detail(
    snapshot_date: date, settings: Settings = Depends(get_settings)
) -> schemas.SnapshotDetail:
    path = _snapshot_path(settings, snapshot_date)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"no snapshot for {snapshot_date}")
    return schemas.SnapshotDetail(
        date=snapshot_date.isoformat(), symbols=_read_snapshot(path)
    )


@router.get("/universe/diff", response_model=schemas.UniverseDiff)
def universe_diff(
    from_date: date = Query(alias="from"),
    to_date: date = Query(alias="to"),
    settings: Settings = Depends(get_settings),
) -> schemas.UniverseDiff:
    sides: dict[str, set[str]] = {}
    for label, snapshot_date in (("from", from_date), ("to", to_date)):
        path = _snapshot_path(settings, snapshot_date)
        if not path.exists():
            raise HTTPException(
                status_code=404, detail=f"no snapshot for {label}={snapshot_date}"
            )
        sides[label] = set(_read_snapshot(path))
    return schemas.UniverseDiff(
        from_date=from_date.isoformat(),
        to_date=to_date.isoformat(),
        added=sorted(sides["to"] - sides["from"]),
        removed=sorted(sides["from"] - sides["to"]),
        unchanged_count=len(sides["from"] & sides["to"]),
    )
