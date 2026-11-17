# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Study harness 16: eh_concentrator_staged@1 on the shipped template's demo
data, as the run's own project, to the hand-off (acceptance 2; GK A (28)).

The template is created through the route as a user does (its finance,
commercial and solver settings), then planned on its first stage only (2027,
one chronological year: `eh_concentrator.build_eh_concentrator_staged(years=)`,
as the template's own tests plan it) so the real solve, the binary check and
the investment case's baseline re-solve fit the CI budget. The readers are
pinned in `test_run_profile_concentrator.py`."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pypsa
import pytest
from fastapi.testclient import TestClient  # noqa: F401 - the client fixture's type

from harness import run_ic
from harness import runner as harness_runner
from services import chat_tools, flow_service, project_registry
from services.pypsa_service import PyPSAService
from tests.test_assistant_tasks_study_brief import helpers_state, session  # noqa: F401
from tests.test_harness_envelope import envelope_of, granted, profile  # noqa: F401
from tests.test_harness_run_loops import Script, go, rated, refused, say  # noqa: F401
from tests.test_harness_run_report_handoff import fake_report  # noqa: F401
from tests.test_harness_run_skeleton import RUN
from tests.test_harness_runner import detached, fake_report_provider  # noqa: F401

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_concentrator as C  # noqa: E402
import eh_templates as T  # noqa: E402

REF = "eh_concentrator_staged@1"
TEMPLATE = "eh_concentrator_staged"
FIXTURES = (granted, rated)


@pytest.fixture
def baseline(client, session_ctx, install_network, tmp_path):
    """The template from the route, its network replaced by the first stage's
    (2027) and saved, bound as the run's project."""
    from services.solver_service import SolverConfig

    name = "Concentrator"
    response = client.post(
        f"/api/projects/from_template/{TEMPLATE}", params={"name": name}
    )
    assert response.status_code == 200, response.text
    C.build_eh_concentrator_staged(T, years=(2027,)).export_to_netcdf(
        tmp_path / "stage1.nc"
    )
    install_network(pypsa.Network(tmp_path / "stage1.nc"))
    # The network swap resets the session's settings: the template's own (its
    # finance and commercial inputs among them) go back before the save.
    ctx = session_ctx(client)
    ctx.solver_state["solver_config"] = SolverConfig(**T.solver_config(TEMPLATE))
    response = client.post(f"/api/projects/{name}")
    assert response.status_code == 200, response.text
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
def site(baseline):
    _, project_id, root = baseline
    (root / flow_service.TEMPLATE_FILE).write_text(json.dumps({"id": TEMPLATE}))
    return project_id, root


@pytest.mark.live_solve
def test_a_run_on_the_demo_data_reaches_the_hand_off(granted, rated, site, monkeypatch):
    """Acceptance 2: solve, the case against the site as today, the result
    gate (the mass balance holds; the DOL mills' motor start and restart FAIL
    as warnings with their numbers; N-1 not evaluated: no site transformer),
    report and hand-off."""
    from services import run_envelope

    from services.results import baseline as baseline_mod

    # GK A (28) Q7: a spy on the case's baseline re-solve, on a cold cache.
    monkeypatch.setattr(baseline_mod, "_CACHE", {})
    real_solve = baseline_mod.solve_baseline
    solved = []

    def spy(n, cfg, plan, *, solve=None):
        from services.solver_service import run_simulation

        def counted(*args, **kwargs):
            solved.append(1)
            return (solve or run_simulation)(*args, **kwargs)

        return real_solve(n, cfg, plan, solve=counted)

    monkeypatch.setattr(baseline_mod, "solve_baseline", spy)
    task, path, _ = granted(run={**RUN, "profile": REF})
    script = Script()  # never consulted: no L2 round on a passing gate
    record = go(harness_runner.Runner("a", agent_provider=script), path, task)
    run = record["run"]
    rules = {
        r["rule"]: r
        for r in (run["stages"].get("result_gate", {}).get("gate") or {}).get(
            "rules", []
        )
    }
    assert run["status"] == "completed" and run["stage"] == "handoff", (
        run["status"],
        run["stage"],
        [(s["id"], s["status"], s.get("error")) for s in record["steps"]],
        {k: (r["verdict"], r.get("reason")) for k, r in rules.items()},
        {k: v for k, v in run["stages"].get(run["stage"], {}).items() if k != "gate"},
        (run["stages"].get(run["stage"], {}).get("gate") or {}).get("rules"),
    )
    assert script.requests == []
    solve = [
        (s["tool"], s["status"])
        for s in record["steps"]
        if s["id"].startswith("solve.")
    ]
    assert solve == [("solve_queue_enqueue", "completed"), (run_ic.TOOL, "completed")]
    verdicts = {k: r["verdict"] for k, r in rules.items()}
    assert verdicts["conc.mass_balance"] == "pass"
    assert rules["conc.mass_balance"]["facts"][0]["value"] <= 1e-6
    assert (
        verdicts["conc.ic.counterfactual"] == "pass"
        and verdicts["conc.ic.basis"] == "pass"
    )
    assert verdicts["conc.motor_start"] == "fail"
    assert any(
        "(dol): FAIL" in d for d in rules["conc.motor_start"]["facts"][0]["detail"]
    )
    assert verdicts["conc.restart"] == "fail"
    assert verdicts["conc.n_1.site_transformer"] == "not_evaluated"
    for kpi in ("conc.kpi.lost_tonnes", "conc.kpi.shed_level", "conc.kpi.npv"):
        assert verdicts[kpi] == "pass", (kpi, rules[kpi].get("reason"))
    # GK A (28) Q5, Q7 = C: the case re-solved its baseline once (a cache
    # miss), and the envelope counts only the plan: the anchor study-harness
    # issue 50 turns into 2 (`run_ic.SOLVES = 0`).
    assert solved == [1]
    envelope = envelope_of(path)
    assert run_envelope.solves_used(envelope) == 1
    assert run["budget"]["used"]["solves"] == 1
