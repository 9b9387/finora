"""Local market-data store: Parquet files fronted by a DuckDB view.

Layout on disk (written by finora.data.etl):

    <parquet_dir>/symbol=<SYM>/data.parquet

Hive-style partitioning: the ``symbol`` column is NOT stored inside the
parquet files — it is derived from the partition directory name by DuckDB's
``read_parquet(..., hive_partitioning=true)``. Each file holds the columns
[date, open, high, low, close, volume, factor] for one symbol.

All query methods return the canonical daily-bar schema:
[symbol, date, open, high, low, close, volume, factor], sorted by
(symbol, date) with an integer RangeIndex.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import duckdb
import numpy as np
import pandas as pd

from finora.core.config import DataConfig, QualityConfig
from finora.core.log import get_logger

logger = get_logger(__name__)

CANONICAL_COLUMNS: list[str] = [
    "symbol",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "factor",
]

_FLOAT_COLUMNS = ["open", "high", "low", "close", "volume", "factor"]


def empty_bars() -> pd.DataFrame:
    """An empty DataFrame in the canonical daily-bar schema."""
    df = pd.DataFrame(
        {
            "symbol": pd.Series(dtype=str),
            "date": pd.Series(dtype="datetime64[ns]"),
            **{c: pd.Series(dtype=np.float64) for c in _FLOAT_COLUMNS},
        }
    )
    return df[CANONICAL_COLUMNS]


@dataclass
class QualityIssue:
    symbol: str
    kind: str
    detail: str
    date: date | None = None


class MarketStore:
    """DuckDB-backed read access over the per-symbol parquet partitions."""

    def __init__(self, cfg: DataConfig) -> None:
        self.cfg = cfg
        cfg.duckdb_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: duckdb.DuckDBPyConnection | None = duckdb.connect(str(cfg.duckdb_path))
        self._refresh_view()

    # -- lifecycle ---------------------------------------------------------

    def __enter__(self) -> MarketStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            raise RuntimeError("MarketStore is closed")
        return self._conn

    # -- view management ---------------------------------------------------

    def _has_data(self) -> bool:
        return any(self.cfg.parquet_dir.glob("symbol=*/*.parquet"))

    def _refresh_view(self) -> None:
        """(Re)create the daily_bars view; falls back to an empty typed view
        when no parquet files exist yet, so queries never blow up on a fresh
        checkout."""
        if self._has_data():
            pattern = str(self.cfg.parquet_dir / "symbol=*" / "*.parquet").replace("'", "''")
            self.conn.execute(
                "CREATE OR REPLACE VIEW daily_bars AS "
                "SELECT CAST(symbol AS VARCHAR) AS symbol, "
                "CAST(date AS TIMESTAMP) AS date, "
                "open, high, low, close, volume, factor "
                f"FROM read_parquet('{pattern}', hive_partitioning=true)"
            )
        else:
            self.conn.execute(
                "CREATE OR REPLACE VIEW daily_bars AS "
                "SELECT CAST(NULL AS VARCHAR) AS symbol, CAST(NULL AS TIMESTAMP) AS date, "
                "CAST(NULL AS DOUBLE) AS open, CAST(NULL AS DOUBLE) AS high, "
                "CAST(NULL AS DOUBLE) AS low, CAST(NULL AS DOUBLE) AS close, "
                "CAST(NULL AS DOUBLE) AS volume, CAST(NULL AS DOUBLE) AS factor "
                "WHERE false"
            )

    # -- queries -----------------------------------------------------------

    def get_prices(
        self,
        symbols: list[str] | None = None,
        start: date | None = None,
        end: date | None = None,
    ) -> pd.DataFrame:
        self._refresh_view()
        clauses: list[str] = []
        params: list[object] = []
        if symbols is not None:
            if not symbols:
                return empty_bars()
            placeholders = ", ".join("?" for _ in symbols)
            clauses.append(f"symbol IN ({placeholders})")
            params.extend(symbols)
        if start is not None:
            clauses.append("CAST(date AS DATE) >= ?")
            params.append(start)
        if end is not None:
            clauses.append("CAST(date AS DATE) <= ?")
            params.append(end)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            "SELECT symbol, date, open, high, low, close, volume, factor "
            f"FROM daily_bars{where} ORDER BY symbol, date"
        )
        df = self.conn.execute(sql, params).fetchdf()
        if df.empty:
            return empty_bars()
        df["date"] = pd.to_datetime(df["date"]).astype("datetime64[ns]")
        for col in _FLOAT_COLUMNS:
            df[col] = df[col].astype(np.float64)
        return df[CANONICAL_COLUMNS].reset_index(drop=True)

    def latest_date(self, symbol: str | None = None) -> date | None:
        self._refresh_view()
        if symbol is None:
            row = self.conn.execute("SELECT max(date) FROM daily_bars").fetchone()
        else:
            row = self.conn.execute(
                "SELECT max(date) FROM daily_bars WHERE symbol = ?", [symbol]
            ).fetchone()
        if row is None or row[0] is None:
            return None
        return pd.Timestamp(row[0]).date()

    def symbols(self) -> list[str]:
        self._refresh_view()
        rows = self.conn.execute(
            "SELECT DISTINCT symbol FROM daily_bars WHERE symbol IS NOT NULL ORDER BY symbol"
        ).fetchall()
        return [r[0] for r in rows]

    def last_closes(self, as_of: date | None = None) -> dict[str, float]:
        """Most recent close per symbol at/before as_of (all history if None)."""
        self._refresh_view()
        where = ""
        params: list[object] = []
        if as_of is not None:
            where = "WHERE CAST(date AS DATE) <= ?"
            params.append(as_of)
        sql = (
            "SELECT symbol, close FROM ("
            "  SELECT symbol, close, "
            "         ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY date DESC) AS rn "
            f"  FROM daily_bars {where}"
            ") WHERE rn = 1"
        )
        rows = self.conn.execute(sql, params).fetchall()
        return {r[0]: float(r[1]) for r in rows}


# -- quality checks ---------------------------------------------------------


def run_quality_checks(df: pd.DataFrame, cfg: QualityConfig) -> list[QualityIssue]:
    """Flag data problems per symbol: long gaps vs the NYSE calendar,
    non-positive volume, implausible daily returns, and penny prices."""
    issues: list[QualityIssue] = []
    if df.empty:
        return issues
    for symbol, grp in df.groupby("symbol", sort=True):
        grp = grp.sort_values("date").reset_index(drop=True)
        issues.extend(_check_missing_runs(str(symbol), grp, cfg))
        for _, row in grp[grp["volume"] <= 0].iterrows():
            issues.append(
                QualityIssue(
                    symbol=str(symbol),
                    kind="nonpositive_volume",
                    detail=f"volume={row['volume']}",
                    date=pd.Timestamp(row["date"]).date(),
                )
            )
        returns = grp["close"].pct_change()
        for idx in returns.index[returns.abs() > cfg.max_abs_daily_return]:
            issues.append(
                QualityIssue(
                    symbol=str(symbol),
                    kind="extreme_return",
                    detail=f"|daily return| {returns[idx]:.4f} > {cfg.max_abs_daily_return}",
                    date=pd.Timestamp(grp.loc[idx, "date"]).date(),
                )
            )
        for _, row in grp[grp["close"] < cfg.min_price].iterrows():
            issues.append(
                QualityIssue(
                    symbol=str(symbol),
                    kind="low_price",
                    detail=f"close={row['close']} < min_price={cfg.min_price}",
                    date=pd.Timestamp(row["date"]).date(),
                )
            )
    for issue in issues:
        logger.warning(
            "quality issue", symbol=issue.symbol, kind=issue.kind,
            detail=issue.detail, date=str(issue.date),
        )
    return issues


def _nyse_trading_days(start: date, end: date) -> pd.DatetimeIndex:
    import pandas_market_calendars as mcal

    cal = mcal.get_calendar("XNYS")
    schedule = cal.schedule(start_date=start, end_date=end)
    return pd.DatetimeIndex(schedule.index).normalize().astype("datetime64[ns]")


def _check_missing_runs(
    symbol: str, grp: pd.DataFrame, cfg: QualityConfig
) -> list[QualityIssue]:
    first = pd.Timestamp(grp["date"].iloc[0]).date()
    last = pd.Timestamp(grp["date"].iloc[-1]).date()
    trading_days = _nyse_trading_days(first, last)
    present = set(pd.DatetimeIndex(grp["date"]).normalize())
    issues: list[QualityIssue] = []
    run_start: pd.Timestamp | None = None
    run_len = 0

    def flush() -> None:
        if run_start is not None and run_len > cfg.max_missing_run_days:
            issues.append(
                QualityIssue(
                    symbol=symbol,
                    kind="missing_days",
                    detail=(
                        f"{run_len} consecutive missing NYSE trading days "
                        f"(> {cfg.max_missing_run_days})"
                    ),
                    date=run_start.date(),
                )
            )

    for day in trading_days:
        if day in present:
            flush()
            run_start, run_len = None, 0
        else:
            if run_start is None:
                run_start = day
            run_len += 1
    flush()
    return issues
