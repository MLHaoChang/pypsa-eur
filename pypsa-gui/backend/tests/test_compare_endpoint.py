"""
Task 19: prove the values the `_compute_*_summary` functions produce
actually reach the HTTP response.

Every prior Compare-tab suite (test_compare_contract.py,
test_compare_invariants.py, test_compare_cross_surface.py) calls the
`_compute_*_summary` functions directly, in-process, on an installed
network — never through `GET /api/projects/{name}/results-summary` itself.
That endpoint takes `project: AuthorizedProject = ProjectAccessDep` and
reads `project.directory / "network.nc"`, so exercising it for real needs a
DB-backed project row plus org-scoped storage (the `client` fixture's
authenticated session + `install_network`'s singleton binding + a real
`POST /api/projects/{name}` save), not just an in-memory network.

`ResultsSummary`'s own docstring says later phases "fill in additional
optional fields on the same payload" — so a tab whose compute function is
provably correct (every other test in this suite checks that) but never
gets wired into the endpoint's return statement is a live failure mode
none of the existing tests are positioned to catch. This file is.
"""
from __future__ import annotations

import copy

import pytest

from services.solver_service import SolverConfig
from tests import compare_support as cs
from tests.golden import fixture as gf


@pytest.fixture()
def golden_project(client, install_network, reset_backend):
    """
    Save the SOLVED GOLDEN network as a real, org-scoped project via the
    authenticated `client` — mirrors `api_project`'s own recipe
    (conftest.py) but installs the golden network (multi-period, non-trivial
    CAPEX/dispatch/loading/prices/emissions/curtailment content) instead of
    conftest's tiny single-bus demo network, so every one of the nine tabs
    has something real to report and the numbers can be cross-checked
    against `compare_support.summarise()` for the identical network.

    Two things this fixture is careful about:

    1. Deep-copies the module-cached golden network before installing.
       `install_network` mutates `n.name` in place, and `solve_golden_
       network()` hands every caller in the process the SAME cached
       object — mutating it here would leak into every other test file
       that reads `gf.solve_golden_network()` later in the same run.
    2. `install_network` resets `routers.simulation._state["solver_config"]`
       to a bare `SolverConfig()` (its own documented default-restoring
       behaviour, conftest.py:524) — re-pin the golden discount rate /
       investment periods afterwards, the same way `fixture.install_golden`
       does, or every overnight_cost-priced asset's CAPEX resolves against
       the wrong discount rate and disagrees with
       `compare_support.summarise()` for a reason that has nothing to do
       with the endpoint under test.
    """
    import routers.simulation as sim_router

    n = copy.deepcopy(gf.solve_golden_network())
    name = "golden-endpoint-check"
    install_network(n, name=name)
    sim_router._state["solver_config"] = SolverConfig(
        discount_rate=gf.GOLDEN_DISCOUNT_RATE,
        multi_investment_periods=True,
        investment_periods=list(gf.GOLDEN_PERIODS),
    )
    resp = client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert resp.status_code == 200, resp.text
    return name, n


def test_the_summary_endpoint_serialises_every_tab(client, golden_project):
    name, n = golden_project

    resp = client.get(f"/api/projects/{name}/results-summary")
    assert resp.status_code == 200, resp.text
    payload = resp.json()

    assert payload["has_solve"] is True
    missing = [f for f in cs.TAB_FIELDS if payload.get(f) is None]
    assert not missing, (
        "results-summary served null for tabs the compute functions "
        f"populate correctly in-process: {missing} — see ResultsSummary's "
        "docstring ('future phases will fill in additional optional "
        "fields'): a tab computing correctly and never reaching the wire "
        "is exactly the failure mode this test exists to catch"
    )

    # Numeric cross-check: the SAME network, summarised in-process
    # (compare_support.summarise, which calls the identical
    # _compute_capacity_summary the endpoint calls), must produce the
    # identical capex_meur_by_carrier the HTTP endpoint served — proving the
    # VALUES, not just their presence, survived the netcdf round-trip +
    # HTTP/Pydantic serialisation layer intact.
    expected = cs.summarise(n)["capacity"].capex_meur_by_carrier
    got = payload["capacity"]["capex_meur_by_carrier"]
    assert expected, "golden network reported no capex_meur_by_carrier at all — fixture regressed"
    assert set(got) == set(expected), (
        f"capacity.capex_meur_by_carrier carriers differ between the live "
        f"endpoint {sorted(got)} and compare_support.summarise {sorted(expected)}"
    )
    for carrier, cpv in expected.items():
        assert got[carrier]["total"] == pytest.approx(cpv.total, rel=1e-9), carrier
        for period, value in cpv.by_period.items():
            assert got[carrier]["by_period"][period] == pytest.approx(value, rel=1e-9), (
                f"{carrier} by_period[{period}]"
            )
