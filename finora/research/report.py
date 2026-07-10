"""Data-driven research report templates, strictly isolated from trading.

This module may read market data and write markdown files — nothing else.
It never touches order flow, risk state, or strategies, and Finora never
trades on its output.
"""
from __future__ import annotations

import math
from datetime import date
from pathlib import Path
from typing import Any

from finora.core.config import Settings
from finora.core.log import get_logger

logger = get_logger(__name__)

_TRADING_DAYS_PER_YEAR = 252
_WINDOWS = (("1m", 21), ("3m", 63), ("6m", 126))

_DISCLAIMER = (
    "> **Disclaimer**: research aid only — Finora never trades on this output."
)


def generate_research_report(ticker: str, settings: Settings) -> Path:
    """Write a markdown research report for one ticker and return its path."""
    ticker = ticker.upper().strip()
    sections: list[str] = [f"# Research Report: {ticker}", ""]

    sections += _performance_section(ticker, settings)
    sections += [
        "## Analyst Notes",
        "",
        "### Thesis",
        "",
        "_(fill in)_",
        "",
        "### Catalysts",
        "",
        "_(fill in)_",
        "",
        "### Risks",
        "",
        "_(fill in)_",
        "",
    ]
    sections += ["---", _DISCLAIMER, ""]

    out_dir = settings.ops.reports_dir / "research"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{ticker}-{date.today():%Y-%m-%d}.md"
    path.write_text("\n".join(sections), encoding="utf-8")
    logger.info("research report written", ticker=ticker, path=str(path))
    return path


def _performance_section(ticker: str, settings: Settings) -> list[str]:
    lines = ["## Performance", ""]
    try:
        bars = _load_bars(ticker, settings)
    except Exception as exc:
        logger.warning("market store unavailable for research report", error=str(exc))
        bars = None
    if bars is None or len(bars) == 0:
        lines += [f"_No local market data available for {ticker}._", ""]
        return lines

    adj = [float(c) * float(f) for c, f in zip(bars["close"], bars["factor"])]
    last_close = float(bars["close"].iloc[-1])
    last_date = bars["date"].iloc[-1]
    lines += [
        f"- last close: {last_close:.2f} (as of {last_date:%Y-%m-%d})",
    ]
    for label, window in _WINDOWS:
        ret = _total_return(adj, window)
        vol = _annualized_vol(adj, window)
        ret_s = f"{ret:+.2%}" if ret is not None else "n/a"
        vol_s = f"{vol:.2%}" if vol is not None else "n/a"
        lines.append(f"- {label}: total return {ret_s}, annualized vol {vol_s}")
    mdd = _max_drawdown(adj, 126)
    lines.append(f"- 6m max drawdown: {mdd:.2%}" if mdd is not None else "- 6m max drawdown: n/a")
    lines.append("")
    return lines


def _load_bars(ticker: str, settings: Settings) -> Any | None:
    """Daily bars for one ticker from the local market store, or None.

    Coded against the canonical bar schema; tolerant of the store module
    being absent (fresh checkout) or exposing a slightly different reader
    method name.
    """
    from finora.data.store import MarketStore  # lazy: sibling module

    store = MarketStore(settings.data)
    df = None
    for name in ("read_bars", "load_bars", "get_bars", "bars", "read"):
        method = getattr(store, name, None)
        if method is None or not callable(method):
            continue
        try:
            df = method([ticker])
            break
        except TypeError:
            try:
                df = method(symbols=[ticker])
                break
            except Exception:
                continue
        except Exception:
            continue
    if df is None or len(df) == 0:
        return None
    df = df[df["symbol"] == ticker].sort_values("date").reset_index(drop=True)
    return df if len(df) else None


def _total_return(adj: list[float], window: int) -> float | None:
    if len(adj) < window + 1 or adj[-1 - window] == 0:
        return None
    return adj[-1] / adj[-1 - window] - 1.0


def _annualized_vol(adj: list[float], window: int) -> float | None:
    if len(adj) < window + 1:
        return None
    tail = adj[-(window + 1):]
    rets = [b / a - 1.0 for a, b in zip(tail, tail[1:]) if a != 0]
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(_TRADING_DAYS_PER_YEAR)


def _max_drawdown(adj: list[float], window: int) -> float | None:
    tail = adj[-window:]
    if len(tail) < 2:
        return None
    peak = tail[0]
    worst = 0.0
    for price in tail:
        peak = max(peak, price)
        if peak > 0:
            worst = min(worst, price / peak - 1.0)
    return worst
