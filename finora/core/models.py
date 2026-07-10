"""Core domain models shared by every Finora layer.

These are the stable contracts between layers: strategies emit Signals,
the rebalancer turns them into Orders, the risk gate returns RiskDecisions,
brokers report Positions and Fills. Keep this module dependency-free
(stdlib only) so every layer can import it.
"""
from __future__ import annotations

import enum
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class OrderSide(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, enum.Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(str, enum.Enum):
    CREATED = "CREATED"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


TERMINAL_STATUSES: frozenset[OrderStatus] = frozenset(
    {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.EXPIRED}
)

VALID_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.CREATED: frozenset(
        {OrderStatus.SUBMITTED, OrderStatus.REJECTED, OrderStatus.CANCELLED}
    ),
    OrderStatus.SUBMITTED: frozenset(
        {
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
        }
    ),
    OrderStatus.PARTIALLY_FILLED: frozenset(
        {
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.EXPIRED,
        }
    ),
    OrderStatus.FILLED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
    OrderStatus.REJECTED: frozenset(),
    OrderStatus.EXPIRED: frozenset(),
}


@dataclass(frozen=True)
class Signal:
    """A strategy's desired exposure for one instrument as of a date.

    target_weight is a fraction of strategy-allocated equity in [-1, 1]
    (long-only strategies use [0, 1]). Signals are intents, not orders:
    quarantine scaling, rebalancing, and the risk gate all sit between
    a Signal and any order reaching a broker.
    """

    instrument: str
    target_weight: float
    confidence: float
    as_of: date
    source: str

    def __post_init__(self) -> None:
        if not self.instrument:
            raise ValueError("Signal.instrument must be non-empty")
        if not -1.0 <= self.target_weight <= 1.0:
            raise ValueError(f"Signal.target_weight {self.target_weight} outside [-1, 1]")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"Signal.confidence {self.confidence} outside [0, 1]")
        if not self.source:
            raise ValueError("Signal.source must be non-empty")


@dataclass(frozen=True)
class Fill:
    client_order_id: str
    qty: float
    price: float
    ts: datetime


@dataclass
class Order:
    instrument: str
    side: OrderSide
    qty: float
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    client_order_id: str = ""
    strategy: str = ""
    status: OrderStatus = OrderStatus.CREATED
    broker_order_id: str | None = None
    fills: list[Fill] = field(default_factory=list)
    reject_reason: str = ""
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.qty <= 0 or not math.isfinite(self.qty):
            raise ValueError(f"Order.qty must be a positive finite number, got {self.qty}")
        if self.order_type is OrderType.LIMIT and (
            self.limit_price is None or self.limit_price <= 0
        ):
            raise ValueError("LIMIT order requires a positive limit_price")

    @property
    def filled_qty(self) -> float:
        return sum(f.qty for f in self.fills)

    @property
    def avg_fill_price(self) -> float | None:
        filled = self.filled_qty
        if filled == 0:
            return None
        return sum(f.qty * f.price for f in self.fills) / filled

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def notional(self, reference_price: float) -> float:
        """Order value at a reference price (limit price for LIMIT orders)."""
        price = self.limit_price if self.order_type is OrderType.LIMIT else reference_price
        assert price is not None
        return self.qty * price

    def transition(self, new_status: OrderStatus) -> None:
        """Move to new_status, raising on transitions the state machine forbids."""
        if new_status not in VALID_TRANSITIONS[self.status]:
            raise InvalidTransitionError(
                f"order {self.client_order_id or '<no id>'}: "
                f"illegal transition {self.status.value} -> {new_status.value}"
            )
        self.status = new_status
        self.updated_at = utc_now()


class InvalidTransitionError(Exception):
    """An order-status transition the state machine forbids."""


@dataclass(frozen=True)
class Position:
    instrument: str
    qty: float
    avg_cost: float

    def market_value(self, price: float) -> float:
        return self.qty * price


@dataclass(frozen=True)
class Quote:
    instrument: str
    price: float
    ts: datetime


@dataclass
class PortfolioState:
    """Internal view of the account; reconciled against the broker's view."""

    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    as_of: datetime = field(default_factory=utc_now)

    def equity(self, prices: dict[str, float]) -> float:
        """Cash plus marked positions. Raises KeyError on a missing price —
        never silently value a position at a guessed price."""
        return self.cash + sum(
            p.market_value(prices[sym]) for sym, p in self.positions.items() if p.qty != 0
        )

    def gross_exposure(self, prices: dict[str, float]) -> float:
        return sum(
            abs(p.market_value(prices[sym])) for sym, p in self.positions.items() if p.qty != 0
        )


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    rule: str = ""
    reason: str = ""

    @classmethod
    def ok(cls) -> "RiskDecision":
        return cls(approved=True)

    @classmethod
    def reject(cls, rule: str, reason: str) -> "RiskDecision":
        return cls(approved=False, rule=rule, reason=reason)
