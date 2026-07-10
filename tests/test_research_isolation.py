"""finora.research must stay strictly isolated from the trading path."""
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

from finora.core.config import DataConfig, OpsConfig, Settings

REPO_ROOT = Path(__file__).resolve().parents[1]
RESEARCH_DIR = REPO_ROOT / "finora" / "research"

FORBIDDEN_MODULES = tuple(
    "finora." + name for name in ("execution", "risk", "strategy", "backtest")
)


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        data=DataConfig(data_dir=tmp_path / "data"),
        ops=OpsConfig(
            log_dir=tmp_path / "logs",
            state_dir=tmp_path / "state",
            reports_dir=tmp_path / "reports",
        ),
    )


def test_source_never_mentions_trading_modules():
    py_files = list(RESEARCH_DIR.rglob("*.py"))
    assert py_files, f"no python files found under {RESEARCH_DIR}"
    for path in py_files:
        text = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_MODULES:
            assert forbidden not in text, f"{path} references {forbidden}"


def test_import_pulls_in_no_trading_modules():
    def loaded_trading_modules() -> set[str]:
        return {
            m
            for m in sys.modules
            if any(m == f or m.startswith(f + ".") for f in FORBIDDEN_MODULES)
        }

    before = loaded_trading_modules()
    sys.modules.pop("finora.research.report", None)
    sys.modules.pop("finora.research", None)
    importlib.import_module("finora.research.report")
    assert loaded_trading_modules() == before


def test_report_written_with_empty_store(tmp_path):
    from finora.research.report import generate_research_report

    settings = make_settings(tmp_path)
    path = generate_research_report("aapl", settings)

    assert path == settings.ops.reports_dir / "research" / path.name
    assert path.name.startswith("AAPL-")
    assert path.exists()

    text = path.read_text(encoding="utf-8")
    assert "research aid only" in text
    assert "Finora never trades on this output" in text
    for heading in ("## Performance", "## Analyst Notes", "### Thesis", "### Catalysts",
                    "### Risks"):
        assert heading in text


def test_performance_metrics_from_fake_store(tmp_path, monkeypatch):
    import pandas as pd

    dates = pd.bdate_range("2025-06-02", periods=200)
    closes = [100.0 * (1.0 + 0.001) ** i for i in range(200)]
    bars = pd.DataFrame(
        {
            "symbol": ["AAPL"] * 200,
            "date": dates,
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [1e6] * 200,
            "factor": [1.0] * 200,
        }
    )

    mod = types.ModuleType("finora.data.store")

    class MarketStore:
        def __init__(self, cfg):
            self.cfg = cfg

        def read_bars(self, symbols):
            return bars[bars["symbol"].isin(symbols)]

    mod.MarketStore = MarketStore  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "finora.data.store", mod)

    from finora.research.report import generate_research_report

    path = generate_research_report("AAPL", make_settings(tmp_path))
    text = path.read_text(encoding="utf-8")

    assert "last close:" in text
    assert "1m: total return" in text
    assert "6m max drawdown:" in text
    assert "No local market data" not in text
