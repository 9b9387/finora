"""MomentumStrategy tests on deterministic synthetic bars (no network, no store)."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from finora.strategy.base import PriceLoader
from finora.strategy.momentum import MomentumStrategy

N_DAYS = 200
DATES = pd.bdate_range("2024-01-02", periods=N_DAYS)
AS_OF: date = DATES[-1].date()

# Deterministic geometric daily growth rates.
GROWTH = {"RISE1": 1.003, "RISE2": 1.0015, "FLAT": 1.0, "FALL": 0.999}


def _symbol_frame(symbol: str, dates: pd.DatetimeIndex, closes: np.ndarray) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": symbol,
            "date": dates,
            "open": closes,
            "high": closes * 1.01,
            "low": closes * 0.99,
            "close": closes,
            "volume": 1_000_000.0,
            "factor": 1.0,
        }
    )


def make_bars() -> pd.DataFrame:
    frames = [
        _symbol_frame(sym, DATES, 100.0 * growth ** np.arange(N_DAYS))
        for sym, growth in GROWTH.items()
    ]
    bars = pd.concat(frames, ignore_index=True)
    return bars.sort_values(["symbol", "date"]).reset_index(drop=True)


def loader_for(bars: pd.DataFrame, honor_end: bool = True) -> PriceLoader:
    def load(
        symbols: list[str] | None, start: date | None, end: date | None
    ) -> pd.DataFrame:
        out = bars
        if symbols:
            out = out[out["symbol"].isin(symbols)]
        if start is not None:
            out = out[out["date"] >= pd.Timestamp(start)]
        if honor_end and end is not None:
            out = out[out["date"] <= pd.Timestamp(end)]
        return out.reset_index(drop=True)

    return load


@pytest.fixture()
def bars() -> pd.DataFrame:
    return make_bars()


def test_top_k_picks_risers_with_equal_weights(bars: pd.DataFrame) -> None:
    strat = MomentumStrategy(
        "mom", {"lookback_days": 126, "top_k": 2}, loader_for(bars)
    )
    signals = strat.generate_signals(AS_OF)

    assert [s.instrument for s in signals] == ["RISE1", "RISE2"]
    weights = [s.target_weight for s in signals]
    assert weights == [0.5, 0.5]
    assert sum(weights) == pytest.approx(1.0)
    for s in signals:
        assert s.source == "mom"
        assert s.as_of == AS_OF
        assert 0.0 < s.confidence <= 1.0
    # Rank percentile: the strongest riser has the highest confidence.
    assert signals[0].confidence == pytest.approx(1.0)
    assert signals[0].confidence > signals[1].confidence


def test_no_lookahead_future_rows_ignored(bars: pd.DataFrame) -> None:
    strat = MomentumStrategy(
        "mom", {"lookback_days": 126, "top_k": 2}, loader_for(bars)
    )
    before = strat.generate_signals(AS_OF)

    # Append 50 future days in which the faller moons; use a sloppy loader
    # that ignores `end` so only the strategy's own guard protects us.
    future_dates = pd.bdate_range(DATES[-1] + pd.Timedelta(days=1), periods=50)
    moon = _symbol_frame("FALL", future_dates, 10_000.0 * 1.5 ** np.arange(50))
    extended = (
        pd.concat([bars, moon], ignore_index=True)
        .sort_values(["symbol", "date"])
        .reset_index(drop=True)
    )
    sloppy = MomentumStrategy(
        "mom", {"lookback_days": 126, "top_k": 2}, loader_for(extended, honor_end=False)
    )
    after = sloppy.generate_signals(AS_OF)

    assert after == before
    assert [s.instrument for s in after] == ["RISE1", "RISE2"]


def test_insufficient_history_symbol_excluded(bars: pd.DataFrame) -> None:
    # NEWB has the highest trailing return by far but only 20 observations,
    # well below 0.8 * 126.
    newb_dates = DATES[-20:]
    newb = _symbol_frame("NEWB", newb_dates, 10.0 * 1.2 ** np.arange(20))
    with_newb = (
        pd.concat([bars, newb], ignore_index=True)
        .sort_values(["symbol", "date"])
        .reset_index(drop=True)
    )
    strat = MomentumStrategy(
        "mom", {"lookback_days": 126, "top_k": 2}, loader_for(with_newb)
    )
    signals = strat.generate_signals(AS_OF)

    picked = {s.instrument for s in signals}
    assert "NEWB" not in picked
    assert picked == {"RISE1", "RISE2"}


def test_adjusted_close_uses_factor(bars: pd.DataFrame) -> None:
    # Halve FLAT's raw close mid-series but set factor so adjusted close stays
    # flat: it must not look like a crash or a rally.
    flat = bars["symbol"] == "FLAT"
    split_point = bars[flat].index[100:]
    bars.loc[split_point, "close"] = bars.loc[split_point, "close"] / 2.0
    bars.loc[split_point, "factor"] = 2.0

    strat = MomentumStrategy(
        "mom", {"lookback_days": 126, "top_k": 3}, loader_for(bars)
    )
    signals = strat.generate_signals(AS_OF)
    assert [s.instrument for s in signals] == ["RISE1", "RISE2", "FLAT"]
    flat_signal = signals[2]
    # FLAT ranks third of four qualifying symbols -> percentile 2/4.
    assert flat_signal.confidence == pytest.approx(0.5)


def test_no_qualifying_symbols_returns_empty(bars: pd.DataFrame) -> None:
    strat = MomentumStrategy(
        "mom", {"lookback_days": 500, "top_k": 2}, loader_for(bars)
    )
    assert strat.generate_signals(AS_OF) == []


def test_as_of_before_data_returns_empty(bars: pd.DataFrame) -> None:
    strat = MomentumStrategy(
        "mom", {"lookback_days": 126, "top_k": 2}, loader_for(bars, honor_end=False)
    )
    assert strat.generate_signals(date(2020, 1, 2)) == []
