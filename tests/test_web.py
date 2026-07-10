"""API tests for finora.web against a tmp_path store."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from finora.core.config import DataConfig, Settings
from finora.web.app import create_app

# Consecutive NYSE trading days (no long gaps).
DAYS = [
    date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5),
    date(2024, 1, 8),
]


def write_partition(
    parquet_dir: Path,
    symbol: str,
    days: list[date],
    closes: list[float] | None = None,
    volumes: list[float] | None = None,
    dividends: list[float] | None = None,
    splits: list[float] | None = None,
) -> None:
    n = len(days)
    closes = closes or [100.0 + i for i in range(n)]
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(days),
            "open": [c - 0.5 for c in closes],
            "high": [c + 1.0 for c in closes],
            "low": [c - 1.0 for c in closes],
            "close": closes,
            "volume": volumes or [1_000.0] * n,
            "factor": [1.0] * n,
            "dividend": dividends or [0.0] * n,
            "split_ratio": splits or [0.0] * n,
        }
    )
    path = parquet_dir / f"symbol={symbol}" / "data.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data=DataConfig(data_dir=tmp_path / "data"))


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))


def write_snapshot(settings: Settings, day: str, symbols: list[str]) -> None:
    universe_dir = settings.data.universe_dir
    universe_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"symbol": symbols}).to_csv(universe_dir / f"{day}.csv", index=False)


def test_health(client: TestClient) -> None:
    assert client.get("/api/health").json() == {"status": "ok"}


def test_cors_allows_the_webapp_origin(client: TestClient) -> None:
    response = client.get("/api/health", headers={"Origin": "http://localhost:3000"})
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_overview(settings: Settings, client: TestClient, monkeypatch) -> None:
    write_partition(settings.data.parquet_dir, "AAA", DAYS)
    write_partition(settings.data.parquet_dir, "BBB", DAYS[:3])
    monkeypatch.setattr(
        "finora.web.routes.last_completed_session", lambda: date(2024, 1, 8)
    )
    body = client.get("/api/store/overview").json()
    assert body["symbol_count"] == 2
    assert body["total_rows"] == 8
    assert body["last_completed_session"] == "2024-01-08"
    assert body["store_size_bytes"] > 0
    by_symbol = {s["symbol"]: s for s in body["symbols"]}
    assert by_symbol["AAA"]["fresh"] is True
    assert by_symbol["AAA"]["first_date"] == "2024-01-02"
    assert by_symbol["BBB"] == {
        "symbol": "BBB", "rows": 3, "first_date": "2024-01-02",
        "last_date": "2024-01-04", "fresh": False,
    }


def test_overview_empty_store(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        "finora.web.routes.last_completed_session", lambda: date(2024, 1, 8)
    )
    body = client.get("/api/store/overview").json()
    assert body == {
        "total_rows": 0, "symbol_count": 0, "last_completed_session": "2024-01-08",
        "store_size_bytes": 0, "symbols": [],
    }


def test_symbols(settings: Settings, client: TestClient) -> None:
    write_partition(settings.data.parquet_dir, "BBB", DAYS)
    write_partition(settings.data.parquet_dir, "AAA", DAYS)
    assert client.get("/api/symbols").json() == {"symbols": ["AAA", "BBB"]}


def test_bars_window_and_case(settings: Settings, client: TestClient) -> None:
    write_partition(settings.data.parquet_dir, "AAA", DAYS)
    body = client.get("/api/symbols/aaa/bars?start=2024-01-04&end=2024-01-05").json()
    assert body["symbol"] == "AAA"
    assert body["count"] == 2
    assert [b["date"] for b in body["bars"]] == ["2024-01-04", "2024-01-05"]
    assert body["bars"][0]["close"] == 102.0
    assert body["bars"][0]["factor"] == 1.0


def test_bars_unknown_symbol_404(settings: Settings, client: TestClient) -> None:
    write_partition(settings.data.parquet_dir, "AAA", DAYS)
    assert client.get("/api/symbols/ZZZ/bars").status_code == 404


def test_bars_empty_window_is_not_404(settings: Settings, client: TestClient) -> None:
    write_partition(settings.data.parquet_dir, "AAA", DAYS)
    body = client.get("/api/symbols/AAA/bars?start=2030-01-01").json()
    assert body["count"] == 0 and body["bars"] == []


def test_events(settings: Settings, client: TestClient) -> None:
    write_partition(
        settings.data.parquet_dir, "AAA", DAYS,
        dividends=[0.0, 0.25, 0.0, 0.0, 0.0],
        splits=[0.0, 0.0, 4.0, 0.0, 0.0],
    )
    body = client.get("/api/symbols/AAA/events").json()
    assert [(e["kind"], e["date"]) for e in body["events"]] == [
        ("dividend", "2024-01-03"),
        ("split", "2024-01-04"),
    ]
    assert body["events"][1]["split_ratio"] == 4.0
    assert body["events"][0]["dividend"] == 0.25


def test_events_unknown_symbol_404(client: TestClient) -> None:
    assert client.get("/api/symbols/ZZZ/events").status_code == 404


def test_quality_reports_issues_and_thresholds(
    settings: Settings, client: TestClient
) -> None:
    write_partition(settings.data.parquet_dir, "GOOD", DAYS)
    write_partition(
        settings.data.parquet_dir, "BAD", DAYS,
        volumes=[1000.0, 0.0, 1000.0, 1000.0, 1000.0],
    )
    body = client.get("/api/quality").json()
    assert body["checked_symbols"] == 2
    assert body["thresholds"] == {
        "max_missing_run_days": 5, "max_abs_daily_return": 0.5, "min_price": 1.0,
    }
    assert [(i["symbol"], i["kind"], i["date"]) for i in body["issues"]] == [
        ("BAD", "nonpositive_volume", "2024-01-03"),
    ]
    assert body["generated_at"]

    filtered = client.get("/api/quality?symbol=GOOD").json()
    assert filtered["checked_symbols"] == 1
    assert filtered["issues"] == []


def test_universe_empty_dir(client: TestClient) -> None:
    assert client.get("/api/universe/snapshots").json() == {"snapshots": []}


def test_universe_snapshots_and_detail(settings: Settings, client: TestClient) -> None:
    write_snapshot(settings, "2024-01-01", ["AAA", "BBB", "CCC"])
    write_snapshot(settings, "2024-06-01", ["BBB", "CCC", "DDD"])
    body = client.get("/api/universe/snapshots").json()
    assert body["snapshots"] == [
        {"date": "2024-06-01", "symbol_count": 3},
        {"date": "2024-01-01", "symbol_count": 3},
    ]
    detail = client.get("/api/universe/snapshots/2024-01-01").json()
    assert detail == {"date": "2024-01-01", "symbols": ["AAA", "BBB", "CCC"]}
    assert client.get("/api/universe/snapshots/2030-01-01").status_code == 404


def test_universe_diff(settings: Settings, client: TestClient) -> None:
    write_snapshot(settings, "2024-01-01", ["AAA", "BBB", "CCC"])
    write_snapshot(settings, "2024-06-01", ["BBB", "CCC", "DDD"])
    body = client.get("/api/universe/diff?from=2024-01-01&to=2024-06-01").json()
    assert body == {
        "from_date": "2024-01-01", "to_date": "2024-06-01",
        "added": ["DDD"], "removed": ["AAA"], "unchanged_count": 2,
    }
    assert client.get("/api/universe/diff?from=2024-01-01&to=2030-01-01").status_code == 404
