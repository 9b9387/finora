"""Shared base for single-instrument technical strategies (RSI, MA cross,
Bollinger, ...): one symbol, indicator warm-up on full history, a
target-weight series replayed by rule, and at most one trade per day.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from finora.core.log import get_logger
from finora.core.models import Signal
from finora.strategy.base import PriceLoader

log = get_logger(__name__)


def adj_close_series(bars: pd.DataFrame, symbol: str) -> pd.Series:
    """Date-indexed adjusted close (close * factor) for one symbol."""
    frame = bars[bars["symbol"] == symbol].sort_values("date")
    adj = (frame["close"].astype(float) * frame["factor"].astype(float))
    adj.index = pd.DatetimeIndex(frame["date"])
    return adj[~adj.index.duplicated(keep="last")].replace([np.inf, -np.inf], np.nan).dropna()


class TechnicalStrategy:
    """Base class. Subclasses define DEFAULT_PARAMS (must include "symbol"),
    validate/bind params in _configure, and implement window_weights."""

    DEFAULT_PARAMS: dict = {"symbol": "SPY"}

    def __init__(self, name: str, params: dict, price_loader: PriceLoader) -> None:
        self.name = name
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        self.symbol = str(merged["symbol"]).upper()
        self._price_loader = price_loader
        self._configure(merged)

    def _configure(self, params: dict) -> None:  # pragma: no cover - trivial default
        pass

    def window_weights(
        self, adj_close: pd.Series, mask: pd.Series
    ) -> tuple[pd.Series, list[dict]]:
        """(target weight per masked date, trade log). Indicators may look at
        the full adj_close history; the position replay starts flat at the
        first masked date unless the rule is stateless (e.g. MA cross)."""
        raise NotImplementedError

    def weight_series(self, as_of: date | None = None) -> tuple[pd.Series, list[dict]]:
        """Full-history replay from all data at or before as_of."""
        bars = self._price_loader([self.symbol], None, as_of)
        if bars is None or bars.empty:
            log.warning("technical_no_data", strategy=self.name, symbol=self.symbol)
            return pd.Series(dtype=float, name="weight"), []
        if as_of is not None:
            bars = bars[bars["date"] <= pd.Timestamp(as_of)]  # lookahead guard
        adj = adj_close_series(bars, self.symbol)
        if adj.empty:
            return pd.Series(dtype=float, name="weight"), []
        mask = pd.Series(True, index=adj.index)
        return self.window_weights(adj, mask)

    def generate_signals(self, as_of: date) -> list[Signal]:
        weights, trades = self.weight_series(as_of)
        if weights.empty:
            return []
        target = float(weights.iloc[-1])
        log.info(
            "technical_signal",
            strategy=self.name,
            symbol=self.symbol,
            as_of=str(as_of),
            target_weight=target,
            n_trades_replayed=len(trades),
        )
        return [
            Signal(
                instrument=self.symbol,
                target_weight=target,
                confidence=1.0,
                as_of=as_of,
                source=self.name,
            )
        ]
