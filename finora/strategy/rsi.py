"""Single-instrument RSI mean-reversion with re-arm hysteresis.

Rule (evaluated once per daily bar, at most one trade per day):
buy one unit when RSI drops below `buy_below`; that trigger then stays
disarmed until RSI recovers to `rearm`, after which another drop below
`buy_below` buys again. Selling above `sell_above` mirrors this: one unit
per trigger, re-armed once RSI falls back to `rearm`. Long-only; the
position is capped at `max_units` units of `unit_fraction` weight each.
"""
from __future__ import annotations

import math

import pandas as pd

from finora.strategy.technical import TechnicalStrategy

DEFAULT_PARAMS = {
    "symbol": "SPY",
    "period": 14,
    "buy_below": 30.0,
    "sell_above": 70.0,
    "rearm": 50.0,
    "unit_fraction": 0.25,
    "max_units": 4,
}


def wilder_rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI on a date-indexed close series (RMA smoothing,
    alpha = 1/period). The first `period` values are NaN (warm-up)."""
    if period < 1:
        raise ValueError(f"RSI period must be >= 1, got {period}")
    delta = closes.astype(float).diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rsi = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    # Degenerate windows: no losses -> 100, no moves at all -> neutral 50.
    rsi = rsi.mask((avg_loss == 0) & (avg_gain > 0), 100.0)
    rsi = rsi.mask((avg_loss == 0) & (avg_gain == 0), 50.0)
    return rsi


def weights_from_rsi(
    rsi: pd.Series,
    buy_below: float = 30.0,
    sell_above: float = 70.0,
    rearm: float = 50.0,
    unit_fraction: float = 0.25,
    max_units: int = 4,
) -> tuple[pd.Series, list[dict]]:
    """Replay the re-arm state machine over an RSI series.

    Returns (target weight per date, trade log). At most one trade per day;
    a consumed trigger re-arms only when RSI comes back to `rearm` (from
    below for buys, from above for sells). NaN RSI days hold the position.
    """
    units = 0
    buy_armed = True
    sell_armed = True
    weights: list[float] = []
    trades: list[dict] = []
    for day, value in rsi.items():
        if not math.isnan(value):
            if not buy_armed and value >= rearm:
                buy_armed = True
            if not sell_armed and value <= rearm:
                sell_armed = True
            if buy_armed and value < buy_below and units < max_units:
                units += 1
                buy_armed = False
                trades.append(
                    {"date": day, "action": "buy", "rsi": round(float(value), 2),
                     "units_after": units, "weight_after": units * unit_fraction}
                )
            elif sell_armed and value > sell_above and units > 0:
                units -= 1
                sell_armed = False
                trades.append(
                    {"date": day, "action": "sell", "rsi": round(float(value), 2),
                     "units_after": units, "weight_after": units * unit_fraction}
                )
        weights.append(units * unit_fraction)
    return pd.Series(weights, index=rsi.index, name="weight", dtype=float), trades


class RsiMeanReversionStrategy(TechnicalStrategy):
    """RSI(period) threshold strategy with re-arm hysteresis on one symbol.

    params (all optional): symbol, period, buy_below, sell_above, rearm,
    unit_fraction, max_units — see DEFAULT_PARAMS.
    """

    DEFAULT_PARAMS = DEFAULT_PARAMS

    def _configure(self, params: dict) -> None:
        self.period = int(params["period"])
        self.buy_below = float(params["buy_below"])
        self.sell_above = float(params["sell_above"])
        self.rearm = float(params["rearm"])
        self.unit_fraction = float(params["unit_fraction"])
        self.max_units = int(params["max_units"])
        if not self.buy_below <= self.rearm <= self.sell_above:
            raise ValueError(
                f"expected buy_below <= rearm <= sell_above, got "
                f"{self.buy_below}/{self.rearm}/{self.sell_above}"
            )

    def window_weights(
        self, adj_close: pd.Series, mask: pd.Series
    ) -> tuple[pd.Series, list[dict]]:
        rsi = wilder_rsi(adj_close, self.period)
        return weights_from_rsi(
            rsi[mask],
            buy_below=self.buy_below,
            sell_above=self.sell_above,
            rearm=self.rearm,
            unit_fraction=self.unit_fraction,
            max_units=self.max_units,
        )
