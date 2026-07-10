"""Finora command-line interface (`finora ...`, wired via pyproject scripts).

Only stages that have landed on main are wired here: data (L1),
strategy/research (L2), and backtest (L3). `trade` and `health` become
real once the risk/execution stage is reviewed and merged.
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import typer

from finora.core.config import Settings, StrategyConfig
from finora.core.errors import FinoraError

app = typer.Typer(
    name="finora",
    help="Personal quant trading system: US equities, daily rebalance.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)

_CONFIG_OPTION = typer.Option(
    None, "--config", "-c", help="Config directory (default: $FINORA_CONFIG or ./config)."
)

_PENDING_STAGE_MSG = (
    "not available yet: the risk/execution stage is pending review "
    "(parked on the draft/risk-execution branch)."
)


def _load_settings(config_dir: Path | None) -> Settings:
    try:
        return Settings.load(config_dir)
    except FinoraError as exc:
        _fail(str(exc))


def _fail(message: str) -> typer.Exit:
    typer.secho(f"error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _strategy_configs(settings: Settings, name: str | None) -> list[StrategyConfig]:
    if not settings.strategies:
        _fail("no strategies configured (config/strategies.yaml)")
    if name is None:
        return list(settings.strategies)
    matches = [cfg for cfg in settings.strategies if cfg.name == name]
    if not matches:
        known = ", ".join(cfg.name for cfg in settings.strategies)
        _fail(f"unknown strategy {name!r} (configured: {known})")
    return matches


@app.command()
def universe(config: Path | None = _CONFIG_OPTION) -> None:
    """Snapshot current S&P 500 constituents into the universe store."""
    from finora.data.universe import load_universe, snapshot_universe

    settings = _load_settings(config)
    try:
        path = snapshot_universe(settings)
        symbols = load_universe(settings)
    except FinoraError as exc:
        _fail(str(exc))
    typer.echo(f"universe snapshot: {len(symbols)} symbols -> {path}")


@app.command()
def etl(
    config: Path | None = _CONFIG_OPTION,
    qlib: bool = typer.Option(False, "--qlib", help="Also convert the store to Qlib bin format."),
    full: bool = typer.Option(
        False, "--full",
        help="Refetch complete history for every symbol (re-applies provider adjustments).",
    ),
) -> None:
    """Incrementally fetch daily bars into the Parquet/DuckDB store."""
    from finora.data.etl import run_etl
    from finora.data.qlib_convert import convert_store

    settings = _load_settings(config)
    try:
        result = run_etl(settings, full_refresh=full)
    except FinoraError as exc:
        _fail(str(exc))
    typer.echo(
        f"etl: {result.rows_written} rows across {len(result.symbols_updated)} symbols"
        + (f", {len(result.symbols_rebuilt)} histories rebuilt" if result.symbols_rebuilt else "")
        + (f", {len(result.symbols_failed)} failed" if result.symbols_failed else "")
    )
    for issue in result.quality_issues:
        typer.secho(f"quality: {issue}", fg=typer.colors.YELLOW, err=True)
    if qlib:
        try:
            stats = convert_store(settings)
        except FinoraError as exc:
            _fail(str(exc))
        typer.echo(f"qlib convert: {stats}")


@app.command()
def signals(
    config: Path | None = _CONFIG_OPTION,
    strategy: str | None = typer.Option(None, "--strategy", "-s", help="Only this strategy."),
    as_of: str | None = typer.Option(None, "--as-of", help="Signal date (YYYY-MM-DD)."),
) -> None:
    """Generate signals from configured strategies (prints, never trades)."""
    from finora.data.store import MarketStore
    from finora.strategy.base import build_strategy

    settings = _load_settings(config)
    configs = _strategy_configs(settings, strategy)
    try:
        with MarketStore(settings.data) as store:
            signal_date = (
                date.fromisoformat(as_of) if as_of else store.latest_date() or date.today()
            )
            for cfg in configs:
                strat = build_strategy(cfg, settings, store.get_prices)
                sigs = strat.generate_signals(signal_date)
                typer.echo(f"{cfg.name} ({cfg.kind}, stage={cfg.stage}) as of {signal_date}:")
                if not sigs:
                    typer.echo("  (no signals)")
                for sig in sorted(sigs, key=lambda s: -s.target_weight):
                    typer.echo(
                        f"  {sig.instrument:<8} weight={sig.target_weight:+.4f} "
                        f"confidence={sig.confidence:.2f}"
                    )
    except FinoraError as exc:
        _fail(str(exc))


@app.command()
def train(
    strategy: str = typer.Argument(..., help="Name of a configured qlib strategy."),
    config: Path | None = _CONFIG_OPTION,
) -> None:
    """Train the Qlib model behind a configured strategy."""
    from finora.strategy.train import train_qlib_model

    settings = _load_settings(config)
    (cfg,) = _strategy_configs(settings, strategy)
    try:
        path = train_qlib_model(settings, cfg)
    except FinoraError as exc:
        _fail(str(exc))
    typer.echo(f"model artifacts -> {path}")


@app.command()
def backtest(
    config: Path | None = _CONFIG_OPTION,
    strategy: str | None = typer.Option(None, "--strategy", "-s", help="Only this strategy."),
    start: str | None = typer.Option(None, help="Start date (YYYY-MM-DD)."),
    end: str | None = typer.Option(None, help="End date (YYYY-MM-DD)."),
    cost_bps: float = typer.Option(15.0, help="Round-trip cost assumption in basis points."),
) -> None:
    """Walk-forward backtest; writes an artifact under artifacts/backtests/."""
    from finora.backtest.runner import run_backtest

    settings = _load_settings(config)
    start_d = date.fromisoformat(start) if start else None
    end_d = date.fromisoformat(end) if end else None
    for cfg in _strategy_configs(settings, strategy):
        try:
            metrics = run_backtest(settings, cfg, start=start_d, end=end_d, cost_bps=cost_bps)
        except FinoraError as exc:
            _fail(str(exc))
        summary = ", ".join(f"{k}={v:.4f}" if isinstance(v, float) else f"{k}={v}"
                            for k, v in metrics.items())
        typer.echo(f"{cfg.name}: {summary}")


@app.command()
def report(
    ticker: str = typer.Argument(..., help="Ticker to write a research report for."),
    config: Path | None = _CONFIG_OPTION,
) -> None:
    """Write a data-driven research report (research aid only, never traded on)."""
    from finora.research.report import generate_research_report

    settings = _load_settings(config)
    try:
        path = generate_research_report(ticker, settings)
    except FinoraError as exc:
        _fail(str(exc))
    typer.echo(f"research report -> {path}")


@app.command()
def serve(
    config: Path | None = _CONFIG_OPTION,
    host: str = typer.Option("127.0.0.1", help="Bind host."),
    port: int = typer.Option(8000, help="Bind port."),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes."),
) -> None:
    """Serve the local data API (the frontend lives in webapp/)."""
    try:
        import uvicorn

        from finora.web.app import create_app
    except ImportError:
        _fail("web dependencies not installed: uv sync --extra web")
    settings = _load_settings(config)  # fail fast on a bad config dir
    typer.echo(f"data API on http://{host}:{port} (docs at /api/docs)")
    if reload:
        # reload mode re-imports by string; the config dir travels via env
        if config is not None:
            os.environ["FINORA_CONFIG"] = str(config)
        uvicorn.run(
            "finora.web.app:create_app", factory=True, host=host, port=port, reload=True
        )
    else:
        uvicorn.run(create_app(settings), host=host, port=port)


@app.command()
def trade(config: Path | None = _CONFIG_OPTION) -> None:
    """Daily trading cycle (pending the risk/execution stage)."""
    _fail(f"trade is {_PENDING_STAGE_MSG}")


@app.command()
def health(config: Path | None = _CONFIG_OPTION) -> None:
    """Daily health report (pending the risk/execution stage)."""
    _fail(f"health is {_PENDING_STAGE_MSG}")


if __name__ == "__main__":
    app()
