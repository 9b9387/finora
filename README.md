# Finora

Personal quantitative trading system for US equities with daily rebalancing.

## Architecture

Six layers; every order must pass the risk gate — no strategy can bypass it.

| Layer | Module | Role |
|-------|--------|------|
| L1 Data | `finora/data/` | OpenBB ETL → Parquet/DuckDB store → Qlib bin format. Live quotes come from the broker feed, never OpenBB. |
| L2 Strategy | `finora/strategy/` | Qlib models emit `Signal` objects (the core interface). |
| — Research | `finora/research/` | TradingAgents produces offline research reports only — no import path into execution. |
| L3 Validation | `finora/backtest/` | Qlib backtester for portfolio strategies; walk-forward splits, cost-aware metrics. |
| L4 Risk | `finora/risk/` | Independent pre-trade gate, tiered circuit breaker, reconciliation, kill switch, strategy quarantine. |
| L5 Execution | `finora/execution/` | Broker abstraction (Futu OpenAPI / sim), OMS state machine, rebalance order diffing. |
| L6 Ops | `finora/ops/` | Structured logging, alerting, daily health report. |

Core contracts live in `finora/core/` (`Signal`, `Order`, `Position`, `PortfolioState`, config models).

## Quickstart

```bash
uv sync --all-extras          # or plain `uv sync` for core only (heavy integrations are lazy)
uv run finora universe        # snapshot S&P 500 constituents
uv run finora etl             # fetch daily bars into data/parquet + DuckDB
uv run finora signals         # generate signals from configured strategies
uv run finora trade --dry-run # full daily cycle without submitting orders
uv run finora health          # daily health report
```

Configuration lives in `config/*.yaml`. Runtime state (positions book, breaker state,
kill switch) lives in `state/`; logs in `logs/`; both are gitignored.

## Daily cycle

After close: `etl` → `signals`. Before next open: `trade` runs
kill-switch check → circuit-breaker state → position reconciliation → quote staleness
check → targets from signals (scaled by quarantine stage) → order diff → risk gate →
OMS submission → fill tracking → health report.

## Broker

Futu/Moomoo OpenAPI via a locally running [OpenD gateway](https://openapi.futunn.com/futu-api-doc/en/).
`config/broker.yaml` defaults to `kind: sim` (in-process simulator) and Futu `trd_env: SIMULATE`
(paper). Switching to live is a deliberate config change, never a default.

## TradingAgents

LLM strategies cannot be historically backtested (the model's training data leaks the
future), so [TradingAgents](https://github.com/TauricResearch/TradingAgents) is wired as a
research assistant only: `finora report TICKER` writes a markdown report under `reports/`.
Install it manually if wanted; `finora/research/` degrades to a data-driven template
without it and is forbidden (by test) from importing execution or risk modules.

## Development

```bash
uv run pytest            # test suite
uv run ruff check .      # lint
```

New strategies graduate: backtest artifact in `artifacts/backtests/` → `stage: paper` →
`stage: small` (5% capital) → `stage: full`, each promotion a manual config change.
