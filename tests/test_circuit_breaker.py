"""Circuit breaker: tier mapping, escalation-only intraday, sticky FLATTEN, persistence."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from finora.core.config import CircuitBreakerConfig
from finora.risk.circuit_breaker import BreakerState, CircuitBreaker

D1 = date(2026, 7, 9)
D2 = date(2026, 7, 10)
D3 = date(2026, 7, 13)

CFG = CircuitBreakerConfig(reduce_at=-0.03, halt_new_at=-0.05, flatten_at=-0.08)


def make_breaker(tmp_path: Path, name: str = "breaker.json") -> CircuitBreaker:
    return CircuitBreaker(CFG, tmp_path / name)


@pytest.mark.parametrize(
    ("pnl", "expected"),
    [
        (0.02, BreakerState.NORMAL),
        (0.0, BreakerState.NORMAL),
        (-0.0299, BreakerState.NORMAL),
        (-0.03, BreakerState.REDUCED),  # exact boundary triggers
        (-0.04, BreakerState.REDUCED),
        (-0.05, BreakerState.HALT_NEW),  # exact boundary
        (-0.06, BreakerState.HALT_NEW),
        (-0.08, BreakerState.FLATTEN),  # exact boundary
        (-0.20, BreakerState.FLATTEN),
    ],
)
def test_tier_mapping(tmp_path: Path, pnl: float, expected: BreakerState) -> None:
    breaker = make_breaker(tmp_path)
    assert breaker.evaluate(pnl, D1) is expected


def test_intraday_escalation_only(tmp_path: Path) -> None:
    breaker = make_breaker(tmp_path)
    assert breaker.evaluate(-0.06, D1) is BreakerState.HALT_NEW
    # pnl recovers the same day: state must NOT improve
    assert breaker.evaluate(-0.01, D1) is BreakerState.HALT_NEW
    # but it can still escalate further
    assert breaker.evaluate(-0.09, D1) is BreakerState.FLATTEN


def test_new_date_resets_to_normal(tmp_path: Path) -> None:
    breaker = make_breaker(tmp_path)
    assert breaker.evaluate(-0.04, D1) is BreakerState.REDUCED
    assert breaker.evaluate(-0.01, D2) is BreakerState.NORMAL
    assert breaker.current(D2) is BreakerState.NORMAL


def test_flatten_sticky_across_dates_until_reset(tmp_path: Path) -> None:
    breaker = make_breaker(tmp_path)
    assert breaker.evaluate(-0.09, D1) is BreakerState.FLATTEN
    assert breaker.current(D2) is BreakerState.FLATTEN
    assert breaker.evaluate(0.05, D2) is BreakerState.FLATTEN  # even on a green day
    assert breaker.current(D3) is BreakerState.FLATTEN
    breaker.reset()
    assert breaker.current(D3) is BreakerState.NORMAL
    assert breaker.evaluate(0.0, D3) is BreakerState.NORMAL


def test_state_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "dir" / "breaker.json"  # parents auto-created
    first = CircuitBreaker(CFG, path)
    assert first.evaluate(-0.055, D1) is BreakerState.HALT_NEW

    second = CircuitBreaker(CFG, path)
    assert second.current(D1) is BreakerState.HALT_NEW
    # escalation-only still applies through the new instance
    assert second.evaluate(-0.001, D1) is BreakerState.HALT_NEW


def test_flatten_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "breaker.json"
    CircuitBreaker(CFG, path).evaluate(-0.10, D1)
    assert CircuitBreaker(CFG, path).current(D3) is BreakerState.FLATTEN


def test_current_without_state_file_is_normal(tmp_path: Path) -> None:
    breaker = make_breaker(tmp_path)
    assert breaker.current(D1) is BreakerState.NORMAL


def test_reset_is_idempotent(tmp_path: Path) -> None:
    breaker = make_breaker(tmp_path)
    breaker.reset()  # no state file yet: must not raise
    breaker.evaluate(-0.09, D1)
    breaker.reset()
    breaker.reset()
    assert breaker.current(D1) is BreakerState.NORMAL


def test_size_multiplier() -> None:
    assert CircuitBreaker.size_multiplier(BreakerState.NORMAL) == 1.0
    assert CircuitBreaker.size_multiplier(BreakerState.REDUCED) == 0.5
    assert CircuitBreaker.size_multiplier(BreakerState.HALT_NEW) == 0.0
    assert CircuitBreaker.size_multiplier(BreakerState.FLATTEN) == 0.0
