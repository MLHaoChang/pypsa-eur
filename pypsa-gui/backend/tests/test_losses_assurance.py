"""`branch-losses/1`: the checked losses reader (assistant-assurance 10, S1b).

Expected values are worked by hand. GK B conditions L1-L10 are in issue 10's
Comments; each test names the one it pins.
"""

# ruff: noqa: F811 -- imported shared fixtures.
import logging
import math
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pypsa
import pytest

from tests.test_branch_loading_assurance import grid  # noqa: F401
from tests.test_workflow_tools import baseline  # noqa: F401

from harness import derived
from harness import losses_calculations as lc
from harness import profile_common

SNAPS = pd.date_range("2030-01-01", periods=3, freq="h")
ALL = ("loss_mwh", "peak_loss_mw", "loss_pct_of_demand")


@pytest.fixture(autouse=True)
def live_lp(monkeypatch):
    monkeypatch.setattr(profile_common, "lp_outputs_live", lambda ctx: True)


def _net(line=(1.0, 2.0, 3.0), trafo=None, load=(100.0, 100.0, 100.0), weights=None):
    n = pypsa.Network()
    n.set_snapshots(SNAPS)
    n.add("Bus", ["a", "b"], v_nom=110)
    n.add("Line", "L", bus0="a", bus1="b", x=0.1, r=0.01, s_nom=500)
    if trafo is not None:
        n.add("Transformer", "T", bus0="a", bus1="b", x=0.1, s_nom=500)
        n.transformers_t.loss = pd.DataFrame({"T": trafo}, index=SNAPS)
    n.add("Load", "d", bus="b")
    if line is not None:
        n.lines_t.loss = pd.DataFrame({"L": line}, index=SNAPS)
    if load is not None:
        n.loads_t.p = pd.DataFrame({"d": load}, index=SNAPS)
    if weights is not None:
        for column, value in weights.items():
            n.snapshot_weightings[column] = value
    return n


def _ctx(n, **state):
    return SimpleNamespace(network=n, solver_state=state)


def _run(n, entity="network", metrics=ALL, component_class=None, state=None, **window):
    network = entity == "network" and not component_class
    raw = lc.source(_ctx(n, **(state or {})), entity, network, component_class)
    profile = lc.NETWORK_PROFILE if network else lc.PROFILE
    scope = {
        "profile": profile,
        "entity_id": entity,
        "metrics": list(metrics),
        **window,
    }
    facts, slots = lc.calculate(raw, scope)
    out = {f["metric"]: f["value"] for f in facts}
    out.update(
        {s["metric"]: s["reason"] for s in slots if s["disposition"] == "unavailable"}
    )
    return out, raw, scope


# ── A1.7: zero vs missing ──────────────────────────────────────────────────


def test_a_real_zero_is_answered_as_zero_mwh():
    out, _, _ = _run(_net(line=(0.0, 0.0, 0.0)))
    assert out == {"loss_mwh": 0.0, "peak_loss_mw": 0.0, "loss_pct_of_demand": 0.0}


def test_hand_values_for_a_complete_network():
    # 1 + 2 + 3 MWh over weight 1; demand 300 MWh.
    out, _, _ = _run(_net())
    assert out == {"loss_mwh": 6.0, "peak_loss_mw": 3.0, "loss_pct_of_demand": 2.0}


@pytest.mark.parametrize("bad", [np.nan, np.inf, -np.inf])
def test_a_missing_or_infinite_loss_is_never_answered(bad):
    out, _, _ = _run(_net(line=(1.0, bad, 3.0)))
    assert out == dict.fromkeys(ALL, "losses_incomplete")


def test_a_frame_missing_a_snapshot_never_certifies():
    n = _net()
    n.lines_t.loss = n.lines_t.loss.iloc[:2]
    assert _run(n)[0] == dict.fromkeys(ALL, "losses_incomplete")


def test_a_zero_row_frame_never_certifies():
    n = _net()
    n.lines_t.loss = pd.DataFrame({"L": []}, index=SNAPS[:0], dtype=float)
    assert _run(n)[0] == dict.fromkeys(ALL, "losses_incomplete")


def test_a_frame_with_a_repeated_row_is_misaligned():
    n = _net()
    n.lines_t.loss = pd.DataFrame({"L": [1.0, 2.0, 3.0]}, index=SNAPS[[0, 0, 1]])
    assert _run(n)[0] == dict.fromkeys(ALL, "losses_misaligned")


def test_losses_switched_off_read_unavailable_not_zero():  # L8
    out, _, _ = _run(_net(line=None))
    assert out == dict.fromkeys(ALL, "losses_absent")


def test_one_incomplete_branch_blocks_the_network_but_not_a_complete_branch():
    n = _net(trafo=(1.0, np.nan, 1.0))
    assert _run(n)[0] == dict.fromkeys(ALL, "losses_incomplete")
    out, _, _ = _run(n, "L", metrics=("loss_mwh", "peak_loss_mw"))
    assert out == {"loss_mwh": 6.0, "peak_loss_mw": 3.0}


def test_a_transformer_is_read_from_the_transformer_frame():
    n = _net(trafo=(10.0, 0.0, 5.0))
    out, _, _ = _run(n, "T", metrics=("loss_mwh", "peak_loss_mw"))
    assert out == {"loss_mwh": 15.0, "peak_loss_mw": 10.0}


def test_the_network_peak_is_the_peak_of_the_snapshot_sum():
    # Line peaks at t2 (3), Transformer at t0 (4): sums 5, 2, 3.
    out, _, _ = _run(_net(trafo=(4.0, 0.0, 0.0)), metrics=("loss_mwh", "peak_loss_mw"))
    assert out == {"loss_mwh": 10.0, "peak_loss_mw": 5.0}


def test_a_branch_has_no_share_of_demand():
    out, _, _ = _run(_net(), "L")
    assert out["loss_pct_of_demand"] == "unsupported_metric"


# ── L1: the window ──────────────────────────────────────────────────────────


def test_a_nan_outside_the_window_does_not_block_and_one_inside_does():
    n = _net(line=(np.nan, 2.0, 3.0), load=(np.nan, 50.0, 50.0))
    out, _, _ = _run(n, start=SNAPS[1].isoformat(), end=SNAPS[2].isoformat())
    assert out == {"loss_mwh": 5.0, "peak_loss_mw": 3.0, "loss_pct_of_demand": 5.0}
    out, _, _ = _run(n, start=SNAPS[0].isoformat(), end=SNAPS[1].isoformat())
    assert out == dict.fromkeys(ALL, "losses_incomplete")


def test_a_window_without_snapshots_or_reversed_is_refused():
    n = _net()
    with pytest.raises(ValueError, match="^unsupported_snapshot_scope$"):
        _run(n, start="2031-01-01T00:00:00", end="2031-01-01T01:00:00")
    with pytest.raises(ValueError, match="^reversed_window$"):
        _run(n, start=SNAPS[2].isoformat(), end=SNAPS[0].isoformat())


# ── L2: sign ────────────────────────────────────────────────────────────────


def test_a_negative_loss_within_tolerance_reads_zero_and_is_disclosed():
    tol = lc.NEGATIVE_TOLERANCE
    assert tol == derived.NEAR_ZERO
    out, raw, scope = _run(_net(line=(-tol, 2.0, 3.0)))
    assert out["loss_mwh"] == 5.0
    assert (
        lc.notes(raw, scope)[1]
        == "1 loss values within 1e-09 MW below zero were read as 0 MW."
    )


def test_a_negative_loss_beyond_tolerance_is_invalid_never_clipped():
    below = math.nextafter(-lc.NEGATIVE_TOLERANCE, -1.0)
    out, raw, scope = _run(_net(line=(below, 2.0, 3.0)))
    assert out == dict.fromkeys(ALL, "losses_invalid")
    assert len(lc.notes(raw, scope)) == 1


# ── L3: weights ─────────────────────────────────────────────────────────────


def test_losses_use_generator_weights_not_objective_weights():
    n = _net(weights={"generators": 2.0, "objective": 5.0})
    out, _, _ = _run(n)
    # 2 × 6 MWh of loss over 2 × 300 MWh of demand.
    assert out == {"loss_mwh": 12.0, "peak_loss_mw": 3.0, "loss_pct_of_demand": 2.0}


def test_a_negative_weight_gives_no_value():
    n = _net()
    n.snapshot_weightings.loc[SNAPS[1], "generators"] = -1.0
    assert _run(n)[0] == dict.fromkeys(ALL, "invalid_or_missing_weight")


# ── L4: the demand denominator ──────────────────────────────────────────────


@pytest.mark.parametrize("bad", [np.nan, np.inf, -5.0])
def test_an_incomplete_or_negative_load_blocks_only_the_share(bad):
    out, _, _ = _run(_net(load=(100.0, bad, 100.0)))
    assert out == {
        "loss_mwh": 6.0,
        "peak_loss_mw": 3.0,
        "loss_pct_of_demand": "load_demand_incomplete",
    }


def test_no_load_demand_frame_blocks_only_the_share():
    out, _, _ = _run(_net(load=None))
    assert out["loss_pct_of_demand"] == "load_demand_absent"
    assert out["loss_mwh"] == 6.0


def test_storage_charging_and_link_consumption_are_not_demand():
    n = _net()
    n.add("StorageUnit", "s", bus="b", p_nom=50)
    n.storage_units_t.p = pd.DataFrame({"s": [-50.0, -50.0, -50.0]}, index=SNAPS)
    n.add("Link", "k", bus0="b", bus1="a", p_nom=50)
    n.links_t.p0 = pd.DataFrame({"k": [20.0, 20.0, 20.0]}, index=SNAPS)
    assert _run(n)[0]["loss_pct_of_demand"] == 2.0
    assert "storage charging and Link consumption are not counted" in lc.DISCLOSURE


def test_the_near_zero_denominator_boundary():
    at = _net(line=(0.0, 0.0, 0.0), load=(derived.NEAR_ZERO, 0.0, 0.0))
    assert _run(at)[0]["loss_pct_of_demand"] == "near_zero_denominator"
    above = math.nextafter(derived.NEAR_ZERO, 1.0)
    out, _, _ = _run(_net(line=(0.0, 0.0, 0.0), load=(above, 0.0, 0.0)))
    assert out["loss_pct_of_demand"] == 0.0


# ── L5 and scope refusals ───────────────────────────────────────────────────


@pytest.mark.parametrize("key", ["ac_pf_results", "lopf_results"])
def test_an_ac_power_flow_present_is_refused(key):
    with pytest.raises(ValueError, match="^ac_pf_present$"):
        _run(_net(), state={key: {}})


def test_an_unverified_solve_gives_no_value(monkeypatch):
    monkeypatch.setattr(profile_common, "lp_outputs_live", lambda ctx: False)
    assert _run(_net())[0] == dict.fromkeys(ALL, "unverified_solve")


def test_branch_scope_refusals():
    n = _net(trafo=(1.0, 1.0, 1.0))
    with pytest.raises(ValueError, match="^unknown_branch$"):
        _run(n, "nope")
    n.add("Transformer", "L", bus0="a", bus1="b", x=0.1, s_nom=500)
    with pytest.raises(ValueError, match="^ambiguous_branch$"):
        _run(n, "L")
    out, _, _ = _run(n, "L", metrics=("loss_mwh",), component_class="Line")
    assert out == {"loss_mwh": 6.0}
    with pytest.raises(ValueError, match="^network_scope_required$"):
        lc.source(_ctx(n), "network", False)


def test_more_branches_than_the_bound_are_refused():
    n = _net()
    n.add("Line", [f"x{i}" for i in range(lc.MAX_BRANCHES)], bus0="a", bus1="b", x=0.1)
    with pytest.raises(ValueError, match="^unsupported_branch_population$"):
        _run(n)


def test_a_raw_record_for_another_entity_is_refused():
    _, raw, _ = _run(_net())
    with pytest.raises(ValueError, match="^losses_source_identity$"):
        lc.calculate(
            raw,
            {"profile": lc.NETWORK_PROFILE, "entity_id": "L", "metrics": ["loss_mwh"]},
        )
    with pytest.raises(ValueError, match="^losses_source_identity$"):
        lc.calculate(
            raw,
            {"profile": lc.PROFILE, "entity_id": "network", "metrics": ["loss_mwh"]},
        )


# ── L8: an independent LP-with-losses oracle ────────────────────────────────


@pytest.mark.live_solve
def test_a_solved_lp_with_losses_matches_the_hand_worked_tangents():
    logging.disable(logging.CRITICAL)
    try:
        n = pypsa.Network()
        n.set_snapshots(SNAPS[:2])
        n.add("Bus", ["a", "b"], v_nom=10.0)
        n.add("Line", "L", bus0="a", bus1="b", x=0.1, r=0.05, s_nom=200.0)
        n.add("Generator", "g", bus="a", p_nom=1000.0, marginal_cost=10.0)
        n.add("Load", "d", bus="b", p_set=[100.0, 40.0])
        n.snapshot_weightings["generators"] = 2.0
        status = n.optimize(
            transmission_losses={"mode": "tangents", "segments": 2}, solver_name="highs"
        )
    finally:
        logging.disable(logging.NOTSET)
    assert status == ("ok", "optimal")
    # Tangent mode: loss = max(0, max_k r_pu (2 p_k |f| - p_k^2)), p_k = k/2 · s_nom,
    # with r_pu = r / v_nom^2 = 0.05 / 100 = 0.0005. The flow is read from the solve;
    # at 40 MW both tangents are negative, so that snapshot is a real 0.
    r_pu = 0.05 / 10.0**2
    hand = []
    # PyPSA puts half the loss at each end: p0 = s + loss/2, p1 = -s + loss/2,
    # so the tangents' flow is s = (p0 - p1) / 2.
    for p0, p1 in zip(n.lines_t.p0["L"], n.lines_t.p1["L"]):
        f = (p0 - p1) / 2
        tangents = [r_pu * (2 * p * abs(f) - p**2) for p in (100.0, 200.0)]
        hand.append(max(0.0, *tangents))
    assert hand[0] > 0 and hand[1] == 0.0  # one real loss, one real zero
    out, _, _ = _run(n, metrics=("loss_mwh", "peak_loss_mw"))
    assert out["loss_mwh"] == pytest.approx(2.0 * sum(hand), rel=1e-6)
    assert out["peak_loss_mw"] == pytest.approx(max(hand), rel=1e-6)


# ── Acceptance drivers (tests/assurance_acceptance/fact_profiles.py) ────────


@pytest.fixture
def lossy(grid):
    """The branch-loading grid, to be solved with LP transmission losses."""
    return grid


def solve_lossy(ctx):
    from services import workflow_tools

    n = ctx.network
    status = n.optimize(
        transmission_losses={"mode": "tangents", "segments": 2}, solver_name="highs"
    )
    assert status == ("ok", "optimal")
    assert not n.lines_t.loss.empty
    ctx.solver_state.update(status="completed", condition="optimal")
    workflow_tools._record_solve_inputs(n, ctx.solver_state["solver_config"])
    return ctx


# ── L6: every reader of the contract accepts the new profiles ───────────────


@pytest.mark.parametrize(
    "profile, entity, ok",
    [
        ("branch-losses/1", "L12", True),
        ("network-losses/1", "network", True),
        ("branch-losses/1", "network", False),
        ("network-losses/1", "L12", False),
    ],
)
def test_the_scope_validator_accepts_each_profile_with_its_entity(profile, entity, ok):
    from harness import assurance_scope

    scope = {"version": 1, "profile": profile, "project_id": "p", "entity_id": entity}
    if ok:
        assert assurance_scope.validate(scope)["profile"] == profile
    else:
        with pytest.raises(ValueError, match="network_scope_required"):
            assurance_scope.validate(scope)


def test_the_scope_validator_refuses_costs_and_takes_a_branch_class():
    from harness import assurance_scope

    base = {
        "version": 1,
        "profile": "branch-losses/1",
        "project_id": "p",
        "entity_id": "L",
    }
    with pytest.raises(ValueError, match="unsupported_result_cost_scope"):
        assurance_scope.validate({**base, "comparison_evidence_id": "0" * 36})
    assert assurance_scope.validate({**base, "component_class": "Transformer"})
    with pytest.raises(ValueError, match="unsupported_component_class"):
        assurance_scope.validate({**base, "component_class": "Link"})


def test_the_contract_snapshot_and_the_frontend_list_both_profiles():
    import json
    from pathlib import Path

    from harness import assurance_contracts

    live = assurance_contracts.build()
    frozen = json.loads(
        (
            Path(assurance_contracts.__file__).parent / "assurance_contracts.v1.json"
        ).read_text()
    )
    for contract in (live, frozen):
        assert contract["profiles"]["branch-losses/1"]["metrics"] == [
            "loss_mwh",
            "peak_loss_mw",
        ]
        assert contract["profiles"]["network-losses/1"]["metrics"] == list(ALL)
    frontend = Path(__file__).parents[2] / "frontend/src/api/checkedAnswers.ts"
    text = frontend.read_text()
    assert "'branch-losses/1'" in text and "'network-losses/1'" in text


# ── G8: one test per remaining guard ────────────────────────────────────────


def test_weights_not_on_the_snapshots_are_refused(monkeypatch):
    from services import period_utils

    monkeypatch.setattr(
        period_utils,
        "snapshot_weights",
        lambda n, column: pd.Series(1.0, index=SNAPS[:2]),
    )
    with pytest.raises(ValueError, match="^misaligned_source$"):
        _run(_net())


def test_a_branch_named_network_is_checkable_by_its_class():
    n = _net(line=None)
    n.add("Line", "network", bus0="a", bus1="b", x=0.1)
    n.lines_t.loss = pd.DataFrame({"L": [1.0] * 3, "network": [2.0] * 3}, index=SNAPS)
    out, _, _ = _run(n, "network", metrics=("loss_mwh",), component_class="Line")
    assert out == {"loss_mwh": 6.0}


def test_exactly_the_branch_bound_is_allowed():
    n = _net()
    n.add(
        "Line", [f"x{i}" for i in range(lc.MAX_BRANCHES - 1)], bus0="a", bus1="b", x=0.1
    )
    _, raw, _ = _run(n)
    assert len(raw["branches"]) == lc.MAX_BRANCHES


def test_a_raw_record_of_another_version_is_refused():
    _, raw, scope = _run(_net())
    with pytest.raises(ValueError, match="^losses_source_identity$"):
        lc.calculate({**raw, "version": 2}, scope)


def test_a_missing_weight_gives_no_value():
    n = _net()
    n.snapshot_weightings.loc[SNAPS[1], "generators"] = np.nan
    assert _run(n)[0] == dict.fromkeys(ALL, "invalid_or_missing_weight")


def test_a_zero_weight_is_a_valid_weight():
    n = _net()
    n.snapshot_weightings.loc[SNAPS[1], "generators"] = 0.0
    # 1 + 3 MWh of loss over 200 MWh of demand.
    assert _run(n)[0] == {
        "loss_mwh": 4.0,
        "peak_loss_mw": 3.0,
        "loss_pct_of_demand": 2.0,
    }


def test_a_network_without_loads_has_no_share_of_demand():
    n = _net(load=None)
    n.remove("Load", "d")
    out, _, _ = _run(n)
    assert out == {
        "loss_mwh": 6.0,
        "peak_loss_mw": 3.0,
        "loss_pct_of_demand": "load_demand_absent",
    }


def test_notes_count_only_values_just_below_zero():
    for line in [(0.0, 0.0, 0.0), (np.nan, 0.0, 1.0)]:
        _, raw, scope = _run(_net(line=line))
        assert len(lc.notes(raw, scope)) == 1
