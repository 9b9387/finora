"""Strategy quarantine: staged capital ramp and promotion criteria.

New strategies start on paper (no capital), graduate to a small fixed slice,
and only reach their configured fraction after their live/paper track record
clears the promotion criteria.
"""
from __future__ import annotations

import math

import pandas as pd

from finora.core.config import SMALL_STAGE_FRACTION, StrategyConfig, StrategyStage

_TRADING_DAYS_PER_YEAR = 252


def stage_capital_fraction(cfg: StrategyConfig) -> float:
    """Fraction of equity the strategy may deploy at its current stage."""
    if cfg.stage is StrategyStage.PAPER:
        return 0.0
    if cfg.stage is StrategyStage.SMALL:
        return min(SMALL_STAGE_FRACTION, cfg.capital_fraction)
    return cfg.capital_fraction


def promotion_report(
    daily_returns: pd.Series,
    min_days: int = 60,
    min_sharpe: float = 0.5,
    max_drawdown_floor: float = -0.10,
) -> dict:
    """Evaluate a strategy's daily-return track record against promotion criteria.

    Returns {'ready': bool, 'criteria': {name: {'value', 'threshold', 'passed'}}}
    for criteria: days (>= min_days), sharpe (annualized, >= min_sharpe) and
    max_drawdown (peak-to-trough on the compounded curve, >= max_drawdown_floor;
    drawdowns are negative numbers, so shallower is larger).
    """
    returns = daily_returns.dropna().astype(float)
    days = int(len(returns))

    if days == 0:
        sharpe = 0.0
        max_drawdown = 0.0
    else:
        std = float(returns.std(ddof=1)) if days > 1 else 0.0
        # treat float-noise variance on a constant series as zero
        if not math.isfinite(std) or std <= 1e-12:
            sharpe = 0.0
        else:
            sharpe = float(returns.mean()) / std * math.sqrt(_TRADING_DAYS_PER_YEAR)
        curve = (1.0 + returns).cumprod()
        max_drawdown = float((curve / curve.cummax() - 1.0).min())

    criteria = {
        "days": {"value": days, "threshold": min_days, "passed": days >= min_days},
        "sharpe": {"value": sharpe, "threshold": min_sharpe, "passed": sharpe >= min_sharpe},
        "max_drawdown": {
            "value": max_drawdown,
            "threshold": max_drawdown_floor,
            "passed": max_drawdown >= max_drawdown_floor,
        },
    }
    return {"ready": all(c["passed"] for c in criteria.values()), "criteria": criteria}
