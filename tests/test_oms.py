"""OMS tests: journaling, idempotent resubmit, crash ambiguity, retries, polling."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from finora.core.errors import BrokerError
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
from finora.execution.oms import OMS, make_client_order_id
from finora.execution.sim_broker import SimBroker

AS_OF = date(2026, 7, 10)


def quote_source(symbols: list[str]) -> dict[str, Quote]:
    return {s: Quote(instrument=s, price=100.0, ts=utc_now()) for s in symbols}


def new_order(symbol: str = "AAPL", side: OrderSide = OrderSide.BUY, qty: float = 5, **kw) -> Order:
    return Order(
        instrument=symbol,
        side=side,
        qty=qty,
        strategy="test",
        client_order_id=make_client_order_id(AS_OF, "test", symbol, side),
        **kw,
    )


@pytest.fixture
def broker() -> SimBroker:
    return SimBroker(quote_source=quote_source)


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def oms(broker: SimBroker, tmp_path: Path, sleeps: list[float]) -> OMS:
    return OMS(broker, state_dir=tmp_path, sleep_fn=sleeps.append)


def journal_path(oms: OMS) -> Path:
    return oms.state_dir / "orders" / f"{utc_now().date():%Y-%m-%d}.jsonl"


def journal_events(oms: OMS) -> list[dict]:
    return [json.loads(line) for line in journal_path(oms).read_text().splitlines()]


class TestMakeClientOrderId:
    def test_deterministic_across_calls(self) -> None:
        a = make_client_order_id(AS_OF, "rebalance", "AAPL", OrderSide.BUY)
        b = make_client_order_id(AS_OF, "rebalance", "AAPL", OrderSide.BUY)
        assert a == b
        assert a.startswith("20260710-rebalance-AAPL-BUY-")
        suffix = a.rsplit("-", 1)[1]
        assert len(suffix) == 8
        int(suffix, 16)  # valid hex

    def test_distinct_inputs_distinct_ids(self) -> None:
        base = make_client_order_id(AS_OF, "s", "AAPL", OrderSide.BUY)
        assert make_client_order_id(AS_OF, "s", "AAPL", OrderSide.SELL) != base
        assert make_client_order_id(AS_OF, "s", "MSFT", OrderSide.BUY) != base
        assert make_client_order_id(date(2026, 7, 11), "s", "AAPL", OrderSide.BUY) != base


class TestSubmitHappyPath:
    def test_submit_then_poll_fills(self, oms: OMS) -> None:
        order = oms.submit(new_order())
        assert order.status is OrderStatus.SUBMITTED
        assert order.broker_order_id == "sim-1"
        oms.poll(order)
        assert order.status is OrderStatus.FILLED
        assert order.filled_qty == 5
        events = journal_events(oms)
        assert [e["event"] for e in events] == ["intent", "submitted", "terminal"]
        assert events[-1]["status"] == "FILLED"
        assert events[-1]["broker_order_id"] == "sim-1"

    def test_load_today_reconstructs_latest_state(self, oms: OMS) -> None:
        order = oms.submit(new_order())
        oms.poll(order)
        other = oms.submit(new_order("MSFT", order_type=OrderType.LIMIT, limit_price=90.0))
        snapshots = oms.load_today()
        assert set(snapshots) == {order.client_order_id, other.client_order_id}
        assert snapshots[order.client_order_id]["status"] == "FILLED"
        assert snapshots[other.client_order_id]["status"] == "SUBMITTED"

    def test_load_today_missing_journal_is_empty(self, oms: OMS) -> None:
        assert oms.load_today(date(1999, 1, 1)) == {}


class TestResubmitSafety:
    def test_second_process_does_not_resubmit(
        self, broker: SimBroker, tmp_path: Path
    ) -> None:
        oms1 = OMS(broker, state_dir=tmp_path, sleep_fn=lambda s: None)
        first = oms1.submit(new_order())
        oms1.poll(first)
        assert first.status is OrderStatus.FILLED
        calls_before = broker.submit_calls

        # Fresh OMS instance simulates a process restart; identical order.
        oms2 = OMS(broker, state_dir=tmp_path, sleep_fn=lambda s: None)
        replay = oms2.submit(new_order())
        assert broker.submit_calls == calls_before  # broker NOT called again
        assert replay.status is OrderStatus.FILLED
        assert replay.broker_order_id == first.broker_order_id
        assert replay.filled_qty == pytest.approx(5)
        assert replay.avg_fill_price == pytest.approx(100.0)

    def test_crash_ambiguity_requires_manual_review(
        self, broker: SimBroker, tmp_path: Path
    ) -> None:
        order = new_order()
        # Simulate a crash between journaling the intent and the broker ack:
        # an 'intent' line exists but no 'submitted'.
        path = tmp_path / "orders" / f"{utc_now().date():%Y-%m-%d}.jsonl"
        path.parent.mkdir(parents=True)
        intent = {
            "ts": utc_now().isoformat(),
            "event": "intent",
            "client_order_id": order.client_order_id,
            "instrument": order.instrument,
            "side": order.side.value,
            "qty": order.qty,
            "order_type": order.order_type.value,
            "limit_price": None,
            "status": "CREATED",
            "broker_order_id": None,
            "filled_qty": 0.0,
            "avg_fill_price": None,
            "reject_reason": "",
        }
        path.write_text(json.dumps(intent) + "\n")

        oms = OMS(broker, state_dir=tmp_path, sleep_fn=lambda s: None)
        result = oms.submit(order)
        assert broker.submit_calls == 0  # never touched the broker
        assert result.status is OrderStatus.REJECTED
        assert "needs manual review" in result.reject_reason
        events = journal_events(oms)
        assert events[-1]["event"] == "error"


class TestRetries:
    def test_transient_failure_then_success(
        self, oms: OMS, broker: SimBroker, sleeps: list[float]
    ) -> None:
        broker.fail_next_submit(BrokerError("gateway hiccup"))
        order = oms.submit(new_order())
        assert order.status is OrderStatus.SUBMITTED
        assert broker.submit_calls == 2  # first attempt failed, second succeeded
        assert sleeps == [0.5]  # backoff_base * 2**0

    def test_exhausted_retries_reject(self, tmp_path: Path, sleeps: list[float]) -> None:
        class DownBroker(SimBroker):
            def submit_order(self, order: Order) -> str:
                self.submit_calls += 1
                raise BrokerError("gateway down")

        broker = DownBroker(quote_source=quote_source)
        oms = OMS(broker, state_dir=tmp_path, max_retries=3, sleep_fn=sleeps.append)
        order = oms.submit(new_order())
        assert broker.submit_calls == 3
        assert sleeps == [0.5, 1.0]  # exponential backoff, no sleep after last attempt
        assert order.status is OrderStatus.REJECTED
        assert "gateway down" in order.reject_reason
        events = journal_events(oms)
        assert [e["event"] for e in events] == ["intent", "error"]


class TestPolling:
    def test_poll_without_broker_id_is_noop(self, oms: OMS) -> None:
        order = new_order()
        oms.poll(order)
        assert order.status is OrderStatus.CREATED

    def test_partial_fill_transition(self, oms: OMS, broker: SimBroker) -> None:
        broker.partial_fill_next(0.4)
        order = oms.submit(new_order(qty=10))
        oms.poll(order)
        assert order.status is OrderStatus.PARTIALLY_FILLED
        assert order.filled_qty == pytest.approx(4)

    def test_illegal_broker_status_logged_not_raised(self, tmp_path: Path) -> None:
        class WeirdBroker(SimBroker):
            def get_order_status(self, order: Order) -> tuple[OrderStatus, list[Fill]]:
                return OrderStatus.CREATED, []  # SUBMITTED -> CREATED is illegal

        broker = WeirdBroker(quote_source=quote_source)
        oms = OMS(broker, state_dir=tmp_path, sleep_fn=lambda s: None)
        order = oms.submit(new_order())
        assert order.status is OrderStatus.SUBMITTED
        oms.poll(order)  # must not raise
        assert order.status is OrderStatus.SUBMITTED  # unchanged
        events = journal_events(oms)
        assert events[-1]["event"] == "error"

    def test_poll_until_terminal_returns_when_all_done(self, oms: OMS) -> None:
        orders = [oms.submit(new_order()), oms.submit(new_order("MSFT"))]
        done = oms.poll_until_terminal(orders, timeout_s=5.0, clock=lambda: 0.0)
        assert all(o.status is OrderStatus.FILLED for o in done)

    def test_poll_until_terminal_times_out(self, oms: OMS, sleeps: list[float]) -> None:
        ticks = iter(range(100))
        order = oms.submit(new_order(order_type=OrderType.LIMIT, limit_price=90.0))
        done = oms.poll_until_terminal(
            [order], timeout_s=3.0, interval_s=1.0, clock=lambda: float(next(ticks))
        )
        assert done[0].status is OrderStatus.SUBMITTED  # still open, caller decides
        assert sleeps.count(1.0) >= 1


class TestSweep:
    def test_sweep_cancels_open_orders(self, oms: OMS) -> None:
        open_order = oms.submit(new_order(order_type=OrderType.LIMIT, limit_price=90.0))
        filled = oms.submit(new_order("MSFT"))
        oms.poll(filled)
        oms.sweep_open_orders([open_order, filled])
        assert open_order.status is OrderStatus.CANCELLED
        assert filled.status is OrderStatus.FILLED
        events = journal_events(oms)
        cancelled = [e for e in events if e["client_order_id"] == open_order.client_order_id]
        assert cancelled[-1]["event"] == "terminal"
        assert cancelled[-1]["status"] == "CANCELLED"

    def test_sweep_skips_never_submitted(self, oms: OMS, broker: SimBroker) -> None:
        order = new_order()  # CREATED, no broker id
        oms.sweep_open_orders([order])
        assert order.status is OrderStatus.CREATED


def test_positions_visible_after_fill(oms: OMS, broker: SimBroker) -> None:
    order = oms.submit(new_order(qty=3))
    oms.poll(order)
    assert broker.get_positions()["AAPL"] == Position("AAPL", 3, 100.0)
    assert broker.get_cash() == pytest.approx(100_000.0 - 300.0)
