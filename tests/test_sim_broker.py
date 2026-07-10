"""SimBroker unit tests: deterministic fills, book-keeping, fault hooks."""
from __future__ import annotations

import pytest

from finora.core.errors import BrokerError
from finora.core.models import Order, OrderSide, OrderStatus, OrderType, Quote, utc_now
from finora.execution.sim_broker import SimBroker


def quote_source_for(prices: dict[str, float]):
    def source(symbols: list[str]) -> dict[str, Quote]:
        return {
            s: Quote(instrument=s, price=prices[s], ts=utc_now()) for s in symbols if s in prices
        }

    return source


@pytest.fixture
def prices() -> dict[str, float]:
    return {"AAPL": 150.0, "MSFT": 300.0}


@pytest.fixture
def broker(prices: dict[str, float]) -> SimBroker:
    return SimBroker(initial_cash=100_000.0, quote_source=quote_source_for(prices))


def buy(qty: float = 10, symbol: str = "AAPL", **kwargs) -> Order:
    return Order(instrument=symbol, side=OrderSide.BUY, qty=qty, **kwargs)


def sell(qty: float = 10, symbol: str = "AAPL", **kwargs) -> Order:
    return Order(instrument=symbol, side=OrderSide.SELL, qty=qty, **kwargs)


def submit(broker: SimBroker, order: Order) -> Order:
    order.broker_order_id = broker.submit_order(order)
    return order


class TestMarketOrders:
    def test_market_buy_fills_fully_and_updates_book(self, broker: SimBroker) -> None:
        order = submit(broker, buy(10))
        status, fills = broker.get_order_status(order)
        assert status is OrderStatus.FILLED
        assert len(fills) == 1
        assert fills[0].qty == 10
        assert fills[0].price == 150.0
        assert broker.get_cash() == pytest.approx(100_000.0 - 1_500.0)
        pos = broker.get_positions()["AAPL"]
        assert pos.qty == 10
        assert pos.avg_cost == 150.0

    def test_broker_does_not_mutate_caller_order(self, broker: SimBroker) -> None:
        order = buy(10)
        broker.submit_order(order)
        assert order.status is OrderStatus.CREATED
        assert order.fills == []

    def test_weighted_average_cost_on_adds(self, prices: dict[str, float]) -> None:
        broker = SimBroker(quote_source=quote_source_for(prices))
        submit(broker, buy(10))
        prices["AAPL"] = 200.0
        submit(broker, buy(10))
        pos = broker.get_positions()["AAPL"]
        assert pos.qty == 20
        assert pos.avg_cost == pytest.approx(175.0)

    def test_sell_adds_cash_and_reduces_position(self, broker: SimBroker) -> None:
        submit(broker, buy(10))
        submit(broker, sell(4))
        assert broker.get_cash() == pytest.approx(100_000.0 - 10 * 150.0 + 4 * 150.0)
        pos = broker.get_positions()["AAPL"]
        assert pos.qty == 6
        assert pos.avg_cost == 150.0  # avg cost unchanged on sells

    def test_sell_all_removes_position(self, broker: SimBroker) -> None:
        submit(broker, buy(10))
        submit(broker, sell(10))
        assert "AAPL" not in broker.get_positions()

    def test_oversell_raises(self, broker: SimBroker) -> None:
        submit(broker, buy(10))
        with pytest.raises(BrokerError, match="oversell"):
            broker.submit_order(sell(11))

    def test_sell_with_no_position_raises(self, broker: SimBroker) -> None:
        with pytest.raises(BrokerError, match="oversell"):
            broker.submit_order(sell(1, symbol="MSFT"))

    def test_order_ids_are_sequential(self, broker: SimBroker) -> None:
        assert broker.submit_order(buy(1)) == "sim-1"
        assert broker.submit_order(buy(1)) == "sim-2"


class TestLimitOrders:
    def test_marketable_buy_limit_fills_at_quote(self, broker: SimBroker) -> None:
        order = submit(broker, buy(10, order_type=OrderType.LIMIT, limit_price=155.0))
        status, fills = broker.get_order_status(order)
        assert status is OrderStatus.FILLED
        assert fills[0].price == 150.0  # fills at quote, not limit

    def test_non_marketable_buy_limit_stays_open(self, broker: SimBroker) -> None:
        order = submit(broker, buy(10, order_type=OrderType.LIMIT, limit_price=140.0))
        status, fills = broker.get_order_status(order)
        assert status is OrderStatus.SUBMITTED
        assert fills == []
        assert broker.get_cash() == 100_000.0

    def test_marketable_sell_limit_fills(self, broker: SimBroker) -> None:
        submit(broker, buy(10))
        order = submit(broker, sell(10, order_type=OrderType.LIMIT, limit_price=145.0))
        status, _ = broker.get_order_status(order)
        assert status is OrderStatus.FILLED

    def test_non_marketable_sell_limit_stays_open(self, broker: SimBroker) -> None:
        submit(broker, buy(10))
        order = submit(broker, sell(10, order_type=OrderType.LIMIT, limit_price=160.0))
        status, _ = broker.get_order_status(order)
        assert status is OrderStatus.SUBMITTED

    def test_cancel_open_limit_order(self, broker: SimBroker) -> None:
        order = submit(broker, buy(10, order_type=OrderType.LIMIT, limit_price=140.0))
        broker.cancel_order(order)
        status, _ = broker.get_order_status(order)
        assert status is OrderStatus.CANCELLED


class TestFaultInjection:
    def test_fail_next_submit_raises_given_exception(self, broker: SimBroker) -> None:
        broker.fail_next_submit(BrokerError("connection lost"))
        with pytest.raises(BrokerError, match="connection lost"):
            broker.submit_order(buy(1))
        # one-shot: next submit succeeds
        assert broker.submit_order(buy(1)).startswith("sim-")

    def test_reject_next_yields_rejected_status(self, broker: SimBroker) -> None:
        broker.reject_next("margin call")
        order = submit(broker, buy(10))
        status, fills = broker.get_order_status(order)
        assert status is OrderStatus.REJECTED
        assert fills == []
        assert broker.get_cash() == 100_000.0
        assert broker.get_positions() == {}

    def test_partial_fill_leaves_partially_filled(self, broker: SimBroker) -> None:
        broker.partial_fill_next(0.5)
        order = submit(broker, buy(10))
        status, fills = broker.get_order_status(order)
        assert status is OrderStatus.PARTIALLY_FILLED
        assert fills[0].qty == 5
        assert broker.get_cash() == pytest.approx(100_000.0 - 5 * 150.0)
        assert broker.get_positions()["AAPL"].qty == 5

    def test_partial_then_cancel(self, broker: SimBroker) -> None:
        broker.partial_fill_next(0.5)
        order = submit(broker, buy(10))
        broker.cancel_order(order)
        status, fills = broker.get_order_status(order)
        assert status is OrderStatus.CANCELLED
        assert fills[0].qty == 5  # fills survive cancellation


class TestQuotesAndErrors:
    def test_get_quotes_without_source_raises(self) -> None:
        broker = SimBroker()
        with pytest.raises(BrokerError, match="quote_source"):
            broker.get_quotes(["AAPL"])

    def test_market_order_without_quote_raises(self, broker: SimBroker) -> None:
        with pytest.raises(BrokerError, match="no quote"):
            broker.submit_order(buy(1, symbol="TSLA"))

    def test_status_of_unknown_order_raises(self, broker: SimBroker) -> None:
        ghost = buy(1)
        ghost.broker_order_id = "sim-999"
        with pytest.raises(BrokerError, match="unknown"):
            broker.get_order_status(ghost)

    def test_context_manager(self, prices: dict[str, float]) -> None:
        with SimBroker(quote_source=quote_source_for(prices)) as broker:
            assert broker.get_cash() == 100_000.0
