"""No Comparison block may ship a figure without saying whether it resolved.

ADR-0001: zero is a legitimate result in an energy-system model, so an
unresolvable figure must never be indistinguishable from a real zero. Every
block therefore carries `available`, and `available=False` is the only way to
ship the default zeros.
"""
from __future__ import annotations

import inspect

import pytest

from models import schemas
from tests import compare_support as cs
from tests.golden import fixture as gf


def _comparison_models():
    for name, obj in vars(schemas).items():
        if (
            inspect.isclass(obj)
            and name.endswith("Comparison")
            and hasattr(obj, "model_fields")
        ):
            yield name, obj


def test_every_comparison_block_declares_available():
    missing = [n for n, m in _comparison_models() if "available" not in m.model_fields]
    assert missing == [], (
        f"these Comparison blocks can ship a zero indistinguishable from a real "
        f"result: {missing}"
    )


def test_available_defaults_to_false():
    wrong = [
        n for n, m in _comparison_models()
        if m.model_fields["available"].default is not False
    ]
    assert wrong == [], (
        f"a default-constructed block is the early-return path and has resolved "
        f"nothing, so it must default to unavailable: {wrong}"
    )


def test_at_least_nine_blocks_are_covered():
    assert len(list(_comparison_models())) >= 9, (
        "the suite found fewer blocks than exist — the discovery filter is wrong"
    )


def _unsolved_network():
    """Assets present, never optimised — `p_nom_opt` is still 0 everywhere."""
    import pandas as pd
    import pypsa

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    n.add("Bus", "B")
    n.add("Carrier", "gas")
    n.add("Generator", "g", bus="B", carrier="gas", p_nom=100.0, capital_cost=1000.0)
    n.add("Load", "L", bus="B", p_set=50.0)
    return n


def test_capacity_on_an_unsolved_network_is_not_available():
    """
    The negative direction, which the golden-fixture test cannot reach.

    `_compute_capacity_summary` is the ONE compute function with no
    `if not has_solve` early return — it takes the flag and never reads it.
    Setting a bare `available=True` on its success path therefore claims the
    figures resolved on a network that was never optimised, where `p_nom_opt`
    defaults to 0 so the capacity walk skips every asset: all-empty figures,
    labelled available. Exactly the fabricated-zero ADR-0001 forbids, in the
    code meant to prevent it.
    """
    import routers.compare as CMP

    block = CMP._compute_capacity_summary(_unsolved_network(), [], False, False)
    assert block.available is False, (
        "capacity reports available=True on an unsolved network — `available` "
        "must track has_solve, not be a bare True"
    )


def test_capacity_on_a_solved_network_is_available(golden):
    """The other direction, so the fix cannot be a blanket False."""
    import routers.compare as CMP

    block = CMP._compute_capacity_summary(golden, list(golden.investment_periods), True, True)
    assert block.available is True


@pytest.fixture()
def golden(reset_backend):
    n = gf.solve_golden_network()
    gf.install_golden(n)
    return n


def test_solved_golden_project_reports_available(golden):
    """
    The other half of the contract. The schema tests above only prove the flag
    EXISTS and defaults to False — which a compute function that never sets it
    would also satisfy, shipping real figures marked unavailable. This pins the
    success path: the golden fixture is solved, so its populated blocks say so.
    """
    summary = cs.summarise(golden)
    for tab in ("capacity", "dispatch", "economics", "prices", "emissions"):
        assert summary[tab].available is True, (
            f"{tab} computed real figures from a solved network but reports "
            f"available=False — the success path does not set the flag"
        )
