"""Issue 02 (FILL-11-6): the returned v1 shapes are pinned from what is served.

Each served path is read on a real checked turn, not a hand-built record: the
answer route's receipt, the evidence route's record and the tool result the
checked stream hands back. `tests/conftest.py` holds the whole suite to the
same models; these tests make sure each path is exercised at least once.
"""

# ruff: noqa: F811 -- imported shared fixtures.
import json
import subprocess
import uuid
from pathlib import Path

import pytest

from harness import assurance_contracts as contracts
from harness import assurance_shapes as shapes
from services import assurance_records
from tests.assurance_acceptance.support import approval
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_workflow_tools import baseline  # noqa: F401

BASE = "e4195e82"  # master with checked_tool_inputs: an input widening is judged additive there (LC-14 guided_step, panel_section)


@pytest.fixture
def served(baseline, checked):
    frames = checked.load_fact()
    record = approval(frames)
    return checked, frames, record


def test_the_answer_route_serves_the_frozen_receipt(served):
    checked, _frames, record = served
    response = checked.client.get("/api/chat/answers/" + record["answer_id"])
    assert response.status_code == 200, response.text
    receipt = response.json()["receipt"]
    shapes.RECEIPTS[receipt["policy_version"]].model_validate(receipt)


def test_the_evidence_route_serves_the_frozen_identity(served):
    checked, _frames, record = served
    assert record["receipt"]["evidence_ids"]
    for evidence_id in record["receipt"]["evidence_ids"]:
        response = checked.client.get("/api/chat/evidence/" + evidence_id)
        assert response.status_code == 200, response.text
        shown = response.json()
        assert shown["adapter_version"] == "static-input/1"
        shapes.evidence_model(shown).model_validate(shown)


def test_the_checked_stream_serves_the_frozen_tool_result(served):
    _checked, frames, _record = served
    results = [d for event, d in frames if event == "tool_result"]
    assert [r["tool_name"] for r in results] == ["get_component"]
    shapes.validate_tool_result("get_component", results[0]["result"])
    assert results[0]["result"]["_assurance"]["evidence_ids"]


def test_every_frozen_receipt_version_is_accepted_by_both_readers(served):
    """F1 (b): a receipt version the contract lists but a reader refuses fails here,
    so a policy bump cannot invalidate stored answers unnoticed."""
    _checked, _frames, record = served
    owner = assurance_records.actor_id()
    stored = assurance_records.load(owner, record["answer_id"], "answer")
    for version in contracts.build()["answer_receipt"]:
        receipt = {**stored["receipt"], "policy_version": version}
        if version == assurance_records.DERIVED_POLICY_VERSION:
            # Policy "2" always carries derived entries (GK B P2).
            receipt["derived"] = [
                {
                    "id": "d1",
                    "formula_id": "difference/1",
                    "operands": ["a", "b"],
                    "value": 1.0,
                    "unit": "MW",
                }
            ]
        if version == assurance_records.PREDICATE_POLICY_VERSION:
            # Policy "3" always carries predicates (GK B C8).
            receipt["predicates"] = [
                {
                    "id": "p1",
                    "quantifier": "top_k",
                    "evidence_id": "E1",
                    "component_class": "Load",
                    "metric": "p_set",
                    "unit": "MW",
                    "population": 2,
                    "warnings": [],
                    "k": 1,
                    "members": ["L1"],
                }
            ]
        rewritten = {**stored, "receipt": receipt}
        # Answer records are immutable, so each version is stored under its own id.
        answer_id = str(uuid.uuid4())
        assurance_records.save(rewritten, answer_id)
        assert assurance_records.answer(owner, answer_id)["receipt"] == receipt
        # harness/pilot_evaluation.py gates on the same receipt_policy_ok.
        assert assurance_records.receipt_policy_ok(receipt), version


def test_returned_schemas_carry_no_notes():
    """F3: a docstring or field title edit is not reported as a break."""

    def notes(node, named=False):
        if isinstance(node, list):
            return any(notes(item) for item in node)
        if not isinstance(node, dict):
            return False
        return any(
            (not named and key in ("title", "description"))
            or notes(value, key in ("properties", "$defs") and not named)
            for key, value in node.items()
        )

    live = contracts.build()
    for key in ("evidence_identity", "answer_receipt", "checked_tool_results"):
        assert not notes(live[key]), key


@pytest.mark.parametrize(
    "table,dropped",
    [("EVIDENCE", "series-input/2"), ("TOOL_RESULTS", "get_timeseries")],
)
def test_a_missing_returned_model_fails_the_build(monkeypatch, table, dropped):
    """F4: an adapter or checked tool without a model cannot be frozen silently."""
    original = getattr(shapes, table)
    monkeypatch.setattr(
        shapes, table, {k: v for k, v in original.items() if k != dropped}
    )
    with pytest.raises(ValueError, match=dropped.replace("/", ".")):
        contracts.build()


def test_the_change_is_additive_against_master():
    """F4: the snapshot this branch writes keeps every entry master froze."""
    path = Path(contracts.SNAPSHOT).resolve()
    root = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=path.parent,
        capture_output=True,
        text=True,
    )
    shown = (
        subprocess.run(
            ["git", "show", f"{BASE}:{path.relative_to(root.stdout.strip())}"],
            cwd=path.parent,
            capture_output=True,
            text=True,
        )
        if root.returncode == 0
        else None
    )
    if shown is None or shown.returncode != 0:
        pytest.skip(f"{BASE} is not in this checkout (shallow clone)")
    master = json.loads(shown.stdout)
    assert contracts.breaking_changes(master, contracts.build()) == []
