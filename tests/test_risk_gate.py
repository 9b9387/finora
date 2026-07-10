"""Exhaustive tests for the pre-trade risk gate: every rule gets approve + reject cases."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from finora.core.config import RiskConfig
from finora.core.models import (
    Order,
    OrderSide,
    OrderType,
    PortfolioState,
    Position,
    Quote,
)
from finora.risk.gate import RiskGate

T0 = datetime(2026, 7, 10, 15, 0, 0, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self, start: datetime = T0) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def make_cfg(**overrides) -> RiskConfig:
    base = dict(
        max_order_notional=10_000.0,
        max_position_pct=0.05,
        price_collar_pct=0.05,
        max_orders_per_minute=30,
        max_gross_exposure=1.0,
        quote_max_age_seconds=900,
    )
    base.update(overrides)
    return RiskConfig(**base)


def quote(symbol: str, price: float, ts: datetime = T0) -> Quote:
    return Quote(instrument=symbol, price=price, ts=ts)


def buy(symbol: str, qty: float, **kw) -> Order:
    return Order(instrument=symbol, side=OrderSide.BUY, qty=qty, **kw)


def sell(symbol: str, qty: float, **kw) -> Order:
    return Order(instrument=symbol, side=OrderSide.SELL, qty=qty, **kw)


def rich_portfolio(cash: float = 1_000_000.0) -> PortfolioState:
    return PortfolioState(cash=cash)


# ---------------------------------------------------------------- quotes


def test_missing_quote_rejected() -> None:
    gate = RiskGate(make_cfg(), clock=FakeClock())
    decision = gate.check_order(buy("MSFT", 10), rich_portfolio(), {"AAPL": quote("AAPL", 100.0)})
    assert not decision.approved
    assert decision.rule == "quote_missing"


def test_stale_quote_rejected_fresh_passes() -> None:
    clock = FakeClock()
    gate = RiskGate(make_cfg(quote_max_age_seconds=900), clock=clock)
    stale = {"AAPL": quote("AAPL", 100.0, ts=T0 - timedelta(seconds=901))}
    decision = gate.check_order(buy("AAPL", 10), rich_portfolio(), stale)
    assert not decision.approved
    assert decision.rule == "quote_stale"

    # exactly at the age limit is not stale (strict >)
    boundary = {"AAPL": quote("AAPL", 100.0, ts=T0 - timedelta(seconds=900))}
    assert gate.check_order(buy("AAPL", 10), rich_portfolio(), boundary).approved


# ---------------------------------------------------------------- price collar


def test_price_collar_rejects_far_limit() -> None:
    gate = RiskGate(make_cfg(price_collar_pct=0.05), clock=FakeClock())
    order = buy("AAPL", 10, order_type=OrderType.LIMIT, limit_price=106.0)
    decision = gate.check_order(order, rich_portfolio(), {"AAPL": quote("AAPL", 100.0)})
    assert not decision.approved
    assert decision.rule == "price_collar"


def test_price_collar_passes_near_limit() -> None:
    gate = RiskGate(make_cfg(price_collar_pct=0.05), clock=FakeClock())
    order = buy("AAPL", 10, order_type=OrderType.LIMIT, limit_price=104.0)
    decision = gate.check_order(order, rich_portfolio(), {"AAPL": quote("AAPL", 100.0)})
    assert decision.approved


def test_price_collar_not_applied_to_market_orders() -> None:
    gate = RiskGate(make_cfg(price_collar_pct=0.05), clock=FakeClock())
    decision = gate.check_order(buy("AAPL", 10), rich_portfolio(), {"AAPL": quote("AAPL", 100.0)})
    assert decision.approved


# ---------------------------------------------------------------- notional


def test_notional_exactly_at_cap_passes() -> None:
    gate = RiskGate(make_cfg(max_order_notional=10_000.0), clock=FakeClock())
    decision = gate.check_order(
        buy("AAPL", 100), rich_portfolio(), {"AAPL": quote("AAPL", 100.0)}
    )
    assert decision.approved  # 100 * 100.0 == 10_000 exactly


def test_notional_above_cap_rejected() -> None:
    gate = RiskGate(make_cfg(max_order_notional=10_000.0), clock=FakeClock())
    decision = gate.check_order(
        buy("AAPL", 100.01), rich_portfolio(), {"AAPL": quote("AAPL", 100.0)}
    )
    assert not decision.approved
    assert decision.rule == "max_order_notional"


def test_notional_uses_limit_price_for_limit_orders() -> None:
    gate = RiskGate(make_cfg(max_order_notional=10_000.0, price_collar_pct=0.05), clock=FakeClock())
    # 99 shares at limit 102 = 10_098 > cap even though 99 * quote 100 = 9_900
    order = buy("AAPL", 99, order_type=OrderType.LIMIT, limit_price=102.0)
    decision = gate.check_order(order, rich_portfolio(), {"AAPL": quote("AAPL", 100.0)})
    assert not decision.approved
    assert decision.rule == "max_order_notional"


# ---------------------------------------------------------------- position pct


def test_buy_exceeding_position_pct_rejected() -> None:
    gate = RiskGate(make_cfg(max_position_pct=0.05), clock=FakeClock())
    portfolio = PortfolioState(cash=100_000.0)
    # 60 * 100 = 6_000 > 5% of 100_000 = 5_000
    decision = gate.check_order(buy("AAPL", 60), portfolio, {"AAPL": quote("AAPL", 100.0)})
    assert not decision.approved
    assert decision.rule == "max_position_pct"


def test_buy_at_exact_position_cap_passes() -> None:
    gate = RiskGate(make_cfg(max_position_pct=0.05), clock=FakeClock())
    portfolio = PortfolioState(cash=100_000.0)
    decision = gate.check_order(buy("AAPL", 50), portfolio, {"AAPL": quote("AAPL", 100.0)})
    assert decision.approved  # 5_000 == cap exactly


def test_sell_reducing_oversized_position_approved() -> None:
    """Risk-reducing orders must never be blocked by the position limit."""
    gate = RiskGate(make_cfg(max_position_pct=0.05), clock=FakeClock())
    portfolio = PortfolioState(
        cash=92_000.0,
        positions={"AAPL": Position("AAPL", 80.0, 100.0)},  # 8_000 = 8% of 100_000 equity
    )
    decision = gate.check_order(sell("AAPL", 10), portfolio, {"AAPL": quote("AAPL", 100.0)})
    assert decision.approved  # post-trade 7_000 still over 5% cap, but reducing


def test_buy_adding_to_existing_position_over_cap_rejected() -> None:
    gate = RiskGate(make_cfg(max_position_pct=0.05), clock=FakeClock())
    portfolio = PortfolioState(
        cash=96_000.0,
        positions={"AAPL": Position("AAPL", 40.0, 100.0)},  # 4_000 of 100_000 equity
    )
    decision = gate.check_order(buy("AAPL", 20), portfolio, {"AAPL": quote("AAPL", 100.0)})
    assert not decision.approved  # post-trade 6_000 > 5_000 cap
    assert decision.rule == "max_position_pct"


def test_equity_unavailable_when_position_lacks_quote() -> None:
    gate = RiskGate(make_cfg(), clock=FakeClock())
    portfolio = PortfolioState(
        cash=100_000.0, positions={"XYZ": Position("XYZ", 10.0, 50.0)}
    )
    decision = gate.check_order(buy("AAPL", 1), portfolio, {"AAPL": quote("AAPL", 100.0)})
    assert not decision.approved
    assert decision.rule == "equity_unavailable"


def test_equity_unavailable_when_equity_not_positive() -> None:
    gate = RiskGate(make_cfg(), clock=FakeClock())
    portfolio = PortfolioState(cash=-500.0)
    decision = gate.check_order(buy("AAPL", 1), portfolio, {"AAPL": quote("AAPL", 100.0)})
    assert not decision.approved
    assert decision.rule == "equity_unavailable"


# ---------------------------------------------------------------- gross exposure


def test_batch_third_buy_rejected_only_because_first_two_approved() -> None:
    cfg = make_cfg(max_position_pct=0.5, max_gross_exposure=1.0)
    gate = RiskGate(cfg, clock=FakeClock())
    portfolio = PortfolioState(cash=25_000.0)
    quotes = {
        "AAPL": quote("AAPL", 100.0),
        "MSFT": quote("MSFT", 100.0),
        "GOOG": quote("GOOG", 100.0),
    }
    orders = [buy("AAPL", 100), buy("MSFT", 100), buy("GOOG", 100)]  # 10k each, equity 25k
    results = gate.check_batch(orders, portfolio, quotes)
    assert results[0][1].approved
    assert results[1][1].approved
    assert not results[2][1].approved
    assert results[2][1].rule == "max_gross_exposure"

    # sanity: the third order alone would have passed
    fresh_gate = RiskGate(cfg, clock=FakeClock())
    assert fresh_gate.check_order(buy("GOOG", 100), portfolio, quotes).approved


def test_batch_second_buy_same_name_consumes_position_headroom() -> None:
    cfg = make_cfg(max_position_pct=0.05)
    gate = RiskGate(cfg, clock=FakeClock())
    portfolio = PortfolioState(cash=100_000.0)
    quotes = {"AAPL": quote("AAPL", 100.0)}
    results = gate.check_batch([buy("AAPL", 30), buy("AAPL", 30)], portfolio, quotes)
    assert results[0][1].approved
    assert not results[1][1].approved  # combined 6_000 > 5_000 cap
    assert results[1][1].rule == "max_position_pct"


def test_sell_passes_gross_check_when_portfolio_at_cap() -> None:
    gate = RiskGate(make_cfg(max_position_pct=1.0, max_gross_exposure=1.0), clock=FakeClock())
    portfolio = PortfolioState(
        cash=0.0, positions={"AAPL": Position("AAPL", 1000.0, 100.0)}
    )  # equity 100k, gross 100k -> exactly at cap
    quotes = {"AAPL": quote("AAPL", 100.0)}
    decision = gate.check_order(sell("AAPL", 100), portfolio, quotes)
    assert decision.approved


def test_buy_rejected_when_portfolio_at_gross_cap() -> None:
    gate = RiskGate(make_cfg(max_gross_exposure=1.0), clock=FakeClock())
    portfolio = PortfolioState(
        cash=0.0, positions={"AAPL": Position("AAPL", 1000.0, 100.0)}
    )
    quotes = {"AAPL": quote("AAPL", 100.0), "MSFT": quote("MSFT", 100.0)}
    decision = gate.check_order(buy("MSFT", 10), portfolio, quotes)
    assert not decision.approved
    assert decision.rule == "max_gross_exposure"


def test_pending_exposure_counts_against_gross_cap() -> None:
    gate = RiskGate(make_cfg(max_position_pct=1.0, max_gross_exposure=1.0), clock=FakeClock())
    portfolio = PortfolioState(cash=100_000.0)
    quotes = {"AAPL": quote("AAPL", 100.0)}
    order = buy("AAPL", 100)  # 10k
    assert gate.check_order(order, portfolio, quotes, pending_exposure=0.0).approved
    decision = gate.check_order(order, portfolio, quotes, pending_exposure=95_000.0)
    assert not decision.approved
    assert decision.rule == "max_gross_exposure"


# ---------------------------------------------------------------- rate limit


def test_rate_limit_trips_at_n_plus_one_and_recovers() -> None:
    clock = FakeClock()
    gate = RiskGate(
        make_cfg(max_orders_per_minute=3, quote_max_age_seconds=100_000), clock=clock
    )
    portfolio = rich_portfolio()
    quotes = {"AAPL": quote("AAPL", 100.0)}

    for _ in range(3):
        assert gate.check_order(buy("AAPL", 1), portfolio, quotes).approved
        clock.advance(1)

    decision = gate.check_order(buy("AAPL", 1), portfolio, quotes)
    assert not decision.approved
    assert decision.rule == "rate_limit"

    # rejected orders do not count as approvals; after the window slides, we recover
    clock.advance(61)
    assert gate.check_order(buy("AAPL", 1), portfolio, quotes).approved


def test_rejections_do_not_consume_rate_limit() -> None:
    clock = FakeClock()
    gate = RiskGate(
        make_cfg(max_orders_per_minute=1, quote_max_age_seconds=100_000), clock=clock
    )
    portfolio = rich_portfolio()
    quotes = {"AAPL": quote("AAPL", 100.0)}
    # a rejected order (missing quote) must not occupy the single approval slot
    assert not gate.check_order(buy("MSFT", 1), portfolio, quotes).approved
    assert gate.check_order(buy("AAPL", 1), portfolio, quotes).approved


# ---------------------------------------------------------------- ordering


def test_first_failing_rule_wins() -> None:
    # order violates both collar and notional; collar is evaluated first
    gate = RiskGate(make_cfg(price_collar_pct=0.05, max_order_notional=10_000.0), clock=FakeClock())
    order = buy("AAPL", 500, order_type=OrderType.LIMIT, limit_price=110.0)
    decision = gate.check_order(order, rich_portfolio(), {"AAPL": quote("AAPL", 100.0)})
    assert decision.rule == "price_collar"


@pytest.mark.parametrize("approved", [True, False])
def test_decision_shape(approved: bool) -> None:
    gate = RiskGate(make_cfg(), clock=FakeClock())
    quotes = {"AAPL": quote("AAPL", 100.0)} if approved else {}
    decision = gate.check_order(buy("AAPL", 1), rich_portfolio(), quotes)
    assert decision.approved is approved
    if approved:
        assert decision.rule == "" and decision.reason == ""
    else:
        assert decision.rule and decision.reason
