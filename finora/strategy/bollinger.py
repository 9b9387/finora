"""Bollinger band breakout on one symbol.

Buy `weight` of equity when the close breaks above the upper band
(middle SMA + num_std * rolling std); exit when the close falls back
below the middle band. Long-only, at most one trade per day.

Bands are the PREVIOUS day's: comparing today's close against a band that
already contains today's jump would swallow the very breakout being traded
(the band inflates with the move), so each close is tested against the
band as it stood yesterday.
"""
from __future__ import annotations

import math

import pandas as pd

from finora.strategy.technical import TechnicalStrategy

DEFAULT_PARAMS = {
    "symbol": "SPY",
    "period": 20,
    "num_std": 2.0,
    "weight": 1.0,
}


class BollingerBreakoutStrategy(TechnicalStrategy):
    """params (all optional): symbol, period, num_std, weight."""

    DEFAULT_PARAMS = DEFAULT_PARAMS

    def _configure(self, params: dict) -> None:
        self.period = int(params["period"])
        self.num_std = float(params["num_std"])
        self.weight = float(params["weight"])
        if self.period < 2:
            raise ValueError(f"period must be >= 2, got {self.period}")
        if self.num_std <= 0:
            raise ValueError(f"num_std must be > 0, got {self.num_std}")
        if not 0.0 < self.weight <= 1.0:
            raise ValueError(f"weight must be in (0, 1], got {self.weight}")

    def window_weights(
        self, adj_close: pd.Series, mask: pd.Series
    ) -> tuple[pd.Series, list[dict]]:
        middle = adj_close.rolling(self.period).mean()
        std = adj_close.rolling(self.period).std(ddof=0)
        upper = middle + self.num_std * std
        # yesterday's bands, so today's move cannot inflate its own trigger
        upper_prev = upper.shift(1)
        middle_prev = middle.shift(1)

        in_position = False
        weights: list[float] = []
        trades: list[dict] = []
        for day, close in adj_close[mask].items():
            band_up = float(upper_prev.loc[day])
            band_mid = float(middle_prev.loc[day])
            if not math.isnan(band_up):
                if not in_position and close > band_up:
                    in_position = True
                    trades.append(
                        {"date": day, "action": "buy", "close": round(float(close), 4),
                         "upper_band": round(band_up, 4), "weight_after": self.weight}
                    )
                elif in_position and close < band_mid:
                    in_position = False
                    trades.append(
                        {"date": day, "action": "sell", "close": round(float(close), 4),
                         "middle_band": round(band_mid, 4), "weight_after": 0.0}
                    )
            weights.append(self.weight if in_position else 0.0)
        series = pd.Series(weights, index=adj_close[mask].index, name="weight", dtype=float)
        return series, trades
