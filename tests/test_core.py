"""Core contracts: model invariants, order state machine, config loading."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from finora.core.config import CircuitBreakerConfig, Settings, StrategyStage
from finora.core.errors import ConfigError
from finora.core.models import (
    Fill,
    InvalidTransitionError,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    PortfolioState,
    Position,
    Signal,
    TERMINAL_STATUSES,
    VALID_TRANSITIONS,
    utc_now,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestSignal:
    def test_valid_signal(self):
        s = Signal("AAPL", 0.05, 0.8, date(2026, 7, 9), "momentum_baseline")
        assert s.instrument == "AAPL"

    @pytest.mark.parametrize("weight", [1.5, -1.5])
    def test_weight_out_of_range(self, weight):
        with pytest.raises(ValueError):
            Signal("AAPL", weight, 0.5, date(2026, 7, 9), "s")

    def test_confidence_out_of_range(self):
        with pytest.raises(ValueError):
            Signal("AAPL", 0.1, 1.1, date(2026, 7, 9), "s")

    def test_empty_instrument_rejected(self):
        with pytest.raises(ValueError):
            Signal("", 0.1, 0.5, date(2026, 7, 9), "s")


class TestOrderStateMachine:
    def test_happy_path(self):
        o = Order("AAPL", OrderSide.BUY, 10)
        o.transition(OrderStatus.SUBMITTED)
        o.transition(OrderStatus.PARTIALLY_FILLED)
        o.transition(OrderStatus.FILLED)
        assert o.is_terminal

    def test_terminal_states_have_no_exits(self):
        for status in TERMINAL_STATUSES:
            assert VALID_TRANSITIONS[status] == frozenset()

    def test_cannot_fill_before_submit(self):
        o = Order("AAPL", OrderSide.BUY, 10)
        with pytest.raises(InvalidTransitionError):
            o.transition(OrderStatus.FILLED)

    def test_cannot_leave_filled(self):
        o = Order("AAPL", OrderSide.BUY, 10, status=OrderStatus.FILLED)
        with pytest.raises(InvalidTransitionError):
            o.transition(OrderStatus.CANCELLED)

    def test_qty_must_be_positive(self):
        with pytest.raises(ValueError):
            Order("AAPL", OrderSide.SELL, 0)

    def test_limit_requires_price(self):
        with pytest.raises(ValueError):
            Order("AAPL", OrderSide.BUY, 10, order_type=OrderType.LIMIT)

    def test_fill_accounting(self):
        o = Order("AAPL", OrderSide.BUY, 10, status=OrderStatus.SUBMITTED)
        o.fills.append(Fill("id", 4, 100.0, utc_now()))
        o.fills.append(Fill("id", 6, 110.0, utc_now()))
        assert o.filled_qty == 10
        assert o.avg_fill_price == pytest.approx(106.0)

    def test_notional_uses_limit_price_for_limit_orders(self):
        o = Order("AAPL", OrderSide.BUY, 10, order_type=OrderType.LIMIT, limit_price=50.0)
        assert o.notional(reference_price=100.0) == 500.0


class TestPortfolioState:
    def test_equity_and_exposure(self):
        ps = PortfolioState(
            cash=1000.0,
            positions={"AAPL": Position("AAPL", 10, 90.0), "MSFT": Position("MSFT", -2, 300.0)},
        )
        prices = {"AAPL": 100.0, "MSFT": 310.0}
        assert ps.equity(prices) == pytest.approx(1000 + 1000 - 620)
        assert ps.gross_exposure(prices) == pytest.approx(1000 + 620)

    def test_missing_price_raises(self):
        ps = PortfolioState(cash=0.0, positions={"AAPL": Position("AAPL", 10, 90.0)})
        with pytest.raises(KeyError):
            ps.equity({})


class TestConfig:
    def test_load_repo_config(self):
        settings = Settings.load(REPO_ROOT / "config")
        assert settings.broker.kind == "sim"
        assert settings.risk.max_gross_exposure == 1.0
        assert settings.risk.circuit_breaker.flatten_at == -0.08
        names = [s.name for s in settings.strategies]
        assert "momentum_baseline" in names
        assert all(s.stage is StrategyStage.PAPER for s in settings.strategies)

    def test_breaker_tiers_must_descend(self):
        with pytest.raises(ValueError):
            CircuitBreakerConfig(reduce_at=-0.05, halt_new_at=-0.03, flatten_at=-0.08)

    def test_unknown_keys_rejected(self, tmp_path):
        (tmp_path / "risk.yaml").write_text("max_order_notional: 100\nmax_order_notionl: 5\n")
        with pytest.raises(ConfigError):
            Settings.load(tmp_path)

    def test_missing_dir_raises(self, tmp_path):
        with pytest.raises(ConfigError):
            Settings.load(tmp_path / "nope")

    def test_defaults_from_empty_dir(self, tmp_path):
        settings = Settings.load(tmp_path)
        assert settings.broker.kind == "sim"
        assert settings.strategies == []
