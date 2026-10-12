"""Opt-in bounded paid conversation through the production harness catalogue."""
import os
import hashlib
from pathlib import Path

import pytest

from harness.catalogue import TOOLS
from harness.providers import wiring
from services import chat_service, chat_tools, project_registry
from services.pypsa_service import PyPSAService
from services.solve_queue import solve_queue
from tests.live_api_support import MeteredProvider
from tests.test_openai_comprehensive_live import comprehensive_run, paid, setup_profile, turn  # noqa: F401
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401


@paid
def test_live_workflow_helpers(baseline, monkeypatch, comprehensive_run):
    ctx, project_id, path = baseline
    meter, encoder, report, checkpoint = comprehensive_run
    backend = Path(__file__).resolve().parents[1]
    sources = ("services/workflow_tools.py", "harness/loop.py", "harness/catalogue.py", "harness/toolsets.py", "harness/providers/wiring.py", "tests/test_workflow_tools_live.py", "harness/skills/project-refinement/SKILL.md")
    digest = hashlib.sha256(b"".join((backend / source).read_bytes() for source in sources)).hexdigest()[:16]
    key = "workflow_helpers:" + digest
    if report["cases"].get(key, {}).get("status") == "passed":
        pytest.skip("unchanged successful live workflow cached; no duplicate spend")
    # A fresh case identity prevents an earlier plan's successful checkpoint
    # from silently standing in for validation of these new implementations.
    original_prompt, original_tools = chat_service._build_system_prompt, wiring._tools_payload
    session = setup_profile(monkeypatch, {t["name"] for t in TOOLS}, max_output_tokens=1024)
    monkeypatch.setattr(chat_service, "_build_system_prompt", original_prompt)
    monkeypatch.setattr(wiring, "_tools_payload", original_tools)
    provider = MeteredProvider(meter, encoder, key, max_calls=18, api_key=os.environ["OPENAI_API_KEY"])
    prompt = (
        "In this disposable integration test, the active saved project is named 'Tool Baseline'. "
        "Use that exact project NAME in project_id/baseline_project_id/compare_to arguments to avoid copying UUIDs. "
        "Use project_readiness to check it, use_toolset simulation, run_simulation, and save_project "
        "with name 'Tool Baseline' after completion. Then run_sensitivity_sweep from that baseline "
        "with two cases: new_name 'AI Helper Cost 20', Generator G1 marginal_cost=20; "
        "new_name 'AI Helper Cost 40', Generator G1 marginal_cost=40. "
        "Use wait_for_job for each returned job ID (timeout_seconds=25), rather than polling model calls. "
        "If a wait returns pending, report it and stop. Otherwise use_toolset results, "
        "get_study_evidence(section='simulation', compare_to='Tool Baseline') for each case. "
        "Finally use_toolset simulation and run the identical sweep again to verify solved cases are reused. "
        "Report actual objectives and whether the original baseline stayed unchanged. I authorize these "
        "bounded operations. Execute with tools now; do not stop at a plan or ask a preference question."
    )
    result = {"status": "failed", "level": "real_handlers_production_catalogue", "tools": []}
    try:
        frames = turn(session, prompt, provider)
        errors = [data for event, data in frames if event in ("error", "tool_error")]
        # A model may recover from a mistyped project identifier. Require all
        # real end-state oracles below, and reject other harness/API failures.
        assert all(data.get("message") == "Project not found" and data.get("tool_name") == "run_sensitivity_sweep" for data in errors), errors
        assert frames[-1][0] == "turn_done"
        outputs = [data for event, data in frames if event == "tool_result"]
        required = {"project_readiness", "use_toolset", "run_sensitivity_sweep", "wait_for_job", "get_study_evidence"}
        assert required <= {data["tool_name"] for data in outputs}
        sweeps = [data["result"] for data in outputs if data["tool_name"] == "run_sensitivity_sweep"]
        assert len(sweeps) >= 2 and len(sweeps[-1].get("cases", [])) == 2 and all(c["reused"] for c in sweeps[-1]["cases"]), sweeps
        assert len(sweeps[0]["cases"]) == 2
        case_ids = {case["project_id"] for case in sweeps[0]["cases"]}
        evidence = [data["result"] for data in outputs if data["tool_name"] == "get_study_evidence" and data["result"].get("project_id") in case_ids]
        assert {data["project_id"] for data in evidence} == case_ids and all(data["available"] for data in evidence)
        assert {round(data["objective"]) for data in evidence} == {200, 400}, evidence
        assert len(solve_queue.list_jobs()) == 2
        assert PyPSAService.get_active_context() is ctx
        assert ctx.network.generators.at["G1", "marginal_cost"] == 10
        with chat_tools._acting() as (db, user):
            base = project_registry.resolve_project(db, user, project_id)
            saved = __import__("pypsa").Network(project_registry.project_dir(base) / "network.nc")
            assert saved.objective == 100 and saved.generators.at["G1", "marginal_cost"] == 10
        result.update(status="passed", tools=sorted(required), completions=provider.calls,
                      baseline_objective=100, case_objectives=[200, 400], reused_cases=2,
                      recovered_identifier_errors=len(errors))
    except Exception as exc:
        result["failure"] = type(exc).__name__
        raise
    finally:
        if "frames" in locals():
            result["tool_calls"] = [{"tool": data["tool_name"], "args": data["args"]} for event, data in frames if event == "tool_request"]
        report["cases"][key] = result
        checkpoint()
