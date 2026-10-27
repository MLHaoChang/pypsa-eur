"""The standalone load flow refuses what the chained Stage 2 refuses (harness 24).

A dispatch counts as ``fresh`` while its column set matches, so an attribute
edit after the solve still passes the dispatch checks. `/run_ac_pf` and the
`loadflow` verdict also read the Stage-2 errors of
``validation_service._check_stage2_ac_pf``; its warnings do not refuse.
"""
from __future__ import annotations

from services import run_separability as RS
from tests.test_run_separability import _line_network, _wait_idle
from tests.test_project_flow import edit, set_cfg, site  # noqa: F401  (fixtures)


def _solve(client):
    assert client.post("/api/simulation/run").status_code == 200
    assert _wait_idle(client)["status"] == "completed"


def _refused(client, bound_ctx, kind):
    with bound_ctx(client):
        v = RS.loadflow_verdict()
    assert v["ready"] is False and v["blocker"]["code"] == kind
    r = client.post("/api/simulation/run_ac_pf")
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == {"error_kind": kind, "message": v["blocker"]["message"]}


def test_a_zero_impedance_edit_after_the_solve_is_refused(client, site, bound_ctx, edit):
    site(_line_network(), name="pf_zimp")
    _solve(client)
    with edit() as n:
        n.lines.loc["ab", ["r", "x"]] = 0.0
    _refused(client, bound_ctx, "stage2_zero_impedance")


def test_multi_investment_periods_is_refused(client, site, bound_ctx, set_cfg):
    site(_line_network(), name="pf_mip")
    _solve(client)
    set_cfg(multi_investment_periods=True)
    _refused(client, bound_ctx, "stage2_multi_period")


def test_a_stage_2_warning_does_not_refuse(client, site, bound_ctx, edit):
    site(_line_network(), name="pf_warn")
    _solve(client)
    with edit() as n:
        n.generators.loc["g", "control"] = "PQ"   # stage2_no_explicit_slack (warning)
    with bound_ctx(client):
        assert RS.loadflow_verdict()["ready"] is True
    assert client.post("/api/simulation/run_ac_pf").status_code == 200
    assert _wait_idle(client)["status"] == "completed"


def test_a_valid_solved_network_still_starts(client, site, bound_ctx):
    site(_line_network(), name="pf_ok")
    _solve(client)
    with bound_ctx(client):
        assert RS.loadflow_verdict() == {"ready": True, "deferred": False, "blocker": None}
    assert client.post("/api/simulation/run_ac_pf").status_code == 200
    assert _wait_idle(client)["status"] == "completed"
