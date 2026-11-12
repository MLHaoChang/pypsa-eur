"""draft"""
from __future__ import annotations
import json, sys
from pathlib import Path
import pypsa, pytest
from fastapi.testclient import TestClient  # noqa: F401
from harness import runner as harness_runner
from services import chat_tools, flow_service, project_registry, run_profiles
from services.pypsa_service import PyPSAService
from tests.test_assistant_tasks_study_brief import helpers_state, session  # noqa: F401
from tests.test_harness_envelope import envelope_of, granted, profile  # noqa: F401
from tests.test_harness_run_loops import Script, call, go, rated, refused, say  # noqa: F401
from tests.test_harness_run_report_handoff import fake_report  # noqa: F401
from tests.test_harness_run_skeleton import RUN
from tests.test_harness_runner import detached, fake_report_provider  # noqa: F401

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_templates as T  # noqa: E402
REF = "eh_ipp_hybrid@1"
TEMPLATE = "eh_ipp_hybrid"
FIXTURES = (granted, rated)

@pytest.fixture
def baseline(client, session_ctx, install_network, tmp_path):
    from services.solver_service import SolverConfig
    T.BUILDERS[TEMPLATE]().export_to_netcdf(tmp_path / "template.nc")
    install_network(pypsa.Network(tmp_path / "template.nc"))
    name = "IPP hybrid"
    response = client.post(f"/api/projects/{name}")
    assert response.status_code == 200, response.text
    ctx = session_ctx(client)
    ctx.solver_state["solver_config"] = SolverConfig(**T.solver_config(TEMPLATE))
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

def test_draft(granted, rated, site):
    task, path, _ = granted(run={**RUN, "profile": REF})
    record = go(harness_runner.Runner("a", agent_provider=Script()), path, task)
    run = record["run"]
    print(run["status"], run["stage"], [(s["id"], s["status"], s.get("error")) for s in record["steps"]])
    print(json.dumps(run["stages"].get("report", {}), default=str)[:1500])
    assert 0
