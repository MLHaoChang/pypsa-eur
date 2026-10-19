"""One ordinary authorized case, real solver evidence, and bounded recovery."""
# ruff: noqa: F811 -- imported pytest fixtures
import json
import time

import pytest
from fastapi import HTTPException

from harness.session import ChatSession
from services import chat_tools, experiments as ex, project_registry, workflow_tools as wt
from services.pypsa_service import PyPSAService
from services.solve_queue import solve_queue
from tests.test_workflow_tools import baseline  # noqa: F401
from tests.test_workflow_tools import helpers_state  # noqa: F401
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_chat_tool_executor_starvation import saturated_pool  # noqa: F401


def tool(name, **args):
    assert name in chat_tools.DISPATCHERS, f"Missing ordinary harness control: {name}"
    return chat_tools.DISPATCHERS[name](**args)


def prepared(limits=None, path="Generator.G1.marginal_cost", value=20):
    plan = ex.start_experiment(title="One checked case", request_id="one",
        cases=[{"id": "cost", "changes": [{"path": path,
            "value_type": "number", "value": value}]}], limits=limits)
    return ex.prepare_experiment(plan["experiment_id"])["experiment_id"]


def arm(experiment_id):
    session = ChatSession()
    chat_tools.set_chat_session(session)
    return session, tool("advance_experiment", experiment_id=experiment_id)


def invoke(session, call, handler=None):
    from harness import experiment_execution as engine
    return engine.invoke(session, call["tool"], call["args"],
                         handler or chat_tools.DISPATCHERS[call["tool"]])


def test_advance_only_arms_an_ordinary_call(baseline):
    ctx, _, root = baseline
    before = (root / "network.nc").read_bytes()
    eid = prepared()
    _, state = arm(eid)
    assert state["status"] == "ready" and state["executed"] is False
    assert state["next_call"]["tool"] == "run_sensitivity_sweep"
    assert len(state["next_call"]["args"]["cases"]) == 1
    assert not solve_queue.list_jobs()
    assert (root / "network.nc").read_bytes() == before and not ctx.results_unsaved


def test_approved_case_without_a_worker_reserves_nothing_and_cannot_run_later(
    baseline, saturated_pool, monkeypatch,
):
    from harness import budget
    from services import chat_service

    release, _ = saturated_pool
    _, _, root = baseline
    before = (root / "network.nc").read_bytes()
    eid = prepared()
    session, state = arm(eid)
    call = state["next_call"]
    monkeypatch.setattr(session, "wait_for_decision", lambda _: "approve")
    monkeypatch.setattr(budget, "PER_TOOL_TIMEOUT_SECONDS", 0.05)
    results = []
    frames = list(chat_service._dispatch_real_tool_call(session,
        {"id": "starved-case", "name": call["tool"], "input": call["args"]}, results))
    assert any(event == "tool_pending_confirmation" for event, _ in frames)
    errors = [p for event, p in frames if event == "tool_error"]
    assert len(errors) == 1 and errors[0]["error_kind"] == "tool_not_started"
    release.set()
    # A barrier after the cancelled future establishes that the pool drained.
    chat_service._TOOL_EXECUTOR.submit(lambda: None).result(timeout=5)
    current = tool("refresh_experiment", experiment_id=eid)
    assert current["status"] == "ready" and current["attempts_used"] == 0
    assert not solve_queue.list_jobs()
    assert (root / "network.nc").read_bytes() == before


def test_real_case_finishes_only_with_terminal_and_intact_evidence(baseline, client):
    ctx, project_id, root = baseline
    before = (root / "network.nc").read_bytes()
    eid = prepared()
    session, state = arm(eid)
    outcome = invoke(session, state["next_call"])
    child = outcome["cases"][0]
    waited = chat_tools.wait_for_job(child["job"]["id"], 25)
    assert waited["completed"], waited
    from services import run_evidence
    with chat_tools._acting() as (db, user):
        child_row = project_registry.resolve_project(db, user, child["project_id"])
        manifest = run_evidence._load_run(project_registry.project_dir(child_row),
            str(child_row.id), run_evidence.queue_run_id(child["job"]["id"]))
        assert manifest is not None, "Queue evidence marker missing"
        assert manifest["status"] == "completed", manifest["status"]
        assert manifest["actor_id"] == str(user.id), (manifest["actor_id"], str(user.id))
        assert run_evidence._evidence_available(project_registry.project_dir(child_row), manifest)
    complete = tool("refresh_experiment", experiment_id=eid)
    assert complete["status"] == "completed" and complete["executed"] is True, complete["last_error"]
    assert complete["result_available"] is True
    assert complete["run_id"] and complete["evidence_sha256"]
    assert complete["objective"] == pytest.approx(200)  # 5 MW * 2 h * 20
    assert complete["objective_unit"] == "EUR"
    with chat_tools._acting() as (db, user):
        row = project_registry.resolve_project(db, user, complete["case_project_id"])
        saved = wt._load_context(row)
    assert saved.network.generators_t.p["G1"].tolist() == [5, 5]
    assert saved.network.generators.at["G1", "marginal_cost"] == 20
    assert (root / "network.nc").read_bytes() == before
    assert PyPSAService.get_active_context() is ctx and ctx.project_uuid == project_id
    assert not ctx.results_unsaved and len(solve_queue.list_jobs()) == 1
    resumed_session, resumed = arm(eid)
    assert resumed["status"] == "completed" and "next_call" not in resumed
    with pytest.raises(HTTPException):
        invoke(resumed_session, state["next_call"])
    assert len(solve_queue.list_jobs()) == 1
    from harness import experiment_execution as engine
    engine.invoke(resumed_session, "activate_project", {"project_id": complete["case_project_id"]}, chat_tools.activate_project)
    assert resumed_session.experiment_id is None
    exported = chat_tools.export_run_evidence(run_id=complete["run_id"])
    delivery = chat_tools.get_file_delivery(exported["file_id"])
    downloaded = client.get(delivery["download_url"])
    assert downloaded.status_code == 200, downloaded.text
    payload = downloaded.json()
    assert payload["run_id"] == complete["run_id"]
    assert payload["metrics"]["objective"] == {"value": 200, "unit": "EUR"}
    engine.invoke(resumed_session, "activate_project", {"project_id": project_id}, chat_tools.activate_project)
    assert (root / "network.nc").read_bytes() == before


def test_changed_call_and_subprecision_baseline_drift_refuse(baseline):
    ctx, _, _ = baseline
    eid = prepared()
    session, state = arm(eid)
    changed = json.loads(json.dumps(state["next_call"]))
    changed["args"]["cases"][0]["changes"][0]["value"] = 21
    with pytest.raises(HTTPException):
        invoke(session, changed)
    ctx.network.generators.at["G1", "marginal_cost"] += 1e-11
    with pytest.raises(HTTPException):
        invoke(session, state["next_call"])
    assert not solve_queue.list_jobs()


def test_reservation_survives_interruption_and_cannot_replay(baseline):
    _, _, root = baseline
    eid = prepared()
    session, state = arm(eid)
    def interrupted(**_):
        checkpoint = json.loads((root / "experiment_execution" / (eid + ".json")).read_text())
        assert checkpoint["status"] == "submitting"
        assert checkpoint["attempts"][0]["status"] == "reserved"
        raise RuntimeError("interrupted before receipt")
    with pytest.raises(RuntimeError):
        invoke(session, state["next_call"], interrupted)
    resumed_session, resumed = arm(eid)
    assert resumed["status"] == "uncertain" and resumed["executed"] is None
    assert "next_call" not in resumed
    with pytest.raises(HTTPException):
        invoke(resumed_session, state["next_call"])
    assert not solve_queue.list_jobs()


def test_cancel_stops_advancement_without_aborting_job(baseline):
    eid = prepared()
    session, state = arm(eid)
    result = invoke(session, state["next_call"])
    job_id = result["cases"][0]["job"]["id"]
    cancelled = tool("cancel_experiment", experiment_id=eid)
    assert cancelled["cancelled"] is True and cancelled["job_id"] == job_id
    assert chat_tools.wait_for_job(job_id, 25)["completed"]
    refreshed = tool("refresh_experiment", experiment_id=eid)
    assert refreshed["cancelled"] and refreshed["status"] == "completed", refreshed
    assert refreshed["result_available"]


def test_elapsed_cap_refuses_new_effect_and_keeps_plan(baseline, monkeypatch):
    eid = prepared({"max_elapsed_seconds": 1})
    session, state = arm(eid)
    from harness import experiment_execution as engine
    now = time.time()
    monkeypatch.setattr(engine, "_clock", lambda: now + 10)
    with pytest.raises(HTTPException) as error:
        invoke(session, state["next_call"])
    assert error.value.detail["error_kind"] == "experiment_budget_exhausted"
    assert not solve_queue.list_jobs()


def test_reserved_scenario_name_cannot_bypass_ledger(baseline):
    eid = prepared()
    _, state = arm(eid)
    call = state["next_call"]
    chat_tools.set_chat_session(None)
    with pytest.raises(HTTPException):
        chat_tools.run_sensitivity_sweep(**call["args"])
    assert not solve_queue.list_jobs()


def test_real_dispatch_denial_creates_no_case(baseline, monkeypatch):
    from harness.providers.fake import FakeProvider
    from tests.test_openai_comprehensive_live import setup_profile, turn
    eid = prepared()
    session = setup_profile(monkeypatch, {"advance_experiment", "run_sensitivity_sweep"})
    chat_tools.set_chat_session(session)
    state = tool("advance_experiment", experiment_id=eid)
    call = state["next_call"]
    fake = FakeProvider([{"blocks": [{"type": "tool_use", "id": "case",
        "name": call["tool"], "input": call["args"]}]},
        {"blocks": [{"type": "text", "text": "Declined."}]}])
    frames = turn(session, "Run the prepared case", fake, decision="deny")
    assert any(event == "tool_pending_confirmation" for event, _ in frames)
    assert not solve_queue.list_jobs()
    assert tool("refresh_experiment", experiment_id=eid)["status"] == "ready"


def test_queued_is_not_completed_and_attempt_cap_does_not_abort(baseline):
    eid = prepared({"max_tool_attempts": 1})
    session, state = arm(eid)
    solve_queue.pause()
    try:
        result = invoke(session, state["next_call"])
        job_id = result["cases"][0]["job"]["id"]
        queued = tool("refresh_experiment", experiment_id=eid)
        assert queued["status"] == "submitted" and queued["executed"] is False
        assert not queued["result_available"] and "objective" not in queued
        assert "next_call" not in queued and queued["attempts_used"] == 1
        with pytest.raises(HTTPException) as error:
            invoke(session, {"tool": "wait_for_job", "args": {"job_id": job_id, "timeout_seconds": 0}})
        assert error.value.detail["error_kind"] == "experiment_budget_exhausted"
    finally:
        solve_queue.resume()
    assert chat_tools.wait_for_job(job_id, 25)["completed"]
    assert tool("refresh_experiment", experiment_id=eid)["status"] == "completed"


def test_infeasible_case_never_publishes_objective(baseline):
    eid = prepared(path="Generator.G1.p_nom", value=1)
    session, state = arm(eid)
    result = invoke(session, state["next_call"])
    job_id = result["cases"][0]["job"]["id"]
    terminal = chat_tools.wait_for_job(job_id, 25)
    assert terminal["terminal"] and not terminal["completed"]
    failed = tool("refresh_experiment", experiment_id=eid)
    assert failed["status"] == "failed" and not failed["result_available"]
    assert "objective" not in failed and "next_call" not in failed
    with pytest.raises(HTTPException):
        invoke(session, state["next_call"])


@pytest.mark.parametrize("damage", ["json", "duplicate", "unknown", "false_complete", "ready_after_submission"])
def test_corrupt_ledger_never_resets_or_replays(baseline, damage):
    _, _, root = baseline
    eid = prepared()
    session, state = arm(eid)
    path = root / "experiment_execution" / (eid + ".json")
    data = json.loads(path.read_text())
    if damage == "json":
        payload = "{"
    elif damage == "duplicate":
        payload = '{"version":1,"version":1}'
    else:
        if damage == "unknown":
            data["status"] = "invented"
        elif damage == "false_complete":
            data["status"] = "completed"
        else:
            data["attempts"] = [{"id": "a" * 32, "tool": "run_sensitivity_sweep", "status": "returned", "started_at": time.time()}]
        payload = json.dumps(data)
    path.write_text(payload)
    with pytest.raises(HTTPException):
        invoke(session, state["next_call"])
    assert path.read_text() == payload and not solve_queue.list_jobs()


@pytest.mark.parametrize("damage", ["evidence", "precision", "solve_identity"])
def test_completed_source_damage_withholds_values(baseline, damage):
    eid = prepared()
    session, state = arm(eid)
    result = invoke(session, state["next_call"])
    child = result["cases"][0]
    assert chat_tools.wait_for_job(child["job"]["id"], 25)["completed"]
    complete = tool("refresh_experiment", experiment_id=eid)
    assert complete["result_available"], complete["last_error"]
    with chat_tools._acting() as (db, user):
        row = project_registry.resolve_project(db, user, child["project_id"])
        root = project_registry.project_dir(row)
        if damage == "evidence":
            (root / "run_evidence" / complete["run_id"] / "sections/summary.json").write_text("{}")
        else:
            saved = wt._load_context(row)
            if damage == "precision":
                saved.network.generators.at["G1", "p_nom"] += 1e-11
            else:
                saved.network.meta.pop("assistant_solve_exact_input_fingerprint")
            saved.network.export_to_netcdf(root / "network.nc")
    stale = tool("refresh_experiment", experiment_id=eid)
    assert stale["status"] == "unavailable" and stale["executed"] is True
    assert not stale["result_available"] and "objective" not in stale


def test_armed_preflight_cannot_be_replaced(baseline):
    eid = prepared()
    arm(eid)
    with pytest.raises(HTTPException) as error:
        ex.prepare_experiment(eid)
    assert error.value.detail["error_kind"] == "experiment_already_armed"


def test_foreign_actor_cannot_advance_refresh_cancel_or_read(baseline, second_identity):
    eid = prepared()
    arm(eid)
    previous = chat_tools.acting_user_id()
    chat_tools.set_acting_user(second_identity["user_id"])
    try:
        for name in ("advance_experiment", "refresh_experiment", "cancel_experiment", "get_experiment"):
            with pytest.raises(HTTPException) as error:
                tool(name, experiment_id=eid)
            assert error.value.status_code == 404
    finally:
        chat_tools.set_acting_user(previous)


@pytest.mark.parametrize("decision", ["approve", "deny"])
def test_authenticated_http_case_uses_real_confirmation_and_receipts(baseline, client, monkeypatch, decision):
    from concurrent.futures import ThreadPoolExecutor
    from harness import experiment_execution as engine
    from services import chat_service
    from tests.assurance_acceptance.support import Harness, parse_sse, text_turn, tool_turn
    ctx, _, root = baseline
    before = (root / "network.nc").read_bytes()
    eid = prepared()
    _, armed = arm(eid)
    call = armed["next_call"]
    sid = "case-http-" + decision
    harness = Harness(client, monkeypatch)
    harness.install([tool_turn("advance_experiment", {"experiment_id": eid}),
        tool_turn(call["tool"], call["args"]), text_turn("The case receipt is available.")])
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.post, "/api/chat/stream", json={"session_id": sid,
                "message": "advance_experiment and run_sensitivity_sweep for this exact case"})
            deadline = time.monotonic() + 10
            token = None
            while time.monotonic() < deadline and not pending.done():
                session = chat_service.get_session(sid)
                if session:
                    with session._lock:
                        token = next((k for k, c in session.pending_confirmations.items()
                            if c.tool_name == "run_sensitivity_sweep"), None)
                if token:
                    break
                time.sleep(0.02)
            if token is None:
                client.post(f"/api/chat/{sid}/abort")
            assert token, "The public execution confirmation was not issued"
            confirmed = client.post(f"/api/chat/{sid}/confirm", json={"token": token, "decision": decision})
            assert confirmed.status_code == 200, confirmed.text
            response = pending.result(timeout=10)
        assert response.status_code == 200, response.text
        frames = parse_sse(response.text)
        assert any(event == "tool_pending_confirmation" for event, _ in frames)
        errors = [p for event, p in frames if event in ("error", "tool_error")]
        if decision == "deny":
            assert len(errors) == 1 and errors[0]["error_kind"] == "confirmation_denied"
            assert not solve_queue.list_jobs()
            assert tool("refresh_experiment", experiment_id=eid)["status"] == "ready"
        else:
            assert not errors, errors
            outputs = [p["result"] for event, p in frames if event == "tool_result" and p.get("tool_name") == call["tool"]]
            assert outputs
            job_id = outputs[0]["cases"][0]["job"]["id"]
            harness.install([tool_turn("wait_for_job", {"job_id": job_id, "timeout_seconds": 20}), text_turn("Job inspected.")])
            response = client.post("/api/chat/stream", json={"session_id": sid, "message": "wait_for_job for the existing case"})
            assert response.status_code == 200
            assert not [p for event, p in parse_sse(response.text) if event in ("error", "tool_error")]
            harness.install([tool_turn("refresh_experiment", {"experiment_id": eid}), text_turn("Verified receipt.")])
            response = client.post("/api/chat/stream", json={"session_id": sid, "message": "refresh_experiment and report its recorded outcome"})
            states = [p["result"] for event, p in parse_sse(response.text)
                if event == "tool_result" and p.get("tool_name") == "refresh_experiment"]
            assert states and states[0]["status"] == "completed", states
            assert states[0]["objective"] == 200 and states[0]["attempts_used"] == 2
            assert len(solve_queue.list_jobs()) == 1
        assert (root / "network.nc").read_bytes() == before
        assert PyPSAService.get_active_context() is ctx and not ctx.results_unsaved
        assert engine._ADMISSION.get() is None
    finally:
        chat_service.drop_session(sid)


def test_partial_creation_preserves_child_and_forbids_retry(baseline, monkeypatch):
    eid = prepared()
    session, state = arm(eid)
    def refuse(*_, **__):
        raise HTTPException(409, {"error_kind": "queue_unavailable"})
    monkeypatch.setattr(chat_tools, "solve_queue_enqueue", refuse)
    partial = invoke(session, state["next_call"])
    assert partial["status"] == "partial"
    view = tool("refresh_experiment", experiment_id=eid)
    assert view["status"] == "uncertain" and view["executed"] is None
    assert view["case_project_id"] == partial["cases"][0]["project_id"]
    assert view["case_effects_reserved"] and not view["result_available"]
    with pytest.raises(HTTPException):
        invoke(session, state["next_call"])
    assert not solve_queue.list_jobs()


def test_unprepared_and_larger_than_two_case_plans_cannot_arm(baseline):
    plan = ex.start_experiment(title="Unprepared", request_id="u",
        cases=[{"id": "a", "changes": [{"path": "Generator.G1.p_nom", "value_type": "number", "value": 30}]}])
    with pytest.raises(HTTPException) as error:
        arm(plan["experiment_id"])
    assert error.value.detail["error_kind"] == "experiment_preflight_required"
    plan = ex.start_experiment(title="Three", request_id="three", cases=[
        {"id": "a", "changes": [{"path": "Generator.G1.p_nom", "value_type": "number", "value": 30}]},
        {"id": "b", "changes": [{"path": "Generator.G1.p_nom", "value_type": "number", "value": 40}]},
        {"id": "c", "changes": [{"path": "Generator.G1.p_nom", "value_type": "number", "value": 50}]}])
    ex.prepare_experiment(plan["experiment_id"])
    with pytest.raises(HTTPException) as error:
        arm(plan["experiment_id"])
    assert error.value.detail["error_kind"] == "experiment_adapter_unavailable"
    assert not solve_queue.list_jobs()


def test_saved_baseline_precision_drift_refuses_before_reservation(baseline):
    _, project_id, root = baseline
    eid = prepared()
    session, state = arm(eid)
    with chat_tools._acting() as (db, user):
        row = project_registry.resolve_project(db, user, project_id)
        saved = wt._load_context(row)
    saved.network.generators.at["G1", "p_nom"] += 1e-11
    saved.network.export_to_netcdf(root / "network.nc")
    with pytest.raises(HTTPException) as error:
        invoke(session, state["next_call"])
    assert error.value.detail["error_kind"] == "experiment_baseline_not_saved"
    assert not solve_queue.list_jobs()
    assert tool("refresh_experiment", experiment_id=eid)["attempts_used"] == 0


def test_reserved_name_conflict_is_not_adopted_as_completed(baseline):
    _, project_id, _ = baseline
    eid = prepared()
    session, state = arm(eid)
    name = state["next_call"]["args"]["cases"][0]["new_name"]
    chat_tools.create_scenario(project_id, name)
    with pytest.raises(HTTPException) as error:
        invoke(session, state["next_call"])
    assert error.value.detail["error_kind"] == "experiment_case_name_conflict"
    assert tool("refresh_experiment", experiment_id=eid)["attempts_used"] == 0
    assert not solve_queue.list_jobs()


@pytest.mark.parametrize("name", ["advance_experiment", "refresh_experiment", "cancel_experiment"])
def test_strict_checked_chat_does_not_admit_new_controls(baseline, checked, name):
    from tests.assurance_acceptance.support import UNSAFE, public_text, text_turn, tool_turn
    _, _, root = baseline
    eid = prepared()
    frames = checked.run([tool_turn(name, {"experiment_id": eid}), text_turn(UNSAFE), text_turn(UNSAFE)])
    assert any(event == "tool_error" and payload.get("tool_name") == name for event, payload in frames)
    assert UNSAFE not in public_text(frames)
    assert not list((root / "experiment_execution").glob("*.json"))
    assert not solve_queue.list_jobs()


def test_missing_reserved_ledger_cannot_be_recreated_or_reprepared(baseline):
    _, _, root = baseline
    eid = prepared()
    arm(eid)
    path = root / "experiment_execution" / (eid + ".json")
    path.unlink()
    for call in (lambda: arm(eid), lambda: ex.get_experiment(eid), lambda: ex.prepare_experiment(eid)):
        with pytest.raises(HTTPException):
            call()
        assert not path.exists()
    assert not solve_queue.list_jobs()
