"""Reconciliation of the internal book against the broker account snapshot."""
from __future__ import annotations

from finora.core.config import RiskConfig
from finora.core.models import PortfolioState, Position
from finora.risk.reconcile import reconcile_positions

CFG = RiskConfig(reconcile_qty_tolerance=0.0, reconcile_cash_tolerance=5.0)


def book(cash: float, **positions: float) -> PortfolioState:
    return PortfolioState(
        cash=cash,
        positions={sym: Position(sym, qty, 10.0) for sym, qty in positions.items()},
    )


def broker(**positions: float) -> dict[str, Position]:
    return {sym: Position(sym, qty, 10.0) for sym, qty in positions.items()}


def test_perfect_match_ok() -> None:
    result = reconcile_positions(
        book(10_000.0, AAPL=100.0, MSFT=50.0),
        broker(AAPL=100.0, MSFT=50.0),
        broker_cash=10_000.0,
        cfg=CFG,
    )
    assert result.ok
    assert result.diffs == []
    assert "OK" in result.summary()


def test_qty_off_by_one_share_fails() -> None:
    result = reconcile_positions(
        book(10_000.0, AAPL=100.0), broker(AAPL=101.0), broker_cash=10_000.0, cfg=CFG
    )
    assert not result.ok
    assert len(result.diffs) == 1
    diff = result.diffs[0]
    assert diff.kind == "qty"
    assert diff.instrument == "AAPL"
    assert diff.internal == 100.0
    assert diff.broker == 101.0
    assert "AAPL" in result.summary()


def test_qty_within_tolerance_ok() -> None:
    cfg = RiskConfig(reconcile_qty_tolerance=2.0, reconcile_cash_tolerance=5.0)
    result = reconcile_positions(
        book(0.0, AAPL=100.0), broker(AAPL=101.0), broker_cash=0.0, cfg=cfg
    )
    assert result.ok


def test_cash_within_tolerance_ok() -> None:
    result = reconcile_positions(book(10_000.0), broker(), broker_cash=10_004.99, cfg=CFG)
    assert result.ok


def test_cash_beyond_tolerance_fails() -> None:
    result = reconcile_positions(book(10_000.0), broker(), broker_cash=10_005.01, cfg=CFG)
    assert not result.ok
    assert len(result.diffs) == 1
    diff = result.diffs[0]
    assert diff.kind == "cash"
    assert diff.internal == 10_000.0
    assert diff.broker == 10_005.01


def test_position_only_at_broker_flagged_missing_internal() -> None:
    result = reconcile_positions(book(0.0), broker(TSLA=5.0), broker_cash=0.0, cfg=CFG)
    assert not result.ok
    diff = result.diffs[0]
    assert diff.kind == "missing_internal"
    assert diff.instrument == "TSLA"
    assert diff.internal == 0.0
    assert diff.broker == 5.0


def test_position_only_internal_flagged_missing_broker() -> None:
    result = reconcile_positions(book(0.0, NVDA=7.0), broker(), broker_cash=0.0, cfg=CFG)
    assert not result.ok
    diff = result.diffs[0]
    assert diff.kind == "missing_broker"
    assert diff.instrument == "NVDA"
    assert diff.internal == 7.0
    assert diff.broker == 0.0


def test_zero_qty_phantoms_ignored_both_sides() -> None:
    result = reconcile_positions(
        book(0.0, AAPL=100.0, GHOST=0.0),
        broker(AAPL=100.0, SPECTRE=0.0),
        broker_cash=0.0,
        cfg=CFG,
    )
    assert result.ok
    assert result.diffs == []


def test_multiple_diffs_reported_together() -> None:
    result = reconcile_positions(
        book(1_000.0, AAPL=100.0, NVDA=7.0),
        broker(AAPL=99.0, TSLA=5.0),
        broker_cash=2_000.0,
        cfg=CFG,
    )
    assert not result.ok
    kinds = sorted(d.kind for d in result.diffs)
    assert kinds == ["cash", "missing_broker", "missing_internal", "qty"]
    summary = result.summary()
    assert "FAILED" in summary
    for token in ("AAPL", "NVDA", "TSLA", "CASH"):
        assert token in summary
