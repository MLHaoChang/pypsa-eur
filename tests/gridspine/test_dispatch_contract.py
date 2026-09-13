import pandas as pd
import pytest

from gridspine.schema.contracts import ContractError
from gridspine.schema.dispatch import validate_dispatch, validate_loads


def good():
    return pd.DataFrame({
        "unit_id": ["G_A", "G_B", "G_A", "G_B"],
        "hour": [0, 0, 1, 1],
        "p_mw": [100.0, 50.0, 0.0, 80.0],
        "q_mvar": [0.0, 0.0, 0.0, 0.0],
        "status": [1, 1, 0, 1],
    })


def test_valid_table_passes_and_normalises_dtypes():
    out = validate_dispatch(good())
    assert out["status"].dtype == "int64"
    assert out["p_mw"].dtype == "float64"


def test_missing_column_rejected():
    with pytest.raises(ContractError, match="q_mvar"):
        validate_dispatch(good().drop(columns=["q_mvar"]))


def test_bad_status_value_rejected():
    df = good()
    df.loc[0, "status"] = 2
    with pytest.raises(ContractError, match="status"):
        validate_dispatch(df)


def test_duplicate_unit_hour_rejected():
    df = pd.concat([good(), good().iloc[[0]]])
    with pytest.raises(ContractError, match="duplicate"):
        validate_dispatch(df)


def test_offline_unit_with_nonzero_p_rejected():
    df = good()
    df.loc[2, "p_mw"] = 25.0  # status 0 but producing
    with pytest.raises(ContractError, match="status 0"):
        validate_dispatch(df)


def test_nan_p_rejected():
    df = good()
    df.loc[1, "p_mw"] = float("nan")
    with pytest.raises(ContractError, match="NaN"):
        validate_dispatch(df)


def test_fractional_status_rejected_not_truncated():
    # 1.5 must NOT survive as int64 1: the guard has to see the raw value.
    df = good()
    df["status"] = [1.5, 1.0, 0.0, 1.0]
    with pytest.raises(ContractError, match="exactly 0 or 1"):
        validate_dispatch(df)


def test_non_integral_hour_rejected_but_integral_float_passes():
    df = good()
    df["hour"] = [1.5, 0.0, 1.0, 1.0]  # whole-column assign: int64 would truncate 1.5
    with pytest.raises(ContractError, match="integral"):
        validate_dispatch(df)

    ok = good()
    ok["hour"] = [0.0, 0.0, 2.0, 2.0]  # integral floats are legal
    assert validate_dispatch(ok)["hour"].tolist() == [0, 0, 2, 2]


def test_null_unit_id_rejected():
    df = good()
    df.loc[0, "unit_id"] = None
    with pytest.raises(ContractError, match="null unit_id"):
        validate_dispatch(df)


def test_inf_p_rejected():
    df = good()
    df.loc[1, "p_mw"] = float("inf")
    with pytest.raises(ContractError, match="inf"):
        validate_dispatch(df)


# ───────────────── hour is a snapshot index, not a timestamp ────────────────
#
# `pd.to_numeric` is the integrality guard's first step, and on a datetime64
# column it succeeds: it returns epoch NANOSECONDS, every one of them integral.
# So a client whose export calls its timestamp column `snapshot` — one of the
# aliases `producers.external` accepts, and what a market model actually names
# it — got a dispatch keyed by 1704067200000000000 rather than a refusal. The
# study then completes: `metrics.csv` indexed by those numbers,
# `lf_1704067200000000000_bus.csv`, `bundle_h1704067200000000000/`, and the
# manifest's `selected_hours` likewise. The same column in a CSV IS refused
# (strings become NaN), so the two file formats disagreed about one client's
# data, which is the worst version of this.

def _hours(df):
    return validate_dispatch(df)["hour"].tolist()


def test_a_timestamp_hour_is_refused_rather_than_read_as_epoch_nanoseconds():
    df = good()
    df["hour"] = pd.to_datetime(
        ["2024-01-01 00:00", "2024-01-01 00:00", "2024-01-01 01:00", "2024-01-01 01:00"]
    )
    with pytest.raises(ContractError) as exc:
        validate_dispatch(df)
    message = str(exc.value)
    # The range guard below would refuse these nanoseconds too, so what this
    # pins is the MESSAGE: a client who shipped a timestamp column is told that,
    # and told which dtype gave it away, rather than being shown a bound.
    assert "datetime64" in message and "snapshot index" in message


def test_a_timestamp_hour_is_refused_in_the_loads_table_too():
    loads = pd.DataFrame({
        "bus": ["L0", "L0"],
        "hour": pd.to_datetime(["2024-01-01 00:00", "2024-01-01 01:00"]),
        "p_mw": [10.0, 11.0], "q_mvar": [1.0, 1.0],
    })
    with pytest.raises(ContractError) as exc:
        validate_loads(loads)
    assert "loads table" in str(exc.value) and "datetime64" in str(exc.value)


def test_an_hour_too_large_to_be_a_snapshot_index_is_refused_not_wrapped():
    """`1e19 % 1 == 0`, so the integrality guard passed it, and `astype("int64")`
    wrapped it to INT64_MIN in silence — no warning, no error, a negative hour in
    the table. Epoch SECONDS (1704067200) lands here too, which is the same
    client mistake one unit smaller."""
    df = good()
    df["hour"] = [0, 0, 1e19, 1e19]
    with pytest.raises(ContractError) as exc:
        validate_dispatch(df)
    assert "hour" in str(exc.value).lower()
    assert all(h >= 0 for h in _hours(good()))


def test_a_negative_hour_is_refused():
    """An hour is a position in the study. A negative one would name a
    `bundle_h-1/` directory and index nothing."""
    df = good()
    df["hour"] = [0, 0, -1, -1]
    with pytest.raises(ContractError):
        validate_dispatch(df)


def test_a_sparse_but_sane_hour_set_still_passes():
    """The bound is there to catch garbage, not to police a study's shape: hours
    need not be 0..N-1, and nothing downstream treats them positionally."""
    df = good()
    df["hour"] = [5, 5, 9000, 9000]
    assert _hours(df) == [5, 5, 9000, 9000]


def test_the_dispatch_table_comes_back_in_the_contract_order():
    """`validate_loads` selects its columns and `validate_dispatch` did not, so
    the stage-1 artifact's column order was whatever the client's file had.
    No number was wrong, but `dispatch.csv` and the client-facing
    `bundle_h*/dispatch_h*.csv` varied run to run for no reason."""
    shuffled = good()[["hour", "status", "unit_id", "q_mvar", "p_mw"]]
    assert list(validate_dispatch(shuffled).columns) == [
        "unit_id", "hour", "p_mw", "q_mvar", "status",
    ]
