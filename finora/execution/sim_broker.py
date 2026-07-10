"""Deterministic in-process broker simulator for tests and dry-runs.

Fills MARKET orders immediately at the current quote and LIMIT orders only
when marketable. Keeps its own cash/position book. Never mutates the
caller's Order — state is reported via get_order_status keyed by
broker_order_id. Fault-injection hooks (fail_next_submit, reject_next,
partial_fill_next) let tests exercise OMS retry/reject/partial paths.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from finora.core.errors import BrokerError
from finora.core.log import get_logger
from finora.core.models import (
    Fill,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    Quote,
    utc_now,
)
from finora.execution.broker import Broker, QuoteSource

log = get_logger(__name__)

_QTY_EPS = 1e-9


@dataclass
class _SimOrder:
    """Broker-side record; independent of the caller's Order object."""

    broker_order_id: str
    client_order_id: str
    instrument: str
    side: OrderSide
    qty: float
    order_type: OrderType
    limit_price: float | None
    status: OrderStatus
    fills: list[Fill] = field(default_factory=list)
    reject_reason: str = ""


class SimBroker(Broker):
    def __init__(
        self, initial_cash: float = 100_000.0, quote_source: QuoteSource | None = None
    ) -> None:
        self._cash = initial_cash
        self._quote_source = quote_source
        self._positions: dict[str, Position] = {}
        self._orders: dict[str, _SimOrder] = {}
        self._next_id = 0
        self.submit_calls = 0  # test observability: how many submits reached the broker
        self._fail_next: Exception | None = None
        self._reject_next: str | None = None
        self._partial_fraction: float | None = None

    # -- fault-injection hooks (one-shot) --------------------------------
    def fail_next_submit(self, exc: Exception) -> None:
        self._fail_next = exc

    def reject_next(self, reason: str) -> None:
        self._reject_next = reason

    def partial_fill_next(self, fraction: float) -> None:
        if not 0.0 < fraction < 1.0:
            raise ValueError(f"partial fill fraction must be in (0, 1), got {fraction}")
        self._partial_fraction = fraction

    # -- Broker interface -------------------------------------------------
    def get_positions(self) -> dict[str, Position]:
        return dict(self._positions)

    def get_cash(self) -> float:
        return self._cash

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        if self._quote_source is None:
            raise BrokerError("SimBroker has no quote_source configured")
        return self._quote_source(symbols)

    def submit_order(self, order: Order) -> str:
        self.submit_calls += 1
        if self._fail_next is not None:
            exc = self._fail_next
            self._fail_next = None
            raise exc

        self._next_id += 1
        broker_order_id = f"sim-{self._next_id}"
        rec = _SimOrder(
            broker_order_id=broker_order_id,
            client_order_id=order.client_order_id,
            instrument=order.instrument,
            side=order.side,
            qty=order.qty,
            order_type=order.order_type,
            limit_price=order.limit_price,
            status=OrderStatus.SUBMITTED,
        )

        if self._reject_next is not None:
            rec.status = OrderStatus.REJECTED
            rec.reject_reason = self._reject_next
            self._reject_next = None
            self._orders[broker_order_id] = rec
            return broker_order_id

        if order.side is OrderSide.SELL:
            held = self._positions.get(order.instrument)
            held_qty = held.qty if held is not None else 0.0
            if order.qty > held_qty + _QTY_EPS:
                raise BrokerError(
                    f"oversell: {order.instrument} SELL {order.qty} > held {held_qty} "
                    "(shorts not allowed)"
                )

        price = self._quote_price(order.instrument)
        if order.order_type is OrderType.LIMIT:
            assert order.limit_price is not None
            marketable = (
                price <= order.limit_price
                if order.side is OrderSide.BUY
                else price >= order.limit_price
            )
            if not marketable:
                self._orders[broker_order_id] = rec  # rests open at SUBMITTED
                return broker_order_id

        fill_qty = order.qty
        if self._partial_fraction is not None:
            fill_qty = order.qty * self._partial_fraction
            self._partial_fraction = None
            rec.status = OrderStatus.PARTIALLY_FILLED
        else:
            rec.status = OrderStatus.FILLED
        self._apply_fill(rec, fill_qty, price)
        self._orders[broker_order_id] = rec
        return broker_order_id

    def cancel_order(self, order: Order) -> None:
        rec = self._lookup(order)
        if rec.status in (OrderStatus.SUBMITTED, OrderStatus.PARTIALLY_FILLED):
            rec.status = OrderStatus.CANCELLED
        else:
            log.debug("cancel ignored for terminal order", broker_order_id=rec.broker_order_id)

    def get_order_status(self, order: Order) -> tuple[OrderStatus, list[Fill]]:
        rec = self._lookup(order)
        return rec.status, list(rec.fills)

    def close(self) -> None:
        pass

    # -- internals ---------------------------------------------------------
    def _lookup(self, order: Order) -> _SimOrder:
        rec = self._orders.get(order.broker_order_id or "")
        if rec is None:
            raise BrokerError(f"unknown broker order id: {order.broker_order_id!r}")
        return rec

    def _quote_price(self, instrument: str) -> float:
        quotes = self.get_quotes([instrument])
        quote = quotes.get(instrument)
        if quote is None or quote.price <= 0:
            raise BrokerError(f"no quote available for {instrument}")
        return quote.price

    def _apply_fill(self, rec: _SimOrder, qty: float, price: float) -> None:
        rec.fills.append(
            Fill(client_order_id=rec.client_order_id, qty=qty, price=price, ts=utc_now())
        )
        instrument = rec.instrument
        pos = self._positions.get(instrument)
        if rec.side is OrderSide.BUY:
            self._cash -= qty * price
            if pos is None:
                self._positions[instrument] = Position(instrument, qty, price)
            else:
                new_qty = pos.qty + qty
                new_avg = (pos.qty * pos.avg_cost + qty * price) / new_qty
                self._positions[instrument] = Position(instrument, new_qty, new_avg)
        else:
            self._cash += qty * price
            assert pos is not None  # oversell rejected at submit
            new_qty = pos.qty - qty
            if new_qty <= _QTY_EPS:
                del self._positions[instrument]
            else:
                self._positions[instrument] = Position(instrument, new_qty, pos.avg_cost)
