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
