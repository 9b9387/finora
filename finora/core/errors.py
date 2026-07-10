"""Exception hierarchy. Layers raise these; the pipeline maps them to halts and alerts."""
from __future__ import annotations


class FinoraError(Exception):
    """Base for all Finora errors."""


class ConfigError(FinoraError):
    """Invalid or missing configuration."""


class DataError(FinoraError):
    """Data unavailable, malformed, or failed quality checks."""


class BrokerError(FinoraError):
    """Broker connectivity or order handling failure."""


class TradingHaltedError(FinoraError):
    """Trading blocked by kill switch, circuit breaker, or reconciliation failure.

    The pipeline treats this as: stop submitting orders, alert, require human action.
    """


class ReconciliationError(TradingHaltedError):
    """Internal book and broker account disagree beyond tolerance."""
