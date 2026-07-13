"""Double moving-average crossover: regime logic, trades, backtest wiring."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from finora.core.config import Settings, StrategyConfig
from finora.strategy.base import build_strategy
from finora.strategy.ma_cross import MaCrossStrategy

PARAMS = {"symbol": "SPY", "fast_days": 3, "slow_days": 5, "weight": 1.0}


def _dates(n: int) -> pd.DatetimeIndex:
    return pd.bdate_range("2024-01-02", periods=n)


def make_bars(closes: pd.Series, symbol: str = "SPY") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": symbol,
            "date": closes.index,
            "open": closes.values,
            "high": closes.values,
            "low": closes.values,
            "close": closes.values,
            "volume": 1e6,
            "factor": 1.0,
            "dividend": 0.0,
            "split_ratio": 0.0,
        }
    )


def strategy_for(closes: pd.Series, params: dict | None = None) -> MaCrossStrategy:
    bars = make_bars(closes)
    return MaCrossStrategy("ma_test", params or PARAMS, lambda *a: bars)


def test_uptrend_enters_downtrend_exits():
    # down for 10 days (fast below slow), then up for 10 (fast crosses above),
    # then down again (fast crosses back below)
    values = list(np.linspace(110, 100, 10)) + list(np.linspace(100, 115, 10)) + list(
        np.linspace(115, 100, 10)
    )
    closes = pd.Series(values, index=_dates(30))
    strat = strategy_for(closes)
    weights, trades = strat.weight_series(closes.index[-1].date())

    assert [t["action"] for t in trades] == ["buy", "sell"]
    buy, sell = trades
    assert buy["fast_ma"] > buy["slow_ma"]
    assert sell["fast_ma"] < sell["slow_ma"]
    assert weights.max() == 1.0
    assert weights.iloc[-1] == 0.0
    assert (weights.iloc[: PARAMS["slow_days"] - 1] == 0.0).all()  # warm-up flat


def test_stateless_regime_enters_at_window_start():
    # steadily rising series: fast > slow everywhere after warm-up
    closes = pd.Series(np.linspace(100, 130, 40), index=_dates(40))
    strat = strategy_for(closes)
    full = strat.weight_series(closes.index[-1].date())[0]
    assert full.iloc[-1] == 1.0

    # a window that starts mid-regime is long from its first masked day
    adj = closes.astype(float)
    mask = pd.Series(adj.index >= adj.index[20], index=adj.index)
    weights, trades = strat.window_weights(adj, mask)
    assert weights.iloc[0] == 1.0
    assert trades[0]["action"] == "buy"
    assert trades[0]["date"] == adj.index[20]


def test_weight_param_scales_position():
    closes = pd.Series(np.linspace(100, 130, 40), index=_dates(40))
    strat = strategy_for(closes, {**PARAMS, "weight": 0.5})
    weights, _ = strat.weight_series(closes.index[-1].date())
    assert weights.max() == pytest.approx(0.5)


def test_invalid_params_rejected():
    with pytest.raises(ValueError):
        MaCrossStrategy("bad", {"fast_days": 5, "slow_days": 5}, lambda *a: None)
    with pytest.raises(ValueError):
        MaCrossStrategy("bad", {**PARAMS, "weight": 0.0}, lambda *a: None)


def test_generate_signals_emits_target():
    closes = pd.Series(np.linspace(100, 130, 40), index=_dates(40))
    strat = strategy_for(closes)
    signals = strat.generate_signals(closes.index[-1].date())
    assert len(signals) == 1
    assert signals[0].instrument == "SPY"
    assert signals[0].target_weight == 1.0


def test_registry_and_backtest_wiring(tmp_path):
    values = list(np.linspace(110, 100, 10)) + list(np.linspace(100, 130, 40))
    closes = pd.Series(values, index=_dates(50))
    bars = make_bars(closes)

    cfg = StrategyConfig(name="ma_test", kind="ma_cross", params=PARAMS)
    strat = build_strategy(cfg, Settings(), lambda *a: bars)
    assert isinstance(strat, MaCrossStrategy)

    from finora.backtest.runner import run_backtest

    metrics = run_backtest(
        Settings(),
        cfg,
        start=date(2024, 1, 16),
        end=closes.index[-1].date(),
        price_loader=lambda *a: bars,
        out_root=tmp_path,
    )
    assert metrics["n_days"] > 10
    out_dirs = list(tmp_path.glob("ma_test_*"))
    assert len(out_dirs) == 1
    config = pd.read_json(out_dirs[0] / "config.json", typ="series")
    assert config["kind"] == "ma_cross"
    assert config["trades"], "trend flip must produce trades"
