"""RSI strategy: indicator values, re-arm state machine, and backtest wiring."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from finora.core.config import Settings, StrategyConfig
from finora.strategy.rsi import (
    RsiMeanReversionStrategy,
    weights_from_rsi,
    wilder_rsi,
)


def _dates(n: int) -> pd.DatetimeIndex:
    return pd.bdate_range("2024-01-02", periods=n)


def rsi_series(values: list[float]) -> pd.Series:
    return pd.Series(values, index=_dates(len(values)), dtype=float)


# -- wilder_rsi ---------------------------------------------------------------


def test_rsi_warmup_is_nan_and_bounded():
    rng = np.random.default_rng(7)
    closes = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 300))), index=_dates(300))
    rsi = wilder_rsi(closes, period=14)
    assert rsi.iloc[:14].isna().all()
    body = rsi.iloc[14:]
    assert body.notna().all()
    assert ((body >= 0) & (body <= 100)).all()


def test_rsi_extremes():
    up = pd.Series(np.linspace(100, 200, 40), index=_dates(40))
    assert wilder_rsi(up, 14).iloc[-1] == pytest.approx(100.0)
    down = pd.Series(np.linspace(200, 100, 40), index=_dates(40))
    assert wilder_rsi(down, 14).iloc[-1] == pytest.approx(0.0)
    flat = pd.Series(100.0, index=_dates(40))
    assert wilder_rsi(flat, 14).iloc[-1] == pytest.approx(50.0)


def test_rsi_matches_hand_computed_wilder():
    # period=2 keeps the hand computation short. Changes: +1, -1, +1
    closes = pd.Series([10.0, 11.0, 10.0, 11.0], index=_dates(4))
    rsi = wilder_rsi(closes, period=2)
    # After 2 changes: avg_gain=(0+ewm)… with adjust=False: g=[1,0,1]->[1,.5,.75]; l=[0,1,.5]
    # day2: rs=0.5/0.5=1 -> 50 ; day3: rs=0.75/0.25=3 -> 75
    assert rsi.iloc[2] == pytest.approx(50.0)
    assert rsi.iloc[3] == pytest.approx(75.0)


# -- weights_from_rsi state machine -------------------------------------------


def test_buy_rearm_cycle():
    # dip -> no rebuy while low -> recover past 50 -> dip buys again
    rsi = rsi_series([45, 25, 20, 35, 55, 28, 60, 40])
    weights, trades = weights_from_rsi(rsi, unit_fraction=0.25, max_units=4)
    assert [(t["action"], str(t["date"].date())) for t in trades] == [
        ("buy", "2024-01-03"),
        ("buy", "2024-01-09"),
    ]
    assert weights.iloc[-1] == pytest.approx(0.5)


def test_sell_requires_position_and_rearm():
    # buy at 25; rally: first close >70 sells, stays >70 (disarmed), back to 50, >70 sells again
    rsi = rsi_series([25, 55, 28, 72, 80, 75, 50, 75])
    weights, trades = weights_from_rsi(rsi, unit_fraction=0.25, max_units=4)
    actions = [t["action"] for t in trades]
    assert actions == ["buy", "buy", "sell", "sell"]
    assert weights.iloc[-1] == pytest.approx(0.0)
    # never short
    assert (weights >= 0).all()


def test_position_capped_at_max_units():
    rsi = rsi_series([25, 55, 25, 55, 25, 55, 25])
    weights, trades = weights_from_rsi(rsi, unit_fraction=0.5, max_units=2)
    assert len([t for t in trades if t["action"] == "buy"]) == 2
    assert weights.max() == pytest.approx(1.0)


def test_at_most_one_trade_per_day():
    rsi = rsi_series([25, 55, 25, 75, 25])
    _weights, trades = weights_from_rsi(rsi)
    per_day = pd.Series([t["date"] for t in trades]).value_counts()
    assert (per_day <= 1).all()


def test_nan_rsi_days_hold_position():
    rsi = rsi_series([25, float("nan"), float("nan"), 55])
    weights, trades = weights_from_rsi(rsi, unit_fraction=0.25)
    assert len(trades) == 1
    assert (weights == 0.25).all()


# -- strategy + backtest wiring ------------------------------------------------


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
        }
    )


def oscillating_closes(n: int = 300) -> pd.Series:
    # A slow sine forces RSI through oversold and overbought repeatedly.
    t = np.arange(n)
    values = 100 + 15 * np.sin(t / 12)
    return pd.Series(values, index=_dates(n))


def test_generate_signals_emits_current_target():
    closes = oscillating_closes()
    bars = make_bars(closes)

    def loader(symbols, start, end):
        return bars

    strat = RsiMeanReversionStrategy("rsi_test", {"symbol": "SPY"}, loader)
    signals = strat.generate_signals(closes.index[-1].date())
    assert len(signals) == 1
    sig = signals[0]
    assert sig.instrument == "SPY"
    assert 0.0 <= sig.target_weight <= 1.0
    weights, _trades = strat.weight_series(closes.index[-1].date())
    assert sig.target_weight == pytest.approx(float(weights.iloc[-1]))


def test_invalid_thresholds_rejected():
    with pytest.raises(ValueError):
        RsiMeanReversionStrategy("bad", {"buy_below": 60, "rearm": 50}, lambda *a: None)


def test_run_backtest_rsi_writes_trades(tmp_path):
    closes = oscillating_closes()
    bars = make_bars(closes)

    def loader(symbols, start, end):
        return bars

    from finora.backtest.runner import run_backtest

    cfg = StrategyConfig(name="rsi_test", kind="rsi", params={"symbol": "SPY"})
    metrics = run_backtest(
        Settings(),
        cfg,
        start=date(2024, 6, 3),
        end=closes.index[-1].date(),
        price_loader=loader,
        out_root=tmp_path,
    )
    assert metrics["n_days"] > 100

    out_dirs = list(tmp_path.glob("rsi_test_*"))
    assert len(out_dirs) == 1
    config = pd.read_json(out_dirs[0] / "config.json", typ="series")
    assert config["symbol"] == "SPY"
    trades = config["trades"]
    assert trades, "oscillating prices must produce trades"
    # position starts flat inside the window: first trade is a buy
    assert trades[0]["action"] == "buy"
    returns = pd.read_csv(out_dirs[0] / "returns.csv")
    assert len(returns) == metrics["n_days"]
