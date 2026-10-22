"""Check answers withholds every result it cannot verify (checked-results S1).

A checked turn runs only `CHECKED_TOOLS`; every other catalogue tool, each
results read, chart and export among them, is refused before its handler
runs. Every registered result view declares which bounded profiles answer it
and why the rest stays unchecked. See `.scratch/checked-results/audit.md`.
"""

# ruff: noqa: F811 -- Imported pytest fixtures are intentionally used as arguments.
import json
from types import SimpleNamespace

import pytest

from harness import assurance, calculations
from harness.catalogue import TOOLS
from services import chat_service, chat_tools
from services.result_views import CHECKED_GAPS, RESULT_VIEWS
from tests.assurance_acceptance.support import (
    UNSAFE,
    Harness,
    approval,
    public_text,
    text_turn,
)
from harness.protocol import LLMEvent
from tests.test_workflow_tools import baseline  # noqa: F401

SENTINEL = 987654.321
CATALOGUE = sorted({t["name"] for t in TOOLS})


def test_checked_turn_runs_exactly_three_tools():
    assert assurance.CHECKED_TOOLS == {"get_component", "ui_open_panel", "update_component"}
    assert assurance.CHECKED_TOOLS <= set(CATALOGUE)


@pytest.mark.parametrize("tool_name", CATALOGUE)
def test_guard_refuses_every_other_catalogue_tool(tool_name):
    turn = assurance.Turn(
        SimpleNamespace(owner_user_id="owner", usage_acc={}),
        SimpleNamespace(project_uuid="00000000-0000-0000-0000-000000000000"),
    )
    if tool_name in assurance.CHECKED_TOOLS:
        pytest.skip("admitted under its own argument checks (test_answer_assurance)")
    for args in ({}, {"component_class": "Generator", "name": "G1"}, {"result_kind": "statistics"}):
        assert turn.tool_guard(tool_name, args) is False


@pytest.mark.parametrize("view", RESULT_VIEWS, ids=lambda v: v.view_id)
def test_every_result_view_declares_its_checked_disposition(view):
    assert set(view.checked_profiles) <= set(calculations.PROFILES)
    assert len(set(view.checked_profiles)) == len(view.checked_profiles)
    # No view is fully checked yet: each names why the rest is withheld.
    assert view.checked_gap in CHECKED_GAPS
    for read in view.reads:
        assert read.tool not in assurance.CHECKED_TOOLS


def _call(name, call_id):
    return {"events": [LLMEvent(type="tool_use_start", tool_name=name, tool_use_id=call_id)],
            "blocks": [{"type": "tool_use", "id": call_id, "name": name, "input": {}}]}


@pytest.fixture
def checked(client, monkeypatch):
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_MODE", "strict")
    harness = Harness(client, monkeypatch)
    yield harness
    for session_id in harness.session_ids:
        chat_service.drop_session(session_id)


def test_checked_turn_never_runs_or_publishes_a_refused_tool(baseline, checked, monkeypatch):
    calls = []

    def recorder(name):
        def run(**kwargs):
            calls.append(name)
            return {"status": "ok", "value": SENTINEL, "unit": "MWh"}
        return run

    refused = [n for n in CATALOGUE if n not in assurance.CHECKED_TOOLS]
    for name in refused:
        monkeypatch.setitem(chat_tools.DISPATCHERS, name, recorder(name))
    seen = {}
    batch = 20  # one call per model round, under the per-turn tool-call cap
    for start in range(0, len(refused), batch):
        names = refused[start:start + batch]
        rounds = [_call(name, f"call-{start + i}") for i, name in enumerate(names)]
        frames = checked.run(rounds + [text_turn(UNSAFE), text_turn(UNSAFE)])
        errors = {p["tool_name"]: p["error_kind"] for e, p in frames if e == "tool_error"}
        assert set(errors) == set(names)
        assert approval(frames)["verdict"] != "passed"
        published = public_text(frames) + json.dumps([p for e, p in frames if e != "tool_error"])
        assert "987654" not in published
        # Refused by the guard, or earlier because this toolset view does not
        # offer it; either way no handler runs (the guard itself is covered per
        # tool above). The results tabs' own reads always reach the guard.
        assert set(errors.values()) <= {"capability_unsupported", "tool_not_offered"}, errors
        seen.update(errors)
    assert calls == []
    assert set(seen) == set(refused)
    view_reads = {r.tool for v in RESULT_VIEWS if v.live() for r in v.reads}
    # A planning-to-dynamics study's reads (Grid code & connection) are offered only to a
    # study project, which this baseline is not; every other read reaches the guard.
    study_reads = {n for n in view_reads if n.startswith("gridspine_")}
    assert {seen[name] for name in view_reads - study_reads} == {"capability_unsupported"}
    assert {seen[name] for name in study_reads} <= {"capability_unsupported", "tool_not_offered"}
