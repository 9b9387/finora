"""Pure rebalancing math: signals -> share targets -> diff orders.

No broker or filesystem access here; everything is deterministic and
exhaustively unit-testable. The risk gate (L4) sits between these orders
and any broker.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date

from finora.core.log import get_logger
from finora.core.models import Order, OrderSide, OrderType, Position, Signal
from finora.execution.oms import make_client_order_id

log = get_logger(__name__)


def build_targets(
    signals: list[Signal],
    capital_fractions: dict[str, float],
    equity: float,
    prices: dict[str, float],
) -> dict[str, int]:
    """Convert signals to whole-share targets per instrument.

    Dollar targets are summed across strategies per instrument, then floored
    toward zero into shares. A strategy absent from capital_fractions (or at
    0.0, the paper stage) contributes nothing.
    """
    dollars: dict[str, float] = defaultdict(float)
    for signal in signals:
        fraction = capital_fractions.get(signal.source, 0.0)
        dollars[signal.instrument] += signal.target_weight * fraction * equity
    targets: dict[str, int] = {}
    for instrument, dollar_target in dollars.items():
        price = prices.get(instrument)
        if price is None or not math.isfinite(price) or price <= 0:
            log.warning(
                "skipping target with missing or invalid price",
                instrument=instrument,
                price=price,
            )
            continue
        shares = int(dollar_target / price)  # truncates toward zero
        if shares != 0:
            targets[instrument] = shares
    return targets


def diff_orders(
    current: dict[str, Position],
    targets: dict[str, int],
    prices: dict[str, float],
    min_notional: float,
    as_of: date,
    strategy: str = "rebalance",
) -> list[Order]:
    """Orders that move `current` to `targets`.

    Instruments held but absent from targets are closed (target 0). Diffs
    below min_notional are skipped as dust. Sells come first (frees cash),
    each group sorted by descending notional. client_order_ids are
    deterministic so re-running the same rebalance is idempotent at the OMS.
    """
    instruments = set(targets) | {sym for sym, pos in current.items() if pos.qty != 0}
    sells: list[tuple[float, Order]] = []
    buys: list[tuple[float, Order]] = []
    for instrument in instruments:
        held = current.get(instrument)
        current_qty = held.qty if held is not None else 0.0
        delta = targets.get(instrument, 0) - current_qty
        if delta == 0:
            continue
        price = prices.get(instrument)
        if price is None or not math.isfinite(price) or price <= 0:
            log.warning(
                "skipping diff with missing or invalid price",
                instrument=instrument,
                price=price,
            )
            continue
        notional = abs(delta * price)
        if notional < min_notional:
            log.debug(
                "skipping dust diff",
                instrument=instrument,
                delta=delta,
                notional=notional,
            )
            continue
        side = OrderSide.BUY if delta > 0 else OrderSide.SELL
        order = Order(
            instrument=instrument,
            side=side,
            qty=abs(delta),
            order_type=OrderType.MARKET,
            strategy=strategy,
            client_order_id=make_client_order_id(as_of, strategy, instrument, side),
        )
        (sells if side is OrderSide.SELL else buys).append((notional, order))

    def key(item: tuple[float, Order]) -> tuple[float, str]:
        return (-item[0], item[1].instrument)  # notional desc, symbol tiebreak

    sells.sort(key=key)
    buys.sort(key=key)
    return [order for _, order in sells] + [order for _, order in buys]


def flatten_orders(
    current: dict[str, Position],
    prices: dict[str, float],
    as_of: date,
    strategy: str = "flatten",
) -> list[Order]:
    """Liquidate everything: SELL longs, BUY back shorts. No dust filter —
    this is the circuit-breaker FLATTEN path."""
    orders: list[tuple[float, Order]] = []
    for instrument, position in current.items():
        if position.qty == 0:
            continue
        side = OrderSide.SELL if position.qty > 0 else OrderSide.BUY
        order = Order(
            instrument=instrument,
            side=side,
            qty=abs(position.qty),
            order_type=OrderType.MARKET,
            strategy=strategy,
            client_order_id=make_client_order_id(as_of, strategy, instrument, side),
        )
        notional = abs(position.qty) * prices.get(instrument, 0.0)
        orders.append((notional, order))
    orders.sort(
        key=lambda item: (item[1].side is not OrderSide.SELL, -item[0], item[1].instrument)
    )
    return [order for _, order in orders]
