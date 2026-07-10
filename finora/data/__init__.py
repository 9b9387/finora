"""L1 data layer: universe snapshots, OpenBB ETL, Parquet/DuckDB store,
and Qlib bin conversion."""
from __future__ import annotations

from finora.data.etl import EtlResult, FetchFn, fetch_daily_bars_openbb, run_etl
from finora.data.qlib_convert import convert_store, convert_to_qlib, read_day_bin
from finora.data.store import (
    CANONICAL_COLUMNS,
    MarketStore,
    QualityIssue,
    empty_bars,
    run_quality_checks,
)
from finora.data.universe import (
    fetch_sp500_symbols,
    load_universe,
    normalize_symbol,
    snapshot_universe,
)

__all__ = [
    "CANONICAL_COLUMNS",
    "EtlResult",
    "FetchFn",
    "MarketStore",
    "QualityIssue",
    "convert_store",
    "convert_to_qlib",
    "empty_bars",
    "fetch_daily_bars_openbb",
    "fetch_sp500_symbols",
    "load_universe",
    "normalize_symbol",
    "read_day_bin",
    "run_etl",
    "run_quality_checks",
    "snapshot_universe",
]
