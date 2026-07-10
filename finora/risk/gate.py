"""Independent pre-trade risk gate.

Every order must pass through :class:`RiskGate` before reaching a broker.
The gate is deliberately self-contained (finora.core + stdlib only) so a bug
in strategy or execution code cannot disable it. Rules are evaluated in a
fixed order and the first failure wins; the returned RiskDecision names the
rule so rejections are auditable.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Callable
from datetime import datetime, timedelta

from finora.core.config import RiskConfig
from finora.core.log import get_logger
from finora.core.models import (
    Order,
    OrderSide,
    OrderType,
    PortfolioState,
    Position,
    Quote,
    RiskDecision,
    utc_now,
)

logger = get_logger(__name__)

_RATE_WINDOW_SECONDS = 60.0


class RiskGate:
    """Stateful pre-trade gate; keeps a sliding-window count of approvals."""

    def __init__(self, cfg: RiskConfig, clock: Callable[[], datetime] = utc_now) -> None:
        self._cfg = cfg
        self._clock = clock
        self._approvals: deque[datetime] = deque()

    def check_order(
        self,
        order: Order,
        portfolio: PortfolioState,
        quotes: dict[str, Quote],
        pending_exposure: float = 0.0,
    ) -> RiskDecision:
        """Evaluate one order against all rules.

        pending_exposure is extra gross exposure (USD) from already-approved
        but not-yet-filled orders that the portfolio does not reflect yet.
        """
        decision = self._evaluate(order, portfolio, quotes, pending_exposure)
        if decision.approved:
            self._approvals.append(self._clock())
        else:
            logger.warning(
                "risk gate rejected order",
                instrument=order.instrument,
                side=order.side.value,
                qty=order.qty,
                rule=decision.rule,
                reason=decision.reason,
            )
        return decision

    def check_batch(
        self,
        orders: list[Order],
        portfolio: PortfolioState,
        quotes: dict[str, Quote],
    ) -> list[tuple[Order, RiskDecision]]:
        """Check orders sequentially against a batch-adjusted portfolio.

        Each approved order is applied to a working copy of the portfolio
        (position qty and offsetting cash at the quote price), so later orders
        are judged with the headroom earlier approvals already consumed —
        both per-instrument and in gross exposure.
        """
        working = PortfolioState(
            cash=portfolio.cash,
            positions=dict(portfolio.positions),
            as_of=portfolio.as_of,
        )
        results: list[tuple[Order, RiskDecision]] = []
        for order in orders:
            decision = self.check_order(order, working, quotes)
            results.append((order, decision))
            if decision.approved:
                price = quotes[order.instrument].price
                signed_qty = order.qty if order.side is OrderSide.BUY else -order.qty
                held = working.positions.get(order.instrument)
                cur_qty = held.qty if held is not None else 0.0
                avg_cost = held.avg_cost if held is not None else price
                working.positions[order.instrument] = Position(
                    instrument=order.instrument, qty=cur_qty + signed_qty, avg_cost=avg_cost
                )
                working.cash -= signed_qty * price
        return results

    # -- rule evaluation -------------------------------------------------

    def _evaluate(
        self,
        order: Order,
        portfolio: PortfolioState,
        quotes: dict[str, Quote],
        pending_exposure: float,
    ) -> RiskDecision:
        cfg = self._cfg
        now = self._clock()

        # 1. quote presence and freshness
        quote = quotes.get(order.instrument)
        if quote is None:
            return RiskDecision.reject("quote_missing", f"no quote for {order.instrument}")
        age = (now - quote.ts).total_seconds()
        if age > cfg.quote_max_age_seconds:
            return RiskDecision.reject(
                "quote_stale",
                f"quote for {order.instrument} is {age:.0f}s old "
                f"(max {cfg.quote_max_age_seconds}s)",
            )

        # 2. price collar for LIMIT orders
        if order.order_type is OrderType.LIMIT:
            assert order.limit_price is not None  # enforced by Order.__post_init__
            deviation = abs(order.limit_price - quote.price) / quote.price
            if deviation > cfg.price_collar_pct:
                return RiskDecision.reject(
                    "price_collar",
                    f"limit {order.limit_price:.4f} is {deviation:.2%} from quote "
                    f"{quote.price:.4f} (max {cfg.price_collar_pct:.2%})",
                )

        # 3. per-order notional cap
        notional = order.notional(quote.price)
        if notional > cfg.max_order_notional:
            return RiskDecision.reject(
                "max_order_notional",
                f"order notional {notional:.2f} exceeds cap {cfg.max_order_notional:.2f}",
            )

        # equity, needed by rules 4 and 5
        prices = {sym: q.price for sym, q in quotes.items()}
        for sym, pos in portfolio.positions.items():
            if pos.qty != 0 and sym not in prices:
                return RiskDecision.reject(
                    "equity_unavailable", f"held position {sym} has no quote; cannot mark equity"
                )
        equity = portfolio.equity(prices)
        if equity <= 0:
            return RiskDecision.reject(
                "equity_unavailable", f"equity {equity:.2f} is not positive"
            )

        # 4. post-trade single-name concentration (never blocks risk-reducing orders)
        held = portfolio.positions.get(order.instrument)
        cur_qty = held.qty if held is not None else 0.0
        signed_qty = order.qty if order.side is OrderSide.BUY else -order.qty
        post_qty = cur_qty + signed_qty
        cur_abs_value = abs(cur_qty) * quote.price
        post_abs_value = abs(post_qty) * quote.price
        increases_exposure = post_abs_value > cur_abs_value
        if increases_exposure:
            position_cap = cfg.max_position_pct * equity
            if post_abs_value > position_cap:
                return RiskDecision.reject(
                    "max_position_pct",
                    f"post-trade position {post_abs_value:.2f} in {order.instrument} exceeds "
                    f"{cfg.max_position_pct:.2%} of equity ({position_cap:.2f})",
                )

        # 5. gross exposure cap (only when the order increases gross exposure)
        exposure_delta = post_abs_value - cur_abs_value
        if exposure_delta > 0:
            gross = portfolio.gross_exposure(prices) + pending_exposure + exposure_delta
            if gross / equity > cfg.max_gross_exposure:
                return RiskDecision.reject(
                    "max_gross_exposure",
                    f"post-trade gross exposure {gross:.2f} is {gross / equity:.2%} of equity "
                    f"(max {cfg.max_gross_exposure:.2%})",
                )

        # 6. order rate limit (sliding 60s window of approvals)
        cutoff = now - timedelta(seconds=_RATE_WINDOW_SECONDS)
        while self._approvals and self._approvals[0] <= cutoff:
            self._approvals.popleft()
        if len(self._approvals) >= cfg.max_orders_per_minute:
            return RiskDecision.reject(
                "rate_limit",
                f"{len(self._approvals)} approvals in the last {_RATE_WINDOW_SECONDS:.0f}s "
                f"(max {cfg.max_orders_per_minute}/min)",
            )

        return RiskDecision.ok()
