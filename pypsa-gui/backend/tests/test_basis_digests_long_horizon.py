# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""ui-modern 08: a case with a long horizon compares with its base.

`basis_digests` hashed the snapshot index through the experiment plan's
`_digest`, which refuses JSON over 64 KiB, so a three-period 504-snapshot
network (77,790 bytes) could never be compared.
"""

# ruff: noqa: F811 -- shared pytest fixtures
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
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401


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
    assert err.value.detail["error_kind"] == "horizon_too_large_to_compare"


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


def test_a_long_horizon_case_compares_with_its_base_end_to_end(baseline):
    """The chat comparison reads both projects' basis digests; past 64 KiB of
    snapshot index it used to answer 422 for the base."""
    from tests.test_sensitivity_comparison import (
        by_name,
        edit_saved,
        solve,
        sweep,
        tool,
    )

    _ctx, base_id, _root = baseline

    def long_horizon(ctx):
        ctx.network.set_snapshots(_hours(1000))

    edit_saved(base_id, long_horizon)
    solve(base_id)
    ids = sweep(base_id, 20)
    report = tool("compare_sensitivities", baseline_project_id=base_id)
    case = by_name(report)["Gas 20"]
    assert case["project_id"] == ids["Gas 20"]
    assert "not_comparable" not in case, case.get("not_comparable")
    assert case["values"]["opex_meur"]["delta"] is not None


def _deep(depth=100_000):
    value = []
    for _ in range(depth):
        value = [value]
    return value


def test_a_too_deep_basis_is_refused_like_a_plan():
    """GK B T3: the same caught set, RecursionError included."""
    for digest in (ec._basis_digest, ex._digest):
        with pytest.raises(HTTPException) as err:
            digest(_deep())
        assert (err.value.status_code, err.value.detail["error_kind"]) == (
            422,
            "invalid_experiment",
        )


def test_a_solver_config_past_the_plan_limit_is_still_refused():
    """GK B T2: the solver config keeps the plan's 64 KiB limit."""
    saved = _saved(_hours(24))
    saved.solver_state["solver_config"] = {
        "solver_options": {"note": "x" * ex.MAX_RECORD_BYTES}
    }
    with pytest.raises(HTTPException) as err:
        ec.basis_digests(saved)
    assert err.value.status_code == 422


def test_an_over_cap_case_reads_unavailable_in_the_experiment_comparison(
    baseline, monkeypatch
):
    """GK B T1: where `_case` catches the refusal, the case is unavailable and
    nothing is compared."""
    from tests.test_experiment_comparison import completed_case
    from tests.test_sensitivity_comparison import tool

    low, _ = completed_case(20, "low")
    high, _ = completed_case(30, "high")
    monkeypatch.setattr(ec, "MAX_BASIS_BYTES", 16)
    report = tool("compare_experiments", experiment_id=high, baseline_experiment_id=low)
    assert not report["comparable"]
    assert report["case"]["status"] == "unavailable"
    assert report["case"]["last_error"] == "experiment_source_unavailable"
    assert report.get("deltas") in (None, {})


def test_an_over_cap_basis_is_a_typed_422_from_the_sensitivity_tool(
    baseline, monkeypatch
):
    """GK B T1: the chat tool answers the typed refusal, never a 500."""
    from tests.test_sensitivity_comparison import refused, solve, sweep

    _ctx, base_id, _root = baseline
    solve(base_id)
    sweep(base_id, 20)
    monkeypatch.setattr(ec, "MAX_BASIS_BYTES", 16)
    detail = refused(
        "horizon_too_large_to_compare", 422, "compare_sensitivities", baseline_project_id=base_id
    )
    assert detail["message"] == "The run's horizon is too large to compare."
