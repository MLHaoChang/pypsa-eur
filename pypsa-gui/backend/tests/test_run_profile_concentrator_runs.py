# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Study harness 16: eh_concentrator_staged@1 on the shipped template's demo
data, as the run's own project, to the hand-off (acceptance 2; GK A (28)).

The template is created through the route as a user does (its finance,
commercial and solver settings), then planned on its first stage only (2027,
one chronological year: `eh_concentrator.build_eh_concentrator_staged(years=)`,
as the template's own tests plan it) so the real solve fits the CI budget. On
@1 the investment case stays out of the run (GK A (28) Q8); on @2 the run
solves the case and its baseline, counted (study-harness issue 52). The readers are
pinned in `test_run_profile_concentrator.py`."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pypsa
import pytest
from fastapi.testclient import TestClient  # noqa: F401 - the client fixture's type

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
REF_V2 = "eh_concentrator_staged@2"
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
    """Acceptance 2: the solve, the result gate (the mass balance holds; the
    DOL mills' motor start and restart FAIL as warnings with their numbers;
    N-1 not evaluated: no site transformer; the case rules not evaluated: no
    case the user ran on this network), report and hand-off."""
    from harness import budget as harness_budget
    from harness import run_handoff

    # The per-tool deadline guards a hung handler; this test reads whether the
    # run reaches the hand-off. solve_queue_enqueue's solve_capture ->
    # run_envelope.identity hashes the 8760 h network through
    # `_exact_frame_json` twice (~9 s locally), which a shared CI runner pushed
    # past the 30 s default twice (tool_timeout at solve.1). GK A (32), option A;
    # the cost itself is .scratch/harness/issues/225 (GK B).
    monkeypatch.setattr(harness_budget, "PER_TOOL_TIMEOUT_SECONDS", 120.0)
    from services import run_envelope

    task, path, _ = granted(run={**RUN, "profile": REF})
    script = Script()  # never consulted: no L2 round on a passing gate
    runner = harness_runner.Runner("a", agent_provider=script)
    record = go(runner, path, task)
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
        {
            k: v
            for k, v in run.items()
            if k in ("waiting", "stop", "reason", "budget", "error")
        },
    )
    assert script.requests == []
    solve = [
        (s["tool"], s["status"])
        for s in record["steps"]
        if s["id"].startswith("solve.")
    ]
    # GK A (28) Q8 = A: the solve stage runs no case (study-harness issue 52).
    assert solve == [("solve_queue_enqueue", "completed")]
    verdicts = {k: r["verdict"] for k, r in rules.items()}
    assert verdicts["conc.mass_balance"] == "pass"
    assert rules["conc.mass_balance"]["facts"][0]["value"] <= 1e-6
    assert verdicts["conc.motor_start"] == "fail"
    assert any(
        "(dol): FAIL" in d for d in rules["conc.motor_start"]["facts"][0]["detail"]
    )
    assert verdicts["conc.restart"] == "fail"
    assert verdicts["conc.n_1.site_transformer"] == "not_evaluated"
    for kpi in ("conc.kpi.lost_tonnes", "conc.kpi.shed_level"):
        assert verdicts[kpi] == "pass", (kpi, rules[kpi].get("reason"))
    # GK A Q8 (2), (3): the case rules and the NPV are not evaluated, with
    # their reason, on the hand-off; never a pass, and the NPV has no number.
    case = ("conc.ic.counterfactual", "conc.ic.basis", "conc.kpi.npv")
    for rid in case:
        assert (verdicts[rid], rules[rid]["severity"]) == ("not_evaluated", "warn"), rid
        assert rules[rid].get("reason"), rid
    assert not any(
        isinstance(f.get("value"), (int, float)) for f in rules["conc.kpi.npv"]["facts"]
    ), rules["conc.kpi.npv"]["facts"]
    unevaluated = {
        r["rule"]: r["reason"] for r in run_handoff.checks(run)["not_evaluated"]
    }
    for rid in case:
        assert unevaluated.get(rid) == rules[rid]["reason"], (rid, unevaluated)
    envelope = envelope_of(path)
    assert not [a for a in envelope["audit"] if a["event"] == "refused"]
    assert run_envelope.solves_used(envelope) == 1
    assert run["budget"]["used"]["solves"] == 1


@pytest.mark.live_solve
def _own_planned_stage_only(ctx):
    """The template's single owner owns every stage's builds; the test plans
    the first stage only, so the later stages' assets leave its value flows
    (else the case refuses `owner_asset_missing`) and their later CODs leave
    its finance, as do its links built after the planned period (else
    `staged_cod_not_active_period`)."""
    n = ctx.network
    last = max(int(p) for p in n.investment_periods)
    n.remove("Link", n.links.index[n.links.build_year > last])
    present = {(c, name) for c, df in (("Generator", n.generators), ("StorageUnit", n.storage_units),
                                       ("Store", n.stores), ("Link", n.links)) for name in df.index}
    cfg = ctx.solver_state["solver_config"]
    commercial = cfg.commercial if isinstance(cfg.commercial, dict) else cfg.commercial.model_dump()
    flows = commercial["value_flows"]
    flows["asset_owners"] = [a for a in flows["asset_owners"] if (a["component"], a["asset_id"]) in present]
    cfg.commercial = commercial
    years = {str(int(p)) for p in n.investment_periods}
    cods = cfg.finance["cod_by_asset"]
    cfg.finance["cod_by_asset"] = {a: d for a, d in cods.items() if str(d)[:4] in years}


def test_a_v2_run_solves_its_case_against_the_site_today(granted, rated, baseline, site, monkeypatch):
    """Study-harness issue 52: @2's solve stage runs the case after the queued
    solve; the case re-solves its baseline (the site as today) on the run's
    counted reservation, both case rules block and pass, and the run's solves
    are the real LPs: the plan and the baseline (a cache miss here)."""
    from harness import budget as harness_budget
    from services import run_envelope
    from services.results import baseline as B

    monkeypatch.setattr(harness_budget, "PER_TOOL_TIMEOUT_SECONDS", 120.0)  # as on @1
    _own_planned_stage_only(baseline[0])
    B.clear_cache()
    task, path, _ = granted(run={**RUN, "profile": REF_V2})
    script = Script()  # never consulted: no L2 round on a passing gate
    record = go(harness_runner.Runner("a", agent_provider=script), path, task)
    run = record["run"]
    rules = {
        r["rule"]: r
        for r in (run["stages"].get("result_gate", {}).get("gate") or {}).get("rules", [])
    }
    steps = [(s["id"], s["tool"], s["status"], s.get("error")) for s in record["steps"]]
    assert run["status"] == "completed" and run["stage"] == "handoff", (
        run["status"], run["stage"], steps,
        {k: (r["verdict"], r.get("reason")) for k, r in rules.items()},
    )
    assert script.requests == []
    assert [(t, st) for i, t, st, _ in steps if i.startswith("solve.")] == [
        ("solve_queue_enqueue", "completed"), ("run_investment_case", "completed")]
    for rid in ("conc.ic.counterfactual", "conc.ic.basis"):
        assert (rules[rid]["verdict"], rules[rid]["severity"]) == ("pass", "block"), (
            rid, rules[rid].get("reason"), rules[rid].get("facts"))
    assert rules["conc.ic.basis"]["facts"][0]["value"] == "site_today"
    assert rules["conc.kpi.npv"]["severity"] == "warn"
    case = next(s for s in record["steps"] if s["tool"] == "run_investment_case")
    envelope = envelope_of(path)
    assert not [a for a in envelope["audit"] if a["event"] == "refused"]
    row = next(a for a in envelope["audit"] if a.get("tool") == "run_investment_case")
    assert (row["reserved"], row["solves"]) == (run_envelope.CASE_BASELINE_SOLVES, 1)
    assert case["result"]["solves"] == 1
    assert run_envelope.solves_used(envelope) == 2  # the plan and the baseline
    assert run["budget"]["used"]["solves"] == 2
