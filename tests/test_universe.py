"""Tests for finora.data.universe (network access mocked)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from finora.core.config import DataConfig, Settings, UniverseConfig
from finora.core.errors import DataError
from finora.data import universe


def make_settings(tmp_path: Path, symbols: list[str] | None = None) -> Settings:
    return Settings(
        data=DataConfig(data_dir=tmp_path / "data"),
        universe=UniverseConfig(symbols=symbols or []),
    )


class FakeResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


SP500_HTML = """
<html><body>
<table>
  <tr><th>Symbol</th><th>Security</th></tr>
  <tr><td>MMM</td><td>3M</td></tr>
  <tr><td>BRK.B</td><td>Berkshire Hathaway</td></tr>
  <tr><td>BF.B</td><td>Brown-Forman</td></tr>
  <tr><td>AAPL</td><td>Apple</td></tr>
</table>
</body></html>
"""


def test_normalize_symbol_class_shares() -> None:
    assert universe.normalize_symbol("BRK.B") == "BRK-B"
    assert universe.normalize_symbol(" aapl ") == "AAPL"
    assert universe.normalize_symbol("BF.B") == "BF-B"


def test_fetch_sp500_symbols_parses_and_normalizes(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def fake_get(url: str, headers: dict, timeout: int) -> FakeResponse:
        calls.append({"url": url, "headers": headers})
        return FakeResponse(SP500_HTML)

    monkeypatch.setattr(universe.requests, "get", fake_get)
    symbols = universe.fetch_sp500_symbols()
    assert symbols == ["AAPL", "BF-B", "BRK-B", "MMM"]
    assert "Mozilla" in calls[0]["headers"]["User-Agent"]


def test_fetch_sp500_symbols_raises_dataerror_on_http_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        universe.requests, "get", lambda *a, **k: FakeResponse("boom", status_code=503)
    )
    with pytest.raises(DataError):
        universe.fetch_sp500_symbols()


def test_fetch_sp500_symbols_raises_dataerror_on_missing_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = "<html><body><table><tr><th>Foo</th></tr><tr><td>x</td></tr></table></body></html>"
    monkeypatch.setattr(universe.requests, "get", lambda *a, **k: FakeResponse(html))
    with pytest.raises(DataError):
        universe.fetch_sp500_symbols()


def test_snapshot_and_load_roundtrip(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    path = universe.snapshot_universe(settings, symbols=["AAPL", "MSFT", "BRK-B"])
    assert path.name == f"{date.today():%Y-%m-%d}.csv"
    assert path.parent == settings.data.universe_dir
    assert path.read_text().splitlines()[0] == "symbol"  # header present
    assert universe.load_universe(settings) == ["AAPL", "MSFT", "BRK-B"]


def test_snapshot_uses_settings_symbols_over_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings(tmp_path, symbols=["NVDA", "TSLA"])

    def explode() -> list[str]:
        raise AssertionError("must not fetch when settings.universe.symbols is set")

    monkeypatch.setattr(universe, "fetch_sp500_symbols", explode)
    path = universe.snapshot_universe(settings)
    assert "NVDA" in path.read_text()


def test_snapshot_falls_back_to_fetch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(universe, "fetch_sp500_symbols", lambda: ["AAA", "BBB"])
    path = universe.snapshot_universe(settings)
    assert universe.load_universe(settings) == ["AAA", "BBB"]
    assert path.exists()


def test_load_universe_prefers_config_symbols(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, symbols=["brk.b", "AAPL"])
    assert universe.load_universe(settings) == ["BRK-B", "AAPL"]


def test_load_universe_picks_newest_snapshot(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    universe_dir = settings.data.universe_dir
    universe_dir.mkdir(parents=True)
    (universe_dir / "2024-01-01.csv").write_text("symbol\nOLD\n")
    (universe_dir / "2024-06-01.csv").write_text("symbol\nNEW\n")
    assert universe.load_universe(settings) == ["NEW"]


def test_load_universe_without_snapshot_raises(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    with pytest.raises(DataError, match="finora universe"):
        universe.load_universe(settings)
