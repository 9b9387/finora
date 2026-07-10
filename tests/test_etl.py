"""Tests for finora.data.etl using an injected deterministic fetch function."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from finora.core.config import DataConfig, Settings
from finora.data.etl import run_etl
from finora.data.store import CANONICAL_COLUMNS

# NYSE trading days, January 2024 (Jan 1 = New Year, Jan 15 = MLK).
JAN_2024_TRADING_DAYS = [
    date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5),
    date(2024, 1, 8), date(2024, 1, 9), date(2024, 1, 10), date(2024, 1, 11),
    date(2024, 1, 12), date(2024, 1, 16), date(2024, 1, 17), date(2024, 1, 18),
    date(2024, 1, 19),
]


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        data=DataConfig(data_dir=tmp_path / "data", start_date=date(2024, 1, 2)),
    )


def make_bars(symbols: list[str], start: date, end: date, base: float = 100.0) -> pd.DataFrame:
    days = [d for d in JAN_2024_TRADING_DAYS if start <= d <= end]
    rows = []
    for symbol in symbols:
        for i, d in enumerate(days):
            px = base + i * 0.5
            rows.append(
                {
                    "symbol": symbol,
                    "date": pd.Timestamp(d),
                    "open": px,
                    "high": px + 1,
                    "low": px - 1,
                    "close": px,
                    "volume": 1_000.0,
                    "factor": 1.0,
                }
            )
    return pd.DataFrame(rows, columns=CANONICAL_COLUMNS)


class RecordingFetch:
    """Deterministic FetchFn that records every (symbols, start, end) call."""

    def __init__(self, base: float = 100.0) -> None:
        self.calls: list[tuple[list[str], date, date]] = []
        self.base = base

    def __call__(self, symbols: list[str], start: date, end: date) -> pd.DataFrame:
        self.calls.append((list(symbols), start, end))
        return make_bars(symbols, start, end, base=self.base)


def test_first_run_writes_parquet_per_symbol(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    fetch = RecordingFetch()
    result = run_etl(settings, symbols=["AAA", "BBB"], fetch_fn=fetch, end=date(2024, 1, 10))

    assert sorted(result.symbols_updated) == ["AAA", "BBB"]
    assert result.symbols_failed == []
    assert result.rows_written == 14  # 7 trading days x 2 symbols
    assert fetch.calls == [(["AAA", "BBB"], date(2024, 1, 2), date(2024, 1, 10))]

    path = settings.data.parquet_dir / "symbol=AAA" / "data.parquet"
    assert path.exists()
    stored = pd.read_parquet(path)
    assert "symbol" not in stored.columns  # symbol lives in the partition dir
    assert list(stored.columns) == ["date", "open", "high", "low", "close", "volume", "factor"]
    assert len(stored) == 7
    assert stored["date"].is_monotonic_increasing


def test_incremental_run_fetches_only_the_gap(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    fetch = RecordingFetch()
    run_etl(settings, symbols=["AAA"], fetch_fn=fetch, end=date(2024, 1, 10))
    result = run_etl(settings, symbols=["AAA"], fetch_fn=fetch, end=date(2024, 1, 17))

    assert len(fetch.calls) == 2
    _, start2, end2 = fetch.calls[1]
    assert start2 == date(2024, 1, 11)  # day after stored max
    assert end2 == date(2024, 1, 17)
    assert result.rows_written == 4  # Jan 11, 12, 16, 17

    stored = pd.read_parquet(settings.data.parquet_dir / "symbol=AAA" / "data.parquet")
    assert len(stored) == 11
    assert not stored["date"].duplicated().any()
    assert stored["date"].is_monotonic_increasing


def test_overlapping_refetch_dedups_keeping_newest(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    run_etl(
        settings,
        symbols=["AAA"],
        fetch_fn=RecordingFetch(base=100.0),
        end=date(2024, 1, 10),
    )

    def overlapping_fetch(symbols: list[str], start: date, end: date) -> pd.DataFrame:
        # Ignore the requested start: re-deliver Jan 10 with a revised close.
        return make_bars(symbols, date(2024, 1, 10), end, base=200.0)

    run_etl(settings, symbols=["AAA"], fetch_fn=overlapping_fetch, end=date(2024, 1, 12))
    stored = pd.read_parquet(settings.data.parquet_dir / "symbol=AAA" / "data.parquet")
    assert not stored["date"].duplicated().any()
    jan10 = stored.loc[stored["date"] == pd.Timestamp("2024-01-10"), "close"].iloc[0]
    assert jan10 == 200.0  # newest row won the dedup


def test_up_to_date_symbol_skips_fetch(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    fetch = RecordingFetch()
    run_etl(settings, symbols=["AAA"], fetch_fn=fetch, end=date(2024, 1, 10))
    result = run_etl(settings, symbols=["AAA"], fetch_fn=fetch, end=date(2024, 1, 10))
    assert len(fetch.calls) == 1  # second run had nothing to fetch
    assert result.symbols_updated == ["AAA"]
    assert result.rows_written == 0


def test_new_symbol_with_no_data_is_failed(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)

    def partial_fetch(symbols: list[str], start: date, end: date) -> pd.DataFrame:
        return make_bars([s for s in symbols if s != "ZZZ"], start, end)

    result = run_etl(
        settings, symbols=["AAA", "ZZZ"], fetch_fn=partial_fetch, end=date(2024, 1, 10)
    )
    assert result.symbols_updated == ["AAA"]
    assert result.symbols_failed == ["ZZZ"]


def test_fetch_exception_marks_symbols_failed(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)

    def broken_fetch(symbols: list[str], start: date, end: date) -> pd.DataFrame:
        raise RuntimeError("provider down")

    result = run_etl(
        settings, symbols=["AAA", "BBB"], fetch_fn=broken_fetch, end=date(2024, 1, 10)
    )
    assert result.symbols_updated == []
    assert sorted(result.symbols_failed) == ["AAA", "BBB"]
    assert result.rows_written == 0


def test_quality_issues_reported(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)

    def jumpy_fetch(symbols: list[str], start: date, end: date) -> pd.DataFrame:
        df = make_bars(symbols, start, end)
        df.loc[df.index[-1], "close"] = df["close"].iloc[-2] * 10  # +900% day
        return df

    result = run_etl(settings, symbols=["AAA"], fetch_fn=jumpy_fetch, end=date(2024, 1, 10))
    kinds = {i.kind for i in result.quality_issues}
    assert "extreme_return" in kinds
    assert all(i.symbol == "AAA" for i in result.quality_issues)


def test_symbols_default_to_universe(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    universe_dir = settings.data.universe_dir
    universe_dir.mkdir(parents=True)
    (universe_dir / "2024-01-01.csv").write_text("symbol\nAAA\n")
    fetch = RecordingFetch()
    result = run_etl(settings, fetch_fn=fetch, end=date(2024, 1, 5))
    assert result.symbols_updated == ["AAA"]
    assert fetch.calls[0][0] == ["AAA"]


def test_parquet_dtypes_are_canonical(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    run_etl(settings, symbols=["AAA"], fetch_fn=RecordingFetch(), end=date(2024, 1, 5))
    stored = pd.read_parquet(settings.data.parquet_dir / "symbol=AAA" / "data.parquet")
    assert stored["date"].dtype == np.dtype("datetime64[ns]")
    for col in ("open", "high", "low", "close", "volume", "factor"):
        assert stored[col].dtype == np.float64


def test_openbb_missing_raises_configerror(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    from finora.core.errors import ConfigError
    from finora.data.etl import fetch_daily_bars_openbb

    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object):
        if name == "openbb":
            raise ImportError("No module named 'openbb'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ConfigError, match="openbb"):
        fetch_daily_bars_openbb(["AAA"], date(2024, 1, 2), date(2024, 1, 5), provider="yfinance")
