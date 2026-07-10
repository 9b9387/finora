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
        for d in days:
            # Price is a function of the DATE, not the fetch window, so an
            # overlap refetch reproduces identical values (no false drift).
            px = base + JAN_2024_TRADING_DAYS.index(d) * 0.5
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


def test_incremental_run_fetches_gap_plus_overlap(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    fetch = RecordingFetch()
    run_etl(settings, symbols=["AAA"], fetch_fn=fetch, end=date(2024, 1, 10))
    result = run_etl(settings, symbols=["AAA"], fetch_fn=fetch, end=date(2024, 1, 17))

    assert len(fetch.calls) == 2
    _, start2, end2 = fetch.calls[1]
    # Stored: Jan 2,3,4,5,8,9,10 -> 5-row overlap starts at Jan 4.
    assert start2 == date(2024, 1, 4)
    assert end2 == date(2024, 1, 17)
    assert result.rows_written == 4  # Jan 11, 12, 16, 17 (overlap rows replaced, not added)
    assert result.symbols_rebuilt == []

    stored = pd.read_parquet(settings.data.parquet_dir / "symbol=AAA" / "data.parquet")
    assert len(stored) == 11
    assert not stored["date"].duplicated().any()
    assert stored["date"].is_monotonic_increasing


def test_small_revision_within_tolerance_replaces_tail_quietly(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    run_etl(settings, symbols=["AAA"], fetch_fn=RecordingFetch(), end=date(2024, 1, 10))

    def revised_fetch(symbols: list[str], start: date, end: date) -> pd.DataFrame:
        df = make_bars(symbols, start, end)
        # Provider revises Jan 10's close by 0.05% — below the 0.1% tolerance.
        mask = df["date"] == pd.Timestamp("2024-01-10")
        df.loc[mask, "close"] = df.loc[mask, "close"] * 1.0005
        return df

    result = run_etl(settings, symbols=["AAA"], fetch_fn=revised_fetch, end=date(2024, 1, 12))
    assert result.symbols_rebuilt == []  # not drift, just a tail revision

    stored = pd.read_parquet(settings.data.parquet_dir / "symbol=AAA" / "data.parquet")
    assert not stored["date"].duplicated().any()
    jan10 = stored.loc[stored["date"] == pd.Timestamp("2024-01-10"), "close"].iloc[0]
    assert jan10 == pytest.approx(103.0 * 1.0005)  # refetched row won the dedup


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


def test_adjustment_drift_triggers_history_rebuild(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    run_etl(settings, symbols=["AAA"], fetch_fn=RecordingFetch(), end=date(2024, 1, 10))

    class SplitFetch(RecordingFetch):
        """Provider rescaled history: every close now half (2:1 split)."""

        def __call__(self, symbols: list[str], start: date, end: date) -> pd.DataFrame:
            df = super().__call__(symbols, start, end)
            for col in ("open", "high", "low", "close"):
                df[col] = df[col] / 2.0
            return df

    split_fetch = SplitFetch()
    result = run_etl(settings, symbols=["AAA"], fetch_fn=split_fetch, end=date(2024, 1, 17))

    assert result.symbols_rebuilt == ["AAA"]
    assert result.symbols_updated == ["AAA"]
    # Overlap fetch first, then the full-history rebuild from start_date.
    assert split_fetch.calls == [
        (["AAA"], date(2024, 1, 4), date(2024, 1, 17)),
        (["AAA"], date(2024, 1, 2), date(2024, 1, 17)),
    ]
    stored = pd.read_parquet(settings.data.parquet_dir / "symbol=AAA" / "data.parquet")
    assert len(stored) == 11  # full history, on the new adjustment basis
    jan2 = stored.loc[stored["date"] == pd.Timestamp("2024-01-02"), "close"].iloc[0]
    assert jan2 == pytest.approx(50.0)  # old history rescaled, no discontinuity


def test_rebuild_failure_keeps_prior_data(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    run_etl(settings, symbols=["AAA"], fetch_fn=RecordingFetch(), end=date(2024, 1, 10))
    before = pd.read_parquet(settings.data.parquet_dir / "symbol=AAA" / "data.parquet")

    calls: list[date] = []

    def flaky_split_fetch(symbols: list[str], start: date, end: date) -> pd.DataFrame:
        calls.append(start)
        if len(calls) > 1:  # the rebuild fetch dies
            raise RuntimeError("provider down")
        df = make_bars(symbols, start, end)
        df["close"] = df["close"] / 2.0  # drift on the overlap
        return df

    result = run_etl(settings, symbols=["AAA"], fetch_fn=flaky_split_fetch, end=date(2024, 1, 17))
    assert result.symbols_failed == ["AAA"]
    assert result.symbols_rebuilt == []

    after = pd.read_parquet(settings.data.parquet_dir / "symbol=AAA" / "data.parquet")
    pd.testing.assert_frame_equal(before, after)  # store untouched


def test_full_refresh_replaces_history(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    run_etl(settings, symbols=["AAA"], fetch_fn=RecordingFetch(base=100.0), end=date(2024, 1, 10))

    fetch = RecordingFetch(base=200.0)
    result = run_etl(
        settings, symbols=["AAA"], fetch_fn=fetch, end=date(2024, 1, 12), full_refresh=True
    )
    assert fetch.calls == [(["AAA"], date(2024, 1, 2), date(2024, 1, 12))]
    assert result.symbols_rebuilt == ["AAA"]

    stored = pd.read_parquet(settings.data.parquet_dir / "symbol=AAA" / "data.parquet")
    assert len(stored) == 9
    assert stored["close"].iloc[0] == pytest.approx(200.0)  # new basis everywhere


def test_default_end_caps_at_last_completed_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import finora.data.etl as etl_module

    settings = make_settings(tmp_path)
    monkeypatch.setattr(etl_module, "last_completed_session", lambda: date(2024, 1, 10))
    fetch = RecordingFetch()
    run_etl(settings, symbols=["AAA"], fetch_fn=fetch)  # no explicit end
    assert fetch.calls == [(["AAA"], date(2024, 1, 2), date(2024, 1, 10))]


def test_last_completed_session_guards_partial_bars() -> None:
    from finora.data.etl import last_completed_session

    # Wed 2024-06-12, 15:00 ET (19:00 UTC): today's bar isn't final yet.
    assert last_completed_session(pd.Timestamp("2024-06-12 19:00", tz="UTC")) == date(2024, 6, 11)
    # Same day, 16:30 ET: session closed, today's bar is final.
    assert last_completed_session(pd.Timestamp("2024-06-12 20:30", tz="UTC")) == date(2024, 6, 12)
    # Saturday: Friday is the last completed session.
    assert last_completed_session(pd.Timestamp("2024-06-15 12:00", tz="UTC")) == date(2024, 6, 14)
    # Half day (Jul 3 closes 13:00 ET): 13:30 ET is already after the close.
    assert last_completed_session(pd.Timestamp("2024-07-03 17:30", tz="UTC")) == date(2024, 7, 3)
    # Naive timestamps are treated as UTC.
    assert last_completed_session(pd.Timestamp("2024-06-12 19:00")) == date(2024, 6, 11)


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
