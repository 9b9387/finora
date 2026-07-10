"""Backtest metrics and artifact persistence. Pure pandas/numpy; no qlib."""
from __future__ import annotations

import json
import math
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from finora.core.log import get_logger
from finora.core.models import utc_now

log = get_logger(__name__)

_METRIC_KEYS = (
    "total_return",
    "annualized_return",
    "annualized_vol",
    "sharpe",
    "max_drawdown",
    "calmar",
)


def compute_metrics(returns: pd.Series, periods_per_year: int = 252) -> dict:
    """Standard performance metrics from a daily return series (rf = 0).

    max_drawdown is reported as a negative float (0.0 if equity never dips).
    Series shorter than 2 observations return zeros.
    """
    returns = returns.dropna().astype(float)
    n = int(len(returns))
    if n < 2:
        metrics = {key: 0.0 for key in _METRIC_KEYS}
        metrics["n_days"] = n
        return metrics

    equity = (1.0 + returns).cumprod()
    total_return = float(equity.iloc[-1] - 1.0)
    annualized_return = float((1.0 + total_return) ** (periods_per_year / n) - 1.0)

    std = float(returns.std(ddof=1))
    mean = float(returns.mean())
    annualized_vol = std * math.sqrt(periods_per_year)
    if std > 0:
        sharpe = mean / std * math.sqrt(periods_per_year)
    elif mean != 0:
        sharpe = math.copysign(math.inf, mean)
    else:
        sharpe = 0.0

    drawdown = equity / equity.cummax() - 1.0
    max_drawdown = float(min(drawdown.min(), 0.0))
    calmar = annualized_return / abs(max_drawdown) if max_drawdown < 0 else 0.0

    return {
        "total_return": total_return,
        "annualized_return": annualized_return,
        "annualized_vol": annualized_vol,
        "sharpe": float(sharpe),
        "max_drawdown": max_drawdown,
        "calmar": float(calmar),
        "n_days": n,
    }


def _jsonable(obj: object) -> object:
    """Coerce numpy/path/date values to strict-JSON-safe types (non-finite -> None)."""
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, (np.floating, float)):
        value = float(obj)
        return value if math.isfinite(value) else None
    if isinstance(obj, (np.integer, int)):
        return int(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    return obj


def save_backtest_artifact(
    name: str,
    metrics: dict,
    config_snapshot: dict,
    returns: pd.Series,
    out_root: Path = Path("artifacts/backtests"),
) -> Path:
    """Write metrics.json, config.json and returns.csv under
    <out_root>/<name>_<last-return-date %Y%m%d>/ and return that directory."""
    if len(returns) > 0:
        stamp = pd.Timestamp(returns.index[-1]).strftime("%Y%m%d")
    else:
        stamp = utc_now().strftime("%Y%m%d")
    out_dir = Path(out_root) / f"{name}_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "metrics.json").write_text(json.dumps(_jsonable(metrics), indent=2))
    (out_dir / "config.json").write_text(json.dumps(_jsonable(config_snapshot), indent=2))
    returns.rename("return").to_csv(out_dir / "returns.csv", index_label="date")

    log.info("backtest_artifact_saved", name=name, out_dir=str(out_dir))
    return out_dir
