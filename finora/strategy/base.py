"""Strategy contract: strategies consume a PriceLoader and emit Signals.

Strategies never import finora.data directly; they receive a PriceLoader
callable so backtests and live runs can inject different data sources.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Protocol, runtime_checkable

import pandas as pd

from finora.core.config import Settings, StrategyConfig
from finora.core.errors import ConfigError
from finora.core.models import Signal

# (symbols, start, end) -> canonical daily-bar frame with columns
# [symbol, date, open, high, low, close, volume, factor], sorted by
# (symbol, date). Contract implemented by finora.data.store.MarketStore.get_prices.
PriceLoader = Callable[[list[str] | None, date | None, date | None], pd.DataFrame]


@runtime_checkable
class Strategy(Protocol):
    name: str

    def generate_signals(self, as_of: date) -> list[Signal]: ...


def _registry() -> dict[str, type]:
    # Imported lazily to avoid a base <-> implementation import cycle.
    from finora.strategy.momentum import MomentumStrategy
    from finora.strategy.qlib_strategy import QlibStrategy
    from finora.strategy.rsi import RsiMeanReversionStrategy

    return {"momentum": MomentumStrategy, "qlib": QlibStrategy, "rsi": RsiMeanReversionStrategy}


def build_strategy(cfg: StrategyConfig, settings: Settings, price_loader: PriceLoader) -> Strategy:
    """Instantiate the strategy named by cfg.kind; unknown kinds raise ConfigError."""
    registry = _registry()
    kind = str(cfg.kind)
    cls = registry.get(kind)
    if cls is None:
        raise ConfigError(
            f"unknown strategy kind {kind!r} for strategy '{cfg.name}'; "
            f"expected one of {sorted(registry)}"
        )
    if kind == "momentum":
        return cls(name=cfg.name, params=cfg.params, price_loader=price_loader)
    return cls(name=cfg.name, params=cfg.params, settings=settings)
