"""The profile-upload answer says what each column did (the Time Series receipt)."""
from __future__ import annotations

import types

import pandas as pd

from routers.network_profiles import _peak_mean, _unmatched_detail


def _net():
    idx = lambda *names: types.SimpleNamespace(index=pd.Index(names))
    return types.SimpleNamespace(
        generators=idx("ccgt", "coal"), loads=idx("town"), links=idx(), buses=idx("b1"),
        lines=idx(), stores=idx(), storage_units=idx(), transformers=idx(),
        carriers=idx(), global_constraints=idx())


def test_each_unmatched_column_names_its_reason():
    n = _net()
    cols = ["ccgt", "ccgt.1", "town", "ghost", "coal"]
    got = {d["name"]: d for d in _unmatched_detail(n, "generators", cols, {"ccgt", "coal"})}
    assert set(got) == {"ccgt.1", "town", "ghost"}
    assert got["ccgt.1"] == {"name": "ccgt.1", "reason": "duplicate", "of": "ccgt"}
    assert got["town"] == {"name": "town", "reason": "other_kind", "kind": "loads"}
    assert got["ghost"] == {"name": "ghost", "reason": "no_such_component"}


def test_peak_mean_is_none_without_a_series():
    assert _peak_mean({"has_profile": False}) is None
    assert _peak_mean({"has_profile": True, "peak": 2.0, "mean": 1.0}) == {"peak": 2.0, "mean": 1.0}
