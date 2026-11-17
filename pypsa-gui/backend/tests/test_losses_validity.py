"""
Raw validity of the `/results/losses` summary (assistant-assurance 10, A1.7).

The summary zero-fills NaN and non-finite loss and load values so the response
serialises. That keeps the screen working, but it means a missing value and a
real 0 MW look the same afterwards. `validity` records, per entity and before
any fill, how many snapshots held a finite nonzero value, a real zero, a
missing value (NaN), +inf or -inf, and whether the entity had no column at all.

Every expected value below is worked by hand from the frames in `_net`, never
read back from the function under test.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pypsa

from services.results.losses import _validity_rows, compute_losses_summary


def _net():
    n = pypsa.Network()
    n.set_snapshots(pd.RangeIndex(3))
    n.snapshot_weightings.loc[:, "generators"] = [1.0, 2.0, 1.0]
    n.snapshot_weightings.loc[:, "objective"] = [1.0, 2.0, 1.0]
    n.add("Bus", ["a", "b"])
    n.add("Line", ["L1", "L2", "L3", "L4"], bus0="a", bus1="b", x=0.1, s_nom=10)
    n.add("Load", ["D1", "D2", "D3"], bus="a")
    # L3 has no column: a branch the solve never reported.
    n.lines_t["loss"] = pd.DataFrame({
        "L1": [0.0, 1.0, np.nan],      # real zero, a value, a missing value
        "L2": [0.0, 0.0, 0.0],         # a genuine zero-loss line
        "L4": [np.inf, 1.0, -np.inf],  # corrupt solver output
    }, index=n.snapshots)
    # D3 has no column.
    n.loads_t["p"] = pd.DataFrame({
        "D1": [10.0, np.nan, 10.0],
        "D2": [5.0, 5.0, 5.0],
    }, index=n.snapshots)
    return n


def _result_df(n, comp_t, attr, _source):
    return getattr(n, comp_t)[attr]


def _summary():
    return compute_losses_summary(_net(), "lopf", False, result_df=_result_df)


def _counts(**kw):
    base = {"finite": 0, "zero": 0, "missing": 0, "pos_inf": 0, "neg_inf": 0}
    base.update(kw)
    return base


def test_existing_fields_are_unchanged():
    # Characterises master's behaviour; the validity field is additive only.
    # L1 zero-filled = [0, 1, 0] -> 0*1 + 1*2 + 0*1 = 2 MWh, peak 1 MW.
    # L4's inf - inf sum and inf peak collapse to 0. Demand: D1 [10, 0, 10]
    # -> 20 MWh, D2 -> 20 MWh, total 40 MWh, so losses are 5 % of demand.
    out = _summary()
    assert {k: v for k, v in out.items() if k != "validity"} == {
        "enabled": True,
        "total_mwh": 2.0,
        "peak_mw": 1.0,
        "total_demand_mwh": 40.0,
        "loss_pct_of_demand": 5.0,
        "by_branch": [
            {"component": "Line", "name": "L1", "loss_mwh": 2.0, "peak_mw": 1.0, "share_pct": 100.0},
            {"component": "Line", "name": "L2", "loss_mwh": 0.0, "peak_mw": 0.0, "share_pct": 0.0},
            {"component": "Line", "name": "L4", "loss_mwh": 0.0, "peak_mw": 0.0, "share_pct": 0.0},
        ],
    }


def test_validity_keeps_zero_missing_infinite_and_absent_apart():
    assert _summary()["validity"] == {
        "version": "losses-validity/1",
        "snapshots": 3,
        "complete": False,
        "branches": [
            {"component": "Line", "name": "L1", "state": "incomplete", **_counts(finite=1, zero=1, missing=1)},
            {"component": "Line", "name": "L2", "state": "complete", **_counts(zero=3)},
            {"component": "Line", "name": "L3", "state": "absent", **_counts()},
            {"component": "Line", "name": "L4", "state": "incomplete", **_counts(finite=1, pos_inf=1, neg_inf=1)},
        ],
        "loads": [
            {"component": "Load", "name": "D1", "state": "incomplete", **_counts(finite=2, missing=1)},
            {"component": "Load", "name": "D2", "state": "complete", **_counts(finite=3)},
            {"component": "Load", "name": "D3", "state": "absent", **_counts()},
        ],
    }


def test_a_fully_valid_network_is_complete():
    n = _net()
    n.remove("Line", ["L1", "L3", "L4"])
    n.remove("Load", ["D1", "D3"])
    n.lines_t["loss"] = n.lines_t["loss"][["L2"]]
    n.loads_t["p"] = n.loads_t["p"][["D2"]]
    v = compute_losses_summary(n, "lopf", False, result_df=_result_df)["validity"]
    assert v["complete"] is True
    assert [b["state"] for b in v["branches"] + v["loads"]] == ["complete", "complete"]


def test_losses_switched_off_reports_every_branch_absent_not_zero():
    n = _net()
    n.lines_t["loss"] = pd.DataFrame(index=n.snapshots)
    out = compute_losses_summary(n, "lopf", False, result_df=_result_df)
    assert out["enabled"] is False and out["total_mwh"] == 0.0
    assert [(b["name"], b["state"]) for b in out["validity"]["branches"]] == [
        ("L1", "absent"), ("L2", "absent"), ("L3", "absent"), ("L4", "absent"),
    ]
    assert out["validity"]["complete"] is False


def test_ac_pf_nan_from_a_nonconverged_line_is_missing_not_zero():
    n = _net()
    n.lines_t["p0"] = pd.DataFrame({"L1": [1.0, np.nan, 2.0], "L2": [1.0, 1.0, 1.0]}, index=n.snapshots)
    n.lines_t["p1"] = pd.DataFrame({"L1": [-0.5, -1.0, -2.0], "L2": [-1.0, -1.0, -1.0]}, index=n.snapshots)
    out = compute_losses_summary(n, "ac_pf", True, result_df=_result_df)
    by_name = {b["name"]: b for b in out["validity"]["branches"]}
    # L1 = [0.5, nan, 0.0]: one value, one missing, one real zero.
    assert by_name["L1"] == {"component": "Line", "name": "L1", "state": "incomplete",
                             **_counts(finite=1, zero=1, missing=1)}
    assert by_name["L2"]["state"] == "complete" and by_name["L2"]["zero"] == 3
    # The zero-filled screen value is unchanged: 0.5 MWh from L1.
    assert math.isclose(out["total_mwh"], 0.5)


def test_a_network_without_branches_is_not_complete():
    n = _net()
    n.remove("Line", ["L1", "L2", "L3", "L4"])
    n.remove("Load", ["D1", "D3"])
    n.lines_t["loss"] = pd.DataFrame(index=n.snapshots)
    n.loads_t["p"] = n.loads_t["p"][["D2"]]
    v = compute_losses_summary(n, "lopf", False, result_df=_result_df)["validity"]
    # Every load is complete, yet with no branch there is nothing to certify.
    assert v["branches"] == [] and [r["state"] for r in v["loads"]] == ["complete"]
    assert v["complete"] is False


def test_a_frame_that_is_not_there_marks_every_entity_absent():
    assert _validity_rows(None, ["L1"], "Line") == [
        {"component": "Line", "name": "L1", "state": "absent", **_counts()},
    ]
