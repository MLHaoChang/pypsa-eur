"""LC-10 / LC-T21: "this", "the" and "latest" resolve by one ladder, in code.

Every case runs through the ordinary turn loop with the UI context the
browser sends; the hints are re-read against the active network and the
project's run records, never trusted as given.
"""
import pytest

from harness.providers.fake import FakeProvider
from services import chat_tools, run_evidence
from services.pypsa_service import PyPSAService
from tests.test_openai_comprehensive_live import setup_profile, turn
from tests.test_chat_stream_session_ownership import fake_provider  # noqa: F401
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401

TOOLS = {"resolve_reference", "get_component", "discuss_project_run", "bulk_update_components"}


@pytest.fixture
def ladder(baseline, monkeypatch):
    n = PyPSAService.get_network()
    n.add("Bus", "B2", v_nom=110)
    n.add("Line", "L2", bus0="B1", bus1="B2", x=0.1, r=0.01, s_nom=100)
    n.add("Line", "L3", bus0="B1", bus1="B2", x=0.1, r=0.01, s_nom=80)
    n.add("StorageUnit", "S1", bus="B1", p_nom=10, max_hours=4)
    session = setup_profile(monkeypatch, TOOLS)
    chat_tools.set_chat_session(session)
    return session


def call(session, name, args, ui_context=None):
    fake = FakeProvider([
        {"blocks": [{"type": "tool_use", "id": "ref", "name": name, "input": args}]},
        {"blocks": [{"type": "text", "text": "Done."}]},
    ])
    frames = turn(session, "Resolve it.", fake, ui_context=ui_context)
    errors = [d for e, d in frames if e == "tool_error"]
    results = [d["result"] for e, d in frames if e == "tool_result"]
    return (results[0] if results else None), errors


def resolve(session, ui_context=None, **args):
    result, errors = call(session, "resolve_reference", args, ui_context)
    assert not errors, errors
    assert result["version"] == 1 and result["says"]
    return result


def selected(klass, name):
    return {"selected_component": {"class": klass, "name": name}}


def test_explicit_name_wins_over_the_selection(ladder):
    out = resolve(ladder, selected("Line", "L2"), target="component", component_class="Line", name="L3")
    assert out["status"] == "resolved" and out["via"] == "explicit"
    assert out["target"] == {"component_class": "Line", "name": "L3"}


def test_an_unknown_explicit_name_never_falls_back_to_the_selection(ladder):
    out = resolve(ladder, selected("Line", "L2"), target="component", component_class="Line", name="L9")
    assert out["status"] == "not_found" and "target" not in out


def test_oj05_selection_beats_what_was_discussed_earlier(ladder):
    _, errors = call(ladder, "get_component", {"component_class": "Line", "name": "L2"})
    assert not errors
    out = resolve(ladder, selected("Line", "L3"), target="component", component_class="Line")
    assert out["status"] == "resolved" and out["via"] == "selected"
    assert out["target"]["name"] == "L3"
    assert "L3" in out["says"] and "selected" in out["says"]


def test_a_selection_of_another_class_asks_instead_of_using_it(ladder):
    out = resolve(ladder, selected("Generator", "G1"), target="component", component_class="Line")
    assert out["status"] == "ambiguous"
    assert {c["name"] for c in out["candidates"]} == {"L2", "L3"}
    assert any("Generator" in note and "G1" in note for note in out["ignored"])


def test_a_stale_selection_is_ignored(ladder):
    out = resolve(ladder, selected("Line", "Ghost"), target="component", component_class="Line")
    assert out["status"] == "ambiguous"
    assert any("Ghost" in note for note in out["ignored"])


def test_the_last_entity_a_tool_read_resolves_without_a_selection(ladder):
    _, errors = call(ladder, "get_component", {"component_class": "Line", "name": "L2"})
    assert not errors
    out = resolve(ladder, target="component", component_class="Line")
    assert out["status"] == "resolved" and out["via"] == "discussed"
    assert out["target"]["name"] == "L2"


def test_a_selection_from_an_earlier_turn_is_not_reused(ladder):
    resolve(ladder, selected("Line", "L3"), target="component", component_class="Line")
    out = resolve(ladder, target="component", component_class="Line")
    # The earlier resolution itself is not a tool read of L3, so nothing is discussed.
    assert out["status"] == "ambiguous"


def test_the_only_storage_across_both_storage_classes_is_used(ladder):
    out = resolve(ladder, target="component", component_class="storage")
    assert out["status"] == "resolved" and out["via"] == "only"
    assert out["target"] == {"component_class": "StorageUnit", "name": "S1"}


def test_oj06_two_candidates_ask_with_real_candidates(ladder):
    out = resolve(ladder, target="component", component_class="Line")
    assert out["status"] == "ambiguous" and out["total_candidates"] == 2
    row = next(c for c in out["candidates"] if c["name"] == "L2")
    assert row["component_class"] == "Line" and row["bus0"] == "B1" and row["bus1"] == "B2"
    assert "ask_user" in out["next"] and "something else" in out["next"].lower()


def _publish(root, project_id, run_id, status, started):
    kwargs = {"metrics": {"objective": {"value": 1.0, "unit": "EUR"}}} if status == "completed" else {}
    if status == "failed":
        kwargs["error"] = {"error_kind": "solver_infeasible", "message": "Infeasible."}
    run_evidence.publish_run(root, project_id, run_id=run_id, kind="simulation", status=status,
                             started_at=started, finished_at=started, mode="lopf", fingerprint="fp", **kwargs)


@pytest.fixture
def runs(ladder, baseline):
    _ctx, project_id, root = baseline
    _publish(root, project_id, "run-old", "completed", "2026-10-01T10:00:00+00:00")
    _publish(root, project_id, "run-new", "completed", "2026-10-02T10:00:00+00:00")
    _publish(root, project_id, "run-failed", "failed", "2026-10-03T10:00:00+00:00")
    run_evidence.start_run(root, project_id, run_id="run-live", kind="simulation",
                           started_at="2026-10-04T10:00:00+00:00", mode="lopf")
    return ladder


def test_latest_skips_failed_and_running_runs_and_names_them(runs):
    out = resolve(runs, target="run", latest=True)
    assert out["status"] == "resolved" and out["via"] == "latest_completed"
    assert out["target"]["run_id"] == "run-new"
    assert {(s["run_id"], s["status"]) for s in out["skipped"]} == {("run-failed", "failed"), ("run-live", "running")}
    assert "run-failed" in out["says"] and "run-live" in out["says"]


def test_a_discussed_run_wins_for_the_run_but_not_for_the_latest(runs):
    _, errors = call(runs, "discuss_project_run", {"run_id": "run-old", "open_ui": False})
    assert not errors
    assert resolve(runs, target="run")["target"]["run_id"] == "run-old"
    assert resolve(runs, target="run", latest=True)["target"]["run_id"] == "run-new"


def test_the_displayed_historical_run_is_on_screen(runs, baseline):
    shown = {"panel": "results", "results_tab": "dispatch", "results_source": {
        "scope": "historical", "project_id": baseline[1], "run_id": "run-old", "source": "dispatch"}}
    out = resolve(runs, shown, target="run")
    assert out["via"] == "displayed" and out["target"]["run_id"] == "run-old"


def test_a_displayed_run_of_another_project_is_ignored(runs):
    shown = {"results_source": {"scope": "historical", "project_id": "00000000-0000-0000-0000-000000000000",
                                "run_id": "run-old"}}
    out = resolve(runs, shown, target="run")
    assert out["status"] == "ambiguous" and out["ignored"]
    assert [c["run_id"] for c in out["candidates"]] == ["run-new", "run-old"]


def test_this_run_with_several_completed_runs_asks(runs):
    out = resolve(runs, target="run")
    assert out["status"] == "ambiguous" and out["total_candidates"] == 2
    assert {s["run_id"] for s in out["skipped"]} == {"run-failed", "run-live"}


def test_this_run_with_one_completed_run_uses_it(ladder, baseline):
    _ctx, project_id, root = baseline
    _publish(root, project_id, "run-only", "completed", "2026-10-01T10:00:00+00:00")
    out = resolve(ladder, target="run")
    assert out["via"] == "only" and out["target"]["run_id"] == "run-only"


def test_the_current_results_on_screen_are_these_results(runs):
    shown = {"panel": "results", "results_source": {"scope": "current", "source": "loadflow"}}
    out = resolve(runs, shown, target="run")
    assert out["via"] == "displayed" and out["target"] == {"scope": "current", "source": "loadflow"}


def test_no_completed_run_is_not_found_and_still_names_the_others(ladder, baseline):
    _ctx, project_id, root = baseline
    _publish(root, project_id, "run-failed", "failed", "2026-10-03T10:00:00+00:00")
    out = resolve(ladder, target="run", latest=True)
    assert out["status"] == "not_found"
    assert [s["run_id"] for s in out["skipped"]] == ["run-failed"]


@pytest.mark.parametrize("args", [
    {"target": "component"},
    {"target": "component", "component_class": "Nope"},
    {"target": "planet"},
    {"target": "component", "component_class": "storage", "name": ""},
    {"target": "run", "run_id": "x" * 65},
])
def test_invalid_requests_refuse(ladder, args):
    result, errors = call(ladder, "resolve_reference", args)
    assert result is None and errors


def test_the_ladder_rule_rides_the_tools_on_prompt_only():
    from harness import compose
    from harness.session import ChatSession

    session = ChatSession()
    assert compose._REFERENCE_LADDER in compose._build_system_prompt(session)
    tools_off = compose._build_system_prompt(session, include_tools=False)
    assert "resolve_reference" not in tools_off


def test_the_browser_selection_reaches_the_ladder_through_the_stream(ladder, client, fake_provider):
    from services import chat_service
    from tests.assurance_acceptance.support import parse_sse, text_turn, tool_turn

    fake_provider([tool_turn("resolve_reference", {"target": "component", "component_class": "Line"}),
                   text_turn("Line L3, selected on screen.")])
    session_id = "ladder-http"
    try:
        response = client.post("/api/chat/stream", json={
            "session_id": session_id, "message": "Show me this line.",
            "ui_context": {"selected_component": {"class": "Line", "name": "L3"}}})
        assert response.status_code == 200, response.text
        frames = parse_sse(response.text)
        result = next(d["result"] for e, d in frames if e == "tool_result")
        assert result["via"] == "selected" and result["target"]["name"] == "L3"
    finally:
        chat_service.drop_session(session_id)


def test_a_name_without_a_class_searches_every_class(ladder):
    out = resolve(ladder, target="component", name="S1")
    assert out["target"] == {"component_class": "StorageUnit", "name": "S1"}


def test_a_single_name_bulk_update_counts_as_discussed(ladder):
    _, errors = call(ladder, "bulk_update_components",
                     {"component_class": "Line", "names": ["L3"], "updates": {"s_nom": 90}})
    assert not errors
    out = resolve(ladder, target="component", component_class="Line")
    assert out["via"] == "discussed" and out["target"]["name"] == "L3"


def test_a_component_discussed_in_another_project_is_not_carried_over(ladder):
    ladder.touched_components.append(("another-project", "Line", "L2"))
    out = resolve(ladder, target="component", component_class="Line")
    assert out["status"] == "ambiguous"


def test_hint_text_is_cleaned_and_capped_before_it_is_echoed(ladder):
    noisy = "Ghost\n" + "x" * 500
    out = resolve(ladder, selected("Generator", noisy), target="component", component_class="Line")
    note = out["ignored"][0]
    assert "\n" not in note and len(note) < 200


@pytest.mark.parametrize("index,status", [(1, "resolved"), (7, "not_found"), (None, "not_found")])
def test_this_hour_is_the_snapshot_on_screen(ladder, index, status):
    hints = {} if index is None else {"snapshot_index": index}
    out = resolve(ladder, hints, target="snapshot")
    assert out["status"] == status
    if status == "resolved":
        assert out["target"]["snapshot_index"] == 1 and "2026-01-01 01:00" in out["target"]["snapshot"]
