"""Study harness issue 32 (SP-1, GK B S6): the standalone `/run_ac_pf` stage
fixes dispatch into `p_set` and sets generator controls in place, then reverts
them. A reader outside the lock mid-stage hashes the inputs from before it."""

# ruff: noqa: F811 -- imported pytest fixtures
from __future__ import annotations

from services import ac_pf_service, workflow_tools as wt
from services.pypsa_service import PyPSAService
from tests.test_project_flow import site  # noqa: F401, F811  (fixture)
from tests.test_run_separability import _line_network, _wait_idle


def _identity():
    c = PyPSAService.get_active_context()
    return wt._network_input_fingerprint(c), wt._network_input_fingerprint(
        c, exact=True
    )


def test_a_reader_mid_route_stage_hashes_the_inputs_from_before_it(
    client, site, bound_ctx, monkeypatch
):
    site(_line_network(), name="acpfid")
    assert client.post("/api/simulation/run").status_code == 200
    _wait_idle(client)
    with bound_ctx(client):
        before = _identity()
    original = ac_pf_service._fix_dispatch_for_ac_pf
    mid = []

    def reading(network):
        original(network)
        assert len(network.generators_t.p_set.columns)  # the transient is on
        mid.append(_identity())

    monkeypatch.setattr(ac_pf_service, "_fix_dispatch_for_ac_pf", reading)
    assert client.post("/api/simulation/run_ac_pf").status_code == 200
    assert _wait_idle(client)["status"] == "completed"
    with bound_ctx(client):
        after = _identity()
    assert mid == [before] and after == before
    assert not wt._SOLVE_INPUTS
