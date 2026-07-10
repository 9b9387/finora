"""CLI smoke tests: command wiring, config loading, and stage gating."""
from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from finora.cli import app

runner = CliRunner()


def make_config_dir(tmp_path: Path) -> Path:
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "data.yaml").write_text(f"data_dir: {tmp_path / 'data'}\n", encoding="utf-8")
    (cfg / "ops.yaml").write_text(
        f"log_dir: {tmp_path / 'logs'}\n"
        f"state_dir: {tmp_path / 'state'}\n"
        f"reports_dir: {tmp_path / 'reports'}\n",
        encoding="utf-8",
    )
    return cfg


def all_output(result) -> str:
    try:
        return result.output + result.stderr
    except ValueError:  # stderr merged into output on this click version
        return result.output


def test_help_lists_all_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("universe", "etl", "signals", "train", "backtest", "report",
                    "serve", "trade", "health"):
        assert command in result.output


def test_missing_config_dir_fails_cleanly(tmp_path):
    result = runner.invoke(app, ["report", "AAPL", "--config", str(tmp_path / "nope")])
    assert result.exit_code == 1
    assert "config directory not found" in all_output(result)


def test_report_writes_markdown(tmp_path):
    cfg = make_config_dir(tmp_path)
    result = runner.invoke(app, ["report", "aapl", "--config", str(cfg)])
    assert result.exit_code == 0, all_output(result)
    files = list((tmp_path / "reports" / "research").glob("AAPL-*.md"))
    assert len(files) == 1


def test_unknown_strategy_rejected(tmp_path):
    cfg = make_config_dir(tmp_path)
    (cfg / "strategies.yaml").write_text(
        "strategies:\n"
        "  - name: momentum_baseline\n"
        "    kind: momentum\n"
        "    stage: paper\n"
        "    capital_fraction: 1.0\n"
        "    params: {lookback_days: 5, top_k: 2}\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["signals", "--strategy", "nope", "--config", str(cfg)])
    assert result.exit_code == 1
    assert "unknown strategy" in all_output(result)


def test_trade_and_health_gated_until_risk_stage(tmp_path):
    cfg = make_config_dir(tmp_path)
    for command in ("trade", "health"):
        result = runner.invoke(app, [command, "--config", str(cfg)])
        assert result.exit_code == 1
        assert "risk/execution" in all_output(result)
