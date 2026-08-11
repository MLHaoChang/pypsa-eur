"""No Comparison block may ship a figure without saying whether it resolved.

ADR-0001: zero is a legitimate result in an energy-system model, so an
unresolvable figure must never be indistinguishable from a real zero. Every
block therefore carries `available`, and `available=False` is the only way to
ship the default zeros.
"""
from __future__ import annotations

import inspect
import types

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


@pytest.fixture()
def golden_summary(reset_backend):
    """
    The nine Comparison blocks for the solved golden network, built by the
    same `_compute_*_summary` functions `get_results_summary` calls — see
    `tests/compare_support.py`. Wrapped in a `SimpleNamespace` so callers can
    use `golden_summary.economics` / `.capacity` etc., the same attribute
    names `ResultsSummary` uses for these fields.

    `reset_backend` (autouse, conftest.py) resets `PyPSAService` AND
    `solver_config` to a bare default both before and after every test;
    `install_golden` re-pins the golden discount rate / investment periods
    afterwards, the same trap `test_compare_endpoint.py`'s `golden_project`
    fixture documents for `install_network` — without the re-pin, every
    overnight_cost-priced asset's CAPEX resolves against the wrong discount
    rate.
    """
    n = gf.solve_golden_network()
    gf.install_golden(n)
    return types.SimpleNamespace(**cs.summarise(n))


def test_solved_golden_project_reports_available(golden_summary):
    """The golden fixture is solved, so its populated blocks must say so."""
    assert golden_summary.economics.available is True
    assert golden_summary.capacity.available is True
