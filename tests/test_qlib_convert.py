"""Tests for finora.data.qlib_convert: bin-format roundtrip plus a real
pyqlib read-back integration test."""
from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from finora.core.errors import DataError
from finora.data.qlib_convert import QLIB_FIELDS, convert_to_qlib, read_day_bin
from finora.data.store import CANONICAL_COLUMNS

# NYSE trading days used as the synthetic calendar.
D1, D2, D3, D4, D5 = (
    date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4),
    date(2024, 1, 5), date(2024, 1, 8),
)

AAA_CLOSES = [100.0, 101.0, 102.0, 103.0, 104.0]
BBB_CLOSES = [50.0, 51.0, 53.0, 54.0]  # missing D3
CCC_CLOSES = [10.0, 11.0, 12.0]  # starts at D3


def bars(symbol: str, days: list[date], closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": symbol,
            "date": pd.to_datetime(days),
            "open": [c - 0.5 for c in closes],
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "volume": [1_000.0 + i for i in range(len(days))],
            "factor": 1.0,
        }
    )[CANONICAL_COLUMNS]


def synthetic_frame() -> pd.DataFrame:
    return pd.concat(
        [
            bars("AAA", [D1, D2, D3, D4, D5], AAA_CLOSES),
            bars("BBB", [D1, D2, D4, D5], BBB_CLOSES),
            bars("CCC", [D3, D4, D5], CCC_CLOSES),
        ],
        ignore_index=True,
    )


@pytest.fixture(scope="module")
def qlib_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("qlib_data")
    counts = convert_to_qlib(synthetic_frame(), out)
    assert counts == {
        "symbols": 3,
        "calendar_days": 5,
        "files_written": 3 * len(QLIB_FIELDS),
    }
    return out


def test_calendar_file(qlib_dir: Path) -> None:
    lines = (qlib_dir / "calendars" / "day.txt").read_text().split()
    assert lines == ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"]


def test_instruments_file(qlib_dir: Path) -> None:
    lines = (qlib_dir / "instruments" / "all.txt").read_text().splitlines()
    assert lines == [
        "AAA\t2024-01-02\t2024-01-08",
        "BBB\t2024-01-02\t2024-01-08",
        "CCC\t2024-01-04\t2024-01-08",
    ]


def test_full_symbol_bin(qlib_dir: Path) -> None:
    idx, values = read_day_bin(qlib_dir / "features" / "aaa" / "close.day.bin")
    assert idx == 0
    np.testing.assert_allclose(values, AAA_CLOSES, rtol=1e-6)


def test_missing_day_becomes_nan(qlib_dir: Path) -> None:
    idx, values = read_day_bin(qlib_dir / "features" / "bbb" / "close.day.bin")
    assert idx == 0
    assert len(values) == 5  # first-to-last calendar span, incl. the hole
    assert np.isnan(values[2])  # D3 missing
    np.testing.assert_allclose(
        values[[0, 1, 3, 4]], BBB_CLOSES, rtol=1e-6
    )


def test_late_start_offset(qlib_dir: Path) -> None:
    idx, values = read_day_bin(qlib_dir / "features" / "ccc" / "close.day.bin")
    assert idx == 2  # D3 is the third calendar date
    assert len(values) == 3
    np.testing.assert_allclose(values, CCC_CLOSES, rtol=1e-6)


def test_all_fields_written(qlib_dir: Path) -> None:
    for fld in QLIB_FIELDS:
        path = qlib_dir / "features" / "aaa" / f"{fld}.day.bin"
        assert path.exists(), fld
    _, volumes = read_day_bin(qlib_dir / "features" / "aaa" / "volume.day.bin")
    np.testing.assert_allclose(volumes, [1000.0, 1001.0, 1002.0, 1003.0, 1004.0], rtol=1e-6)
    _, factors = read_day_bin(qlib_dir / "features" / "aaa" / "factor.day.bin")
    np.testing.assert_allclose(factors, [1.0] * 5, rtol=1e-6)


def test_bin_is_little_endian_float32(qlib_dir: Path) -> None:
    raw = np.fromfile(qlib_dir / "features" / "ccc" / "close.day.bin", dtype="<f4")
    assert raw.dtype == np.dtype("<f4")
    assert raw[0] == 2.0  # index stored as float32
    assert raw.nbytes == 4 * (1 + 3)


def test_empty_frame_raises(tmp_path: Path) -> None:
    from finora.data.store import empty_bars

    with pytest.raises(DataError):
        convert_to_qlib(empty_bars(), tmp_path / "qlib")


@pytest.mark.skipif(
    importlib.util.find_spec("qlib") is None, reason="pyqlib not installed"
)
def test_pyqlib_reads_converted_data(qlib_dir: Path) -> None:
    """The critical proof: qlib itself can read what we wrote."""
    import qlib
    from qlib.data import D

    qlib.init(provider_uri=str(qlib_dir), region="us")

    cal = D.calendar(start_time="2024-01-02", end_time="2024-01-08", freq="day")
    assert [pd.Timestamp(t).date() for t in cal] == [D1, D2, D3, D4, D5]

    df = D.features(
        ["AAA", "BBB", "CCC"],
        ["$close"],
        start_time="2024-01-02",
        end_time="2024-01-08",
        freq="day",
    )
    closes = df["$close"]

    aaa = closes.loc["AAA"]
    np.testing.assert_allclose(aaa.to_numpy(), AAA_CLOSES, rtol=1e-6)
    assert [t.date() for t in aaa.index] == [D1, D2, D3, D4, D5]

    bbb = closes.loc["BBB"].dropna()
    np.testing.assert_allclose(bbb.to_numpy(), BBB_CLOSES, rtol=1e-6)
    assert [t.date() for t in bbb.index] == [D1, D2, D4, D5]

    ccc = closes.loc["CCC"].dropna()
    np.testing.assert_allclose(ccc.to_numpy(), CCC_CLOSES, rtol=1e-6)
    assert ccc.index[0].date() == D3  # late start honoured via the index offset
