# ruff: noqa: F811  (pytest fixtures imported by name)
"""MIP gap band on retained cost comparisons and the gap disclosure
(assistant-assurance 10, S2; GK B G2c, G2e).

The baseline (260 EUR) and candidate (230 EUR) are test_harness_reliability's
hand-worked LP; delta -30 EUR. The solve classification is what
`solve_validity.capture` seals into each raw record; here it is set per run so
the band can be placed exactly at, and one ULP past, |delta| = 30. Whether
`classify` reads a real solve correctly is pinned separately against the live
solve path in test_solve_validity.py.
"""

import math

import pytest

from harness import solve_validity
from services import workflow_tools
from tests.assurance_acceptance.support import approval, public_text
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_assurance_metrics import NumericalProvider, solved  # noqa: F401
from tests.test_harness_reliability import VALUES, scope
from tests.test_workflow_tools import baseline  # noqa: F401

LP = {"kind": "lp", "gap": 0.0, "optimal": True, "disclosure": None}


def _mip(gap, objective):
    return {
        "kind": "mip",
        "gap": gap,
        "optimal": False,
        "objective": objective,
        "disclosure": f"Integer (MIP) solve to a relative optimality gap of {gap:.3g}; "
        "costs may exceed the optimum by up to that share.",
    }


def _compare(solved, checked, monkeypatch, base_solve, cand_solve):
    solved.network.generators.at["G1", "capital_cost"] = 4.5
    workflow_tools._record_solve_inputs(
        solved.network, solved.solver_state["solver_config"]
    )
    monkeypatch.setattr(
        solve_validity, "capture", lambda ctx: dict(base_solve) if base_solve else None
    )
    base_values = {
        **VALUES,
        "annual_fixed_cost": 90,
        "allocated_fixed_cost": 90,
        "total_cost": 260,
    }
    base_frames = checked.run(
        NumericalProvider(base_values), checked_scope=scope(solved, retain=True)
    )
    eid = approval(base_frames)["receipt"]["evidence_ids"][0]
    solved.network.generators.at["G1", "capital_cost"] = 3
    workflow_tools._record_solve_inputs(
        solved.network, solved.solver_state["solver_config"]
    )
    monkeypatch.setattr(solve_validity, "capture", lambda ctx: dict(cand_solve))
    request_scope = scope(
        solved,
        metrics=["total_cost", "cost_delta", "cost_change_percent"],
        comparison_evidence_id=eid,
    )
    values = {
        "total_cost": 230,
        "cost_delta": -30,
        "cost_change_percent": -30 / 260 * 100,
    }
    return checked.run(NumericalProvider(values), checked_scope=request_scope)


def _slots(frames):
    return {
        s["metric"]: (s["disposition"], s.get("reason"))
        for s in approval(frames)["receipt"]["result_slots"]
    }


def test_two_lps_compare_as_before_with_no_gap_line(solved, checked, monkeypatch):
    frames = _compare(
        solved,
        checked,
        monkeypatch,
        {**LP, "objective": 260.0},
        {**LP, "objective": 230.0},
    )
    assert approval(frames)["verdict"] == "passed"
    assert "optimality gap" not in public_text(frames)


def test_a_delta_at_the_combined_band_is_limited(solved, checked, monkeypatch):
    # band = 0.5·|-30| + 0.5·|30| = 30 = |delta| (exact in binary)
    frames = _compare(solved, checked, monkeypatch, _mip(0.5, -30.0), _mip(0.5, 30.0))
    assert _slots(frames) == {
        "total_cost": ("answered", None),
        "cost_delta": ("unavailable", solve_validity.DELTA_IN_GAP),
        "cost_change_percent": ("unavailable", solve_validity.DELTA_IN_GAP),
    }
    assert approval(frames)["verdict"] == "limited"
    assert "relative optimality gap of 0.5" in public_text(frames)


def test_a_delta_one_ulp_past_the_band_is_answered_with_the_gap_named(
    solved, checked, monkeypatch
):
    below = math.nextafter(30.0, 0.0)  # band = 0.5·below·2 = one ULP under 30
    assert 0.5 * below + 0.5 * below < 30.0
    frames = _compare(solved, checked, monkeypatch, _mip(0.5, below), _mip(0.5, below))
    assert {m: d for m, (d, _) in _slots(frames).items()} == {
        "total_cost": "answered",
        "cost_delta": "answered",
        "cost_change_percent": "answered",
    }
    assert "relative optimality gap of 0.5" in public_text(frames)


def test_an_lp_baseline_counts_zero_in_the_band(solved, checked, monkeypatch):
    # band = 0 (LP) + 0.1·|299| = 29.9 < 30: answered
    frames = _compare(
        solved, checked, monkeypatch, {**LP, "objective": 260.0}, _mip(0.1, 299.0)
    )
    assert _slots(frames)["cost_delta"] == ("answered", None)


def test_an_unrecorded_gap_on_either_side_limits_the_delta(
    solved, checked, monkeypatch
):
    unrecorded = {
        "kind": "mip",
        "gap": None,
        "optimal": False,
        "objective": 260.0,
        "disclosure": "Integer (MIP) solve; the relative optimality gap was not recorded.",
    }
    frames = _compare(
        solved, checked, monkeypatch, unrecorded, {**LP, "objective": 230.0}
    )
    assert _slots(frames)["cost_delta"] == (
        "unavailable",
        solve_validity.GAP_NOT_RECORDED,
    )


@pytest.mark.parametrize("old", [None])
def test_a_baseline_sealed_before_this_check_establishes_no_sign(
    solved, checked, monkeypatch, old
):
    frames = _compare(solved, checked, monkeypatch, old, {**LP, "objective": 230.0})
    assert _slots(frames)["cost_delta"] == (
        "unavailable",
        solve_validity.GAP_NOT_RECORDED,
    )
