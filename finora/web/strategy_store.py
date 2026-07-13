"""Read/write config/strategies.yaml for the web strategy manager.

The YAML file stays the single source of truth shared with the CLI. Writes
are validated (pydantic StrategyConfig plus the strategy class's own param
validation), then applied atomically. Hand-written comments in the file are
not preserved once the web UI edits it — a generated header says so.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import yaml

from finora.core.config import Settings, StrategyConfig
from finora.core.errors import ConfigError

_HEADER = (
    "# L2 strategies. stage: paper -> small -> full (quarantine ramp; promotions are\n"
    "# manual config edits, gated on forward performance).\n"
    "# This file is managed by the finora web UI; hand-written comments are not preserved.\n"
)

_TECHNICAL_KINDS = ("rsi", "ma_cross", "bollinger", "momentum")


def strategies_path(config_dir: Path) -> Path:
    return config_dir / "strategies.yaml"


def load_strategies(config_dir: Path) -> list[StrategyConfig]:
    """Current strategies, freshly parsed and validated from disk."""
    return list(Settings.load(config_dir).strategies)


def validate_strategy(cfg: StrategyConfig) -> None:
    """Run the strategy class's own parameter validation where possible
    (threshold ordering, window sizes, ...). qlib strategies are exempt:
    constructing one requires model artifacts."""
    if cfg.kind not in _TECHNICAL_KINDS:
        return
    from finora.strategy.base import build_strategy

    def _no_loader(*_args: object) -> None:  # construction never loads prices
        raise AssertionError("price loader must not be called during validation")

    try:
        build_strategy(cfg, Settings(), _no_loader)
    except ValueError as exc:
        raise ConfigError(f"invalid params for '{cfg.name}': {exc}") from exc


def save_strategies(config_dir: Path, strategies: list[StrategyConfig]) -> None:
    """Validate and atomically rewrite strategies.yaml."""
    names = [cfg.name for cfg in strategies]
    if len(names) != len(set(names)):
        raise ConfigError("strategy names must be unique")
    for cfg in strategies:
        validate_strategy(cfg)

    payload = {
        "strategies": [cfg.model_dump(mode="json") for cfg in strategies]
    }
    text = _HEADER + yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)

    path = strategies_path(config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".strategies-", suffix=".yaml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
