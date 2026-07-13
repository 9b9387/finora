"""Bollinger breakout: band math, entry/exit, no self-inflating trigger."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from finora.core.config import Settings, StrategyConfig
from finora.strategy.bollinger import BollingerBreakoutStrategy

PARAMS = {"symbol": "SPY", "period": 5, "num_std": 2.0, "weight": 1.0}


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


def strategy_for(closes: pd.Series, params: dict | None = None) -> BollingerBreakoutStrategy:
    bars = make_bars(closes)
    return BollingerBreakoutStrategy("boll_test", params or PARAMS, lambda *a: bars)


def test_breakout_buys_and_middle_band_exits():
    # 10 flat days (tight bands), a jump above the prior upper band, a few
    # strong days, then a collapse below the middle band
    values = [100.0] * 10 + [105.0, 106.0, 106.0, 90.0, 90.0]
    closes = pd.Series(values, index=_dates(len(values)))
    strat = strategy_for(closes)
    weights, trades = strat.weight_series(closes.index[-1].date())

    assert [t["action"] for t in trades] == ["buy", "sell"]
    buy, sell = trades
    assert buy["date"] == closes.index[10]  # the 105 jump day
    assert buy["close"] > buy["upper_band"]
    assert sell["date"] == closes.index[13]  # the 90 collapse day
    assert sell["close"] < sell["middle_band"]
    assert weights.iloc[10] == 1.0
    assert weights.iloc[-1] == 0.0


def test_trigger_compares_against_prior_day_band():
    # The jump day inflates its own band: with same-day bands 105 would not
    # exceed upper (~105.2); against yesterday's tight band it must trigger.
    values = [100.0] * 10 + [105.0]
    closes = pd.Series(values, index=_dates(len(values)))
    _, trades = strategy_for(closes).weight_series(closes.index[-1].date())
    assert [t["action"] for t in trades] == ["buy"]


def test_flat_series_never_trades():
    closes = pd.Series(100.0, index=_dates(30))
    weights, trades = strategy_for(closes).weight_series(closes.index[-1].date())
    assert trades == []
    assert (weights == 0.0).all()


def test_warmup_days_stay_flat():
    values = [100.0, 120.0, 80.0, 130.0]  # wild moves inside the warm-up window
    closes = pd.Series(values, index=_dates(len(values)))
    weights, trades = strategy_for(closes).weight_series(closes.index[-1].date())
    assert trades == []
    assert (weights == 0.0).all()


def test_invalid_params_rejected():
    with pytest.raises(ValueError):
        BollingerBreakoutStrategy("bad", {"period": 1}, lambda *a: None)
    with pytest.raises(ValueError):
        BollingerBreakoutStrategy("bad", {"num_std": 0.0}, lambda *a: None)
    with pytest.raises(ValueError):
        BollingerBreakoutStrategy("bad", {"weight": 1.5}, lambda *a: None)


def test_run_backtest_wiring(tmp_path):
    rng = np.random.default_rng(3)
    base = 100 + np.cumsum(rng.normal(0.05, 0.4, 200))
    base[50:60] += np.linspace(0, 8, 10)  # engineered breakout
    closes = pd.Series(base, index=_dates(200))
    bars = make_bars(closes)

    from finora.backtest.runner import run_backtest

    cfg = StrategyConfig(name="boll_test", kind="bollinger", params=PARAMS)
    metrics = run_backtest(
        Settings(),
        cfg,
        start=date(2024, 2, 1),
        end=closes.index[-1].date(),
        price_loader=lambda *a: bars,
        out_root=tmp_path,
    )
    assert metrics["n_days"] > 100
    out_dirs = list(tmp_path.glob("boll_test_*"))
    assert len(out_dirs) == 1
    config = pd.read_json(out_dirs[0] / "config.json", typ="series")
    assert config["kind"] == "bollinger"
    assert config["symbol"] == "SPY"
