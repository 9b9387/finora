"""Tests for corporate-action event detection from stored action columns."""
from __future__ import annotations

from datetime import date

import pandas as pd

from finora.data.events import detect_adjustment_events
from finora.data.store import CANONICAL_COLUMNS, empty_bars

DAYS = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)]


def make_bars(
    symbol: str = "AAA",
    dividends: list[float] | None = None,
    splits: list[float] | None = None,
) -> pd.DataFrame:
    n = len(DAYS)
    return pd.DataFrame(
        {
            "symbol": symbol,
            "date": pd.to_datetime(DAYS),
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": [100.0 + i for i in range(n)],
            "volume": 1_000.0,
            "factor": 1.0,
            "dividend": dividends or [0.0] * n,
            "split_ratio": splits or [0.0] * n,
        }
    )[CANONICAL_COLUMNS]


def test_no_actions_no_events():
    assert detect_adjustment_events(make_bars()) == []
    assert detect_adjustment_events(empty_bars()) == []


def test_split_event():
    events = detect_adjustment_events(make_bars(splits=[0.0, 4.0, 0.0, 0.0]))
    assert len(events) == 1
    e = events[0]
    assert (e.kind, e.date, e.split_ratio, e.dividend) == ("split", date(2024, 1, 3), 4.0, None)
    assert e.close == 101.0


def test_reverse_split_event():
    events = detect_adjustment_events(make_bars(splits=[0.0, 0.0, 0.1, 0.0]))
    assert [e.kind for e in events] == ["split"]
    assert events[0].split_ratio == 0.1


def test_unit_split_ratio_ignored():
    # ratio 1.0 is a no-op corporate action; not an event
    assert detect_adjustment_events(make_bars(splits=[0.0, 1.0, 0.0, 0.0])) == []


def test_dividend_event():
    events = detect_adjustment_events(make_bars(dividends=[0.0, 0.0, 0.205, 0.0]))
    assert len(events) == 1
    e = events[0]
    assert (e.kind, e.date, e.dividend, e.split_ratio) == (
        "dividend", date(2024, 1, 4), 0.205, None,
    )


def test_same_day_split_and_dividend_yield_two_events():
    events = detect_adjustment_events(
        make_bars(dividends=[0.0, 0.5, 0.0, 0.0], splits=[0.0, 2.0, 0.0, 0.0])
    )
    assert [e.kind for e in events] == ["split", "dividend"]
    assert events[0].date == events[1].date == date(2024, 1, 3)


def test_multiple_symbols_sorted():
    df = pd.concat(
        [
            make_bars("BBB", dividends=[0.1, 0.0, 0.0, 0.0]),
            make_bars("AAA", splits=[0.0, 0.0, 0.0, 3.0]),
        ],
        ignore_index=True,
    )
    events = detect_adjustment_events(df)
    assert [(e.symbol, e.kind) for e in events] == [("AAA", "split"), ("BBB", "dividend")]


def test_frames_without_action_columns_produce_no_events():
    legacy = make_bars().drop(columns=["dividend", "split_ratio"])
    assert detect_adjustment_events(legacy) == []


def test_nan_action_values_ignored():
    df = make_bars(dividends=[0.0, 0.3, 0.0, 0.0])
    df.loc[2, "dividend"] = float("nan")
    df.loc[3, "split_ratio"] = float("nan")
    events = detect_adjustment_events(df)
    assert [e.kind for e in events] == ["dividend"]
