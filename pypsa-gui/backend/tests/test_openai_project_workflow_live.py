"""Autonomous project → sensitivity → GridSpine workflow, with real engines."""
import json
import os
import re
import time
import uuid

import pytest

from harness.providers.fake import FakeProvider
from harness.providers import wiring
from services import chat_service, chat_tools
from services.pypsa_service import PyPSAService
from tests.live_api_support import MeteredProvider
from tests.test_openai_comprehensive_live import comprehensive_run, paid, setup_profile, turn

BASE = "AI Workflow Baseline"
SENS = "AI Workflow Sensitivity"
STUDY = "AI Workflow GridSpine"
CONFIG = {"hours": 24, "window": 24, "overlap": 0, "k": 1, "screen": True}


@pytest.fixture
def workflow_environment(client, session_ctx, _auth_db, tmp_path, monkeypatch):
    from project_templates._build import build_ieee39
    from routers import projects
    from services.auth_service import resolve_session_row
    from settings import get_settings
    template = build_ieee39()
    assert template is not None
    path = tmp_path / "templates" / "ieee39"
    path.mkdir(parents=True)
    template.export_to_netcdf(path / "network.nc")
    monkeypatch.setattr(projects, "_PROJECT_TEMPLATES_DIR", path.parent)
    with _auth_db[1]() as db:
        row = resolve_session_row(db, client.cookies.get(get_settings().session_cookie_name))
        chat_tools.set_acting_session(row.id)
    binding = PyPSAService.bind_request_context(session_ctx(client))
    cell = PyPSAService.bind_turn_cell()
    original_prompt, original_tools = chat_service._build_system_prompt, wiring._tools_payload
    session = setup_profile(monkeypatch, {t["name"] for t in wiring.TOOLS}, max_output_tokens=1024)
    # Use the actual production prompt, project-kind filter, bounded catalogue,
    # handler dispatch, confirmation cards, and solver queue.
    monkeypatch.setattr(chat_service, "_build_system_prompt", original_prompt)
    monkeypatch.setattr(wiring, "_tools_payload", original_tools)
    try:
        yield session, list(template.generators.index)
    finally:
        thread = PyPSAService.get_active_context().solver_state.get("thread")
        if thread is not None and thread.is_alive():
            thread.join(timeout=240)
        chat_tools.set_acting_session(None)
        PyPSAService._turn_cell.reset(cell)
        PyPSAService.reset_request_context(binding)


def phases(names):
    return [
        ("baseline", f"In this disposable integration test, create a new ieee39 project named {BASE!r}. "
         "Read its generators, set every generator's marginal_cost to 10 using one bulk update, "
         "run the simulation, verify completed simulation status before saving the solved project, then read cost_breakdown. "
         "Report the actual baseline objective and cost. I authorize these prescribed test operations. "
         "Use tools to execute them now, do not stop at a plan or ask a preference question. "
         "Relevant tools: create_project_from_template, list_components, bulk_update_components, "
         "run_simulation, save_project, get_results, get_simulation_status.", [
             ("create_project_from_template", {"template_id": "ieee39", "new_name": BASE}),
             ("list_components", {"component_class": "Generator", "limit": 100}),
             ("bulk_update_components", {"component_class": "Generator", "names": names, "updates": {"marginal_cost": 10}}),
             ("run_simulation", {}), ("get_simulation_status", {}), ("save_project", {"name": BASE}),
             ("get_results", {"result_kind": "cost_breakdown"})]),
        ("sensitivity", f"Branch {BASE!r} into a new scenario named {SENS!r}, activate that scenario, "
         "set every generator's marginal_cost to 20, rerun, verify completed status, save the solved scenario, and read its cost "
         "and objective. Compare both saved scenarios for economics and explain the cost change. "
         "Keep the baseline unchanged. Use create_scenario, activate_project, bulk_update_components, "
         "run_simulation, save_project, get_results, get_simulation_status, compare_scenarios.", [
             ("create_scenario", {"base": BASE, "new_name": SENS, "description": "Uniform marginal cost sensitivity 10 to 20"}),
             ("activate_project", {"project_id": SENS}),
             ("bulk_update_components", {"component_class": "Generator", "names": names, "updates": {"marginal_cost": 20}}),
             ("run_simulation", {}), ("get_simulation_status", {}), ("save_project", {"name": SENS}),
             ("get_results", {"result_kind": "cost_breakdown"}),
             ("compare_scenarios", {"project_a": BASE, "project_b": SENS, "focus": "economics"})]),
        ("create_study", f"Create a GridSpine planning-to-dynamics study named {STUDY!r} with config "
         + json.dumps(CONFIG) + ". Activate it. Stop after creation and activation; we will set its source next. "
         "Use gridspine_create_study and activate_project.", [
             ("gridspine_create_study", {"name": STUDY, "config": CONFIG}),
             ("activate_project", {"project_id": STUDY})]),
        ("pipeline", f"For {STUDY!r}, use the solved saved {BASE!r} as from_project dispatch source, "
         "read the study config to verify that source, then start the pipeline. Report the returned job ID "
         "and state; do not repeatedly poll. Use gridspine_set_dispatch_source, gridspine_get_config, gridspine_run_pipeline.", [
             ("gridspine_set_dispatch_source", {"project_id": STUDY, "from_project": BASE}),
             ("gridspine_get_config", {"project_id": STUDY}),
             ("gridspine_run_pipeline", {"project_id": STUDY})]),
        ("assessment", f"The test runner has waited for the queued {STUDY!r} pipeline. Verify its actual "
         "stage status, list ranked snapshots and their load-flow convergence, get load capacity at BUS_16, "
         "assess a 1 MW load at BUS_16 with load_pf=0.98 and no on-site generation, then read the stored "
         "assessment. Explain pass/fail findings and limits without calling this a compliance certificate. "
         "Use gridspine_get_stage_status, gridspine_list_ranked_snapshots, gridspine_get_capacity, "
         "gridspine_assess_connection, gridspine_get_connection_assessments.", [
             ("gridspine_get_stage_status", {"project_id": STUDY}),
             ("gridspine_list_ranked_snapshots", {"project_id": STUDY}),
             ("gridspine_get_capacity", {"project_id": STUDY, "bus": "BUS_16", "kind": "load"}),
             ("gridspine_assess_connection", {"project_id": STUDY, "bus": "BUS_16", "load_mw": 1, "load_pf": 0.98}),
             ("gridspine_get_connection_assessments", {"project_id": STUDY})]),
        ("refinement", f"Refine {STUDY!r}: change k from 1 to 2, verify its updated config, and rerun "
         "the pipeline. Keep hours, window, overlap and screen unchanged. Report its job ID and stop polling. "
         "Use gridspine_update_config, gridspine_get_config, gridspine_run_pipeline.", [
             ("gridspine_update_config", {"project_id": STUDY, "k": 2}),
             ("gridspine_get_config", {"project_id": STUDY}),
             ("gridspine_run_pipeline", {"project_id": STUDY})]),
        ("interpretation", f"The runner has waited for the refined {STUDY!r} run. Check its stage status "
         "and ranked snapshots; compute exact AC load connection capacity at BUS_16, read back that capacity, "
         "and assess a 1 MW load there again. Interpret the actual results, compare ranked-hour coverage "
         "with the k=1 run, and identify a justified next investigation. This is a steady-state screen, "
         "not dynamic/FRT compliance evidence. Use gridspine_get_stage_status, gridspine_list_ranked_snapshots, "
         "gridspine_compute_capacity, gridspine_get_capacity, gridspine_assess_connection.", [
             ("gridspine_get_stage_status", {"project_id": STUDY}),
             ("gridspine_list_ranked_snapshots", {"project_id": STUDY}),
             ("gridspine_compute_capacity", {"project_id": STUDY, "bus": "BUS_16", "kind": "load"}),
             ("gridspine_get_capacity", {"project_id": STUDY, "bus": "BUS_16", "kind": "load"}),
             ("gridspine_assess_connection", {"project_id": STUDY, "bus": "BUS_16", "load_mw": 1})]),
        ("assessment_followup", f"Finish the refined {STUDY!r} interpretation: avoid truncated whole-study "
         "output by reading gridspine_get_connection_assessments separately for EACH selected hour "
         "(7, 8, 11, 18, 19, 22, 23), using its hour filter. Count only rows whose check is connection. "
         "Include 'connection_pass=X; connection_fail=Y' with the actual counts, and explain the failures "
         "and next investigation. Retain the steady-state/compliance limitations. Do not rerun engines.", [
             ("gridspine_get_connection_assessments", {"project_id": STUDY, "hour": h})
             for h in (7, 8, 11, 18, 19, 22, 23)]),
    ]


def wait_for_job(frames):
    from services.solve_queue import solve_queue
    for event, data in frames:
        if event != "tool_result" or data["tool_name"] != "gridspine_run_pipeline":
            continue
        job_id = uuid.UUID(data["result"]["id"])
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            job = solve_queue.get_job(job_id)
            if job["status"] in ("completed", "failed", "aborted", "interrupted"):
                assert job["status"] == "completed", job
                return
            time.sleep(0.2)  # Local CPU wait; no model/API polling charges.
        raise AssertionError("GridSpine job did not finish within 240 seconds")


def state_oracle(phase, frames, state, *, check_text=True):
    errors = [d for e, d in frames if e in ("error", "tool_error")]
    assert not errors, errors
    assert frames[-1][0] == "turn_done"
    calls = [d for e, d in frames if e == "tool_request"]
    text = "".join(d["delta"] for e, d in frames if e == "token")
    if check_text:
        assert text.strip()
    from services import gridspine_service as gs, project_registry
    import pypsa
    def saved(name):
        with chat_tools._acting() as (db, user):
            p = project_registry.resolve_project(db, user, name)
            return pypsa.Network(project_registry.project_dir(p) / "network.nc")
    if phase == "baseline":
        n = saved(BASE)
        assert set(n.generators.marginal_cost) == {10} and n.objective > 0
        state["baseline_objective"] = float(n.objective)
        state["generation_energy"] = float(n.generators_t.p.sum(axis=1).mul(n.snapshot_weightings.objective).sum())
        if check_text:
            assert any(abs(float(v.replace(",", "")) - n.objective) < 1
                       for v in re.findall(r"\d[\d,]*(?:\.\d+)?", text)), text
    elif phase == "sensitivity":
        n, base = saved(SENS), saved(BASE)
        assert set(n.generators.marginal_cost) == {20} and set(base.generators.marginal_cost) == {10}
        assert abs(n.objective - base.objective - 10 * state["generation_energy"]) < 10
        assert any(c["tool_name"] == "compare_scenarios" for c in calls)
        state["sensitivity_objective"] = float(n.objective)
        if check_text:
            assert any(abs(float(v.replace(",", "")) - n.objective) < 1
                       for v in re.findall(r"\d[\d,]*(?:\.\d+)?", text)), text
    else:
        with chat_tools._acting() as (db, user):
            p = project_registry.resolve_project(db, user, STUDY)
            assert gs.kind_of(p) == "planning_dynamics"
            if phase == "create_study":
                assert PyPSAService.get_loaded_project() == STUDY
                assert wiring._bound_project_kind(None) == "planning_dynamics"
                assert "gridspine_run_pipeline" in {t["name"] for t in wiring._tools_payload()}
            elif phase in ("pipeline", "refinement"):
                config = gs.get_config(p, db=db)
                assert config["from_project"] == BASE
                assert config["k"] == (1 if phase == "pipeline" else 2)
                for field in ("hours", "window", "overlap", "screen"):
                    assert config[field] == CONFIG[field]
                wait_for_job(frames)
                assert gs.get_stage_status(p)["status"] == "completed"
            elif phase in ("assessment", "interpretation", "assessment_followup"):
                status = gs.get_stage_status(p)
                rows = gs.list_ranked_snapshots(p)
                assert status["status"] == "completed" and rows
                assert status["converged_hours"] and status["bundles"]
                capacity = gs.get_capacity(p, bus="BUS_16", kind="load")["rows"]
                assessments = gs.get_connection(p)["rows"]
                assert capacity and assessments
                assert {r["status"] for r in assessments} <= {"pass", "fail", "reported"}
                if phase == "assessment":
                    state["baseline_ranked_hours"] = len(rows)
                elif phase == "interpretation":
                    assert len(rows) >= state["baseline_ranked_hours"]
                    state["refined_ranked_hours"] = len(rows)
                    assert any(c["tool_name"] == "gridspine_compute_capacity" for c in calls)
                    if check_text:
                        assert "steady" in text.lower() or "compliance" in text.lower()
                else:
                    requested = {c["args"].get("hour") for c in calls
                                 if c["tool_name"] == "gridspine_get_connection_assessments"}
                    assert {r["hour"] for r in rows} <= requested
                    connection = [r for r in assessments if r["check"] == "connection"]
                    passed = sum(r["status"] == "pass" for r in connection)
                    failed = sum(r["status"] == "fail" for r in connection)
                    state.update(connection_pass=passed, connection_fail=failed)
                    if check_text:
                        assert f"connection_pass={passed}; connection_fail={failed}" in text, text
    return {"phase": phase, "text": text, "tools": calls,
            "tool_errors": errors, "result_count": sum(e == "tool_result" for e, _ in frames)}


class WaitingFakeProvider(FakeProvider):
    """A scripted model has no inference latency; wait for its prior real solve."""
    def stream(self, request):
        thread = PyPSAService.get_active_context().solver_state.get("thread")
        if thread is not None and thread.is_alive():
            thread.join(timeout=240)
            assert not thread.is_alive(), "solver fixture timed out"
        yield from super().stream(request)


def test_real_workflow_fixture_without_api(workflow_environment):
    session, names = workflow_environment
    state = {}
    for phase, prompt, operations in phases(names):
        provider = WaitingFakeProvider([{"blocks": [{"type": "tool_use", "id": f"{phase}_{i}", "name": name,
                                               "input": args}]} for i, (name, args) in enumerate(operations)]
                                + [{"blocks": [{"type": "text", "text": "fixture"}]}])
        frames = turn(session, prompt, provider)
        state_oracle(phase, frames, state, check_text=False)


@paid
def test_live_autonomous_project_gridspine_workflow(workflow_environment, comprehensive_run):
    meter, encoder, report, checkpoint = comprehensive_run
    key = "conversation:autonomous_project_gridspine"
    cached = report["cases"].get(key, {})
    if cached.get("status") == "passed" and len(cached.get("turns", [])) == len(phases([])):
        pytest.skip("cached successful workflow; no duplicate API spend")
    session, names = workflow_environment
    result = {"level": "autonomous_production_catalogue_and_real_engines", "status": "failed",
              "turns": list(cached.get("turns", [])), "oracles": dict(cached.get("oracles", {})),
              "phase_histories": dict(cached.get("phase_histories", {}))}
    state = result["oracles"]
    for i, (phase, prompt, _) in enumerate(phases(names)):
        try:
            if i < len(result["turns"]):
                prior = result["turns"][i]
                replay, old_ids = [], {}
                for c in prior["tools"]:
                    args = dict(c["args"])
                    ref = args.get("project_id")
                    try:
                        uuid.UUID(str(ref))
                    except ValueError:
                        pass
                    else:
                        # Fresh fixture rows have fresh UUIDs. Resolve recorded
                        # references by their stable test project names.
                        target = SENS if phase == "sensitivity" else STUDY
                        old_ids[ref] = target
                        args["project_id"] = target
                    replay.append({"blocks": [{"type": "tool_use", "id": c["tool_use_id"],
                                                "name": c["tool_name"], "input": args}]})
                replay.append({"blocks": [{"type": "text", "text": prior["text"]}]})
                frames = turn(session, prompt, WaitingFakeProvider(replay))
                state_oracle(phase, frames, state, check_text=False)
                if cached.get("phase_histories", {}).get(phase):
                    history_json = json.dumps(cached["phase_histories"][phase])
                    with chat_tools._acting() as (db, user):
                        from services import project_registry
                        for old_id, target in old_ids.items():
                            new_id = str(project_registry.resolve_project(db, user, target).id)
                            history_json = history_json.replace(old_id, new_id)
                    session.messages.clear()
                    session.messages.extend(json.loads(history_json))
                continue
            provider = MeteredProvider(meter, encoder, key + ":" + phase, max_calls=16,
                choices=(None,) * 15 + ("none",), api_key=os.environ["OPENAI_API_KEY"])
            frames = turn(session, prompt, provider)
            result["last_frames"] = [(e, d) for e, d in frames if e in ("error", "tool_error", "tool_request", "tool_result", "token")]
            outcome = state_oracle(phase, frames, state)
            outcome.update(prompt=prompt, calls=provider.calls)
            result["turns"].append(outcome)
            result.setdefault("phase_histories", dict(cached.get("phase_histories", {})))[phase] = list(session.messages)
            report["cases"][key] = result
            checkpoint()
            print("LIVE_WORKFLOW " + json.dumps({"phase": phase, "calls": provider.calls, "tokens": meter.charged}), flush=True)
        except Exception as exc:
            result["reason"] = str(exc)[:2000]
            report["cases"][key] = result
            checkpoint()
            raise
    result.pop("last_frames", None)
    result.pop("reason", None)
    result["status"] = "passed"
    report["cases"][key] = result
    checkpoint()
