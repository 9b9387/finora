"""Quarantine staging fractions and promotion criteria."""
from __future__ import annotations

import pandas as pd
import pytest

from finora.core.config import SMALL_STAGE_FRACTION, StrategyConfig, StrategyStage
from finora.risk.quarantine import promotion_report, stage_capital_fraction

# ---------------------------------------------------------------- stage fractions


def test_paper_stage_gets_zero_capital() -> None:
    cfg = StrategyConfig(name="mom", stage=StrategyStage.PAPER, capital_fraction=0.8)
    assert stage_capital_fraction(cfg) == 0.0


def test_default_stage_is_paper() -> None:
    assert stage_capital_fraction(StrategyConfig(name="mom")) == 0.0


def test_small_stage_capped_at_small_fraction() -> None:
    cfg = StrategyConfig(name="mom", stage=StrategyStage.SMALL, capital_fraction=0.5)
    assert stage_capital_fraction(cfg) == SMALL_STAGE_FRACTION


def test_small_stage_capped_by_capital_fraction_when_smaller() -> None:
    cfg = StrategyConfig(name="mom", stage=StrategyStage.SMALL, capital_fraction=0.02)
    assert stage_capital_fraction(cfg) == 0.02


def test_full_stage_uses_capital_fraction() -> None:
    cfg = StrategyConfig(name="mom", stage=StrategyStage.FULL, capital_fraction=0.7)
    assert stage_capital_fraction(cfg) == 0.7


# ---------------------------------------------------------------- promotion report


def steady_series(n_pairs: int) -> pd.Series:
    """Deterministic high-sharpe, shallow-drawdown series of 2*n_pairs days."""
    return pd.Series([0.004, -0.001] * n_pairs)


def test_promotion_ready_when_all_criteria_pass() -> None:
    report = promotion_report(steady_series(60))  # 120 days
    assert report["ready"] is True
    for name in ("days", "sharpe", "max_drawdown"):
        crit = report["criteria"][name]
        assert crit["passed"] is True
        assert "value" in crit and "threshold" in crit


def test_promotion_fails_on_too_few_days() -> None:
    report = promotion_report(steady_series(15))  # 30 days: good sharpe, shallow dd
    assert report["ready"] is False
    assert report["criteria"]["days"]["passed"] is False
    assert report["criteria"]["days"]["value"] == 30
    assert report["criteria"]["days"]["threshold"] == 60
    assert report["criteria"]["sharpe"]["passed"] is True
    assert report["criteria"]["max_drawdown"]["passed"] is True


def test_promotion_fails_on_low_sharpe() -> None:
    returns = pd.Series([0.01, -0.01] * 40)  # 80 days, zero mean
    report = promotion_report(returns)
    assert report["ready"] is False
    assert report["criteria"]["sharpe"]["passed"] is False
    assert report["criteria"]["sharpe"]["value"] == pytest.approx(0.0, abs=1e-9)
    assert report["criteria"]["days"]["passed"] is True
    assert report["criteria"]["max_drawdown"]["passed"] is True


def test_promotion_fails_on_deep_drawdown() -> None:
    values = [0.005] * 50 + [-0.2] + [0.005] * 49  # one crash day
    report = promotion_report(pd.Series(values))
    assert report["ready"] is False
    crit = report["criteria"]["max_drawdown"]
    assert crit["passed"] is False
    assert crit["value"] == pytest.approx(-0.2, rel=1e-6)
    assert crit["threshold"] == -0.10
    assert report["criteria"]["days"]["passed"] is True
    assert report["criteria"]["sharpe"]["passed"] is True


def test_promotion_custom_thresholds() -> None:
    series = steady_series(15)  # 30 days
    strict = promotion_report(series, min_days=31)
    lenient = promotion_report(series, min_days=30)
    assert strict["ready"] is False
    assert lenient["ready"] is True


def test_promotion_empty_series_not_ready() -> None:
    report = promotion_report(pd.Series([], dtype=float))
    assert report["ready"] is False
    assert report["criteria"]["days"]["value"] == 0
    assert report["criteria"]["days"]["passed"] is False


def test_promotion_constant_returns_have_zero_sharpe() -> None:
    # zero variance must not divide by zero; sharpe defined as 0.0
    report = promotion_report(pd.Series([0.001] * 100))
    assert report["criteria"]["sharpe"]["value"] == 0.0
    assert report["criteria"]["sharpe"]["passed"] is False


def test_promotion_ignores_nans() -> None:
    series = pd.concat([steady_series(30), pd.Series([float("nan")] * 10)], ignore_index=True)
    report = promotion_report(series)
    assert report["criteria"]["days"]["value"] == 60
