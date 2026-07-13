"""Double moving-average crossover on one symbol.

Long `weight` of equity whenever the fast SMA is above the slow SMA
(golden cross), flat otherwise (death cross). The rule is stateless — the
position is fully determined by the two averages — so a backtest window
that starts inside a golden-cross regime enters (and pays costs) on its
first day.
"""
from __future__ import annotations

import pandas as pd

from finora.strategy.technical import TechnicalStrategy

DEFAULT_PARAMS = {
    "symbol": "SPY",
    "fast_days": 50,
    "slow_days": 200,
    "weight": 1.0,
}


class MaCrossStrategy(TechnicalStrategy):
    """params (all optional): symbol, fast_days, slow_days, weight."""

    DEFAULT_PARAMS = DEFAULT_PARAMS

    def _configure(self, params: dict) -> None:
        self.fast_days = int(params["fast_days"])
        self.slow_days = int(params["slow_days"])
        self.weight = float(params["weight"])
        if not 0 < self.fast_days < self.slow_days:
            raise ValueError(
                f"expected 0 < fast_days < slow_days, got {self.fast_days}/{self.slow_days}"
            )
        if not 0.0 < self.weight <= 1.0:
            raise ValueError(f"weight must be in (0, 1], got {self.weight}")

    def window_weights(
        self, adj_close: pd.Series, mask: pd.Series
    ) -> tuple[pd.Series, list[dict]]:
        fast = adj_close.rolling(self.fast_days).mean()
        slow = adj_close.rolling(self.slow_days).mean()
        long_regime = (fast > slow) & slow.notna()
        weights = long_regime[mask].astype(float) * self.weight
        weights.name = "weight"

        trades: list[dict] = []
        previous = 0.0
        for day, value in weights.items():
            if value != previous:
                trades.append(
                    {
                        "date": day,
                        "action": "buy" if value > previous else "sell",
                        "fast_ma": round(float(fast.loc[day]), 4),
                        "slow_ma": round(float(slow.loc[day]), 4),
                        "weight_after": value,
                    }
                )
            previous = value
        return weights, trades
