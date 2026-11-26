"""Assistant-assurance 06, A5.2 (S1): the displayed-data adapter seam, and the
closed-tab and newer-navigation cases through the chat stream.

The stream cases post to /api/chat/stream with the fake provider (ordinary
turn, `ui_ack` declared). The browser's half is played from the issued ACK the
server hands to the ui_event frame: it either never ACKs (closed tab, or a
newer navigation made the view stale so the frontend's `isCurrent()` is false)
or ACKs late, after a newer navigation superseded it.
"""

# ruff: noqa: F811 -- imported pytest fixtures
import copy
import threading
import uuid

import pytest

from harness import displayed_adapters, navigation
from harness.navigation import OrdinaryTurn
from harness.session import ChatSession
from services import assurance_records, chat_service, result_views
from services.loadflow_summary import get_loadflow_view
from tests.assurance_acceptance.support import (
    OMIT,
    Harness,
    parse_sse,
    text_turn,
    tool_turn,
)
from tests.test_results_source_navigation import result_project  # noqa: F401
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401

LOADFLOW = {"panel_id": "results", "results_tab": "loadflow"}
SUMMARY = {"panel_id": "results", "results_tab": "summary"}


# ── Registry guard: displayed_data <=> an adapter exists ────────────────────


def test_registry_matches_the_adapter_table():
    displayed_adapters.check_registry(result_views.RESULT_VIEWS)
    assert {
        v.tab for v in result_views.RESULT_VIEWS if v.verification == "displayed_data"
    } == set(displayed_adapters.ADAPTERS)


def test_a_view_claiming_displayed_data_without_an_adapter_fails():
    import dataclasses

    views = [
        dataclasses.replace(v, verification="displayed_data")
        if v.tab == "summary"
        else v
        for v in result_views.RESULT_VIEWS
    ]
    with pytest.raises(AssertionError, match="summary"):
        displayed_adapters.check_registry(views)


def test_an_adapter_without_a_displayed_data_view_fails():
    adapters = {
        **displayed_adapters.ADAPTERS,
        "prices": displayed_adapters.ADAPTERS["loadflow"],
    }
    with pytest.raises(AssertionError, match="prices"):
        displayed_adapters.check_registry(result_views.RESULT_VIEWS, adapters)


# ── Load flow through the table equals the pre-table behaviour ──────────────


def test_loadflow_issue_ack_and_read_are_unchanged_through_the_table(
    client, result_project
):
    ctx = result_project[0]
    owner = assurance_records.actor_id()
    turn = OrdinaryTurn(ChatSession(owner_user_id=owner), ctx, 3)
    navigation.begin_turn(turn, 3)
    action = navigation.issue(turn, {"kind": "navigate", **LOADFLOW})
    view = get_loadflow_view()
    # Issue: v2, the tab's default view's binding verbatim, the same fields.
    assert set(action) == {
        "version",
        "action_id",
        "turn_id",
        "binding_generation",
        "target",
        "project_id",
        "project_name",
        "input_sha256",
        "source_binding",
    }
    assert action["version"] == 2 and action["target"] == LOADFLOW
    assert action["source_binding"] == view["binding"]
    # ACK: the same re-read decides.
    body = {
        k: action[k]
        for k in (
            "version",
            "action_id",
            "turn_id",
            "binding_generation",
            "target",
            "source_binding",
        )
    }
    response = client.post("/api/chat/ui-ack", json=body)
    assert response.json() == {
        "version": 2,
        "action_id": action["action_id"],
        "outcome": "completed",
        "verification": "browser_displayed_data",
    }
    # note_read: the summary's ui_reference names source and window.
    turn.note_read(
        "get_loadflow_summary",
        {
            "status": "ok",
            "ui_reference": {
                "results_tab": "loadflow",
                "source": "lopf",
                "window_from": 0,
                "window_to": 1,
            },
        },
    )
    turn.note_read("get_loadflow_summary", {"status": "unavailable"})
    turn.note_read("get_results", {"status": "ok"})
    assert turn.reads == {
        "loadflow": {"source": "lopf", "window_from": 0, "window_to": 1}
    }
    shown = {k: view["binding"][k] for k in ("source", "window_from", "window_to")}
    turn.reads["loadflow"] = dict(shown)
    assert navigation.display(turn, action, wait=0) == {
        "verification": "browser_displayed_data",
        "say": "showing",
    }


def test_a_source_binding_for_a_tab_without_an_adapter_is_invalid(
    client, result_project
):
    body = {
        "version": 2,
        "action_id": "a",
        "turn_id": "t",
        "binding_generation": 1,
        "target": SUMMARY,
        "source_binding": copy.deepcopy(get_loadflow_view()["binding"]),
    }
    response = client.post("/api/chat/ui-ack", json=body)
    assert response.status_code == 422
    assert response.json()["detail"]["error_kind"] == "invalid_assurance_request"


# ── Stream cases ────────────────────────────────────────────────────────────


@pytest.fixture
def stream(client, monkeypatch):
    harness = Harness(client, monkeypatch)
    issued = []
    original = navigation.issue

    def recording_issue(turn, payload):
        ack = original(turn, payload)
        if ack is not None:
            issued.append(ack)
            on_issue = getattr(recording_issue, "browser", None)
            if on_issue:
                on_issue(ack, issued)
        return ack

    monkeypatch.setattr(navigation, "issue", recording_issue)

    def run(steps, browser=None):
        recording_issue.browser = browser
        response = harness.request(
            [
                *[
                    tool_turn("ui_open_panel", s, call_id=f"nav-{i}")
                    for i, s in enumerate(steps)
                ],
                text_turn("Done."),
            ],
            assurance=OMIT,
            ui_ack=True,
            message="Open the load flow results.",
        )
        assert response.status_code == 200, response.text[:400]
        return parse_sse(response.text), issued

    yield run
    for sid in harness.session_ids:
        chat_service.drop_session(sid)


def _body(ack):
    body = {
        k: ack[k]
        for k in ("version", "action_id", "turn_id", "binding_generation", "target")
    }
    if ack["version"] == 2:
        body["source_binding"] = ack["source_binding"]
    return body


def _results(frames):
    return [
        d
        for e, d in frames
        if e == "tool_result" and d.get("tool_name") == "ui_open_panel"
    ]


def _display(result):
    return (
        (result.get("result") or {}).get("display")
        if isinstance(result.get("result"), dict)
        else None
    )


def test_closed_tab_never_acks_so_the_tool_says_opened_and_the_record_stays_submitted(
    client, result_project, stream, monkeypatch
):
    monkeypatch.setattr(navigation, "ACK_WAIT_SECONDS", 0.3)
    frames, issued = stream([LOADFLOW])
    events = [d for e, d in frames if e == "ui_event"]
    assert (
        len(events) == 1
        and events[0]["acknowledgement"]["action_id"] == issued[0]["action_id"]
    )
    displays = [r for r in (_display(r) for r in _results(frames)) if r]
    assert displays == [{"verification": "submitted", "say": "opened"}], _results(
        frames
    )
    record = client.get(f"/api/chat/navigation/{issued[0]['action_id']}").json()
    assert record["outcome"] == "submitted"
    # The tab is reopened after the record's 30 s expiry: its late ACK cannot complete.
    saved = assurance_records.load(
        assurance_records.actor_id(), issued[0]["action_id"], "ui_action"
    )
    assurance_records.save({**saved, "expires_at": 0}, issued[0]["action_id"])
    late = client.post("/api/chat/ui-ack", json=_body(issued[0]))
    assert late.status_code == 409
    assert late.json()["detail"]["error_kind"] == "ui_ack_mismatch"


def test_newer_navigation_supersedes_so_a_late_ack_is_refused_and_stays_submitted(
    client, result_project, stream, monkeypatch
):
    """The browser holds the first view's ACK (the user moved on, so the
    frontend's isCurrent() is false) and posts it only after the newer
    navigation is issued: 409, the first stays submitted and the tool says
    opened; the newer one completes on its own ACK."""
    monkeypatch.setattr(navigation, "ACK_WAIT_SECONDS", 0.3)
    late = []

    def browser(ack, issued):
        if len(issued) == 2:
            late.append(client.post("/api/chat/ui-ack", json=_body(issued[0])))
            # The newer view is current and mounted: its own ACK, off-thread like a browser.
            done = threading.Event()

            def post():
                late.append(client.post("/api/chat/ui-ack", json=_body(ack)))
                done.set()

            threading.Thread(target=post).start()
            done.wait(5)

    frames, issued = stream([LOADFLOW, SUMMARY], browser)
    assert [a["target"] for a in issued] == [LOADFLOW, SUMMARY]
    assert late[0].status_code == 409
    assert late[0].json()["detail"]["error_kind"] == "ui_ack_mismatch"
    assert late[1].status_code == 200
    assert late[1].json()["verification"] == "browser_mounted_view"
    first = client.get(f"/api/chat/navigation/{issued[0]['action_id']}").json()
    assert first["outcome"] == "submitted"
    displays = [_display(r) for r in _results(frames)]
    assert displays[0] == {"verification": "submitted", "say": "opened"}, displays
    assert displays[1]["verification"] == "browser_mounted_view"
    assert displays[1]["say"] == "opened"
    assert uuid.UUID(issued[1]["action_id"])


# ── Binding and target validation (each refused before any record read) ────

_LF_BINDING = {
    "version": 1,
    "project_id": "p",
    "project_name": "n",
    "scope": "current",
    "source": "lopf",
    "input_sha256": "i",
    "data_sha256": "d",
    "window_from": 0,
    "window_to": 1,
}


@pytest.mark.parametrize(
    "change",
    [
        {"extra": "x"},
        {"version": 2},
        {"version": True},
        {"window_from": -1},
        {"window_to": True},
        {"window_from": 1.0},
        {"data_sha256": 5},
        {"project_id": None},
    ],
)
def test_valid_binding_refuses_each_malformed_field(change):
    adapter = displayed_adapters.ADAPTERS["loadflow"]
    assert displayed_adapters.valid_binding(adapter, _LF_BINDING)
    assert not displayed_adapters.valid_binding(adapter, {**_LF_BINDING, **change})


def test_valid_binding_refuses_a_missing_key_and_a_non_dict():
    adapter = displayed_adapters.ADAPTERS["loadflow"]
    missing = {k: v for k, v in _LF_BINDING.items() if k != "source"}
    assert not displayed_adapters.valid_binding(adapter, missing)
    assert not displayed_adapters.valid_binding(adapter, [_LF_BINDING])


@pytest.mark.parametrize(
    "target,found",
    [
        (LOADFLOW, True),
        ({**LOADFLOW, "highlight": {"component": "Line", "name": "L1"}}, True),
        ({"panel_id": "map", "results_tab": "loadflow"}, False),
        ({**LOADFLOW, "extra": 1}, False),
        ({"panel_id": "results", "results_tab": ["loadflow"]}, False),
        ({"panel_id": "results"}, False),
        (SUMMARY, False),
        ("results/loadflow", False),
    ],
)
def test_for_target_names_an_adapter_only_for_an_exact_results_tab(target, found):
    assert (displayed_adapters.for_target(target) is not None) is found


@pytest.mark.parametrize(
    "target",
    [
        {"panel_id": "results", "results_tab": ["loadflow"]},
        {"panel_id": "results", "results_tab": {"tab": "loadflow"}},
    ],
)
def test_an_unhashable_results_tab_on_a_v2_ack_is_invalid_not_a_server_error(
    client, target
):
    body = {
        "version": 2,
        "action_id": "a",
        "turn_id": "t",
        "binding_generation": 1,
        "target": target,
        "source_binding": dict(_LF_BINDING),
    }
    response = client.post("/api/chat/ui-ack", json=body)
    assert response.status_code == 422
    assert response.json()["detail"]["error_kind"] == "invalid_assurance_request"


@pytest.mark.parametrize(
    "change", [{"version": 2}, {"window_from": -1}, {"extra": "x"}]
)
def test_a_malformed_binding_on_the_ack_route_is_invalid(client, change):
    body = {
        "version": 2,
        "action_id": "a",
        "turn_id": "t",
        "binding_generation": 1,
        "target": LOADFLOW,
        "source_binding": {**_LF_BINDING, **change},
    }
    assert client.post("/api/chat/ui-ack", json=body).status_code == 422


def test_loadflow_read_ref_ignores_non_reads():
    ref = displayed_adapters.ADAPTERS["loadflow"].read_ref
    assert ref(["not", "a", "dict"]) is None
    assert ref({"status": "unavailable"}) is None
    assert ref({"status": "ok"}) == {
        "source": None,
        "window_from": None,
        "window_to": None,
    }


def test_display_of_a_mounted_view_never_says_showing(client, result_project):
    ctx = result_project[0]
    turn = OrdinaryTurn(ChatSession(owner_user_id=assurance_records.actor_id()), ctx, 3)
    navigation.begin_turn(turn, 3)
    action = navigation.issue(turn, {"kind": "navigate", **SUMMARY})
    assert action["version"] == 1
    body = {
        k: action[k]
        for k in ("version", "action_id", "turn_id", "binding_generation", "target")
    }
    assert (
        client.post("/api/chat/ui-ack", json=body).json()["verification"]
        == "browser_mounted_view"
    )
    turn.reads["summary"] = {}
    assert navigation.display(turn, action, wait=0) == {
        "verification": "browser_mounted_view",
        "say": "opened",
    }
