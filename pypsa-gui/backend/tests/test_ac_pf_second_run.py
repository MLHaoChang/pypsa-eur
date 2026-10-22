"""A second `/run_ac_pf` on a network where AC PF already ran is refused by name.

The first PF overwrites the LP flows and voltages in place and keeps the LP's
aside in `lopf_results`. The claim nulls `lopf_results`, so a second PF would
snapshot the PF-overwritten network as the "LOPF" baseline and fix dispatch from
the slack-adjusted p. Until a new LOPF, the route, the load-flow verdict and the
chat tool refuse with `ac_pf_already_run`.
"""
from __future__ import annotations

from services import run_separability as RS
from services.pypsa_service import PyPSAService
from tests.test_run_separability import _line_network, _wait_idle
from tests.test_project_flow import site  # noqa: F401  (fixture)


def _run_and_pf(client):
    assert client.post("/api/simulation/run").status_code == 200
    _wait_idle(client)
    assert client.post("/api/simulation/run_ac_pf").status_code == 200
    assert _wait_idle(client)["status"] == "completed"


def test_second_ac_pf_is_refused_and_keeps_the_lp_baseline(client, site, bound_ctx):
    site(_line_network(), name="acpf2")
    _run_and_pf(client)
    with bound_ctx(client):
        before = PyPSAService.get_solver_state()["lopf_results"]
        assert before is not None
        v = RS.loadflow_verdict()
    assert v["ready"] is False and v["blocker"]["code"] == "ac_pf_already_run"

    r = client.post("/api/simulation/run_ac_pf")
    assert r.status_code == 400 and r.json()["detail"] == v["blocker"]["message"]
    with bound_ctx(client):
        st = PyPSAService.get_solver_state()
        assert st["lopf_results"] is before
        assert st["ac_pf_results"] is not None


def test_ac_pf_is_allowed_again_after_a_new_lopf(client, site, bound_ctx):
    site(_line_network(), name="acpf3")
    _run_and_pf(client)
    assert client.post("/api/simulation/run").status_code == 200
    _wait_idle(client)
    assert client.post("/api/simulation/run_ac_pf").status_code == 200
    assert _wait_idle(client)["status"] == "completed"
