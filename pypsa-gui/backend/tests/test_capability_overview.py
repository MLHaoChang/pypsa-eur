"""OJ-45: "What can you do in this project?" answered from the catalogue.

find_capabilities(overview=true) lists the core-feature families with what
is available for this project, role and mode, a reason for each one that is
not, and one example sentence; every tool it names exists and is offered.
"""
import json

import pytest

from harness.catalogue import TOOLS
from harness.providers.fake import FakeProvider
from services import chat_tools, run_evidence
from tests.test_chat_restrictions import save_rule
from tests.test_openai_comprehensive_live import setup_profile, turn
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401

FAMILIES = {"projects", "results", "checks", "edits", "sensitivities", "studies", "reports"}


def overview(monkeypatch, ui_context=None):
    session = setup_profile(monkeypatch, {"find_capabilities"})
    chat_tools.set_chat_session(session)
    fake = FakeProvider([
        {"blocks": [{"type": "tool_use", "id": "cap", "name": "find_capabilities", "input": {"overview": True}}]},
        {"blocks": [{"type": "text", "text": "Here is what I can do."}]},
    ])
    frames = turn(session, "What can you do in this project?", fake, ui_context=ui_context)
    assert not [d for e, d in frames if e == "tool_error"], frames
    out = next(d["result"] for e, d in frames if e == "tool_result")
    return out, {row["family"]: row for row in out["families"]}


def test_every_core_family_is_listed_with_an_example_and_real_tools(baseline, monkeypatch):
    out, families = overview(monkeypatch)
    assert set(families) == FAMILIES
    names = {t["name"] for t in TOOLS}
    for row in families.values():
        assert row["example"] and row["tools"] and set(row["tools"]) <= names
        assert isinstance(row["available"], bool)
        assert row["available"] or row["reason"]
    assert out["project"]["name"] == "Tool Baseline" and out["mode"] == "expert"
    assert len(json.dumps(out)) <= 3800


def test_results_need_a_completed_run(baseline, monkeypatch):
    _, families = overview(monkeypatch)
    assert families["results"]["available"] is False and "run" in families["results"]["reason"].lower()
    _ctx, project_id, root = baseline
    run_evidence.publish_run(root, project_id, run_id="r1", kind="simulation", status="completed",
                             started_at="2026-10-01T10:00:00+00:00", finished_at="2026-10-01T10:01:00+00:00",
                             mode="lopf", fingerprint="fp", metrics={"objective": {"value": 1.0, "unit": "EUR"}})
    _, families = overview(monkeypatch)
    assert families["results"]["available"] is True


def test_guided_mode_says_each_change_gets_a_card(baseline, monkeypatch):
    out, families = overview(monkeypatch, {"ui_mode": "guided"})
    assert out["mode"] == "guided"
    assert "card" in families["edits"]["note"].lower()


def test_a_read_only_personal_rule_makes_changes_unavailable(baseline, client, monkeypatch):
    save_rule(client, baseline[1], "read_only")
    out, families = overview(monkeypatch)
    assert out["personal_rule"] == "read_only"
    for family in ("edits", "sensitivities", "studies"):
        assert families[family]["available"] is False and "read-only" in families[family]["reason"]
    assert families["projects"]["available"] and families["checks"]["available"]


def test_without_a_project_only_opening_one_is_available(monkeypatch):
    out, families = overview(monkeypatch)
    assert out["project"] is None
    assert families["projects"]["available"] is True
    assert all(not row["available"] and "project" in row["reason"].lower()
               for name, row in families.items() if name != "projects")


def test_a_goal_search_is_unchanged(baseline):
    session = chat_tools.chat_session()
    del session
    out = chat_tools.DISPATCHERS["find_capabilities"](goal="get_component_schema", limit=1)
    assert out["items"][0]["name"] == "get_component_schema"


@pytest.mark.parametrize("args", [{}, {"overview": False}, {"goal": ""}])
def test_without_a_goal_or_overview_it_refuses(args):
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        chat_tools.DISPATCHERS["find_capabilities"](**args)
