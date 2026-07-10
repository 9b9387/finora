"""Tests for backtest metrics, artifact persistence, and the momentum runner."""
from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from finora.backtest.report import compute_metrics, save_backtest_artifact
from finora.backtest.runner import run_backtest
from finora.core.config import Settings, StrategyConfig


def test_constant_positive_returns() -> None:
    idx = pd.bdate_range("2024-01-02", periods=60)
    returns = pd.Series(0.001, index=idx)
    metrics = compute_metrics(returns)

    assert metrics["sharpe"] > 0
    assert metrics["max_drawdown"] == 0.0
    assert metrics["calmar"] == 0.0
    assert metrics["n_days"] == 60
    assert metrics["total_return"] == pytest.approx(1.001**60 - 1)
    assert metrics["annualized_return"] == pytest.approx(1.001**252 - 1, rel=1e-9)


def test_known_ten_percent_drawdown() -> None:
    values = [0.01] * 10 + [-0.10] + [0.005] * 5
    idx = pd.bdate_range("2024-01-02", periods=len(values))
    metrics = compute_metrics(pd.Series(values, index=idx))

    assert metrics["max_drawdown"] == pytest.approx(-0.10, abs=1e-12)
    assert metrics["calmar"] == pytest.approx(metrics["annualized_return"] / 0.10)


def test_short_series_returns_zeros() -> None:
    for series in (pd.Series(dtype=float), pd.Series([0.02])):
        metrics = compute_metrics(series)
        assert metrics["n_days"] == len(series)
        for key in (
            "total_return",
            "annualized_return",
            "annualized_vol",
            "sharpe",
            "max_drawdown",
            "calmar",
        ):
            assert metrics[key] == 0.0


def test_save_backtest_artifact(tmp_path) -> None:
    idx = pd.bdate_range("2024-06-03", periods=20)
    returns = pd.Series(np.linspace(-0.01, 0.01, 20), index=idx)
    metrics = compute_metrics(returns)
    snapshot = {"name": "demo", "kind": "momentum", "cost_bps": 15.0, "params": {"top_k": 2}}

    out_dir = save_backtest_artifact("demo", metrics, snapshot, returns, out_root=tmp_path)

    assert out_dir == tmp_path / "demo_20240628"
    assert sorted(p.name for p in out_dir.iterdir()) == [
        "config.json",
        "metrics.json",
        "returns.csv",
    ]

    saved_metrics = json.loads((out_dir / "metrics.json").read_text())
    for key, value in metrics.items():
        assert saved_metrics[key] == pytest.approx(value)

    saved_config = json.loads((out_dir / "config.json").read_text())
    assert saved_config == snapshot

    saved_returns = pd.read_csv(out_dir / "returns.csv", parse_dates=["date"])
    assert list(saved_returns.columns) == ["date", "return"]
    assert len(saved_returns) == 20
    assert saved_returns["return"].to_numpy() == pytest.approx(returns.to_numpy())
    assert saved_returns["date"].iloc[-1] == idx[-1]


def _synthetic_bars() -> pd.DataFrame:
    dates = pd.bdate_range("2023-01-02", periods=300)
    growth = {"RISE1": 1.003, "RISE2": 1.0015, "FLAT": 1.0, "FALL": 0.999}
    frames = []
    for symbol, g in growth.items():
        closes = 100.0 * g ** np.arange(len(dates))
        frames.append(
            pd.DataFrame(
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
        )
    return (
        pd.concat(frames, ignore_index=True)
        .sort_values(["symbol", "date"])
        .reset_index(drop=True)
    )


def test_run_backtest_momentum(tmp_path) -> None:
    bars = _synthetic_bars()
    dates = pd.DatetimeIndex(sorted(bars["date"].unique()))

    def loader(
        symbols: list[str] | None, start: date | None, end: date | None
    ) -> pd.DataFrame:
        out = bars
        if symbols:
            out = out[out["symbol"].isin(symbols)]
        if start is not None:
            out = out[out["date"] >= pd.Timestamp(start)]
        if end is not None:
            out = out[out["date"] <= pd.Timestamp(end)]
        return out.reset_index(drop=True)

    cfg = StrategyConfig(
        name="bt_mom", kind="momentum", params={"lookback_days": 60, "top_k": 2}
    )
    start = dates[100].date()
    end = dates[-1].date()
    metrics = run_backtest(
        Settings(),
        cfg,
        start=start,
        end=end,
        cost_bps=10.0,
        price_loader=loader,
        out_root=tmp_path,
    )

    # 200 trade dates in the window -> 199 realized next-day returns.
    assert metrics["n_days"] == 199
    # The two steady risers dominate; net of costs the portfolio makes money.
    assert metrics["total_return"] > 0
    assert metrics["max_drawdown"] <= 0

    out_dir = tmp_path / f"bt_mom_{dates[-1]:%Y%m%d}"
    assert out_dir.is_dir()
    assert sorted(p.name for p in out_dir.iterdir()) == [
        "config.json",
        "metrics.json",
        "returns.csv",
    ]
    saved_config = json.loads((out_dir / "config.json").read_text())
    assert saved_config["cost_bps"] == 10.0
    assert saved_config["name"] == "bt_mom"
    assert saved_config["start"] == str(start)
    assert saved_config["end"] == str(end)
