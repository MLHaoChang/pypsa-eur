# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""ui-modern 08: a case with a long horizon compares with its base.

`basis_digests` hashed the snapshot index through the experiment plan's
`_digest`, which refuses JSON over 64 KiB, so a three-period 504-snapshot
network (77,790 bytes) could never be compared.
"""

import hashlib
import json
from types import SimpleNamespace

import pandas as pd
import pypsa
import pytest
from fastapi import HTTPException

from harness import experiment_comparison as ec
from services import experiments as ex, workflow_tools as wt
from services.solver_service import SolverConfig


def _saved(snapshots, periods=None):
    n = pypsa.Network()
    if periods:
        n.set_snapshots(pd.MultiIndex.from_product([periods, snapshots]))
        n.set_investment_periods(periods)
    else:
        n.set_snapshots(snapshots)
    return SimpleNamespace(network=n, solver_state={"solver_config": SolverConfig()})


def _hours(count):
    return pd.date_range("2030-01-01", periods=count, freq="h")


def test_a_504_snapshot_horizon_has_basis_digests():
    saved = _saved(_hours(168), periods=[2030, 2040, 2050])
    blob = json.dumps(
        wt._exact_frame_json(saved.network.snapshots.to_series().to_frame()),
        sort_keys=True,
        allow_nan=False,
    ).encode()
    assert len(blob) > ex.MAX_RECORD_BYTES // 2, (
        "the fixture must be past the plan limit"
    )
    digests = ec.basis_digests(saved)
    assert digests["snapshots_sha256"] == hashlib.sha256(blob).hexdigest()
    assert set(digests) == {
        "snapshots_sha256",
        "weights_sha256",
        "solver_config_sha256",
    }


def test_a_short_horizon_keeps_the_digest_saved_records_hold():
    """Every index that passed before digests to the same value, so records
    saved before the fix stay comparable."""
    n = _saved(_hours(168)).network
    for obj in (
        wt._exact_frame_json(n.snapshots.to_series().to_frame()),
        [
            wt._exact_frame_json(n.snapshot_weightings),
            wt._exact_frame_json(n.investment_period_weightings),
        ],
    ):
        assert ec._basis_digest(obj) == ex._digest(obj)
    saved = _saved(_hours(168))
    digests = ec.basis_digests(saved)
    assert digests["snapshots_sha256"] == ex._digest(
        wt._exact_frame_json(saved.network.snapshots.to_series().to_frame())
    )


def test_the_plan_limit_still_holds_for_plans():
    with pytest.raises(HTTPException) as err:
        ex._digest({"blob": "x" * ex.MAX_RECORD_BYTES})
    assert err.value.status_code == 422


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), object()])
def test_a_basis_that_is_not_finite_json_is_still_refused(bad):
    with pytest.raises(HTTPException) as err:
        ec._basis_digest([bad])
    assert err.value.status_code == 422


@pytest.mark.parametrize(
    "value",
    [
        {"b": {"nested": [1, 2, {"c": "d"}]}, "a": 1},
        ["Zürich", "Ørsted", "北京", "☃"],
        [0.1, 1e-7, 1e300, -0.0, 2.5],
        [1, 1.0, 0, 0.0, True, None],
    ],
)
def test_the_basis_digest_equals_the_plan_digest_under_the_plan_limit(value):
    assert ec._basis_digest(value) == ex._digest(value)


def test_ints_and_floats_digest_apart_as_before():
    assert ec._basis_digest([1]) != ec._basis_digest([1.0])
    assert ex._digest([1]) != ex._digest([1.0])


def test_a_basis_just_over_the_cap_is_a_typed_refusal(monkeypatch):
    monkeypatch.setattr(ec, "MAX_BASIS_BYTES", 1024)
    at_cap = ["x" * (1024 - len('[""]'))]
    assert len(json.dumps(at_cap).encode()) == 1024
    ec._basis_digest(at_cap)
    with pytest.raises(HTTPException) as err:
        ec._basis_digest(["x" * (1024 - len('[""]') + 1)])
    assert err.value.status_code == 422
    assert err.value.detail["error_kind"] == "invalid_experiment"


def test_the_cap_holds_the_largest_realistic_basis():
    assert ec.MAX_BASIS_BYTES >= 10 * 5_099_065


def test_the_solver_config_digest_is_unchanged():
    saved = _saved(_hours(24))
    cfg = saved.solver_state["solver_config"]
    from services.reliability_goal import goal_free_config

    assert ec.basis_digests(saved)["solver_config_sha256"] == ex._digest(
        goal_free_config(cfg)
    )


def test_a_change_in_snapshot_weightings_changes_the_weights_digest():
    saved = _saved(_hours(24))
    before = ec.basis_digests(saved)
    saved.network.snapshot_weightings.loc[:, :] = 2.0
    after = ec.basis_digests(saved)
    assert after["weights_sha256"] != before["weights_sha256"]
    assert after["snapshots_sha256"] == before["snapshots_sha256"]
