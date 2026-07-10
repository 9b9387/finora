"""ops.health: defensive report assembly across missing/present components."""
from __future__ import annotations

import json
import sys
import types
from datetime import date, timedelta
from pathlib import Path

import pytest

from finora.core.config import DataConfig, OpsConfig, Settings
from finora.ops.health import generate_health_report, write_health_report

AS_OF = date(2026, 6, 10)  # a regular NYSE Wednesday


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        data=DataConfig(data_dir=tmp_path / "data"),
        ops=OpsConfig(
            log_dir=tmp_path / "logs",
            state_dir=tmp_path / "state",
            reports_dir=tmp_path / "reports",
        ),
    )


def install_fake_store(monkeypatch, latest: date | None) -> None:
    """Inject a fake finora.data.store (sibling module is built concurrently)."""
    mod = types.ModuleType("finora.data.store")

    class MarketStore:
        def __init__(self, cfg):
            self.cfg = cfg

        def latest_date(self):
            return latest

    mod.MarketStore = MarketStore  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "finora.data.store", mod)


def block_risk_modules(monkeypatch) -> None:
    """Force the file-based fallbacks regardless of sibling-module progress."""
    monkeypatch.setitem(sys.modules, "finora.risk.kill_switch", None)
    monkeypatch.setitem(sys.modules, "finora.risk.circuit_breaker", None)


def last_nyse_session(as_of: date) -> date:
    import pandas_market_calendars as mcal

    cal = mcal.get_calendar("XNYS")
    sched = cal.schedule(start_date=as_of - timedelta(days=14), end_date=as_of)
    return sched.index[-1].date()


class TestFreshCheckout:
    def test_report_renders_with_nothing_on_disk(self, tmp_path):
        settings = make_settings(tmp_path)
        report = generate_health_report(settings, AS_OF)

        assert "# Finora Health Report — 2026-06-10" in report
        for header in ("## Data freshness", "## Safety state", "## Today's orders", "## Signals"):
            assert header in report
        assert "no data" in report or "unavailable" in report
        assert "kill switch:" in report
        assert "circuit breaker:" in report
        assert "no orders file for 2026-06-10" in report
        assert "no signals file for 2026-06-10" in report
        assert "Generated at " in report

    def test_write_health_report_creates_file(self, tmp_path):
        settings = make_settings(tmp_path)
        path = write_health_report(settings, AS_OF)
        assert path == settings.ops.log_dir / "health" / "2026-06-10.md"
        assert path.exists()
        assert "# Finora Health Report" in path.read_text()


class TestDataFreshness:
    def test_fresh_store_is_ok(self, tmp_path, monkeypatch):
        session = last_nyse_session(AS_OF)
        install_fake_store(monkeypatch, latest=session)
        report = generate_health_report(make_settings(tmp_path), AS_OF)
        assert f"latest bar date: {session:%Y-%m-%d}" in report
        assert "status: OK" in report
        assert "STALE" not in report

    def test_old_store_is_flagged_stale(self, tmp_path, monkeypatch):
        install_fake_store(monkeypatch, latest=date(2026, 4, 1))
        report = generate_health_report(make_settings(tmp_path), AS_OF)
        assert "latest bar date: 2026-04-01" in report
        assert "STALE" in report

    def test_empty_store_shows_no_data(self, tmp_path, monkeypatch):
        install_fake_store(monkeypatch, latest=None)
        report = generate_health_report(make_settings(tmp_path), AS_OF)
        assert "no data in market store" in report


class TestSafetyState:
    def test_kill_file_marks_switch_active(self, tmp_path, monkeypatch):
        block_risk_modules(monkeypatch)
        settings = make_settings(tmp_path)
        settings.ops.state_dir.mkdir(parents=True)
        (settings.ops.state_dir / "kill_switch.json").write_text("{}")
        report = generate_health_report(settings, AS_OF)
        assert "kill switch: ACTIVE" in report

    def test_breaker_state_file_is_rendered(self, tmp_path, monkeypatch):
        block_risk_modules(monkeypatch)
        settings = make_settings(tmp_path)
        settings.ops.state_dir.mkdir(parents=True)
        (settings.ops.state_dir / "circuit_breaker.json").write_text(
            json.dumps({"state": "HALT_NEW", "drawdown": -0.06})
        )
        report = generate_health_report(settings, AS_OF)
        assert "HALT_NEW" in report

    def test_no_state_files_reads_inactive(self, tmp_path, monkeypatch):
        block_risk_modules(monkeypatch)
        report = generate_health_report(make_settings(tmp_path), AS_OF)
        assert "kill switch: inactive" in report
        assert "circuit breaker: inactive (no state file)" in report


class TestOrdersAndSignals:
    def test_order_counts_and_reject_reasons(self, tmp_path):
        settings = make_settings(tmp_path)
        orders_dir = settings.ops.state_dir / "orders"
        orders_dir.mkdir(parents=True)
        rows = [
            {"instrument": "MSFT", "status": "FILLED"},
            {"instrument": "NVDA", "status": "FILLED"},
            {"instrument": "AAPL", "status": "REJECTED", "reject_reason": "notional cap exceeded"},
            {"instrument": "TSLA", "status": "CANCELLED"},
        ]
        (orders_dir / "2026-06-10.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\nnot-json-garbage\n"
        )
        report = generate_health_report(settings, AS_OF)
        assert "total: 4" in report
        assert "FILLED: 2" in report
        assert "REJECTED: 1" in report
        assert "CANCELLED: 1" in report
        assert "AAPL: notional cap exceeded" in report

    def test_signal_counts_per_strategy(self, tmp_path):
        settings = make_settings(tmp_path)
        signals_dir = settings.ops.state_dir / "signals"
        signals_dir.mkdir(parents=True)
        rows = [
            {"instrument": "AAPL", "source": "momentum_baseline"},
            {"instrument": "MSFT", "source": "momentum_baseline"},
            {"instrument": "NVDA", "source": "qlib_lgb_alpha158"},
        ]
        (signals_dir / "2026-06-10.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n"
        )
        report = generate_health_report(settings, AS_OF)
        assert "momentum_baseline: 2" in report
        assert "qlib_lgb_alpha158: 1" in report


def test_report_never_raises_even_if_a_section_breaks(tmp_path, monkeypatch):
    import finora.ops.health as health

    def boom(settings, as_of):
        raise RuntimeError("section exploded")

    monkeypatch.setattr(health, "_data_freshness_section", boom)
    report = generate_health_report(make_settings(tmp_path), AS_OF)
    assert "section unavailable: section exploded" in report
    assert "## Signals" in report  # later sections still render


@pytest.mark.parametrize("as_of", [None, AS_OF])
def test_default_as_of_accepted(tmp_path, as_of):
    report = generate_health_report(make_settings(tmp_path), as_of)
    assert report.startswith("# Finora Health Report")
