import pandas as pd
import pytest

from gridspine.schema.contracts import ContractError
from gridspine.schema.dispatch import validate_dispatch


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
