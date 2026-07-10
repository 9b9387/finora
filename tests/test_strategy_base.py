"""Registry dispatch and Strategy protocol tests for finora.strategy.base."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from finora.core.config import Settings, StrategyConfig
from finora.core.errors import ConfigError
from finora.strategy import Strategy, build_strategy
from finora.strategy.momentum import MomentumStrategy
from finora.strategy.qlib_strategy import QlibStrategy

CANONICAL_COLUMNS = ["symbol", "date", "open", "high", "low", "close", "volume", "factor"]


def _empty_loader(
    symbols: list[str] | None, start: date | None, end: date | None
) -> pd.DataFrame:
    frame = pd.DataFrame(columns=CANONICAL_COLUMNS)
    frame["date"] = pd.to_datetime(frame["date"])
    return frame


def test_build_momentum_strategy() -> None:
    cfg = StrategyConfig(name="mom", kind="momentum", params={"lookback_days": 63, "top_k": 5})
    strat = build_strategy(cfg, Settings(), _empty_loader)
    assert isinstance(strat, MomentumStrategy)
    assert strat.name == "mom"
    assert strat.lookback_days == 63
    assert strat.top_k == 5
    assert isinstance(strat, Strategy)


def test_build_qlib_strategy_without_qlib_import() -> None:
    # Construction must never import qlib or require artifacts.
    cfg = StrategyConfig(name="q", kind="qlib", params={"model_dir": "artifacts/models/q"})
    strat = build_strategy(cfg, Settings(), _empty_loader)
    assert isinstance(strat, QlibStrategy)
    assert strat.name == "q"
    assert strat.top_k == 30
    assert isinstance(strat, Strategy)


def test_unknown_kind_raises_config_error() -> None:
    cfg = StrategyConfig.model_construct(
        name="bad", kind="bogus", params={}, capital_fraction=1.0
    )
    with pytest.raises(ConfigError, match="bogus"):
        build_strategy(cfg, Settings(), _empty_loader)


def test_momentum_empty_data_returns_no_signals() -> None:
    cfg = StrategyConfig(name="mom", kind="momentum", params={})
    strat = build_strategy(cfg, Settings(), _empty_loader)
    assert strat.generate_signals(date(2024, 6, 3)) == []


def test_qlib_missing_artifacts_raises_before_qlib_init(tmp_path) -> None:
    cfg = StrategyConfig(
        name="q", kind="qlib", params={"model_dir": str(tmp_path / "does_not_exist")}
    )
    strat = build_strategy(cfg, Settings(), _empty_loader)
    with pytest.raises(ConfigError, match="finora train q"):
        strat.generate_signals(date(2024, 6, 3))
