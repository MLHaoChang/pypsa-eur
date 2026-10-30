"""LC-06 S11, LC-D24: the ``ui_event`` frames the assistant's UI tools put on the stream.

The seam is the ordinary dispatch (``_dispatch_real_tool_call``) of ``ui_set_snapshot``,
``ui_select_component``, ``ui_open_asset_detail`` and ``ui_open_panel`` against the
baseline project (G1, two hourly snapshots 2026-01-01 00:00 and 01:00). The frames are
what ChatPanel hands to ``applyUiNavigate``; ``uiNavigate.lc06.test.ts`` takes their
shapes from here and checks the store they drive.

Hand oracle:
- 01:00 is snapshot index 1 and 00:00 index 0, sent as ISO with the index beside it;
- a timestamp off the axis, an unparsable one, an unknown component opened in the editor,
  an unknown panel and an unknown results tab are refused as ``invalid_navigation`` or
  ``component_not_found`` with NO ``ui_event`` frame, so the screen never moves to a
  wrong time or asset;
- a UI tool is a read: the network is untouched.
"""
# ruff: noqa: F811 -- shared public harness fixtures
import hashlib
import uuid

import pytest

from services import chat_service
from services.pypsa_service import PyPSAService
from tests.test_assistant_capabilities import session  # noqa: F401
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401


def frames(session, name, args):
    return [(event, data) for event, data in chat_service._dispatch_real_tool_call(
        session, {"id": uuid.uuid4().hex, "name": name, "input": args}, [])]


def only(session, name, args, kind):
    return [d for e, d in frames(session, name, args) if e == kind]


def fingerprint():
    n = PyPSAService.get_network()
    return hashlib.sha256((n.generators.to_json() + n.snapshots.astype(str).str.cat(sep=",")).encode()).hexdigest()


@pytest.mark.parametrize("iso, index", [("2026-01-01T00:00", 0), ("2026-01-01T01:00", 1)])
def test_a_snapshot_is_addressed_by_its_hand_index(baseline, session, iso, index):
    events = only(session, "ui_set_snapshot", {"snapshot_iso": iso}, "ui_event")
    assert events == [{"kind": "set_snapshot", "snapshot_iso": iso + ":00", "period": None, "snapshot_index": index}]


def test_component_asset_and_panel_events_carry_exactly_what_was_asked(baseline, session):
    assert only(session, "ui_select_component", {"component_class": "Generator", "name": "G1"}, "ui_event") == [
        {"kind": "select_component", "component_class": "Generator", "name": "G1"}]
    assert only(session, "ui_select_component", {"component_class": "Generator", "name": "G1", "open_editor": True},
                "ui_event") == [{"kind": "open_properties", "component_class": "Generator", "name": "G1"}]
    assert only(session, "ui_open_asset_detail", {"component_class": "Generator", "name": "G1",
                                                   "metrics": ["p"], "mode": "duration"}, "ui_event") == [
        {"kind": "open_asset_detail", "component_class": "Generator", "name": "G1", "metrics": ["p"], "mode": "duration"}]
    (panel,) = only(session, "ui_open_panel", {"panel_id": "results"}, "ui_event")
    assert panel["kind"] == "navigate" and panel["panel_id"] == "results"


@pytest.mark.parametrize("name, args, kind", [
    ("ui_set_snapshot", {"snapshot_iso": "2026-02-01T00:00"}, "invalid_navigation"),
    ("ui_set_snapshot", {"snapshot_iso": "nonsense"}, "invalid_navigation"),
    ("ui_select_component", {"component_class": "Generator", "name": "Nope", "open_editor": True}, "component_not_found"),
    ("ui_open_panel", {"panel_id": "nowhere"}, "invalid_navigation"),
    ("ui_open_panel", {"panel_id": "results", "results_tab": "loading"}, "invalid_navigation"),
])
def test_a_wrong_target_is_refused_without_moving_the_screen(baseline, session, name, args, kind):
    before = fingerprint()
    out = frames(session, name, args)
    assert [e for e, _ in out if e == "ui_event"] == []
    assert [d["error_kind"] for e, d in out if e == "tool_error"] == [kind]
    assert fingerprint() == before
