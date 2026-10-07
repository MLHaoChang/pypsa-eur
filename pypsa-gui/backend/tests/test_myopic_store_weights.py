"""
Limited foresight writes each representative snapshot's weight into every
``snapshot_weightings`` column. The weight from ``time_aggregation_service``
is cluster count × the step's OBJECTIVE weight. On a network whose columns
differ (the Energy Hub templates since S0: objective 52.14, stores 1 h),
writing that into ``stores`` put 52.14 h back into the state-of-charge
balance. Each column gets count × its own step weight instead, which is the
old behaviour exactly when the columns are equal.
"""
from __future__ import annotations

import pandas as pd
import pytest

from services.solver.myopic import _column_weight_overrides


def _sw(objective, stores, generators):
    idx = pd.MultiIndex.from_product([[2030, 2040], [0, 1]],
                                     names=["period", "timestep"])
    return pd.DataFrame({"objective": objective, "stores": stores,
                         "generators": generators}, index=idx, dtype=float)


def test_each_column_scales_its_own_step_weight():
    sw = _sw([52.0] * 4, [1.0] * 4, [52.0] * 4)
    # Two 2040 representatives standing for 3 and 5 steps.
    ov = pd.Series([3 * 52.0, 5 * 52.0], index=sw.index[2:])
    out = _column_weight_overrides(sw, ov, ["objective", "generators", "stores"])
    assert list(out["objective"]) == pytest.approx([156.0, 260.0])
    assert list(out["generators"]) == pytest.approx([156.0, 260.0])
    assert list(out["stores"]) == pytest.approx([3.0, 5.0])


def test_equal_columns_keep_the_override_unchanged():
    sw = _sw([1.0] * 4, [1.0] * 4, [1.0] * 4)
    ov = pd.Series([3.0, 5.0], index=sw.index[2:])
    out = _column_weight_overrides(sw, ov, ["objective", "generators", "stores"])
    for col in ("objective", "generators", "stores"):
        assert list(out[col]) == pytest.approx([3.0, 5.0])


def test_a_zero_objective_step_falls_back_to_the_override():
    sw = _sw([1.0, 1.0, 0.0, 1.0], [1.0] * 4, [1.0] * 4)
    ov = pd.Series([0.0, 5.0], index=sw.index[2:])
    out = _column_weight_overrides(sw, ov, ["stores"])
    assert list(out["stores"]) == pytest.approx([0.0, 5.0])
