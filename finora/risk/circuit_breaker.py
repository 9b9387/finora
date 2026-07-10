"""Tiered daily-drawdown circuit breaker with file-persisted state.

Intraday the breaker can only escalate — a recovering P&L never relaxes the
state within the same date. A new date starts back at NORMAL, except FLATTEN,
which is sticky across dates until a human calls reset().
"""
from __future__ import annotations

import enum
import json
from datetime import date
from pathlib import Path

from finora.core.config import CircuitBreakerConfig
from finora.core.log import get_logger

logger = get_logger(__name__)


class BreakerState(str, enum.Enum):
    NORMAL = "NORMAL"
    REDUCED = "REDUCED"
    HALT_NEW = "HALT_NEW"
    FLATTEN = "FLATTEN"


_SEVERITY: dict[BreakerState, int] = {
    BreakerState.NORMAL: 0,
    BreakerState.REDUCED: 1,
    BreakerState.HALT_NEW: 2,
    BreakerState.FLATTEN: 3,
}

_SIZE_MULTIPLIER: dict[BreakerState, float] = {
    BreakerState.NORMAL: 1.0,
    BreakerState.REDUCED: 0.5,
    BreakerState.HALT_NEW: 0.0,
    BreakerState.FLATTEN: 0.0,
}


class CircuitBreaker:
    def __init__(self, cfg: CircuitBreakerConfig, state_path: Path) -> None:
        self._cfg = cfg
        self._path = Path(state_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def evaluate(self, day_pnl_pct: float, on_date: date) -> BreakerState:
        """Fold today's P&L tier into the persisted state and return the result."""
        tier = self._tier(day_pnl_pct)
        prior = self.current(on_date)
        state = tier if _SEVERITY[tier] > _SEVERITY[prior] else prior
        self._path.write_text(
            json.dumps({"date": on_date.isoformat(), "state": state.value})
        )
        if state is not prior:
            logger.warning(
                "circuit breaker escalated",
                state=state.value,
                day_pnl_pct=day_pnl_pct,
                date=on_date.isoformat(),
            )
        return state

    def current(self, on_date: date) -> BreakerState:
        """Persisted state for on_date. FLATTEN is sticky across dates;
        any other stored state applies only to its own date."""
        record = self._load()
        if record is None:
            return BreakerState.NORMAL
        stored_date, state = record
        if state is BreakerState.FLATTEN:
            return BreakerState.FLATTEN
        if stored_date != on_date.isoformat():
            return BreakerState.NORMAL
        return state

    def reset(self) -> None:
        """Manual human action: clear persisted state (including sticky FLATTEN)."""
        self._path.unlink(missing_ok=True)
        logger.warning("circuit breaker reset", path=str(self._path))

    @staticmethod
    def size_multiplier(state: BreakerState) -> float:
        return _SIZE_MULTIPLIER[state]

    def _tier(self, day_pnl_pct: float) -> BreakerState:
        if day_pnl_pct <= self._cfg.flatten_at:
            return BreakerState.FLATTEN
        if day_pnl_pct <= self._cfg.halt_new_at:
            return BreakerState.HALT_NEW
        if day_pnl_pct <= self._cfg.reduce_at:
            return BreakerState.REDUCED
        return BreakerState.NORMAL

    def _load(self) -> tuple[str, BreakerState] | None:
        if not self._path.exists():
            return None
        try:
            doc = json.loads(self._path.read_text())
            return str(doc["date"]), BreakerState(doc["state"])
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            logger.warning("unreadable circuit breaker state file", path=str(self._path))
            return None
