# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
# ruff: noqa: F811 -- imported pytest fixture
"""SC-07 S5b R6 (GK B V1–V3): a browser ACK for a chat navigate into Site 3D."""

import pytest

from harness import assurance, navigation
from harness.session import ChatSession
from services import assurance_records
from tests.test_loadflow_summary import build_network, _record_fixture_inputs
from tests.test_workflow_tools import baseline  # noqa: F401

SITE = {"kind": "navigate", "panel_id": "map", "canvas_view": "site"}
ISSUED_KEYS = {"version", "action_id", "turn_id", "binding_generation", "target",
               "project_id", "project_name", "input_sha256"}


@pytest.fixture
def loaded(baseline):
    ctx, _project_id, _root = baseline
    ctx.network = build_network()
    _record_fixture_inputs(ctx.network)
    return ctx


def _turn(ctx):
    turn = assurance.Turn(ChatSession(owner_user_id=assurance_records.actor_id()), ctx)
    navigation.begin_turn(turn, 3)
    return turn


def _body(action):
    return {k: action[k] for k in ("version", "action_id", "turn_id", "binding_generation", "target")}


def test_site3d_navigate_issues_a_mounted_view_ack(client, loaded):
    action = navigation.issue(_turn(loaded), {**SITE, "tool_use_id": "t1"})
    assert set(action) == ISSUED_KEYS
    assert action["version"] == 1
    assert action["target"] == {"panel_id": "map", "canvas_view": "site"}
    ack = client.post("/api/chat/ui-ack", json=_body(action))
    assert ack.status_code == 200, ack.text
    assert ack.json()["outcome"] == "completed"
    assert ack.json()["verification"] == "browser_mounted_view"


@pytest.mark.parametrize("payload", [
    {"kind": "navigate", "panel_id": "results", "canvas_view": "site"},   # another panel
    {"kind": "navigate", "panel_id": "topology", "canvas_view": "blank"},  # another panel
    {"kind": "navigate", "panel_id": "MapCanvas", "canvas_view": "site"},  # an alias, not "map"
    {"kind": "navigate", "panel_id": "map", "canvas_view": "foo"},
    {"kind": "navigate", "panel_id": "map", "canvas_view": "satellite"},
    {**SITE, "site_id": "s1"},                                             # an extra key
    {**SITE, "results_tab": "loadflow"},
    {"kind": "open", "panel_id": "map", "canvas_view": "site"},
    {"kind": "navigate", "panel_id": "map"},                               # no canvas view
])
def test_only_the_exact_site3d_payload_is_admitted(loaded, payload):
    assert navigation.issue(_turn(loaded), payload) is None


def test_no_network_issues_no_site3d_ack(baseline):
    ctx = baseline[0]
    ctx.network = None
    assert navigation.issue(_turn(ctx), dict(SITE)) is None


@pytest.mark.parametrize("target", [
    {"panel_id": "overview"},
    {"panel_id": "map", "canvas_view": "satellite"},
    {"panel_id": "map", "canvas_view": "site", "site_id": "s1"},
    {"panel_id": "map"},
])
def test_ack_with_another_target_is_refused(client, loaded, target):
    action = navigation.issue(_turn(loaded), dict(SITE))
    body = {**_body(action), "target": target}
    assert client.post("/api/chat/ui-ack", json=body).status_code == 409
    record = client.get("/api/chat/navigation/" + action["action_id"]).json()
    assert record["outcome"] == "submitted"


def test_existing_panel_ack_shape_is_unchanged(loaded):
    """V1 pin: an existing panel's issued ACK keeps its keys, version and target."""
    action = navigation.issue(_turn(loaded), {"kind": "navigate", "panel_id": "overview", "tool_use_id": "t"})
    assert set(action) == ISSUED_KEYS
    assert action["version"] == 1 and action["target"] == {"panel_id": "overview"}
    lf = navigation.issue(_turn(loaded), {"kind": "navigate", "panel_id": "results", "results_tab": "loadflow"})
    assert set(lf) == ISSUED_KEYS | {"source_binding"}
    assert lf["target"] == {"panel_id": "results", "results_tab": "loadflow"}


# ── V3 at the dispatch seam: the frame names its call even with no ACK ──────

def _frames(name, args):
    import uuid

    from services import chat_service, chat_tools

    session = chat_service.ChatSession()
    chat_tools.set_chat_session(session)
    return [data for event, data in chat_service._dispatch_real_tool_call(
        session, {"id": "call-" + uuid.uuid4().hex[:8], "name": name, "input": args}, [])
        if event == "ui_event"]


def test_a_site3d_frame_without_a_network_names_its_call_and_has_no_ack(baseline):
    baseline[0].network = None
    [frame] = _frames("ui_open_panel", {"panel_id": "map", "canvas_view": "site"})
    assert frame["canvas_view"] == "site" and frame["tool_use_id"].startswith("call-")
    assert "acknowledgement" not in frame


def test_another_canvas_view_frame_is_unchanged(baseline):
    baseline[0].network = None
    [frame] = _frames("ui_open_panel", {"panel_id": "map", "canvas_view": "satellite"})
    assert "tool_use_id" not in frame and "acknowledgement" not in frame
