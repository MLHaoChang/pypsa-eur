"""
The Results economics endpoint must forward the block's `available` flag.

`_compute_economics_summary` sets `available` (previous branch, Task 4), but
`get_economics_by_carrier` returned only `by_carrier` — so the whole
Compare-side availability fix stopped at Compare, and the Results tab had no
way to tell a real zero from a figure that was never resolved. Same ADR-0001
property as every block on that branch, one wire short.

Two drop sites, not one:

  1. `if not _dispatch_ready(n): return {}` — an unsolved network got a bare
     `{}`, indistinguishable from "solved, and this network genuinely has no
     carriers". The wider hole of the two.
  2. the success path's `{"by_carrier": ...}` — dropped the computed flag.

Both are covered below. Fixing only the second would leave the unsolved case
still lying, which is the case a user hits first.
"""
from __future__ import annotations

import pandas as pd
import pypsa

from services.pypsa_service import PyPSAService


def _two_bus_network(solve: bool) -> pypsa.Network:
    """A network that solves cleanly, optionally left unsolved."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.add("Bus", "b1", carrier="AC")
    n.add("Carrier", "AC")
    n.add("Carrier", "gas")
    n.add(
        "Generator", "gas",
        bus="b1", carrier="gas",
        p_nom=100.0, marginal_cost=50.0, capital_cost=1000.0,
    )
    n.add("Load", "load1", bus="b1", p_set=20.0)
    if solve:
        n.optimize(solver_name="highs")
    return n


def test_unsolved_network_reports_unavailable_rather_than_an_empty_body(monkeypatch):
    """
    Drop site 1. A bare `{}` cannot be distinguished from a solved network
    whose carriers roll up to nothing, so the Results tab renders zeros for
    an absence — ADR-0001's exact failure mode.
    """
    import routers.results as R

    n = _two_bus_network(solve=False)
    monkeypatch.setattr(PyPSAService, "get_network", staticmethod(lambda: n))

    payload = R.get_economics_by_carrier()

    assert "available" in payload, (
        "an unsolved network must SAY it is unresolved; `{}` is "
        "indistinguishable from a measured empty result — see ADR-0001"
    )
    assert payload["available"] is False
    assert payload["by_carrier"] == {}


def test_solved_network_forwards_the_computed_available_flag(monkeypatch):
    """
    Drop site 2. The block computes `available`; the endpoint must not
    discard it on the way out.
    """
    import routers.results as R

    n = _two_bus_network(solve=True)
    monkeypatch.setattr(PyPSAService, "get_network", staticmethod(lambda: n))

    payload = R.get_economics_by_carrier()

    assert "available" in payload, (
        "the block computes `available`; dropping it at the wire means the "
        "Results tab cannot distinguish a real zero from an unresolved figure"
    )
    assert payload["available"] is True
    assert "by_carrier" in payload
    assert "gas" in payload["by_carrier"]
