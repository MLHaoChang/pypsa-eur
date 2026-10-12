"""Workflow helpers: real ACLs/state/queue, bounded evidence and catalogue refresh."""
import json
import threading
import time
import uuid

import pandas as pd
import pypsa
import pytest
from fastapi import HTTPException

from harness.catalogue import TOOLS, safety_tier_for
from harness.providers import wiring
from harness.providers.fake import FakeProvider
from harness.toolsets import CONTROL_TOOLS, DOMAINS, filter_tools
from services import chat_service, chat_tools, gridspine_service as gs, project_registry, workflow_tools as wt
from services.pypsa_service import PyPSAService
from services.solve_queue import solve_queue
from tests.test_openai_comprehensive_live import setup_profile, turn


@pytest.fixture(autouse=True)
def helpers_state():
    chat_tools.set_chat_session(None)
    wt._EVIDENCE_CACHE.clear()
    yield
    chat_tools.set_chat_session(None)
    wt._EVIDENCE_CACHE.clear()
    solve_queue.reset_for_tests()


@pytest.fixture
def baseline(client, session_ctx, install_network):
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2026-01-01", periods=2, freq="h"))
    n.add("Bus", "B1", v_nom=110)
    n.add("Generator", "G1", bus="B1", p_nom=20, marginal_cost=10)
    n.add("Load", "L1", bus="B1", p_set=5)
    install_network(n)
    response = client.post("/api/projects/Tool Baseline")
    assert response.status_code == 200, response.text
    ctx = session_ctx(client)
    binding = PyPSAService.bind_request_context(ctx)
    cell = PyPSAService.bind_turn_cell()
    try:
        with chat_tools._acting() as (db, user):
            project = project_registry.resolve_project(db, user, "Tool Baseline")
            path = project_registry.project_dir(project)
        yield ctx, str(project.id), path
    finally:
        PyPSAService._turn_cell.reset(cell)
        PyPSAService.reset_request_context(binding)


def case(name="Tool Sensitivity", value=20):
    return {"new_name": name, "changes": [{"component_class": "Generator", "names": ["G1"], "attribute": "marginal_cost", "value": value}]}


@pytest.fixture
def study(monkeypatch):
    created = chat_tools.gridspine_create_study("Evidence Study", {"hours": 24, "k": 1, "screen": True})
    with chat_tools._acting() as (db, user):
        project = project_registry.resolve_project(db, user, created["id"])
        root = project_registry.project_dir(project) / "gridspine"
        config = gs.config_for_dir(root).to_json()
        run = root / "run"
        run.mkdir()
        (run / "manifest.json").write_text(json.dumps({"config": config, "status": "completed"}))
    # Table readers are real; engine stage completion is fixture-controlled here.
    monkeypatch.setattr(gs, "get_stage_status", lambda p: {"status": "completed"})
    rows = []
    for hour in range(7):
        for check in ("connection", "energisation"):
            rows.append({"assessment_id": "facility", "hour": hour, "bus": "BUS_16", "load_mw": 1,
                         "load_pf": 0.98, "onsite_mw": 0, "onsite_converter": True, "profile": "eu_rfg_dcc_ce",
                         "check": check, "status": "fail" if hour == 3 else "pass", "value": 1,
                         "unit": "MW", "limit": 1, "detail": "N-1: high voltage" if hour == 3 else "OK",
                         "clause": "engineering screen", "source": "assumed"})
    pd.DataFrame(rows).to_csv(run / "connection.csv", index=False)
    return created["id"], root


def test_readiness_uses_real_validation_and_saved_state(baseline):
    ctx, project_id, _ = baseline
    ready = chat_tools.project_readiness()
    assert ready["project_id"] == project_id and ready["ready"]
    assert ready["saved"] and not ready["dirty"] and ready["solver_name"] == "highs"
    assert ready["dispatch"]["state"] == "none" and ready["next_tools"] == ["run_simulation"]
    ctx.results_unsaved = True
    assert chat_tools.project_readiness()["next_tools"] == ["save_project"]
    ctx.solver_state["solver_config"].solver_name = "missing-solver"
    assert not chat_tools.project_readiness()["ready"]


def test_readiness_unbound():
    assert chat_tools.project_readiness()["kind"] == "unbound"


def test_evidence_counts_before_page_and_status_filter_and_caches(study):
    project_id, _ = study
    first = chat_tools.get_study_evidence(project_id, check="connection", status="fail", limit=1)
    assert first["available"] and first["counts_before_status_filter"] == {"fail": 1, "pass": 6}
    assert first["total_count"] == 1 and first["items"][0]["hour"] == 3 and not first["cache_hit"]
    assert "high voltage" in first["items"][0]["detail"]
    first["items"][0]["detail"] = "edited by caller"
    second = chat_tools.get_study_evidence(project_id, check="connection", status="fail", limit=1)
    assert second["cache_hit"] and "high voltage" in second["items"][0]["detail"]
    page = chat_tools.get_study_evidence(project_id, check="connection", offset=4, limit=2)
    assert page["total_count"] == 7 and page["has_more"] and page["returned"] == 2


def test_evidence_keeps_counts_and_cursor_when_first_row_is_oversized(study):
    project_id, root = study
    table = root / "run" / "connection.csv"
    rows = pd.read_csv(table)
    rows.loc[0, "detail"] = "長い制約説明" * 2000
    rows.to_csv(table, index=False)
    result = chat_tools.get_study_evidence(project_id, check="connection", limit=50)
    assert not chat_service._truncate_result(result).get("_truncated"), result
    assert result["counts_before_status_filter"] == {"fail": 1, "pass": 6}
    assert result["returned"] == 1 and result["has_more"]
    assert result["items"][0]["hour"] == 0
    assert "detail" in result["items"][0]["_truncated_fields"]
    next_page = chat_tools.get_study_evidence(project_id, check="connection", offset=1, limit=1)
    assert next_page["items"][0]["hour"] == 1


def test_evidence_invalidates_when_table_changes_and_when_config_stales(study):
    project_id, root = study
    first = chat_tools.get_study_evidence(project_id, check="connection")
    table = pd.read_csv(root / "run" / "connection.csv")
    table.loc[table["hour"] == 0, "status"] = "fail"
    table.to_csv(root / "run" / "connection.csv", index=False)
    second = chat_tools.get_study_evidence(project_id, check="connection")
    assert not second["cache_hit"] and second["counts_before_status_filter"]["fail"] == 2
    assert first["evidence"]["fingerprint"] != second["evidence"]["fingerprint"]
    chat_tools.gridspine_update_config(project_id, k=2)
    stale = chat_tools.get_study_evidence(project_id, check="connection")
    assert not stale["available"] and stale["items"] == []
    assert not stale["evidence"]["config_matches_run"]


def test_evidence_detects_source_and_overlay_changes(study):
    project_id, root = study
    (root / "templates_overlay.json").write_text("{}")
    assert not chat_tools.get_study_evidence(project_id)["available"]
    assert chat_tools.project_readiness(project_id)["next_tools"] == ["gridspine_get_config", "gridspine_run_pipeline"]


def test_evidence_detects_deleted_overlay(study):
    project_id, root = study
    overlay = root / "templates_overlay.json"
    overlay.write_text("{}")
    config = gs.config_for_dir(root).to_json()
    (root / "run" / "manifest.json").write_text(json.dumps({"config": config, "status": "completed"}))
    assert chat_tools.get_study_evidence(project_id)["available"]
    overlay.unlink()
    assert not chat_tools.get_study_evidence(project_id)["available"]


@pytest.mark.parametrize("kwargs", [{"limit": 0}, {"limit": 51}, {"offset": -1}, {"hour": True}, {"section": "unknown"}])
def test_evidence_rejects_invalid_arguments(study, kwargs):
    with pytest.raises(HTTPException) as exc:
        chat_tools.get_study_evidence(study[0], **kwargs)
    assert exc.value.status_code == 422


@pytest.mark.parametrize("kind", ["solve", "gridspine"])
def test_wait_terminal_failure_timeout_and_cancel(baseline, kind):
    ctx, _, path = baseline
    solve_queue.pause()
    job = solve_queue.enqueue("Tool Baseline", project_key=ctx.registry_key, storage_dir=str(path), kind=kind)
    timed = chat_tools.wait_for_job(str(job.id), 0)
    assert timed["timed_out"] and not timed["terminal"]
    session = chat_service.ChatSession()
    chat_tools.set_chat_session(session)
    session.abort_event.set()
    cancelled = chat_tools.wait_for_job(str(job.id), 25)
    assert cancelled["wait_cancelled"] and job.status == "queued"
    job.status, job.error = "failed", "solver infeasible"
    failed = chat_tools.wait_for_job(str(job.id), 0)
    assert failed["terminal"] and not failed["completed"] and failed["job"]["error"] == "solver infeasible"


def test_wait_observes_local_completion_without_model_calls(baseline):
    ctx, _, path = baseline
    solve_queue.pause()
    job = solve_queue.enqueue("Tool Baseline", project_key=ctx.registry_key, storage_dir=str(path))
    timer = threading.Timer(0.05, lambda: setattr(job, "status", "completed"))
    timer.start()
    try:
        result = chat_tools.wait_for_job(str(job.id), 1)
        assert result["completed"] and result["waited_seconds"] < 1
    finally:
        timer.join()


@pytest.mark.parametrize("timeout", [-1, 26, float("nan"), True, "20"])
def test_wait_rejects_unbounded_or_invalid_timeout(timeout):
    with pytest.raises(HTTPException) as exc:
        chat_tools.wait_for_job(str(uuid.uuid4()), timeout)
    assert exc.value.status_code == 422


def test_new_tools_keep_project_acl_boundaries(baseline, study, second_identity):
    ctx, project_id, path = baseline
    job = solve_queue.enqueue("Tool Baseline", project_key=ctx.registry_key, storage_dir=str(path))
    previous = chat_tools.acting_user_id()
    chat_tools.set_acting_user(second_identity["user_id"])
    try:
        for invoke in (lambda: chat_tools.project_readiness(project_id), lambda: chat_tools.wait_for_job(str(job.id), 0),
                       lambda: chat_tools.get_study_evidence(study[0]), lambda: chat_tools.run_sensitivity_sweep(project_id, [case()])):
            with pytest.raises(HTTPException) as exc:
                invoke()
            assert exc.value.status_code == 404
    finally:
        chat_tools.set_acting_user(previous)


@pytest.mark.parametrize("change", [{"attribute": "extra_functionality_code"}, {"value": "20"}, {"value": float("inf")},
                                    {"names": ["missing"]}, {"attribute": "p_nom_extendable", "value": 1}])
def test_sweep_preflights_every_case_before_creating(baseline, change):
    _, project_id, path = baseline
    before = (path / "network.nc").read_bytes()
    bad = case("Invalid Case")
    bad["changes"][0].update(change)
    with pytest.raises(HTTPException):
        chat_tools.run_sensitivity_sweep(project_id, [case(), bad])
    assert (path / "network.nc").read_bytes() == before
    assert not any(p["name"] in ("Tool Sensitivity", "Invalid Case") for p in chat_tools.list_projects())


def test_sweep_uses_real_queue_preserves_baseline_and_reuses_solved_case(baseline):
    ctx, project_id, path = baseline
    before = (path / "network.nc").read_bytes()
    cases = [case(), case("Tool Sensitivity 40", 40)]
    first = chat_tools.run_sensitivity_sweep(project_id, cases)
    assert first["status"] == "submitted", first
    child = first["cases"][0]
    assert child["status"] == "queued" and child["created"]
    assert PyPSAService.get_active_context() is ctx and (path / "network.nc").read_bytes() == before
    assert not ctx.results_unsaved
    result = chat_tools.wait_for_job(child["job"]["id"], 25)
    assert result["completed"], result
    assert chat_tools.wait_for_job(first["cases"][1]["job"]["id"], 25)["completed"]
    evidence = chat_tools.get_study_evidence(child["project_id"], section="simulation", compare_to=project_id)
    assert evidence["summary"]["has_solve"] and evidence["comparison"]["project_a"] == project_id
    assert not chat_service._truncate_result(evidence).get("_truncated"), evidence
    with chat_tools._acting() as (db, user):
        base_row = project_registry.resolve_project(db, user, project_id)
        child_row = project_registry.resolve_project(db, user, child["project_id"])
        a, b = wt._load_context(base_row), wt._load_context(child_row)
        with wt._isolated(a):
            chat_tools.bulk_update_components("Generator", ["G1"], {"marginal_cost": 20})
        assert wt._network_input_fingerprint(a) == wt._network_input_fingerprint(b)
    second = chat_tools.run_sensitivity_sweep(project_id, cases)
    assert all(c["reused"] for c in second["cases"]), second
    assert {c["objective"] for c in second["cases"]} == {200, 400}
    assert not chat_service._truncate_result(second).get("_truncated"), second
    assert len(solve_queue.list_jobs()) == 2
    assert (path / "network.nc").read_bytes() == before and ctx.network.generators.at["G1", "marginal_cost"] == 10
    with pytest.raises(HTTPException) as exc:
        chat_tools.run_sensitivity_sweep(project_id, [case(value=30)])
    assert exc.value.detail["error_kind"] == "sensitivity_name_conflict"


def test_sweep_refuses_unsaved_baseline_and_surfaces_partial_queue_failure(baseline, monkeypatch):
    ctx, project_id, _ = baseline
    ctx.results_unsaved = True
    with pytest.raises(HTTPException) as exc:
        chat_tools.run_sensitivity_sweep(project_id, [case()])
    assert exc.value.detail["error_kind"] == "baseline_not_saved"
    ctx.results_unsaved = False
    def refuse(*args):
        raise HTTPException(409, {"error_kind": "queue_unavailable"})
    monkeypatch.setattr(chat_tools, "solve_queue_enqueue", refuse)
    result = chat_tools.run_sensitivity_sweep(project_id, [case()])
    assert result["status"] == "partial" and result["cases"][0]["project_id"]
    assert result["cases"][0]["error_kind"] == "queue_unavailable"


def test_sweep_retains_created_project_id_after_save_failure(baseline, monkeypatch):
    from routers import projects
    def refuse(*args, **kwargs):
        raise OSError("synthetic failed write")
    monkeypatch.setattr(projects, "_save_context", refuse)
    result = chat_tools.run_sensitivity_sweep(baseline[1], [case()])
    assert result["status"] == "partial" and result["cases"][0]["created"]
    with chat_tools._acting() as (db, user):
        child = project_registry.resolve_project(db, user, result["cases"][0]["project_id"])
        assert child.name == "Tool Sensitivity" and child.parent_project_id == uuid.UUID(baseline[1])


def test_sweep_rejects_case_folded_duplicate_names_before_creation(baseline):
    with pytest.raises(HTTPException) as exc:
        chat_tools.run_sensitivity_sweep(baseline[1], [case("Case A"), case("case a")])
    assert exc.value.detail["error_kind"] == "invalid_sensitivity_cases"
    assert not any(p["name"].casefold() == "case a" for p in chat_tools.list_projects())


def test_sweep_fingerprint_includes_investment_period_weights(baseline):
    ctx = baseline[0]
    ctx.network.set_investment_periods([2030, 2040])
    before = wt._network_input_fingerprint(ctx)
    ctx.network.investment_period_weightings.at[2030, "objective"] = 2
    assert wt._network_input_fingerprint(ctx) != before


def test_sweep_does_not_reuse_saved_results_while_a_job_is_active(baseline):
    first = chat_tools.run_sensitivity_sweep(baseline[1], [case()])
    child = first["cases"][0]
    assert chat_tools.wait_for_job(child["job"]["id"], 25)["completed"]
    solve_queue.pause()
    queued = chat_tools.solve_queue_enqueue(child["project_id"])
    second = chat_tools.run_sensitivity_sweep(baseline[1], [case()])
    assert not second["cases"][0]["reused"]
    assert second["cases"][0]["job"]["id"] == queued["id"]
    assert len(solve_queue.list_jobs()) == 2


def test_anthropic_catalogue_retains_full_registry_and_respects_toolsets(baseline):
    from types import SimpleNamespace
    session = chat_service.ChatSession()
    chat_tools.set_chat_session(session)
    profile = SimpleNamespace(wire="anthropic", tools=True)
    eligible = wiring._tools_payload()
    assert len(eligible) > 128 and wiring._tools_payload_for_profile(profile) == eligible
    session.toolset = "network"
    assert wiring._tools_payload_for_profile(profile) == filter_tools(eligible, "network")


@pytest.mark.parametrize("domain", DOMAINS)
def test_toolsets_keep_controls_catalogue_order_and_navigation(domain):
    tools = filter_tools(TOOLS, domain)
    names = {t["name"] for t in tools}
    assert CONTROL_TOOLS <= names and {"activate_project", "gridspine_create_study"} <= names
    assert tools == [t for t in TOOLS if t in tools]


def test_toolset_refresh_and_new_study_tools_are_available_within_same_turn(monkeypatch):
    session = setup_profile(monkeypatch, {t["name"] for t in TOOLS})
    # setup_profile's double is intentionally restored to the project-kind filter.
    monkeypatch.setattr(wiring, "_tools_payload", lambda *a: [t for t in TOOLS if not t["name"].startswith("gridspine_") or t["name"] == "gridspine_create_study"])
    fake = FakeProvider([
        {"blocks": [{"type": "tool_use", "id": "switch", "name": "use_toolset", "input": {"domain": "simulation"}}]},
        {"blocks": [{"type": "tool_use", "id": "read", "name": "get_solver_config", "input": {}}]},
        {"blocks": [{"type": "text", "text": "Ready."}]},
    ])
    frames = turn(session, "use_toolset simulation then get_solver_config", fake)
    assert not [d for e, d in frames if e in ("error", "tool_error")]
    assert len(fake.requests[1].tools) < len(fake.requests[0].tools)
    assert "get_solver_config" in {t["name"] for t in fake.requests[1].tools}
    assert "gridspine_run_pipeline" not in {t["name"] for t in fake.requests[1].tools}
    assert session.toolset == "simulation"


def test_sweep_confirmation_denial_does_not_create_projects(baseline, monkeypatch):
    _, project_id, _ = baseline
    session = setup_profile(monkeypatch, {"run_sensitivity_sweep"})
    fake = FakeProvider([
        {"blocks": [{"type": "tool_use", "id": "sweep", "name": "run_sensitivity_sweep",
                     "input": {"baseline_project_id": project_id, "cases": [case()]}}]},
        {"blocks": [{"type": "text", "text": "Declined."}]},
    ])
    frames = turn(session, "Run a sensitivity", fake, decision="deny")
    assert any(e == "tool_pending_confirmation" for e, _ in frames)
    assert not any(p["name"] == "Tool Sensitivity" for p in chat_tools.list_projects())
    assert safety_tier_for("run_sensitivity_sweep") == "execution"


def test_study_create_activate_and_read_config_in_one_turn(baseline, monkeypatch):
    original = wiring._tools_payload
    session = setup_profile(monkeypatch, {t["name"] for t in TOOLS})
    monkeypatch.setattr(wiring, "_tools_payload", original)
    fake = FakeProvider([
        {"blocks": [{"type": "tool_use", "id": "create", "name": "gridspine_create_study", "input": {"name": "Same Turn Study", "config": {"hours": 24}}}]},
        {"blocks": [{"type": "tool_use", "id": "activate", "name": "activate_project", "input": {"project_id": "Same Turn Study"}}]},
        {"blocks": [{"type": "tool_use", "id": "config", "name": "gridspine_get_config", "input": {"project_id": "Same Turn Study"}}]},
        {"blocks": [{"type": "text", "text": "Study activated and configuration read."}]},
    ])
    frames = turn(session, "Create, activate, then gridspine_get_config for a new study", fake)
    assert not [d for e, d in frames if e in ("error", "tool_error")]
    assert "gridspine_get_config" not in {t["name"] for t in fake.requests[0].tools}
    assert "gridspine_get_config" in {t["name"] for t in fake.requests[2].tools}
    assert PyPSAService.get_loaded_project() == "Same Turn Study"


def test_wait_progress_uses_existing_frames_and_preserves_continuation(baseline, monkeypatch):
    ctx, _, path = baseline
    solve_queue.pause()
    job = solve_queue.enqueue("Tool Baseline", project_key=ctx.registry_key, storage_dir=str(path))
    session = setup_profile(monkeypatch, {"wait_for_job"})
    fake = FakeProvider([
        {"blocks": [{"type": "tool_use", "id": "wait", "name": "wait_for_job", "input": {"job_id": str(job.id), "timeout_seconds": 1}}]},
        {"blocks": [{"type": "text", "text": "Completed."}]},
    ])
    timer = threading.Timer(0.4, lambda: setattr(job, "status", "completed"))
    timer.start()
    try:
        frames = turn(session, "Wait for the job", fake)
        assert any(e == "tool_progress" and d["tool_name"] == "wait_for_job" for e, d in frames)
        assert next(d for e, d in frames if e == "tool_result")["result"]["completed"]
        assert len(fake.requests) == 2 and frames[-1][0] == "turn_done"
    finally:
        timer.join()


def test_wait_reads_persisted_interrupted_job(baseline):
    from services import solve_job_store
    ctx, _, path = baseline
    solve_queue.pause()
    job = solve_queue.enqueue("Tool Baseline", project_key=ctx.registry_key, storage_dir=str(path))
    solve_job_store.record_enqueued(job, enqueued_by_user_id=uuid.UUID(chat_tools.acting_user_id()), solver_config_json=None)
    job.status = "interrupted"
    assert solve_job_store.record_status(job)
    with solve_queue._lock:
        solve_queue._jobs.pop(job.id)
    result = chat_tools.wait_for_job(str(job.id), 0)
    assert result["terminal"] and result["job"]["status"] == "interrupted" and not result["completed"]


def test_real_gridspine_wait_rank_evidence_and_refinement(client):
    study = chat_tools.gridspine_create_study("Real Evidence", {"hours": 24, "window": 24, "overlap": 0, "k": 1, "screen": True})
    job = chat_tools.gridspine_run_pipeline(study["id"])
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        result = chat_tools.wait_for_job(job["id"], 25)
        if result["terminal"]:
            break
    assert result["completed"], result
    readiness = chat_tools.project_readiness(study["id"])
    assert readiness["evidence"]["fresh"], readiness
    ranked = chat_tools.get_study_evidence(study["id"], section="ranked_snapshots", limit=50)
    assert ranked["available"] and ranked["total_count"] > 0
    assert ranked["counts_before_status_filter"] == {"converged": ranked["total_count"]}
    assessment = chat_tools.gridspine_assess_connection(study["id"], bus="BUS_16", load_mw=1, load_pf=0.98)
    evidence = chat_tools.get_study_evidence(study["id"], check="connection", assessment_id=assessment["assessment_id"], limit=1)
    expected = [r for r in assessment["rows"] if r["check"] == "connection"]
    assert sum(evidence["counts_before_status_filter"].values()) == len(expected)
    capacity = chat_tools.get_study_evidence(study["id"], section="capacity", bus="BUS_16", limit=50)
    assert capacity["total_count"] > 0 and set(capacity["counts_before_status_filter"]) <= {"preexisting_violation", "ac_computed", "dc_only"}
    chat_tools.gridspine_update_config(study["id"], k=2)
    assert not chat_tools.get_study_evidence(study["id"])["available"]
