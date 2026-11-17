# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Study harness 15: grid_only@1 on the shipped 3bus and ieee14 templates, as
the run's own project: the real runner and a real solve with Load flow on reach
the hand-off, and a seeded overload ends the run for the user with no agent
edit. The rules are pinned in `test_run_profile_grid_only.py`."""

from __future__ import annotations

import json

import pytest

from harness import runner as harness_runner
from services import chat_tools, flow_service, project_registry
from services.pypsa_service import PyPSAService
from tests.test_assistant_tasks_study_brief import helpers_state, session  # noqa: F401
from tests.test_harness_envelope import granted, profile  # noqa: F401
from tests.test_harness_run_loops import Script, go, rated  # noqa: F401
from tests.test_harness_run_report_handoff import fake_report  # noqa: F401
from tests.test_harness_run_skeleton import RUN
from tests.test_harness_runner import detached, fake_report_provider  # noqa: F401

REF = "grid_only@1"
FIXTURES = (granted, rated, profile)


@pytest.fixture(params=["3bus", "ieee14"])
def baseline(request, client, session_ctx):
    """The shipped template created through the route as a user does, Load flow
    on, saved once as the run's hand-over does, then bound as the run's project."""
    name = f"Grid {request.param}"
    response = client.post(
        f"/api/projects/from_template/{request.param}", params={"name": name}
    )
    assert response.status_code == 200, response.text
    response = client.put(
        "/api/simulation/solver_config", json={"run_ac_pf_after_lopf": False}
    )
    assert response.status_code == 200, response.text
    response = client.post(f"/api/projects/{name}")
    assert response.status_code == 200, response.text
    ctx = session_ctx(client)
    binding = PyPSAService.bind_request_context(ctx)
    cell = PyPSAService.bind_turn_cell()
    try:
        with chat_tools._acting() as (db, user):
            project = project_registry.resolve_project(db, user, name)
            path = project_registry.project_dir(project)
        yield ctx, str(project.id), path
    finally:
        PyPSAService._turn_cell.reset(cell)
        PyPSAService.reset_request_context(binding)


@pytest.fixture
def site(baseline, request):
    _, project_id, root = baseline
    (root / flow_service.TEMPLATE_FILE).write_text(
        json.dumps({"id": request.node.callspec.params["baseline"]})
    )
    return project_id, root


def _profile_dict():
    from services import run_profiles

    return run_profiles.resolve(REF)


def test_a_run_with_load_flow_on_reaches_the_hand_off_with_the_network_chapters(
    granted, rated, site, monkeypatch
):
    from routers import report_jobs

    enqueued = []
    real_start = report_jobs._start
    monkeypatch.setattr(
        report_jobs, "_start", lambda *a, **k: enqueued.append(k) or real_start(*a, **k)
    )
    task, path, _ = granted(run={**RUN, "profile": REF})
    script = Script()
    record = go(harness_runner.Runner("a", agent_provider=script), path, task)
    run = record["run"]
    assert run["status"] == "completed" and run["stage"] == "handoff", (
        run["status"],
        run["stage"],
        [(s["id"], s["status"], s.get("error")) for s in record["steps"]],
    )
    rules = {
        r["rule"]: r["verdict"] for r in run["stages"]["result_gate"]["gate"]["rules"]
    }
    assert rules == {
        "res.solve.status": "pass",
        "res.fresh": "pass",
        "grid.pf.converged": "pass",
        "grid.pf.loading": "pass",
        "grid.pf.voltage": "not_evaluated",
        "grid.n1.secured": "not_evaluated",
    }
    assert run["stages"]["explore"]["status"] == "skipped" and script.requests == []
    assert run["stages"]["handoff"]["gate"]["passed"] is True
    # GK B (61) SH-15: no report job is ever enqueued; the stage reads skipped, naming issue 50.
    report = run["stages"]["report"]
    assert enqueued == [] and report["status"] == "skipped", report
    assert "issue 50" in report.get("reason", ""), report
    # GK B SH-15 A1: no LP figure is read back after the flow rewrote the live dispatch (issue 50).
    assert run["handoff"]["kpis"] == []


def test_a_seeded_overload_fails_the_loading_rule_and_the_run_asks_the_user_with_no_edit(
    granted, rated, site, monkeypatch
):
    """The loading arithmetic is pinned on real flows in `test_run_profile_grid_only.py`; the
    solve keeps every line within its P rating, so the overload is seeded at the reader."""
    from services import run_grid_pf

    monkeypatch.setattr(
        run_grid_pf, "loading", lambda ctx, ev: (118.4, ["Line 0-1 118.4 %"])
    )
    task, path, _ = granted(run={**RUN, "profile": REF})
    script = Script()  # no change to propose: the L2 round diagnoses and ends
    record = go(harness_runner.Runner("a", agent_provider=script), path, task)
    run = record["run"]
    assert run["status"] == "needs_user" and run["stage"] == "result_gate"
    rules = {r["rule"]: r for r in run["stages"]["result_gate"]["gate"]["rules"]}
    assert rules["grid.pf.loading"]["verdict"] == "fail"
    assert rules["grid.pf.loading"]["facts"][0]["value"] == 118.4
    assert rules["grid.pf.converged"]["verdict"] == "pass"
    assert not [
        s for s in record["steps"] if s["tool"] in ("update_component", "add_component")
    ]
    assert run["stages"]["report"]["status"] == "skipped"
