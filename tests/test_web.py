"""API tests for finora.web against a tmp_path store."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from finora.core.config import Settings
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


INITIAL_STRATEGIES_YAML = """strategies:
  - name: rsi_spy
    kind: rsi
    stage: paper
    capital_fraction: 1.0
    params:
      symbol: SPY
"""


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """A real config dir so strategy CRUD round-trips through the yaml file."""
    d = tmp_path / "config"
    d.mkdir()
    (d / "data.yaml").write_text(f"data_dir: {(tmp_path / 'data').as_posix()}\n")
    (d / "ops.yaml").write_text(
        f"log_dir: {(tmp_path / 'logs').as_posix()}\n"
        f"state_dir: {(tmp_path / 'state').as_posix()}\n"
        f"reports_dir: {(tmp_path / 'reports').as_posix()}\n"
        f"backtests_dir: {(tmp_path / 'artifacts' / 'backtests').as_posix()}\n"
    )
    (d / "strategies.yaml").write_text(INITIAL_STRATEGIES_YAML)
    return d


@pytest.fixture
def settings(config_dir: Path) -> Settings:
    return Settings.load(config_dir)


@pytest.fixture
def client(settings: Settings, config_dir: Path) -> TestClient:
    return TestClient(create_app(settings, config_dir=config_dir))


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


def make_artifact(
    settings: Settings,
    name: str,
    daily_returns: list[float],
    start: str = "2024-01-02",
    trades: list[dict] | None = None,
    kind: str = "rsi",
) -> str:
    from finora.backtest.report import compute_metrics, save_backtest_artifact

    returns = pd.Series(
        daily_returns,
        index=pd.bdate_range(start, periods=len(daily_returns), name="date"),
        name="return",
    )
    snapshot: dict = {"name": name, "kind": kind, "params": {}, "cost_bps": 15.0,
                      "start": start, "end": None}
    if trades is not None:
        snapshot["trades"] = trades
    out_dir = save_backtest_artifact(
        name, compute_metrics(returns), snapshot, returns,
        out_root=settings.ops.backtests_dir,
    )
    return out_dir.name


def test_backtests_empty(client: TestClient) -> None:
    assert client.get("/api/backtests").json() == {"runs": []}


def test_backtests_list_sorted_with_metrics(
    settings: Settings, client: TestClient
) -> None:
    make_artifact(settings, "older", [0.01, -0.02, 0.005], start="2023-06-01")
    run_id = make_artifact(settings, "newer", [0.01, 0.01, 0.01], kind="qlib")
    body = client.get("/api/backtests").json()
    assert [r["name"] for r in body["runs"]] == ["newer", "older"]
    newest = body["runs"][0]
    assert newest["id"] == run_id
    assert newest["kind"] == "qlib"
    assert newest["cost_bps"] == 15.0
    assert newest["metrics"]["n_days"] == 3
    assert newest["metrics"]["total_return"] == pytest.approx(1.01**3 - 1)


def test_backtest_detail_points_and_trades(
    settings: Settings, client: TestClient
) -> None:
    trades = [{"date": "2024-01-03", "action": "buy", "rsi": 25.0}]
    run_id = make_artifact(settings, "rsi_test", [0.10, -0.50], trades=trades)
    body = client.get(f"/api/backtests/{run_id}").json()
    assert body["name"] == "rsi_test"
    assert [p["date"] for p in body["points"]] == ["2024-01-02", "2024-01-03"]
    assert body["points"][0]["equity"] == pytest.approx(1.10)
    assert body["points"][1]["equity"] == pytest.approx(0.55)
    assert body["points"][1]["drawdown"] == pytest.approx(-0.5)
    assert body["trades"] == trades
    assert "trades" not in body["config"]  # popped into its own field
    assert body["config"]["cost_bps"] == 15.0


def test_backtest_detail_404_and_traversal(
    settings: Settings, client: TestClient
) -> None:
    make_artifact(settings, "only", [0.01])
    assert client.get("/api/backtests/nope_20240101").status_code == 404
    assert client.get("/api/backtests/..%2F..%2Fetc").status_code == 404


MA_PAYLOAD = {
    "name": "ma_test",
    "kind": "ma_cross",
    "stage": "paper",
    "capital_fraction": 1.0,
    "params": {"symbol": "SPY", "fast_days": 3, "slow_days": 5, "weight": 1.0},
}


def test_strategies_list_initial(client: TestClient) -> None:
    body = client.get("/api/strategies").json()
    assert [s["name"] for s in body["strategies"]] == ["rsi_spy"]
    assert body["strategies"][0]["kind"] == "rsi"


def test_create_strategy_persists_to_yaml(
    client: TestClient, config_dir: Path
) -> None:
    response = client.post("/api/strategies", json=MA_PAYLOAD)
    assert response.status_code == 201, response.text
    names = [s["name"] for s in client.get("/api/strategies").json()["strategies"]]
    assert names == ["rsi_spy", "ma_test"]
    assert "ma_test" in (config_dir / "strategies.yaml").read_text()


def test_create_duplicate_name_conflicts(client: TestClient) -> None:
    assert client.post("/api/strategies", json=MA_PAYLOAD).status_code == 201
    assert client.post("/api/strategies", json=MA_PAYLOAD).status_code == 409


def test_create_invalid_params_rejected(client: TestClient) -> None:
    bad = {**MA_PAYLOAD, "params": {**MA_PAYLOAD["params"], "fast_days": 9}}
    response = client.post("/api/strategies", json=bad)
    assert response.status_code == 400
    assert "fast_days" in response.json()["detail"]


def test_create_unknown_kind_rejected(client: TestClient) -> None:
    assert (
        client.post("/api/strategies", json={**MA_PAYLOAD, "kind": "nope"}).status_code
        == 422
    )


def test_update_strategy_params(client: TestClient, config_dir: Path) -> None:
    payload = {
        "kind": "rsi",
        "stage": "small",
        "capital_fraction": 0.5,
        "params": {"symbol": "SPY", "period": 21},
    }
    response = client.put("/api/strategies/rsi_spy", json=payload)
    assert response.status_code == 200, response.text
    stored = client.get("/api/strategies").json()["strategies"][0]
    assert stored["params"]["period"] == 21
    assert stored["stage"] == "small"
    assert stored["capital_fraction"] == 0.5
    assert client.put("/api/strategies/ghost", json=payload).status_code == 404


def test_update_with_invalid_params_leaves_file_unchanged(
    client: TestClient, config_dir: Path
) -> None:
    before = (config_dir / "strategies.yaml").read_text()
    payload = {"kind": "rsi", "params": {"buy_below": 80, "rearm": 50}}
    assert client.put("/api/strategies/rsi_spy", json=payload).status_code == 400
    assert (config_dir / "strategies.yaml").read_text() == before


def test_delete_strategy(client: TestClient) -> None:
    assert client.delete("/api/strategies/rsi_spy").status_code == 204
    assert client.get("/api/strategies").json()["strategies"] == []
    assert client.delete("/api/strategies/rsi_spy").status_code == 404


def many_days(n: int) -> list[date]:
    return [d.date() for d in pd.bdate_range("2024-01-02", periods=n)]


def test_run_backtest_endpoint_creates_artifact(
    settings: Settings, client: TestClient
) -> None:
    days = many_days(30)
    closes = [100.0 - i for i in range(10)] + [90.0 + 2.0 * i for i in range(20)]
    write_partition(settings.data.parquet_dir, "SPY", days, closes=closes)
    assert client.post("/api/strategies", json=MA_PAYLOAD).status_code == 201

    response = client.post(
        "/api/backtests/run",
        json={"name": "ma_test", "start": str(days[8]), "cost_bps": 10.0},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "ma_test"
    assert body["id"].startswith("ma_test_")
    assert body["metrics"]["n_days"] > 10

    detail = client.get(f"/api/backtests/{body['id']}")
    assert detail.status_code == 200
    assert detail.json()["config"]["cost_bps"] == 10.0


def test_run_backtest_symbol_override_uses_derived_name(
    settings: Settings, client: TestClient
) -> None:
    days = many_days(30)
    write_partition(settings.data.parquet_dir, "AAA", days,
                    closes=[100.0 + i for i in range(30)])
    assert client.post("/api/strategies", json=MA_PAYLOAD).status_code == 201

    response = client.post(
        "/api/backtests/run",
        json={"name": "ma_test", "symbol": "aaa", "start": str(days[8])},
    )
    assert response.status_code == 200, response.text
    assert response.json()["id"].startswith("ma_test_AAA_")


def test_run_backtest_unknown_strategy_404(client: TestClient) -> None:
    assert (
        client.post("/api/backtests/run", json={"name": "ghost"}).status_code == 404
    )


def test_run_backtest_no_data_maps_to_400(client: TestClient) -> None:
    response = client.post("/api/backtests/run", json={"name": "rsi_spy"})
    assert response.status_code == 400
    assert "no price data" in response.json()["detail"]


def test_universe_diff(settings: Settings, client: TestClient) -> None:
    write_snapshot(settings, "2024-01-01", ["AAA", "BBB", "CCC"])
    write_snapshot(settings, "2024-06-01", ["BBB", "CCC", "DDD"])
    body = client.get("/api/universe/diff?from=2024-01-01&to=2024-06-01").json()
    assert body == {
        "from_date": "2024-01-01", "to_date": "2024-06-01",
        "added": ["DDD"], "removed": ["AAA"], "unchanged_count": 2,
    }
    assert client.get("/api/universe/diff?from=2024-01-01&to=2030-01-01").status_code == 404
