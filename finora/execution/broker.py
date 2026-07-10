"""Broker abstraction for L5 execution.

Every broker (in-process simulator, Futu OpenD gateway) implements the same
narrow interface so the OMS and rebalancer never care which one is live.
Brokers report order state via get_order_status keyed by broker_order_id;
they never mutate the caller's Order objects — the OMS owns order state.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

from finora.core.config import Settings
from finora.core.errors import ConfigError
from finora.core.models import Fill, Order, OrderStatus, Position, Quote

QuoteSource = Callable[[list[str]], dict[str, Quote]]


class Broker(ABC):
    """Abstract broker: account state, quotes, and order lifecycle."""

    @abstractmethod
    def get_positions(self) -> dict[str, Position]: ...

    @abstractmethod
    def get_cash(self) -> float: ...

    @abstractmethod
    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]: ...

    @abstractmethod
    def submit_order(self, order: Order) -> str:
        """Submit; returns broker_order_id. Raises BrokerError on failure."""

    @abstractmethod
    def cancel_order(self, order: Order) -> None: ...

    @abstractmethod
    def get_order_status(self, order: Order) -> tuple[OrderStatus, list[Fill]]: ...

    @abstractmethod
    def close(self) -> None: ...

    def __enter__(self) -> "Broker":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()


def build_broker(settings: Settings, quote_source: QuoteSource | None = None) -> Broker:
    """Instantiate the broker named by settings.broker.kind."""
    kind = settings.broker.kind
    if kind == "sim":
        from finora.execution.sim_broker import SimBroker

        return SimBroker(quote_source=quote_source)
    if kind == "futu":
        from finora.execution.futu_broker import FutuBroker

        return FutuBroker(settings.broker.futu)
    raise ConfigError(f"unknown broker kind: {kind!r}")
