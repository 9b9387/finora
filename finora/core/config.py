"""Typed configuration for all layers, loaded from a directory of YAML files.

One file per concern: data.yaml, universe.yaml, risk.yaml, broker.yaml,
strategies.yaml, ops.yaml. Missing files fall back to model defaults;
unknown keys are rejected so typos fail loudly instead of silently
disabling a risk limit.
"""
from __future__ import annotations

import enum
import os
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from finora.core.errors import ConfigError


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QualityConfig(StrictModel):
    max_missing_run_days: int = 5
    max_abs_daily_return: float = 0.5
    min_price: float = 1.0


class DataConfig(StrictModel):
    provider: str = "yfinance"
    data_dir: Path = Path("data")
    start_date: date = date(2015, 1, 1)
    # Incremental ETL re-fetches this many most-recent stored rows so revised
    # values (dividend/split re-adjustments) are noticed; 0 disables overlap.
    refetch_overlap_days: int = Field(default=5, ge=0)
    # Relative close/factor divergence on overlap rows beyond which the
    # symbol's full history is refetched (provider rescaled its history).
    max_adjustment_drift: float = Field(default=1e-3, gt=0.0)
    quality: QualityConfig = QualityConfig()

    @property
    def parquet_dir(self) -> Path:
        return self.data_dir / "parquet" / "daily"

    @property
    def duckdb_path(self) -> Path:
        return self.data_dir / "duckdb" / "finora.duckdb"

    @property
    def qlib_dir(self) -> Path:
        return self.data_dir / "qlib"

    @property
    def universe_dir(self) -> Path:
        return self.data_dir / "universe"


class UniverseConfig(StrictModel):
    name: str = "sp500"
    symbols: list[str] = Field(default_factory=list)  # static override; empty = fetch index


class CircuitBreakerConfig(StrictModel):
    """Daily-drawdown tiers, expressed as negative fractions of equity."""

    reduce_at: float = -0.03
    halt_new_at: float = -0.05
    flatten_at: float = -0.08

    @model_validator(mode="after")
    def _tiers_descend(self) -> "CircuitBreakerConfig":
        if not (0 > self.reduce_at > self.halt_new_at > self.flatten_at):
            raise ValueError(
                "circuit breaker tiers must satisfy 0 > reduce_at > halt_new_at > flatten_at, "
                f"got {self.reduce_at}, {self.halt_new_at}, {self.flatten_at}"
            )
        return self


class RiskConfig(StrictModel):
    max_order_notional: float = 10_000.0
    max_position_pct: float = 0.05
    price_collar_pct: float = 0.05
    max_orders_per_minute: int = 30
    max_gross_exposure: float = 1.0
    quote_max_age_seconds: int = 900
    min_order_notional: float = 200.0  # skip dust orders below this
    reconcile_qty_tolerance: float = 0.0  # shares; equities should match exactly
    reconcile_cash_tolerance: float = 5.0  # USD; fees/interest cause small drift
    circuit_breaker: CircuitBreakerConfig = CircuitBreakerConfig()


class FutuConfig(StrictModel):
    host: str = "127.0.0.1"
    port: int = 11111
    trd_env: Literal["SIMULATE", "REAL"] = "SIMULATE"
    security_firm: str = "FUTUSECURITIES"
    unlock_password_env: str = "FUTU_UNLOCK_PWD"


class BrokerConfig(StrictModel):
    kind: Literal["sim", "futu"] = "sim"
    futu: FutuConfig = FutuConfig()


class StrategyStage(str, enum.Enum):
    """Quarantine ramp for new strategies. paper: signals logged, no real orders.
    small: capped at SMALL_STAGE_FRACTION of equity. full: capital_fraction applies."""

    PAPER = "paper"
    SMALL = "small"
    FULL = "full"


SMALL_STAGE_FRACTION = 0.05


class StrategyConfig(StrictModel):
    name: str
    kind: Literal["qlib", "momentum", "rsi", "ma_cross", "bollinger"] = "momentum"
    stage: StrategyStage = StrategyStage.PAPER
    capital_fraction: float = Field(default=1.0, gt=0.0, le=1.0)
    params: dict = Field(default_factory=dict)


class TelegramConfig(StrictModel):
    token_env: str = "FINORA_TG_TOKEN"
    chat_id_env: str = "FINORA_TG_CHAT"


class EmailConfig(StrictModel):
    smtp_host: str = ""
    smtp_port: int = 587
    from_addr: str = ""
    to_addr: str = ""
    password_env: str = "FINORA_SMTP_PWD"


class OpsConfig(StrictModel):
    log_dir: Path = Path("logs")
    state_dir: Path = Path("state")
    reports_dir: Path = Path("reports")
    backtests_dir: Path = Path("artifacts/backtests")
    notifier: Literal["stdout", "telegram", "email"] = "stdout"
    telegram: TelegramConfig = TelegramConfig()
    email: EmailConfig = EmailConfig()


class Settings(StrictModel):
    data: DataConfig = DataConfig()
    universe: UniverseConfig = UniverseConfig()
    risk: RiskConfig = RiskConfig()
    broker: BrokerConfig = BrokerConfig()
    strategies: list[StrategyConfig] = Field(default_factory=list)
    ops: OpsConfig = OpsConfig()

    @classmethod
    def load(cls, config_dir: Path | str | None = None) -> "Settings":
        """Load settings from a config directory (default: $FINORA_CONFIG or ./config)."""
        root = Path(config_dir or os.environ.get("FINORA_CONFIG", "config"))
        if not root.is_dir():
            raise ConfigError(f"config directory not found: {root.resolve()}")
        payload: dict = {}
        sections = {
            "data": "data.yaml",
            "universe": "universe.yaml",
            "risk": "risk.yaml",
            "broker": "broker.yaml",
            "ops": "ops.yaml",
        }
        for key, filename in sections.items():
            doc = _read_yaml(root / filename)
            if doc is not None:
                payload[key] = doc
        strategies_doc = _read_yaml(root / "strategies.yaml")
        if strategies_doc is not None:
            if not isinstance(strategies_doc, dict) or "strategies" not in strategies_doc:
                raise ConfigError("strategies.yaml must contain a top-level 'strategies' list")
            payload["strategies"] = strategies_doc["strategies"]
        try:
            return cls.model_validate(payload)
        except ValueError as exc:
            raise ConfigError(f"invalid configuration under {root}: {exc}") from exc


def _read_yaml(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        doc = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ConfigError(f"malformed YAML in {path}: {exc}") from exc
    if doc is None:
        return None
    if not isinstance(doc, dict):
        raise ConfigError(f"{path} must contain a YAML mapping")
    return doc
