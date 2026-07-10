"""Cross-sectional momentum: rank symbols by trailing total return, hold the top k."""
from __future__ import annotations

import math
from datetime import date

import pandas as pd

from finora.core.log import get_logger
from finora.core.models import Signal
from finora.strategy.base import PriceLoader

log = get_logger(__name__)


class MomentumStrategy:
    """Equal-weight top-k trailing-return momentum on adjusted close.

    params:
        lookback_days: trailing window in trading days (default 126)
        top_k: number of names to hold (default 20)
        universe: optional list[str] restricting the symbols loaded
    """

    def __init__(self, name: str, params: dict, price_loader: PriceLoader) -> None:
        self.name = name
        self.lookback_days = int(params.get("lookback_days", 126))
        self.top_k = int(params.get("top_k", 20))
        self.universe: list[str] | None = params.get("universe")
        self._price_loader = price_loader

    def generate_signals(self, as_of: date) -> list[Signal]:
        bars = self._price_loader(self.universe, None, as_of)
        if bars is None or bars.empty:
            log.warning("momentum_no_data", strategy=self.name, as_of=str(as_of))
            return []
        # Lookahead guard: never read rows after as_of, even if the loader
        # claims to have filtered already.
        bars = bars[bars["date"] <= pd.Timestamp(as_of)]
        if bars.empty:
            log.warning("momentum_no_data_at_or_before", strategy=self.name, as_of=str(as_of))
            return []
        bars = bars.sort_values(["symbol", "date"])
        adj_close = bars["close"] * bars["factor"]
        frame = bars.assign(adj_close=adj_close)

        min_obs = 0.8 * self.lookback_days
        trailing: dict[str, float] = {}
        for symbol, group in frame.groupby("symbol", sort=False):
            window = group["adj_close"].tail(self.lookback_days)
            if len(window) < min_obs:
                continue
            first = float(window.iloc[0])
            last = float(window.iloc[-1])
            if first <= 0 or not math.isfinite(first) or not math.isfinite(last):
                continue
            trailing[str(symbol)] = last / first - 1.0

        if not trailing:
            log.warning(
                "momentum_no_qualifying_symbols",
                strategy=self.name,
                as_of=str(as_of),
                lookback_days=self.lookback_days,
            )
            return []

        ranked = sorted(trailing.items(), key=lambda kv: (-kv[1], kv[0]))
        n = len(ranked)
        weight = 1.0 / self.top_k
        signals = [
            Signal(
                instrument=symbol,
                target_weight=weight,
                confidence=(n - i) / n,  # rank percentile in (0, 1]; best = 1.0
                as_of=as_of,
                source=self.name,
            )
            for i, (symbol, _ret) in enumerate(ranked[: self.top_k])
        ]
        log.info(
            "momentum_signals",
            strategy=self.name,
            as_of=str(as_of),
            n_qualifying=n,
            n_selected=len(signals),
        )
        return signals
