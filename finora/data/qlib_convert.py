"""Convert the canonical daily-bar store into Qlib's binary data layout.

Produced under <qlib_dir>:

    calendars/day.txt              one %Y-%m-%d trading date per line (union
                                   of all dates present in the data, sorted)
    instruments/all.txt            'SYMBOL\\tSTART\\tEND' per line (%Y-%m-%d)
    features/<sym lower>/<field>.day.bin
                                   little-endian float32 array; element 0 is
                                   float(index of the instrument's first date
                                   in the calendar), the rest are values
                                   aligned one-per-calendar-date from the
                                   instrument's first to last date, NaN for
                                   missing days.

The bin format mirrors qlib.data.storage.file_storage.FileFeatureStorage
(`np.hstack([index, data]).astype("<f").tofile(...)`), so pyqlib reads these
files natively via qlib.init(provider_uri=<qlib_dir>, region="us").
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from finora.core.config import Settings
from finora.core.errors import DataError
from finora.core.log import get_logger

logger = get_logger(__name__)

QLIB_FIELDS: list[str] = ["open", "close", "high", "low", "volume", "factor"]


def convert_to_qlib(df: pd.DataFrame, qlib_dir: Path) -> dict:
    """Write the canonical daily-bar frame to qlib's bin layout.

    Returns counts: {"symbols", "calendar_days", "files_written"}.
    """
    if df.empty:
        raise DataError("cannot convert an empty daily-bar frame to qlib format")

    dates = pd.DatetimeIndex(pd.to_datetime(df["date"])).normalize()
    df = df.assign(date=dates)
    calendar = pd.DatetimeIndex(sorted(dates.unique()))
    calendar_pos = {ts: i for i, ts in enumerate(calendar)}

    calendars_dir = qlib_dir / "calendars"
    instruments_dir = qlib_dir / "instruments"
    features_dir = qlib_dir / "features"
    for d in (calendars_dir, instruments_dir, features_dir):
        d.mkdir(parents=True, exist_ok=True)

    (calendars_dir / "day.txt").write_text(
        "\n".join(ts.strftime("%Y-%m-%d") for ts in calendar) + "\n"
    )

    instrument_lines: list[str] = []
    files_written = 0
    for symbol, grp in df.groupby("symbol", sort=True):
        symbol = str(symbol)
        grp = grp.sort_values("date").drop_duplicates(subset="date", keep="last")
        first, last = grp["date"].iloc[0], grp["date"].iloc[-1]
        start_idx, end_idx = calendar_pos[first], calendar_pos[last]
        instrument_lines.append(
            f"{symbol.upper()}\t{first:%Y-%m-%d}\t{last:%Y-%m-%d}"
        )
        span = calendar[start_idx : end_idx + 1]
        aligned = grp.set_index("date").reindex(span)
        sym_dir = features_dir / symbol.lower()
        sym_dir.mkdir(parents=True, exist_ok=True)
        for fld in QLIB_FIELDS:
            values = aligned[fld].to_numpy(dtype=np.float64)
            payload = np.hstack([np.float64(start_idx), values]).astype("<f4")
            payload.tofile(sym_dir / f"{fld}.day.bin")
            files_written += 1

    (instruments_dir / "all.txt").write_text("\n".join(instrument_lines) + "\n")

    counts = {
        "symbols": len(instrument_lines),
        "calendar_days": len(calendar),
        "files_written": files_written,
    }
    logger.info("qlib conversion complete", qlib_dir=str(qlib_dir), **counts)
    return counts


def convert_store(settings: Settings) -> dict:
    """Convert the full parquet store to qlib format under settings.data.qlib_dir."""
    from finora.data.store import MarketStore

    with MarketStore(settings.data) as store:
        df = store.get_prices()
    if df.empty:
        raise DataError("market store is empty; run `finora etl` before converting to qlib")
    return convert_to_qlib(df, settings.data.qlib_dir)


def read_day_bin(path: Path) -> tuple[int, np.ndarray]:
    """Read one qlib .day.bin file: (calendar index of first date, values)."""
    raw = np.fromfile(path, dtype="<f4")
    if raw.size == 0:
        raise DataError(f"empty qlib bin file: {path}")
    return int(raw[0]), raw[1:]
