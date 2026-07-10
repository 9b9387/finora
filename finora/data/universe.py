"""Trading-universe management: fetch S&P 500 constituents, snapshot them
per-date for point-in-time history, and load the active symbol list."""
from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import pandas as pd
import requests

from finora.core.config import Settings
from finora.core.errors import DataError
from finora.core.log import get_logger

logger = get_logger(__name__)

SP500_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def normalize_symbol(symbol: str) -> str:
    """Normalize an index-style ticker for yfinance-style feeds
    (class shares use '-' instead of '.', e.g. BRK.B -> BRK-B)."""
    return symbol.strip().upper().replace(".", "-")


def fetch_sp500_symbols() -> list[str]:
    """Scrape current S&P 500 constituents from Wikipedia."""
    try:
        resp = requests.get(
            SP500_WIKI_URL, headers={"User-Agent": _USER_AGENT}, timeout=30
        )
        resp.raise_for_status()
        tables = pd.read_html(io.StringIO(resp.text))
    except Exception as exc:
        raise DataError(f"failed to fetch S&P 500 constituents: {exc}") from exc
    for table in tables:
        if "Symbol" in table.columns:
            symbols = sorted({normalize_symbol(str(s)) for s in table["Symbol"] if str(s)})
            if symbols:
                logger.info("fetched S&P 500 constituents", count=len(symbols))
                return symbols
    raise DataError("no table with a 'Symbol' column found on the S&P 500 Wikipedia page")


def snapshot_universe(settings: Settings, symbols: list[str] | None = None) -> Path:
    """Write today's constituent list to universe_dir/YYYY-MM-DD.csv.

    Resolution order: explicit arg > settings.universe.symbols > live fetch.
    Snapshots are kept per-date to preserve point-in-time membership history.
    """
    if symbols is None:
        symbols = list(settings.universe.symbols) or fetch_sp500_symbols()
    if not symbols:
        raise DataError("refusing to snapshot an empty universe")
    universe_dir = settings.data.universe_dir
    universe_dir.mkdir(parents=True, exist_ok=True)
    path = universe_dir / f"{date.today():%Y-%m-%d}.csv"
    pd.DataFrame({"symbol": symbols}).to_csv(path, index=False)
    logger.info("universe snapshot written", path=str(path), count=len(symbols))
    return path


def load_universe(settings: Settings) -> list[str]:
    """Active symbol list: the static config override if set, otherwise the
    newest snapshot in universe_dir."""
    if settings.universe.symbols:
        return [normalize_symbol(s) for s in settings.universe.symbols]
    universe_dir = settings.data.universe_dir
    snapshots = sorted(universe_dir.glob("*.csv")) if universe_dir.is_dir() else []
    if not snapshots:
        raise DataError(
            f"no universe snapshot found in {universe_dir}; run `finora universe` first"
        )
    latest = snapshots[-1]  # YYYY-MM-DD names sort chronologically
    df = pd.read_csv(latest)
    if "symbol" not in df.columns:
        raise DataError(f"universe snapshot {latest} has no 'symbol' column")
    symbols = [normalize_symbol(str(s)) for s in df["symbol"].dropna()]
    if not symbols:
        raise DataError(f"universe snapshot {latest} is empty; run `finora universe`")
    return symbols
