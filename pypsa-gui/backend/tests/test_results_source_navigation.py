"""Public owned result projections and displayed-data ACKs, without inference."""
# ruff: noqa: F811 -- imported pytest fixture
import copy

import pytest

from harness import assurance, navigation
from harness.session import ChatSession
from services import assurance_records, chat_tools, loadflow_summary, run_evidence
from tests.test_loadflow_summary import build_network, SNAPSHOTS, _record_fixture_inputs
from tests.test_workflow_tools import baseline  # noqa: F401


@pytest.fixture
def result_project(baseline):
    ctx, project_id, root = baseline
    ctx.network = build_network()
    ctx.solver_state.update(status="completed", condition="optimal", objective=1000)
    _record_fixture_inputs(ctx.network)
    return ctx, project_id, root


def test_public_view_and_chat_match_independent_terminal_window_oracle(client, result_project):
    response = client.get("/api/results/loadflow-view", params={"window_from": 1, "window_to": 2})
    assert response.status_code == 200, response.text
    view = response.json()
    summary = chat_tools.get_loadflow_summary(limit=25, window_from=1, window_to=2)
    assert view["top"] == summary["top"]
    assert view["evidence_token"] == summary["evidence_token"]
    row = next(r for r in view["rows"] if r["name"] == "L_p1")
    assert row["util_pct"] == 60 and row["mean_util_pct"] == 50
    assert view["binding"]["project_id"] == result_project[1]


def issued(ctx):
    owner = assurance_records.actor_id()
    turn = assurance.Turn(ChatSession(owner_user_id=owner), ctx)
    navigation.begin_turn(turn, 3)
    action = navigation.issue(turn, {"kind": "navigate", "panel_id": "results", "results_tab": "loadflow"})
    assert action["version"] == 2
    return {k: action[k] for k in ("version", "action_id", "turn_id", "binding_generation", "target", "source_binding")}


def test_source_ack_requires_exact_rendered_evidence_and_is_idempotent(client, result_project):
    body = issued(result_project[0])
    wrong = copy.deepcopy(body)
    wrong["source_binding"]["window_from"] = 1
    assert client.post("/api/chat/ui-ack", json=wrong).status_code == 409
    first = client.post("/api/chat/ui-ack", json=body)
    assert first.status_code == 200, first.text
    assert first.json()["verification"] == "browser_displayed_data"
    assert client.post("/api/chat/ui-ack", json=body).json() == first.json()


def test_source_ack_uses_http_owner_without_a_model_turn_actor(client, result_project):
    body = issued(result_project[0])
    previous = chat_tools.acting_user_id()
    chat_tools.set_acting_user(None)
    try:
        view = client.get("/api/results/loadflow-view")
        assert view.status_code == 200, view.text
        ack = client.post("/api/chat/ui-ack", json=body)
        assert ack.status_code == 200, ack.text
    finally:
        chat_tools.set_acting_user(previous)


@pytest.mark.parametrize("key,value", [("version", True), ("window_from", False)])
def test_boolean_alias_cannot_forge_a_typed_source_ack(client, result_project, key, value):
    body = issued(result_project[0])
    body["source_binding"][key] = value
    assert client.post("/api/chat/ui-ack", json=body).status_code == 422
    record = client.get("/api/chat/navigation/" + body["action_id"]).json()
    assert record["outcome"] == "submitted"


@pytest.mark.parametrize("fault", ["non_peak_flow", "weights", "failed_solve"])
def test_mounted_target_with_stale_result_cannot_complete(client, result_project, fault):
    ctx = result_project[0]
    body = issued(ctx)
    if fault == "non_peak_flow":
        ctx.network.lines_t.p0.loc[SNAPSHOTS[1], "L_hot"] += 0.00000001
    elif fault == "weights":
        ctx.network.snapshot_weightings.loc[SNAPSHOTS[1], "generators"] = 8
    else:
        ctx.solver_state["status"] = "failed"
    assert client.post("/api/chat/ui-ack", json=body).status_code == 409
    record = client.get("/api/chat/navigation/" + body["action_id"]).json()
    assert record["outcome"] == "submitted"


def test_retained_projection_never_reads_live_results_and_missing_section_refuses(client, result_project):
    ctx, project_id, root = result_project
    original = loadflow_summary.get_loadflow_summary()
    run_evidence.publish_run(root, project_id, run_id="retained-old", kind="simulation", status="completed",
                            mode="lopf", fingerprint="old-inputs", sections={"summary": {"objective": 123}},
                            metrics={"objective": {"value": 123, "unit": "EUR"}})
    view = client.get("/api/results/run-view", params={"run_id": "retained-old", "section": "summary"})
    assert view.status_code == 200, view.text
    assert view.json()["data"]["data"] == {"objective": 123}
    assert view.json()["binding"]["scope"] == "historical"
    missing = client.get("/api/results/run-view", params={"run_id": "retained-old", "section": "loadflow"})
    assert missing.status_code == 409
    assert missing.json()["detail"]["error_kind"] == "evidence_unavailable"
    assert loadflow_summary.get_loadflow_summary() == original
    assert ctx.network is not None


def test_read_projection_cannot_reuse_inherited_actor_without_http_auth(client, result_project):
    client.cookies.clear()
    assert client.get("/api/results/loadflow-view").status_code == 401
    assert client.get("/api/results/run-view", params={"run_id": "unknown"}).status_code == 401


def test_a_project_switch_forgets_the_load_flow_just_read(monkeypatch):
    """Showing means the data this turn read. A mid-turn project switch drops
    that read, so the new project is not described as showing the old numbers.
    A read that did not succeed never counts."""
    from types import SimpleNamespace

    from services.pypsa_service import PyPSAService

    session = SimpleNamespace(owner_user_id="owner-nav", session_id="nav-session")
    turn = navigation.OrdinaryTurn(session, SimpleNamespace(project_uuid="project-a"), 1)
    shown = {"source": "lopf", "window_from": 0, "window_to": 3}
    turn.note_read("get_loadflow_summary", {"status": "unavailable", "ui_reference": shown})
    assert turn.reads == {}
    turn.note_read("get_loadflow_summary", {"status": "ok", "ui_reference": shown})
    ack = {"action_id": "act-1", "target": {"results_tab": "loadflow"}, "source_binding": shown}
    monkeypatch.setattr(navigation.records, "load", lambda *_a, **_k: {
        "outcome": "completed", "verification": "browser_displayed_data"})
    assert navigation.display(turn, ack, wait=0) == {
        "verification": "browser_displayed_data", "say": "showing"}

    monkeypatch.setattr(PyPSAService, "get_active_context",
                        lambda: SimpleNamespace(project_uuid="project-b"))
    navigation.rebind(turn)
    assert turn.project_id == "project-b" and turn.reads == {}
    after = navigation.display(turn, ack, wait=0)
    assert after["say"] == "opened" and after["verification"] == "browser_displayed_data"
    assert after["differs_from_read"] == {"read": None, "shown": shown}


def test_corrupt_retained_section_and_unknown_baseline_refuse(client, result_project):
    _ctx, project_id, root = result_project
    run_evidence.publish_run(root, project_id, run_id="old", kind="simulation", status="completed",
                            sections={"loadflow": {"window": {"from": 0, "to": 3}, "value": 12}})
    missing_baseline = client.get("/api/results/run-view", params={"run_id": "old", "baseline_run_id": "unknown"})
    assert missing_baseline.status_code == 404
    artifact = root / "run_evidence" / "old" / "sections" / "loadflow.json"
    artifact.write_text('{"value":999}')
    corrupt = client.get("/api/results/run-view", params={"run_id": "old", "section": "loadflow"})
    assert corrupt.status_code == 409
    assert corrupt.json()["detail"]["reason"] == "artifact_corrupt"
