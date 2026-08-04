"""
Positional slicing for /results/* time series.

Pure-function tests: no app, no network, no fixtures. `slice_ts` imports
nothing from FastAPI on purpose, which is what makes this possible.
"""
import numpy as np
import pandas as pd
import pytest

from services.serialization import MAX_RESPONSE_VALUES, slice_ts, ts_payload


def frame(rows: int, cols: int = 3) -> pd.DataFrame:
    return pd.DataFrame(
        np.arange(rows * cols, dtype=float).reshape(rows, cols),
        index=pd.date_range("2030-01-01", periods=rows, freq="h"),
        columns=[f"A{i}" for i in range(cols)],
    )


def multi_frame(periods=(2030, 2035), per_period=4, cols=2) -> pd.DataFrame:
    idx = pd.MultiIndex.from_product(
        [list(periods), pd.date_range("2030-01-01", periods=per_period, freq="h")],
        names=["period", "timestep"],
    )
    idx.name = "snapshot"
    return pd.DataFrame(
        np.arange(len(idx) * cols, dtype=float).reshape(len(idx), cols),
        index=idx, columns=[f"A{i}" for i in range(cols)],
    )


def test_no_bounds_returns_everything_and_reports_complete():
    df = frame(10)

    out, meta = slice_ts(df, None, None)

    assert len(out) == 10
    assert meta == {"from": 0, "to": 9, "total": 10, "complete": True, "capped": False}


def test_bounds_are_inclusive():
    """from=2, to=4 must return rows 2, 3 AND 4 — three rows, not two."""
    out, meta = slice_ts(frame(10), 2, 4)

    assert len(out) == 3
    assert out.iloc[0]["A0"] == 6.0    # row 2, first column of arange(30).reshape(10,3)
    assert out.iloc[-1]["A0"] == 12.0  # row 4
    assert (meta["from"], meta["to"]) == (2, 4)


def test_negative_from_clamps_to_zero():
    _out, meta = slice_ts(frame(10), -5, 3)

    assert meta["from"] == 0


def test_to_beyond_the_end_clamps_and_still_reports_complete():
    """
    Asking for more rows than exist and receiving all of them IS complete.
    Reporting False would make a consumer refuse to total data it holds whole.
    """
    out, meta = slice_ts(frame(10), 0, 99999)

    assert len(out) == 10
    assert meta["to"] == 9
    assert meta["complete"] is True


def test_a_window_is_not_complete():
    _out, meta = slice_ts(frame(10), 1, 8)

    assert meta["complete"] is False


def test_inverted_range_yields_empty_rather_than_raising():
    """The client already treats from>to as empty; a 400 would toast an error."""
    out, meta = slice_ts(frame(10), 7, 3)

    assert len(out) == 0
    assert meta["total"] == 10
    assert meta["complete"] is False


def test_multiindex_slices_positionally_and_keeps_period_alignment():
    df = multi_frame()          # 2 periods x 4 timesteps = 8 rows

    out, meta = slice_ts(df, 4, 7)

    assert len(out) == 4
    assert list(out.index.get_level_values(0)) == [2035, 2035, 2035, 2035]
    assert meta["total"] == 8


def test_row_cap_trips_and_is_reported():
    df = frame(1000, cols=10)

    out, meta = slice_ts(df, 0, 999, max_values=100)

    assert len(out) == 10          # 100 values / 10 columns
    assert meta["capped"] is True
    assert meta["complete"] is False


def test_row_cap_does_not_trip_below_the_limit():
    _out, meta = slice_ts(frame(10, cols=2), None, None, max_values=MAX_RESPONSE_VALUES)

    assert meta["capped"] is False


def test_empty_frame_does_not_raise():
    out, meta = slice_ts(frame(0), 0, 5)

    assert len(out) == 0
    assert meta["total"] == 0


def test_ts_payload_without_range_meta_is_unchanged():
    """The no-range path must stay byte-identical for existing consumers."""
    payload = ts_payload(frame(3))

    assert "range" not in payload
    assert set(payload) == {"index", "columns", "data"}


def test_ts_payload_emits_the_range_block():
    df, meta = slice_ts(frame(10), 2, 4)

    payload = ts_payload(df, range_meta=meta)

    assert payload["range"] == {
        "from": 2, "to": 4, "total": 10, "complete": False, "capped": False,
    }
    assert len(payload["data"]) == 3


def test_range_meta_wins_over_a_colliding_extra():
    """`range` is authoritative — an endpoint's extra dict must not shadow it."""
    df, meta = slice_ts(frame(10), 0, 1)

    payload = ts_payload(df, extra={"range": "nonsense"}, range_meta=meta)

    assert payload["range"] == meta
