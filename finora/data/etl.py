"""Daily-bar ETL: OpenBB provider -> per-symbol Parquet partitions.

Incremental by design: each run reads the newest stored date per symbol and
fetches only the missing tail, then merges, dedups, and rewrites that
symbol's single parquet file (see finora.data.store for the on-disk layout).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from finora.core.config import Settings
from finora.core.errors import ConfigError
from finora.core.log import get_logger
from finora.data.store import CANONICAL_COLUMNS, QualityIssue, run_quality_checks

logger = get_logger(__name__)

#: Fetches canonical daily bars for symbols in [start, end] inclusive.
FetchFn = Callable[[list[str], date, date], pd.DataFrame]

_BAR_COLUMNS = ["date", "open", "high", "low", "close", "volume", "factor"]


@dataclass
class EtlResult:
    symbols_updated: list[str] = field(default_factory=list)
    symbols_failed: list[str] = field(default_factory=list)
    rows_written: int = 0
    quality_issues: list[QualityIssue] = field(default_factory=list)


def fetch_daily_bars_openbb(
    symbols: list[str], start: date, end: date, provider: str
) -> pd.DataFrame:
    """Fetch daily bars via OpenBB, one symbol at a time so a single bad
    ticker cannot sink the batch. Returns the canonical schema; symbols that
    failed are simply absent from the result (warnings are logged)."""
    try:
        from openbb import obb
    except ImportError as exc:
        raise ConfigError(
            "openbb is not installed; run `uv sync --extra openbb`"
        ) from exc

    frames: list[pd.DataFrame] = []
    for symbol in symbols:
        try:
            raw = _fetch_one_openbb(obb, symbol, start, end, provider)
        except Exception as exc:
            logger.warning("openbb fetch failed", symbol=symbol, error=str(exc))
            continue
        if raw is None or raw.empty:
            logger.warning("openbb returned no rows", symbol=symbol)
            continue
        frames.append(_normalize_openbb_frame(raw, symbol))
    if not frames:
        from finora.data.store import empty_bars

        return empty_bars()
    out = pd.concat(frames, ignore_index=True)
    return out.sort_values(["symbol", "date"]).reset_index(drop=True)


def _fetch_one_openbb(
    obb: object, symbol: str, start: date, end: date, provider: str
) -> pd.DataFrame | None:
    kwargs = dict(
        symbol=symbol,
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        provider=provider,
    )
    try:
        result = obb.equity.price.historical(  # type: ignore[attr-defined]
            **kwargs, adjustment="splits_and_dividends"
        )
    except Exception:
        # provider does not support the adjustment parameter
        result = obb.equity.price.historical(**kwargs)  # type: ignore[attr-defined]
    return result.to_df()


def _normalize_openbb_frame(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    df = raw.reset_index()
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    if "date" not in df.columns:
        for cand in ("index", "datetime", "timestamp"):
            if cand in df.columns:
                df = df.rename(columns={cand: "date"})
                break
    out = pd.DataFrame()
    out["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None).dt.normalize()
    for col in ("open", "high", "low", "close", "volume"):
        out[col] = pd.to_numeric(df[col], errors="coerce").astype(np.float64)
    adj = next((c for c in ("adj_close", "adjusted_close") if c in df.columns), None)
    if adj is not None:
        adj_close = pd.to_numeric(df[adj], errors="coerce").astype(np.float64)
        with np.errstate(divide="ignore", invalid="ignore"):
            factor = adj_close / out["close"]
        out["factor"] = factor.replace([np.inf, -np.inf], np.nan).fillna(1.0)
    else:
        out["factor"] = 1.0
    out.insert(0, "symbol", symbol)
    out = out.dropna(subset=["close"])
    return out[CANONICAL_COLUMNS].sort_values("date").reset_index(drop=True)


def run_etl(
    settings: Settings,
    symbols: list[str] | None = None,
    fetch_fn: FetchFn | None = None,
    end: date | None = None,
) -> EtlResult:
    """Incrementally update the parquet store and run quality checks."""
    if symbols is None:
        from finora.data.universe import load_universe

        symbols = load_universe(settings)
    if fetch_fn is None:
        provider = settings.data.provider

        def fetch_fn(syms: list[str], s: date, e: date) -> pd.DataFrame:
            return fetch_daily_bars_openbb(syms, s, e, provider=provider)

    end = end or date.today()
    result = EtlResult()
    existing: dict[str, pd.DataFrame] = {}
    groups: dict[date, list[str]] = defaultdict(list)

    for symbol in symbols:
        prior = _read_symbol_parquet(settings.data.parquet_dir, symbol)
        existing[symbol] = prior
        if prior.empty:
            start = settings.data.start_date
        else:
            start = (pd.Timestamp(prior["date"].max()) + timedelta(days=1)).date()
        if start > end:
            result.symbols_updated.append(symbol)  # already current
            continue
        groups[start].append(symbol)

    checked_frames: list[pd.DataFrame] = []
    for start, group in sorted(groups.items()):
        try:
            fetched = fetch_fn(group, start, end)
        except Exception as exc:
            logger.warning(
                "fetch failed for batch", start=str(start), symbols=group, error=str(exc)
            )
            result.symbols_failed.extend(group)
            continue
        by_symbol = (
            dict(tuple(fetched.groupby("symbol", sort=False)))
            if not fetched.empty
            else {}
        )
        for symbol in group:
            new_rows = by_symbol.get(symbol)
            prior = existing[symbol]
            if new_rows is None or new_rows.empty:
                if prior.empty:
                    logger.warning("no data fetched for new symbol", symbol=symbol)
                    result.symbols_failed.append(symbol)
                else:
                    result.symbols_updated.append(symbol)
                    checked_frames.append(_with_symbol(prior, symbol))
                continue
            merged = _merge_bars(prior, new_rows[_BAR_COLUMNS])
            _write_symbol_parquet(settings.data.parquet_dir, symbol, merged)
            result.rows_written += max(len(merged) - len(prior), 0)
            result.symbols_updated.append(symbol)
            checked_frames.append(_with_symbol(merged, symbol))

    if checked_frames:
        checked = pd.concat(checked_frames, ignore_index=True)
        checked = checked.sort_values(["symbol", "date"]).reset_index(drop=True)
        result.quality_issues = run_quality_checks(checked, settings.data.quality)
    logger.info(
        "etl complete",
        updated=len(result.symbols_updated),
        failed=len(result.symbols_failed),
        rows_written=result.rows_written,
        quality_issues=len(result.quality_issues),
    )
    return result


# -- parquet helpers ---------------------------------------------------------


def _symbol_path(parquet_dir: Path, symbol: str) -> Path:
    return parquet_dir / f"symbol={symbol}" / "data.parquet"


def _read_symbol_parquet(parquet_dir: Path, symbol: str) -> pd.DataFrame:
    """Per-symbol bars WITHOUT the symbol column (it lives in the partition
    directory name, not inside the file)."""
    path = _symbol_path(parquet_dir, symbol)
    if not path.exists():
        return _empty_symbol_bars()
    df = pd.read_parquet(path)
    df["date"] = pd.to_datetime(df["date"]).astype("datetime64[ns]")
    return df[_BAR_COLUMNS]


def _write_symbol_parquet(parquet_dir: Path, symbol: str, df: pd.DataFrame) -> None:
    path = _symbol_path(parquet_dir, symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    df[_BAR_COLUMNS].to_parquet(path, index=False)


def _empty_symbol_bars() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.Series(dtype="datetime64[ns]"),
            **{
                c: pd.Series(dtype=np.float64)
                for c in ("open", "high", "low", "close", "volume", "factor")
            },
        }
    )[_BAR_COLUMNS]


def _merge_bars(prior: pd.DataFrame, new_rows: pd.DataFrame) -> pd.DataFrame:
    new_rows = new_rows.copy()
    new_rows["date"] = pd.to_datetime(new_rows["date"]).astype("datetime64[ns]")
    merged = pd.concat([prior, new_rows], ignore_index=True)
    merged = merged.drop_duplicates(subset="date", keep="last")
    merged = merged.sort_values("date").reset_index(drop=True)
    merged["date"] = pd.to_datetime(merged["date"]).astype("datetime64[ns]")
    for col in ("open", "high", "low", "close", "volume", "factor"):
        merged[col] = merged[col].astype(np.float64)
    return merged


def _with_symbol(bars: pd.DataFrame, symbol: str) -> pd.DataFrame:
    out = bars.copy()
    out.insert(0, "symbol", symbol)
    return out[CANONICAL_COLUMNS]
