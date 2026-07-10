"""Rebalance math tests: signal -> targets -> diff orders -> flatten."""
from __future__ import annotations

from datetime import date

from finora.core.models import Order, OrderSide, OrderType, Position, Signal
from finora.execution.oms import make_client_order_id
from finora.execution.rebalance import build_targets, diff_orders, flatten_orders

AS_OF = date(2026, 7, 10)


def sig(instrument: str, weight: float, source: str = "momo") -> Signal:
    return Signal(
        instrument=instrument, target_weight=weight, confidence=1.0, as_of=AS_OF, source=source
    )


class TestBuildTargets:
    def test_basic_sizing(self) -> None:
        targets = build_targets(
            [sig("AAPL", 0.5)], {"momo": 1.0}, equity=100_000.0, prices={"AAPL": 100.0}
        )
        assert targets == {"AAPL": 500}

    def test_capital_fraction_scales_dollars(self) -> None:
        targets = build_targets(
            [sig("AAPL", 0.5)], {"momo": 0.4}, equity=100_000.0, prices={"AAPL": 100.0}
        )
        assert targets == {"AAPL": 200}

    def test_multi_strategy_dollars_sum_per_instrument(self) -> None:
        signals = [sig("AAPL", 0.5, "a"), sig("AAPL", 0.25, "b"), sig("MSFT", 0.5, "b")]
        targets = build_targets(
            signals,
            {"a": 0.5, "b": 1.0},
            equity=100_000.0,
            prices={"AAPL": 100.0, "MSFT": 200.0},
        )
        # AAPL: 0.5*0.5*100k + 0.25*1.0*100k = 50k -> 500 sh; MSFT: 50k -> 250 sh
        assert targets == {"AAPL": 500, "MSFT": 250}

    def test_paper_stage_zero_fraction_yields_nothing(self) -> None:
        targets = build_targets(
            [sig("AAPL", 1.0)], {"momo": 0.0}, equity=100_000.0, prices={"AAPL": 100.0}
        )
        assert targets == {}

    def test_unknown_source_defaults_to_zero_fraction(self) -> None:
        targets = build_targets(
            [sig("AAPL", 1.0)], {}, equity=100_000.0, prices={"AAPL": 100.0}
        )
        assert targets == {}

    def test_shares_floor_toward_zero(self) -> None:
        assert build_targets(
            [sig("AAPL", 0.999)], {"momo": 1.0}, equity=1_000.0, prices={"AAPL": 100.0}
        ) == {"AAPL": 9}
        # Negative dollars truncate toward zero (-3.33 -> -3, not floor -4).
        assert build_targets(
            [sig("AAPL", -0.01)], {"momo": 1.0}, equity=100_000.0, prices={"AAPL": 300.0}
        ) == {"AAPL": -3}

    def test_missing_or_invalid_price_skipped(self) -> None:
        signals = [sig("AAPL", 0.5), sig("GHOST", 0.5), sig("FREE", 0.5)]
        targets = build_targets(
            signals, {"momo": 1.0}, equity=100_000.0, prices={"AAPL": 100.0, "FREE": 0.0}
        )
        assert targets == {"AAPL": 500}

    def test_zero_share_targets_dropped(self) -> None:
        targets = build_targets(
            [sig("AAPL", 0.0005)], {"momo": 1.0}, equity=100_000.0, prices={"AAPL": 100.0}
        )
        assert targets == {}  # $50 target < 1 share

    def test_empty_signals(self) -> None:
        assert build_targets([], {"momo": 1.0}, 100_000.0, {}) == {}


class TestDiffOrders:
    PRICES = {"AAPL": 100.0, "MSFT": 50.0, "GOOG": 200.0}

    def test_no_delta_no_order(self) -> None:
        current = {"AAPL": Position("AAPL", 10, 90.0)}
        orders = diff_orders(current, {"AAPL": 10}, self.PRICES, 0.0, AS_OF)
        assert orders == []

    def test_sells_first_then_buys_by_descending_notional(self) -> None:
        current = {"MSFT": Position("MSFT", 5, 40.0), "AAPL": Position("AAPL", 10, 90.0)}
        targets = {"AAPL": 20, "GOOG": 3}  # MSFT absent -> close it
        orders = diff_orders(current, targets, self.PRICES, 0.0, AS_OF)
        assert [(o.instrument, o.side, o.qty) for o in orders] == [
            ("MSFT", OrderSide.SELL, 5),
            ("AAPL", OrderSide.BUY, 10),  # $1000 before
            ("GOOG", OrderSide.BUY, 3),  # $600
        ]

    def test_held_position_absent_from_targets_is_closed(self) -> None:
        current = {"AAPL": Position("AAPL", 10, 90.0)}
        orders = diff_orders(current, {}, self.PRICES, 0.0, AS_OF)
        assert len(orders) == 1
        assert orders[0].side is OrderSide.SELL
        assert orders[0].qty == 10

    def test_dust_below_min_notional_skipped(self) -> None:
        current = {"AAPL": Position("AAPL", 10, 90.0)}
        targets = {"AAPL": 11, "MSFT": 100}  # AAPL delta $100 < $200 dust threshold
        orders = diff_orders(current, targets, self.PRICES, 200.0, AS_OF)
        assert [o.instrument for o in orders] == ["MSFT"]

    def test_missing_price_skipped(self) -> None:
        orders = diff_orders({}, {"GHOST": 10}, self.PRICES, 0.0, AS_OF)
        assert orders == []

    def test_orders_are_market_with_deterministic_client_ids(self) -> None:
        current = {"AAPL": Position("AAPL", 10, 90.0)}
        first = diff_orders(current, {"AAPL": 20}, self.PRICES, 0.0, AS_OF)
        second = diff_orders(current, {"AAPL": 20}, self.PRICES, 0.0, AS_OF)
        assert first[0].order_type is OrderType.MARKET
        assert first[0].strategy == "rebalance"
        assert first[0].client_order_id == second[0].client_order_id
        assert first[0].client_order_id == make_client_order_id(
            AS_OF, "rebalance", "AAPL", OrderSide.BUY
        )

    def test_zero_qty_position_ignored(self) -> None:
        current = {"AAPL": Position("AAPL", 0, 0.0)}
        assert diff_orders(current, {}, self.PRICES, 0.0, AS_OF) == []


class TestFlattenOrders:
    PRICES = {"AAPL": 100.0, "MSFT": 50.0}

    def test_sells_everything_no_dust_filter(self) -> None:
        current = {
            "AAPL": Position("AAPL", 10, 90.0),
            "MSFT": Position("MSFT", 1, 40.0),  # $50 — below any dust threshold
        }
        orders = flatten_orders(current, self.PRICES, AS_OF)
        assert {(o.instrument, o.side, o.qty) for o in orders} == {
            ("AAPL", OrderSide.SELL, 10),
            ("MSFT", OrderSide.SELL, 1),
        }
        assert all(isinstance(o, Order) and o.order_type is OrderType.MARKET for o in orders)
        assert all(o.strategy == "flatten" for o in orders)

    def test_buys_back_shorts(self) -> None:
        current = {"AAPL": Position("AAPL", -5, 100.0)}
        orders = flatten_orders(current, self.PRICES, AS_OF)
        assert [(o.side, o.qty) for o in orders] == [(OrderSide.BUY, 5)]

    def test_sells_before_buys(self) -> None:
        current = {
            "AAPL": Position("AAPL", -5, 100.0),
            "MSFT": Position("MSFT", 10, 40.0),
        }
        orders = flatten_orders(current, self.PRICES, AS_OF)
        assert [o.side for o in orders] == [OrderSide.SELL, OrderSide.BUY]

    def test_missing_price_still_flattens(self) -> None:
        current = {"GHOST": Position("GHOST", 7, 10.0)}
        orders = flatten_orders(current, {}, AS_OF)
        assert len(orders) == 1
        assert orders[0].qty == 7

    def test_deterministic_client_ids(self) -> None:
        current = {"AAPL": Position("AAPL", 10, 90.0)}
        orders = flatten_orders(current, self.PRICES, AS_OF)
        assert orders[0].client_order_id == make_client_order_id(
            AS_OF, "flatten", "AAPL", OrderSide.SELL
        )

    def test_empty_book(self) -> None:
        assert flatten_orders({}, self.PRICES, AS_OF) == []


def test_end_to_end_signal_to_orders() -> None:
    """Signals -> targets -> orders round trip with a mixed book."""
    prices = {"AAPL": 100.0, "MSFT": 200.0, "OLD": 50.0}
    signals = [sig("AAPL", 0.6), sig("MSFT", 0.3)]
    targets = build_targets(signals, {"momo": 0.5}, equity=100_000.0, prices=prices)
    assert targets == {"AAPL": 300, "MSFT": 75}
    current = {"OLD": Position("OLD", 100, 45.0), "AAPL": Position("AAPL", 100, 80.0)}
    orders = diff_orders(current, targets, prices, min_notional=200.0, as_of=AS_OF)
    assert [(o.instrument, o.side, o.qty) for o in orders] == [
        ("OLD", OrderSide.SELL, 100),  # closed: absent from targets
        ("AAPL", OrderSide.BUY, 200),  # $20k buy
        ("MSFT", OrderSide.BUY, 75),  # $15k buy
    ]
