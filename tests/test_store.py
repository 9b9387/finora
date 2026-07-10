"""Tests for finora.data.store: DuckDB view queries and quality checks."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from finora.core.config import DataConfig, QualityConfig
from finora.data.store import CANONICAL_COLUMNS, MarketStore, empty_bars, run_quality_checks

# Consecutive NYSE trading days (no long gaps).
DAYS = [
    date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5),
    date(2024, 1, 8),
]


def write_partition(
    parquet_dir: Path,
    symbol: str,
    days: list[date],
    closes: list[float] | None = None,
    volumes: list[float] | None = None,
) -> None:
    closes = closes or [100.0 + i for i in range(len(days))]
    volumes = volumes or [1_000.0] * len(days)
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(days),
            "open": [c - 0.5 for c in closes],
            "high": [c + 1.0 for c in closes],
            "low": [c - 1.0 for c in closes],
            "close": closes,
            "volume": volumes,
            "factor": [1.0] * len(days),
        }
    )
    path = parquet_dir / f"symbol={symbol}" / "data.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


@pytest.fixture
def cfg(tmp_path: Path) -> DataConfig:
    return DataConfig(data_dir=tmp_path / "data")


def make_bars_df(symbol: str, days: list[date], closes: list[float],
                 volumes: list[float] | None = None) -> pd.DataFrame:
    volumes = volumes or [1_000.0] * len(days)
    return pd.DataFrame(
        {
            "symbol": symbol,
            "date": pd.to_datetime(days),
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "volume": volumes,
            "factor": 1.0,
        }
    )[CANONICAL_COLUMNS]


class TestMarketStore:
    def test_get_prices_canonical_schema(self, cfg: DataConfig) -> None:
        write_partition(cfg.parquet_dir, "AAA", DAYS)
        write_partition(cfg.parquet_dir, "BBB", DAYS)
        with MarketStore(cfg) as store:
            df = store.get_prices()
        assert list(df.columns) == CANONICAL_COLUMNS
        assert len(df) == 10
        assert df["date"].dtype == np.dtype("datetime64[ns]")
        assert isinstance(df.index, pd.RangeIndex)
        assert list(df["symbol"].unique()) == ["AAA", "BBB"]  # sorted by (symbol, date)
        assert df.groupby("symbol")["date"].apply(lambda s: s.is_monotonic_increasing).all()

    def test_get_prices_filters(self, cfg: DataConfig) -> None:
        write_partition(cfg.parquet_dir, "AAA", DAYS)
        write_partition(cfg.parquet_dir, "BBB", DAYS)
        with MarketStore(cfg) as store:
            only_a = store.get_prices(symbols=["AAA"])
            windowed = store.get_prices(start=date(2024, 1, 4), end=date(2024, 1, 5))
            none_requested = store.get_prices(symbols=[])
        assert set(only_a["symbol"]) == {"AAA"}
        assert len(only_a) == 5
        assert sorted(windowed["date"].dt.date.unique()) == [date(2024, 1, 4), date(2024, 1, 5)]
        assert none_requested.empty

    def test_latest_date(self, cfg: DataConfig) -> None:
        write_partition(cfg.parquet_dir, "AAA", DAYS)
        write_partition(cfg.parquet_dir, "BBB", DAYS[:3])
        with MarketStore(cfg) as store:
            assert store.latest_date() == date(2024, 1, 8)
            assert store.latest_date("BBB") == date(2024, 1, 4)
            assert store.latest_date("ZZZ") is None

    def test_symbols(self, cfg: DataConfig) -> None:
        write_partition(cfg.parquet_dir, "BBB", DAYS)
        write_partition(cfg.parquet_dir, "AAA", DAYS)
        with MarketStore(cfg) as store:
            assert store.symbols() == ["AAA", "BBB"]

    def test_last_closes(self, cfg: DataConfig) -> None:
        write_partition(cfg.parquet_dir, "AAA", DAYS, closes=[10, 11, 12, 13, 14])
        write_partition(cfg.parquet_dir, "BBB", DAYS[:3], closes=[20, 21, 22])
        with MarketStore(cfg) as store:
            latest = store.last_closes()
            as_of = store.last_closes(as_of=date(2024, 1, 3))
        assert latest == {"AAA": 14.0, "BBB": 22.0}
        assert as_of == {"AAA": 11.0, "BBB": 21.0}

    def test_empty_store(self, cfg: DataConfig) -> None:
        with MarketStore(cfg) as store:
            df = store.get_prices()
            assert df.empty
            assert list(df.columns) == CANONICAL_COLUMNS
            assert store.latest_date() is None
            assert store.symbols() == []
            assert store.last_closes() == {}

    def test_sees_data_written_after_open(self, cfg: DataConfig) -> None:
        with MarketStore(cfg) as store:
            assert store.symbols() == []
            write_partition(cfg.parquet_dir, "AAA", DAYS)
            assert store.symbols() == ["AAA"]

    def test_creates_duckdb_file(self, cfg: DataConfig) -> None:
        with MarketStore(cfg):
            pass
        assert cfg.duckdb_path.exists()


class TestQualityChecks:
    def setup_method(self) -> None:
        self.cfg = QualityConfig()  # defaults: 5 days, 0.5 return, $1 min

    def test_clean_data_no_issues(self) -> None:
        df = make_bars_df("AAA", DAYS, [100.0, 101.0, 102.0, 101.5, 103.0])
        assert run_quality_checks(df, self.cfg) == []

    def test_empty_frame_no_issues(self) -> None:
        assert run_quality_checks(empty_bars(), self.cfg) == []

    def test_missing_run_flagged(self) -> None:
        # Jan 2 then Mar 1: ~40 missing NYSE trading days in between.
        df = make_bars_df("AAA", [date(2024, 1, 2), date(2024, 3, 1)], [100.0, 101.0])
        issues = run_quality_checks(df, self.cfg)
        assert [i.kind for i in issues] == ["missing_days"]
        assert issues[0].symbol == "AAA"
        assert issues[0].date == date(2024, 1, 3)  # first missing trading day

    def test_short_gap_not_flagged(self) -> None:
        # One missing trading day (Jan 3) is within tolerance.
        days = [d for d in DAYS if d != date(2024, 1, 3)]
        df = make_bars_df("AAA", days, [100.0, 101.0, 102.0, 103.0])
        assert run_quality_checks(df, self.cfg) == []

    def test_nonpositive_volume_flagged(self) -> None:
        df = make_bars_df(
            "AAA", DAYS, [100.0] * 5, volumes=[1000.0, 0.0, 1000.0, -5.0, 1000.0]
        )
        issues = [i for i in run_quality_checks(df, self.cfg) if i.kind == "nonpositive_volume"]
        assert len(issues) == 2
        assert issues[0].date == date(2024, 1, 3)

    def test_extreme_return_flagged(self) -> None:
        df = make_bars_df("AAA", DAYS, [100.0, 100.0, 500.0, 500.0, 500.0])
        issues = [i for i in run_quality_checks(df, self.cfg) if i.kind == "extreme_return"]
        assert len(issues) == 1
        assert issues[0].date == date(2024, 1, 4)

    def test_low_price_flagged(self) -> None:
        df = make_bars_df("AAA", DAYS, [100.0, 100.0, 0.5, 100.0, 100.0])
        kinds = [i.kind for i in run_quality_checks(df, self.cfg)]
        assert "low_price" in kinds
        # the 100 -> 0.5 -> 100 swings also trip the return check
        assert "extreme_return" in kinds

    def test_issues_are_per_symbol(self) -> None:
        good = make_bars_df("GOOD", DAYS, [100.0, 101.0, 102.0, 103.0, 104.0])
        bad = make_bars_df("BAD", DAYS, [100.0] * 5, volumes=[0.0] + [1000.0] * 4)
        df = pd.concat([good, bad], ignore_index=True)
        issues = run_quality_checks(df, self.cfg)
        assert {i.symbol for i in issues} == {"BAD"}
