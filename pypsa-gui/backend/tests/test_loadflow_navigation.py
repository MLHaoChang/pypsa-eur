"""Live-voice 07: navigation to the Load flow view with source, window and line.

The ACK binds the exact view the assistant described (the loadflow-view binding,
verbatim) and the highlighted branch; a re-solve, a project switch, a newer
turn or a changed highlight cannot complete it.
"""
import copy
import json
from importlib import import_module

import httpx
import pytest
from fastapi import HTTPException
from jsonschema import Draft202012Validator

from harness import assurance, navigation
from harness.providers.anthropic import AnthropicProvider
from harness.providers.openai_compat import OpenAICompatProvider
from harness.session import ChatSession
from services import assurance_records, chat_service, chat_tools, llm_config
from services.loadflow_summary import get_loadflow_view
from tests.test_loadflow_summary import SNAPSHOTS, _record_fixture_inputs, build_network
from tests.test_workflow_provider_parity import (
    PROVIDERS, _anthropic_stream, _compatible_stream, _last_result,
)
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401

HOT = {"component": "Line", "name": "L_hot"}
ACK_KEYS = ("version", "action_id", "turn_id", "binding_generation", "target", "source_binding")


@pytest.fixture
def result_project(baseline):  # noqa: F811 — pytest fixture
    ctx, project_id, root = baseline
    ctx.network = build_network()
    ctx.solver_state.update(status="completed", condition="optimal", objective=1000)
    _record_fixture_inputs(ctx.network)
    return ctx, project_id, root


def _turn(ctx, generation=3):
    turn = assurance.Turn(ChatSession(owner_user_id=assurance_records.actor_id()), ctx)
    navigation.begin_turn(turn, generation)
    return turn


def _navigate(**loadflow):
    return {"kind": "navigate", "panel_id": "results", "results_tab": "loadflow", **loadflow}


def _ack_body(action):
    return {k: action[k] for k in ACK_KEYS}


def _kind(exc):
    return exc.value.detail["error_kind"]


# ── Argument rules ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("kwargs", [
    {"panel_id": "results", "results_tab": "dispatch", "loadflow_source": "lopf"},
    {"panel_id": "Issues", "highlight_branch": HOT},
    {"panel_id": "results", "results_tab": "loadflow", "loadflow_source": "dc"},
    {"panel_id": "results", "results_tab": "loadflow", "window_from": 1},
    {"panel_id": "results", "results_tab": "loadflow", "window_to": 1},
    {"panel_id": "results", "results_tab": "loadflow", "window_from": True, "window_to": 2},
    {"panel_id": "results", "results_tab": "loadflow", "window_from": 2, "window_to": 1},
    {"panel_id": "results", "results_tab": "loadflow", "window_from": 0, "window_to": len(SNAPSHOTS)},
    {"panel_id": "results", "results_tab": "loadflow", "window_from": -1, "window_to": 1},
    {"panel_id": "results", "results_tab": "loadflow", "highlight_branch": {"component": "Bus", "name": "b"}},
    {"panel_id": "results", "results_tab": "loadflow", "highlight_branch": {"component": "Line", "name": ""}},
    {"panel_id": "results", "results_tab": "loadflow", "highlight_branch": {"component": "Line", "name": "x" * 129}},
    {"panel_id": "results", "results_tab": "loadflow", "highlight_branch": {**HOT, "extra": 1}},
    {"panel_id": "results", "results_tab": "loadflow", "highlight_branch": "L_hot"},
])
def test_malformed_loadflow_arguments_are_invalid_navigation(result_project, kwargs):
    with pytest.raises(HTTPException) as exc:
        chat_tools.ui_open_panel(**kwargs)
    assert _kind(exc) == "invalid_navigation"


def test_unknown_branch_refuses_with_no_event(result_project):
    with pytest.raises(HTTPException) as exc:
        chat_tools.ui_open_panel("results", results_tab="loadflow",
                                 highlight_branch={"component": "Line", "name": "nope"})
    assert exc.value.status_code == 404 and _kind(exc) == "unknown_branch"


@pytest.mark.parametrize("state,source,unavailable", [
    ("unsolved", "lopf", "not_solved"), ("failed", "lopf", "solve_failed"),
    ("infeasible", "lopf", "solve_infeasible"), ("stale", "lopf", "stale_results"),
    ("ok", "ac_pf", "ac_pf_not_available"), ("no_network", "lopf", "no_network"),
])
def test_unavailable_view_refuses_only_when_loadflow_args_are_given(result_project, state, source, unavailable):
    ctx = result_project[0]
    if state == "unsolved":
        ctx.network._objective = None
    elif state == "failed":
        ctx.solver_state.update(status="failed", condition="error")
    elif state == "infeasible":
        ctx.solver_state.update(status="completed", condition="infeasible")
    elif state == "stale":
        ctx.network.add("Line", "L_new", bus0="A", bus1="C", s_nom=10.0, x=0.1)
    elif state == "no_network":
        ctx.network = None
    with pytest.raises(HTTPException) as exc:
        chat_tools.ui_open_panel("results", results_tab="loadflow", loadflow_source=source)
    assert _kind(exc) == "loadflow_view_unavailable"
    assert exc.value.detail["unavailable_kind"] == unavailable
    # A4: the arg-less call is unchanged — an event, and no ACK on a non-ok view.
    event = chat_tools.ui_open_panel("results", results_tab="loadflow")
    assert event == {"_ui_event": True, "kind": "navigate", "panel_id": "results",
                     "results_tab": "loadflow"}
    if ctx.network is not None and source == "lopf":
        assert navigation.issue(_turn(ctx), _navigate()) is None


def test_argless_loadflow_issue_is_unchanged(result_project):
    assert chat_tools.ui_open_panel("results", results_tab="loadflow") == {
        "_ui_event": True, "kind": "navigate", "panel_id": "results", "results_tab": "loadflow"}
    action = navigation.issue(_turn(result_project[0]), _navigate())
    assert action["target"] == {"panel_id": "results", "results_tab": "loadflow"}
    assert action["source_binding"] == get_loadflow_view()["binding"]
    assert "loadflow" not in action


# ── Branch facts and binding ─────────────────────────────────────────────────

def test_event_carries_source_window_and_ranked_highlight(result_project):
    event = chat_tools.ui_open_panel("results", results_tab="loadflow", loadflow_source="lopf",
                                     window_from=1, window_to=2, highlight_branch=HOT)
    assert {k: event[k] for k in ("loadflow_source", "window_from", "window_to", "highlight")} == {
        "loadflow_source": "lopf", "window_from": 1, "window_to": 2, "highlight": HOT}
    facts = event["highlight_facts"]
    assert facts["ranked"] is True and facts["shown"] is True and facts["rank"] >= 1
    assert facts["basis"] == "active_power_proxy"


def test_skipped_branch_navigates_but_is_not_ranked(result_project):
    event = chat_tools.ui_open_panel("results", results_tab="loadflow",
                                     highlight_branch={"component": "Line", "name": "L_zero"})
    facts = event["highlight_facts"]
    assert facts["ranked"] is False and facts["shown"] is False
    assert facts["reason"] == "rating_missing_or_zero"
    assert "neither loaded nor unloaded" in facts["note"]


def test_issued_binding_is_the_requested_view_verbatim(result_project):
    action = navigation.issue(_turn(result_project[0]),
                              _navigate(loadflow_source="lopf", window_from=1, window_to=2, highlight=HOT))
    assert action["version"] == 2
    assert action["target"] == {"panel_id": "results", "results_tab": "loadflow", "highlight": HOT}
    assert action["source_binding"] == get_loadflow_view("lopf", 1, 2)["binding"]
    assert set(action["source_binding"]) == {
        "version", "project_id", "project_name", "scope", "source", "input_sha256",
        "data_sha256", "window_from", "window_to"}
    assert action["loadflow"]["highlight"]["ranked"] is True


def test_issue_refuses_typed_when_the_view_is_gone(result_project):
    ctx = result_project[0]
    ctx.solver_state["status"] = "failed"
    with pytest.raises(HTTPException) as exc:
        navigation.issue(_turn(ctx), _navigate(highlight=HOT))
    assert _kind(exc) == "loadflow_view_unavailable"


# ── ACK ──────────────────────────────────────────────────────────────────────

def _issued_window(ctx, generation=3):
    return navigation.issue(_turn(ctx, generation),
                            _navigate(loadflow_source="lopf", window_from=1, window_to=2, highlight=HOT))


def test_window_and_highlight_ack_completes(client, result_project):
    body = _ack_body(_issued_window(result_project[0]))
    response = client.post("/api/chat/ui-ack", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["verification"] == "browser_displayed_data"


@pytest.mark.parametrize("change", ["omit", "other_line", "other_class"])
def test_ack_with_changed_highlight_mismatches(client, result_project, change):
    body = _ack_body(_issued_window(result_project[0]))
    wrong = copy.deepcopy(body)
    if change == "omit":
        wrong["target"].pop("highlight")
    elif change == "other_line":
        wrong["target"]["highlight"]["name"] = "L_p1"
    else:
        wrong["target"]["highlight"]["component"] = "Transformer"
    response = client.post("/api/chat/ui-ack", json=wrong)
    assert response.status_code == 409
    assert response.json()["detail"]["error_kind"] == "ui_ack_mismatch"


def test_window_ack_fails_after_a_resolve_changes_that_window(client, result_project):
    ctx = result_project[0]
    body = _ack_body(_issued_window(ctx))
    ctx.network.lines_t.p0.loc[SNAPSHOTS[2], "L_hot"] += 1.0
    assert client.post("/api/chat/ui-ack", json=body).status_code == 409
    assert client.get("/api/chat/navigation/" + body["action_id"]).json()["outcome"] == "submitted"


def test_window_ack_fails_after_a_project_switch(client, result_project):
    ctx = result_project[0]
    body = _ack_body(_issued_window(ctx))
    original = ctx.project_uuid
    ctx.project_uuid = "00000000-0000-4000-8000-000000000001"
    try:
        assert client.post("/api/chat/ui-ack", json=body).status_code == 409
    finally:
        ctx.project_uuid = original
    assert client.get("/api/chat/navigation/" + body["action_id"]).json()["outcome"] == "submitted"


def test_window_ack_fails_after_a_newer_turn(client, result_project):
    ctx = result_project[0]
    turn = _turn(ctx, 3)
    action = navigation.issue(turn, _navigate(window_from=1, window_to=2, highlight=HOT))
    navigation.begin_turn(turn, 4)  # a newer generation clears the cursor
    assert client.post("/api/chat/ui-ack", json=_ack_body(action)).status_code == 409


# ── Typed-chat journey on every harness provider ─────────────────────────────

JOURNEY = [
    ("get_loadflow_summary", {}),
    ("get_component", {"component_class": "Line", "name": "L_hot"}),
    ("get_loadflow_summary", {"window_from": 1, "window_to": 2}),
    ("ui_open_panel", {"panel_id": "results", "results_tab": "loadflow", "loadflow_source": "lopf",
                       "window_from": 1, "window_to": 2, "highlight_branch": HOT}),
]


def _journey(kind, model, base_url, auth):
    anthropic = kind == "anthropic"
    http_module = httpx
    if anthropic:
        import anthropic as sdk
        module = next(c.__module__.split(".")[0] for c in sdk.DefaultHttpxClient.__mro__
                      if c.__module__.split(".")[0] in ("httpx", "httpx2"))
        http_module = import_module(module)
    wire = "anthropic" if anthropic else "openai"
    profile = llm_config.LLMProfile(
        id="loadflow-journey", label="Load flow journey", preset="custom",
        wire=wire, base_url=base_url, model=model, tools=True, vision=False,
        auth=auth, fallback_model=None, max_output_tokens=256,
    )
    llm_config.save_profiles([profile], profile.id)
    session = chat_service.ChatSession(model=model)
    session.profile_id, session.bound_wire = profile.id, wire
    results, requests = [], []

    def respond(request):
        body = json.loads(request.content)
        index = len(requests)
        requests.append(body)
        if index:
            call_id, content, error = _last_result(body, anthropic)
            assert call_id == f"lf_{index-1}" and not error, content
            results.append(content)
        name, values = JOURNEY[index] if index < len(JOURNEY) else (None, {})
        if name:
            declaration = next(t for t in body["tools"]
                               if (t["name"] if anthropic else t["function"]["name"]) == name)
            schema = declaration["input_schema"] if anthropic else declaration["function"]["parameters"]
            Draft202012Validator(schema).validate(values)
        stream = (_anthropic_stream(model, f"lf_{index}", name, values) if anthropic
                  else _compatible_stream(f"lf_{index}", name, values))
        return http_module.Response(200, headers={"content-type": "text/event-stream"}, content=stream)

    with http_module.Client(transport=http_module.MockTransport(respond)) as http:
        provider = (AnthropicProvider(sdk.Anthropic(
            api_key="synthetic-key", base_url=base_url, http_client=http, max_retries=0,
        )) if anthropic else OpenAICompatProvider(
            base_url, api_key="synthetic-key" if auth == "bearer" else None,
            http_client=http, token_param="max_tokens",
        ))
        frames = list(chat_service.run_turn(
            session, "Summarise the load flow, show L_hot's parameters, narrow to "
            "snapshots 1-2 and open that view on L_hot.", provider=provider))
    return frames, results


def _decode(content):
    text = content if isinstance(content, str) else "".join(
        c.get("text", "") for c in content if isinstance(c, dict))
    text = text.strip()
    if text.startswith("<untrusted_data>"):
        text = text[len("<untrusted_data>"):text.rindex("</untrusted_data>")]
    return json.loads(text)


@pytest.mark.parametrize("kind,model,base_url,auth", PROVIDERS, ids=[p[0] for p in PROVIDERS])
def test_typed_chat_loadflow_journey_matches_software_values(result_project, kind, model, base_url, auth):
    frames, results = _journey(kind, model, base_url, auth)
    assert frames[-1][0] == "turn_done"
    assert not [data for event, data in frames if event in ("error", "tool_error")]
    assert len(results) == len(JOURNEY)
    summary, component, window, opened = (_decode(r) for r in results)

    oracle = chat_tools.get_loadflow_summary()
    assert summary["top"] == oracle["top"]
    assert summary["claims"]["full_ac_thermal_compliance"] is False
    assert summary["ui_reference"] == {"results_tab": "loadflow", "source": "lopf",
                                       "window_from": 0, "window_to": len(SNAPSHOTS) - 1}
    assert component.get("name", "L_hot") == "L_hot"
    windowed = chat_tools.get_loadflow_summary(window_from=1, window_to=2)
    assert window["top"] == windowed["top"]
    assert window["ui_reference"]["window_from"] == 1 and window["ui_reference"]["window_to"] == 2
    assert all(r["basis"] == "active_power_proxy" for r in window["top"])

    assert opened["ui_navigated"] is True
    assert opened["highlight"]["ranked"] is True and opened["highlight"]["name"] == "L_hot"
    [event] = [data for name, data in frames if name == "ui_event"]
    assert {k: event[k] for k in ("panel_id", "results_tab", "loadflow_source",
                                  "window_from", "window_to", "highlight")} == {
        "panel_id": "results", "results_tab": "loadflow", "loadflow_source": "lopf",
        "window_from": 1, "window_to": 2, "highlight": HOT}
    assert "highlight_facts" not in event


def test_new_refusal_kinds_are_classified_in_the_manifest():
    from pathlib import Path

    kinds = json.loads((Path(__file__).resolve().parents[2] / "tool-error-kinds.json").read_text())["kinds"]
    for kind in ("loadflow_view_unavailable", "unknown_branch", "invalid_navigation", "ui_ack_mismatch"):
        assert kinds[kind]["surface"] in ("banner", "inline")


def test_window_outside_the_axis_at_issue_time_is_invalid_navigation(result_project):
    # The axis can change between the tool's check and the ACK record.
    with pytest.raises(HTTPException) as exc:
        navigation.issue(_turn(result_project[0]), _navigate(window_from=0, window_to=len(SNAPSHOTS)))
    assert _kind(exc) == "invalid_navigation"
