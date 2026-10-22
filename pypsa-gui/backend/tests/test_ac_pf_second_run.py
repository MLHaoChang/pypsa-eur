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
    assert r.status_code == 400 and r.json()["detail"]["message"] == v["blocker"]["message"]
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


# ── the other ways in ───────────────────────────────────────────────────────

import numpy as np
import pypsa
import pytest
from fastapi import HTTPException

from services import chat_tools
from services.project_context import cleared_result_state
from tests.test_project_flow import edit, set_cfg  # noqa: F401  (fixtures)


def test_the_refusal_follows_the_context_not_the_process(client, site, bound_ctx):
    """A project whose PF never ran stays allowed beside one where it did."""
    site(_line_network(), name="pf_ran")
    _run_and_pf(client)
    site(_line_network(), name="pf_fresh")
    assert client.post("/api/simulation/run").status_code == 200
    _wait_idle(client)
    assert client.post("/api/simulation/run_ac_pf").status_code == 200
    assert _wait_idle(client)["status"] == "completed"
    assert client.post("/api/projects/pf_ran/activate").status_code == 200
    r = client.post("/api/simulation/run_ac_pf")
    assert r.status_code == 400 and r.json()["detail"]["error_kind"] == "ac_pf_already_run"


def test_the_refusal_survives_save_and_reload(client, site):
    site(_line_network(), name="pf_reload")
    _run_and_pf(client)
    r = client.post("/api/projects/pf_reload", params={"force": True})
    assert r.status_code == 200, r.text
    with PyPSAService._registry_lock:
        for key, ctx in list(PyPSAService._contexts.items()):
            if ctx.loaded_project == "pf_reload":
                PyPSAService._contexts.pop(key, None)
    assert client.post("/api/projects/pf_reload/activate").status_code == 200
    r = client.post("/api/simulation/run_ac_pf")
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["error_kind"] == "ac_pf_already_run"


def test_a_pf_that_raises_midway_leaves_the_lp_values(client, site, bound_ctx, monkeypatch):
    site(_line_network(), name="pf_fail")
    assert client.post("/api/simulation/run").status_code == 200
    _wait_idle(client)
    with bound_ctx(client):
        n = PyPSAService.get_network()
        lp_p0 = n.lines_t.p0.copy()
        lp_gen = n.generators_t.p.copy()
    real_pf = pypsa.Network.pf

    def _half_pf(self, *a, **k):
        self.lines_t.p0 = self.lines_t.p0 + 7.0
        self.generators_t.p = self.generators_t.p + 7.0
        raise RuntimeError("boom")

    monkeypatch.setattr(pypsa.Network, "pf", _half_pf)
    assert client.post("/api/simulation/run_ac_pf").status_code == 200
    assert _wait_idle(client)["status"] == "failed"
    with bound_ctx(client):
        n = PyPSAService.get_network()
        np.testing.assert_allclose(n.lines_t.p0, lp_p0)
        np.testing.assert_allclose(n.generators_t.p, lp_gen)
    monkeypatch.setattr(pypsa.Network, "pf", real_pf)
    assert client.post("/api/simulation/run_ac_pf").status_code == 200
    assert _wait_idle(client)["status"] == "completed"
    with bound_ctx(client):
        base = PyPSAService.get_solver_state()["lopf_results"]["lines_t.p0"]
    np.testing.assert_allclose(base, lp_p0)


def test_the_chained_stage_2_also_blocks_a_standalone_run(client, site, set_cfg):
    site(_line_network(), name="pf_chain")
    set_cfg(run_ac_pf_after_lopf=True)
    assert client.post("/api/simulation/run").status_code == 200
    assert _wait_idle(client)["status"] == "completed"
    r = client.post("/api/simulation/run_ac_pf")
    assert r.status_code == 400 and r.json()["detail"]["error_kind"] == "ac_pf_already_run"


def test_every_claim_wipes_ac_pf_results():
    """/run and the solve_queue claim both splat this."""
    assert cleared_result_state()["ac_pf_results"] is None
    assert "ac_pf_results" in cleared_result_state()


def test_a_topology_edit_after_pf_still_says_rerun_lopf(client, site, edit):
    site(_line_network(), name="pf_edit")
    _run_and_pf(client)
    with edit() as n:
        n.add("Load", "l2", bus="a", p_set=1.0)
    r = client.post("/api/simulation/run_ac_pf")
    assert r.status_code == 400
    assert "ac_pf_already_run" not in r.text      # the dispatch check answers first


def test_the_chat_tool_carries_the_kind(client, site, bound_ctx):
    site(_line_network(), name="pf_chat")
    _run_and_pf(client)
    with bound_ctx(client), pytest.raises(HTTPException) as ei:
        chat_tools.run_ac_pf_stage()
    assert ei.value.detail["error_kind"] == "ac_pf_already_run"
