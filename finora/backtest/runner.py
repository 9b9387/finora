"""L3 backtest runner: vectorized momentum walk-forward and qlib model backtests."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from finora.core.config import Settings, StrategyConfig
from finora.core.errors import ConfigError, DataError
from finora.core.log import get_logger
from finora.strategy.base import PriceLoader

from finora.backtest.report import compute_metrics, save_backtest_artifact

log = get_logger(__name__)

DEFAULT_WINDOW_DAYS = 730  # default backtest span: last 2 years of data


def _build_price_loader(settings: Settings) -> PriceLoader:
    from finora.data.store import MarketStore  # lazy cross-module import

    try:
        store = MarketStore(settings.data)
    except TypeError:
        store = MarketStore(settings)
    return store.get_prices


def run_backtest(
    settings: Settings,
    cfg: StrategyConfig,
    start: date | None = None,
    end: date | None = None,
    cost_bps: float = 15.0,
    price_loader: PriceLoader | None = None,
    out_root: Path = Path("artifacts/backtests"),
) -> dict:
    """Backtest one strategy config and persist an artifact; returns the metrics dict.

    price_loader / out_root exist for dependency injection (tests); production
    callers use the defaults.
    """
    extra_snapshot: dict = {}
    if cfg.kind == "momentum":
        returns = _run_momentum(settings, cfg, start, end, cost_bps, price_loader)
    elif cfg.kind == "qlib":
        returns = _run_qlib(settings, cfg, start, end)
    elif cfg.kind in TECHNICAL_KINDS:
        returns, extra_snapshot = _run_technical(
            settings, cfg, start, end, cost_bps, price_loader
        )
    else:
        raise ConfigError(f"unknown strategy kind {cfg.kind!r} for backtest of '{cfg.name}'")

    metrics = compute_metrics(returns)
    snapshot = {
        "name": cfg.name,
        "kind": str(cfg.kind),
        "params": cfg.params,
        "cost_bps": cost_bps,
        "start": str(start) if start else None,
        "end": str(end) if end else None,
        **extra_snapshot,
    }
    out_dir = save_backtest_artifact(cfg.name, metrics, snapshot, returns, out_root=out_root)
    log.info("backtest_done", strategy=cfg.name, out_dir=str(out_dir), **metrics)
    return metrics


def _run_momentum(
    settings: Settings,
    cfg: StrategyConfig,
    start: date | None,
    end: date | None,
    cost_bps: float,
    price_loader: PriceLoader | None,
) -> pd.Series:
    """Daily walk-forward: weights from data <= d earn next-day adjusted returns.

    The return matrix is fully vectorized; only the top-k selection loops daily.
    """
    params = cfg.params
    lookback = int(params.get("lookback_days", 126))
    top_k = int(params.get("top_k", 20))
    universe: list[str] | None = params.get("universe")

    loader = price_loader or _build_price_loader(settings)
    bars = loader(universe, None, end)
    if bars is None or bars.empty:
        raise DataError(
            f"no price data available to backtest '{cfg.name}'; run `finora etl` first"
        )
    if end is not None:
        bars = bars[bars["date"] <= pd.Timestamp(end)]  # lookahead guard
    if bars.empty:
        raise DataError(f"no price data at or before {end} for backtest of '{cfg.name}'")

    adj = (
        bars.assign(adj_close=bars["close"] * bars["factor"])
        .pivot(index="date", columns="symbol", values="adj_close")
        .sort_index()
    )
    all_dates = adj.index
    end_ts = pd.Timestamp(end) if end is not None else all_dates[-1]
    start_ts = (
        pd.Timestamp(start) if start is not None else end_ts - pd.Timedelta(days=DEFAULT_WINDOW_DAYS)
    )
    trade_dates = all_dates[(all_dates >= start_ts) & (all_dates <= end_ts)]
    if len(trade_dates) < 2:
        log.warning("backtest_window_too_short", strategy=cfg.name, n_dates=len(trade_dates))
        return pd.Series(dtype=float, name="return")

    # Trailing total return over the lookback window, aligned so mom.loc[d]
    # only uses data at or before d.
    momentum = adj / adj.shift(lookback - 1) - 1.0
    observations = adj.notna().rolling(lookback, min_periods=1).sum()
    valid = observations >= 0.8 * lookback
    daily_returns = adj.pct_change(fill_method=None)

    cost_rate = cost_bps / 1e4
    prev_weights = pd.Series(0.0, index=adj.columns)
    port_returns: list[float] = []
    port_dates: list[pd.Timestamp] = []
    for i, day in enumerate(trade_dates[:-1]):
        scores = momentum.loc[day].where(valid.loc[day]).replace([np.inf, -np.inf], np.nan)
        scores = scores.dropna()
        weights = pd.Series(0.0, index=adj.columns)
        if not scores.empty:
            weights.loc[scores.nlargest(top_k).index] = 1.0 / top_k
        next_day = trade_dates[i + 1]
        realized = daily_returns.loc[next_day].fillna(0.0)
        turnover = float((weights - prev_weights).abs().sum())
        port_returns.append(float((weights * realized).sum()) - turnover * cost_rate)
        port_dates.append(next_day)
        prev_weights = weights

    return pd.Series(
        port_returns, index=pd.DatetimeIndex(port_dates, name="date"), name="return"
    )


TECHNICAL_KINDS = ("rsi", "ma_cross", "bollinger")


def _run_technical(
    settings: Settings,
    cfg: StrategyConfig,
    start: date | None,
    end: date | None,
    cost_bps: float,
    price_loader: PriceLoader | None,
) -> tuple[pd.Series, dict]:
    """Replay a single-instrument technical rule over the window.

    Indicators warm up on all history before the window; path-dependent rules
    start flat at the window start. Weight set on day d earns day d+1's
    adjusted return; each weight change pays cost_bps on the traded fraction.
    """
    from finora.strategy.base import build_strategy
    from finora.strategy.technical import adj_close_series

    loader = price_loader or _build_price_loader(settings)
    strat = build_strategy(cfg, settings, loader)

    bars = loader([strat.symbol], None, end)
    if bars is None or bars.empty:
        raise DataError(
            f"no price data for {strat.symbol} to backtest '{cfg.name}'; run `finora etl` first"
        )
    if end is not None:
        bars = bars[bars["date"] <= pd.Timestamp(end)]  # lookahead guard
    adj = adj_close_series(bars, strat.symbol)
    if adj.empty:
        raise DataError(f"no usable {strat.symbol} closes for backtest of '{cfg.name}'")

    end_ts = pd.Timestamp(end) if end is not None else adj.index[-1]
    start_ts = (
        pd.Timestamp(start) if start is not None else end_ts - pd.Timedelta(days=DEFAULT_WINDOW_DAYS)
    )
    in_window = pd.Series((adj.index >= start_ts) & (adj.index <= end_ts), index=adj.index)
    if int(in_window.sum()) < 2:
        log.warning("backtest_window_too_short", strategy=cfg.name, n_dates=int(in_window.sum()))
        return pd.Series(dtype=float, name="return"), {"symbol": strat.symbol, "trades": []}

    weights, trades = strat.window_weights(adj, in_window)
    # artifact-friendly trade dates: plain YYYY-MM-DD instead of timestamps
    trades = [{**t, "date": pd.Timestamp(t["date"]).date().isoformat()} for t in trades]
    daily_returns = adj[in_window].pct_change(fill_method=None).fillna(0.0)
    cost_rate = cost_bps / 1e4
    turnover = weights.diff().abs().fillna(weights.abs())
    strat_returns = (weights.shift(1) * daily_returns - turnover.shift(1) * cost_rate).iloc[1:]
    strat_returns.name = "return"
    strat_returns.index.name = "date"
    return strat_returns, {"symbol": strat.symbol, "trades": trades}


def _run_qlib(
    settings: Settings,
    cfg: StrategyConfig,
    start: date | None,
    end: date | None,
) -> pd.Series:
    """Predict over the test segment and run qlib's TopkDropout daily backtest.

    Trading costs come from qlib's own exchange model on this path.
    """
    from finora.strategy.qlib_strategy import ensure_qlib_init, load_artifacts

    params = cfg.params
    top_k = int(params.get("top_k", 30))
    n_drop = int(params.get("n_drop", 0))
    model_dir = Path(params.get("model_dir") or Path("artifacts/models") / cfg.name)

    ensure_qlib_init(settings)
    model, dataset_config, _meta = load_artifacts(model_dir, cfg.name)

    from qlib.contrib.evaluate import backtest_daily
    from qlib.contrib.strategy import TopkDropoutStrategy
    from qlib.utils import init_instance_by_config

    segment = dataset_config.get("segments", {}).get("test")
    if not segment:
        raise ConfigError(
            f"dataset_config.json under {model_dir} has no 'test' segment; "
            f"re-run `finora train {cfg.name}`"
        )
    seg_start = str(start) if start is not None else str(segment[0])
    seg_end = str(end) if end is not None else str(segment[1])

    handler_cfg = dict(dataset_config["handler"])
    handler_kwargs = dict(handler_cfg.get("kwargs", {}))
    if str(handler_kwargs.get("end_time", "")) < seg_end:
        handler_kwargs["end_time"] = seg_end
    handler_cfg["kwargs"] = handler_kwargs

    dataset = init_instance_by_config(
        {
            "class": "DatasetH",
            "module_path": "qlib.data.dataset",
            "kwargs": {"handler": handler_cfg, "segments": {"test": (seg_start, seg_end)}},
        }
    )
    predictions = model.predict(dataset, segment="test")
    if isinstance(predictions, pd.DataFrame):
        predictions = predictions.iloc[:, 0]
    if predictions is None or len(predictions) == 0:
        raise DataError(
            f"model for '{cfg.name}' produced no predictions over [{seg_start}, {seg_end}]; "
            "check the qlib data store (`finora etl`) and the trained segments"
        )

    strategy = TopkDropoutStrategy(topk=top_k, n_drop=n_drop, signal=predictions)
    # qlib defaults the benchmark to CSI300 (SH000300); point it at a symbol
    # that exists in the local US store instead.
    benchmark = str(params.get("benchmark", "SPY"))
    report, _positions = backtest_daily(
        start_time=seg_start, end_time=seg_end, strategy=strategy, benchmark=benchmark
    )
    returns = report["return"].astype(float)
    if "cost" in report.columns:
        returns = returns - report["cost"].astype(float)
    returns.name = "return"
    returns.index.name = "date"
    return returns
