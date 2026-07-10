"""Corporate-action events read from the stored dividend/split_ratio columns.

The ETL persists the provider's explicit per-day action values (cash dividend
per share on the ex-date; split share multiplier on the split date), so event
detection is a lookup, not an inference from price or factor jumps.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

DIVIDEND = "dividend"
SPLIT = "split"


@dataclass
class AdjustmentEvent:
    symbol: str
    date: date
    kind: str  # "split" | "dividend"
    close: float  # stored (split-adjusted) close on the event date
    dividend: float | None = None  # cash per share, dividend events only
    split_ratio: float | None = None  # share multiplier (4.0 = 4-for-1), splits only


def detect_adjustment_events(df: pd.DataFrame) -> list[AdjustmentEvent]:
    """All split/dividend events in a canonical daily-bar frame.

    A day carrying both actions yields two events (split first). Frames from
    stores that predate the action columns simply produce no events.
    """
    if df.empty or not {"dividend", "split_ratio"}.issubset(df.columns):
        return []
    events: list[AdjustmentEvent] = []
    frame = df.sort_values(["symbol", "date"])
    active = frame[(frame["split_ratio"].fillna(0.0) > 0.0) | (frame["dividend"].fillna(0.0) > 0.0)]
    for row in active.itertuples(index=False):
        day = pd.Timestamp(row.date).date()
        split_ratio = float(row.split_ratio or 0.0)
        dividend = float(row.dividend or 0.0)
        if split_ratio > 0.0 and split_ratio != 1.0:
            events.append(
                AdjustmentEvent(
                    symbol=str(row.symbol),
                    date=day,
                    kind=SPLIT,
                    close=float(row.close),
                    split_ratio=split_ratio,
                )
            )
        if dividend > 0.0:
            events.append(
                AdjustmentEvent(
                    symbol=str(row.symbol),
                    date=day,
                    kind=DIVIDEND,
                    close=float(row.close),
                    dividend=dividend,
                )
            )
    return events
