"""A0.4: server-owned request/guard contracts; client-supplied intent is data, never proof."""

# ruff: noqa: F811 -- shared pytest fixtures.
import uuid

import pytest

from tests.assurance_acceptance.support import approval, public_text, text_turn
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_assurance_metrics import NumericalProvider, solved  # noqa: F401
from tests.test_harness_reliability import scope
from tests.test_workflow_tools import baseline  # noqa: F401


def _operation(ctx, **overrides):
    return {
        "version": 1,
        "operation_id": str(uuid.uuid4()),
        "project_id": ctx.project_uuid,
        "tool_name": "update_component",
        "arguments": {"component_class": "Load", "name": "L1", "attrs": {"p_set": 6.0}},
        "before_sha256": "0" * 64,
        **overrides,
    }


def test_scope_naming_another_project_gets_no_checked_result(solved, checked):
    frames = checked.run(
        NumericalProvider({}), checked_scope=scope(solved, project_id=str(uuid.uuid4()))
    )
    assert approval(frames)["verdict"] == "limited"
    slots = approval(frames)["receipt"]["result_slots"]
    assert slots and all(slot["reason"] == "wrong_project" for slot in slots)
    assert "230" not in public_text(frames)


@pytest.mark.parametrize(
    "forged",
    [
        {"owner": "someone-else"},
        {"authorized": True},
        {"verified": True},
        {"profile": "generator-cost/2"},
        {"profile": "objective/1"},
        {"version": 2},
        {"version": True},
    ],
)
def test_forged_scope_intent_is_refused_before_model_use(solved, checked, forged):
    response = checked.request(
        [text_turn("unused")], checked_scope=scope(solved, **forged)
    )
    assert response.status_code == 422
    assert response.json()["detail"]["error_kind"] == "invalid_assurance_request"
    assert checked.provider.requests == []


def test_metric_outside_the_registry_is_never_answered_by_a_nearby_metric(
    solved, checked
):
    frames = checked.run(
        NumericalProvider({}), checked_scope=scope(solved, metrics=["objective"])
    )
    assert approval(frames)["verdict"] == "limited"
    slots = approval(frames)["receipt"]["result_slots"]
    assert [slot["metric"] for slot in slots] == ["objective"]
    assert slots[0]["disposition"] != "answered"


@pytest.mark.parametrize(
    "forged",
    [
        {"tool_name": "delete_component"},
        {"confirmed": True},
        {
            "arguments": {
                "component_class": "Bus",
                "name": "B1",
                "attrs": {"v_nom": 1.0},
            }
        },
        {
            "arguments": {
                "component_class": "Load",
                "name": "L1",
                "attrs": {"p_set": True},
            }
        },
        {
            "arguments": {
                "component_class": "Load",
                "name": "L1",
                "attrs": {"sign": -1.0},
            }
        },
        {"before_sha256": "Z" * 64},
    ],
)
def test_forged_effect_guard_is_refused_before_model_use(solved, checked, forged):
    response = checked.request(
        [text_turn("unused")], checked_operation=_operation(solved, **forged)
    )
    assert response.status_code == 422
    assert response.json()["detail"]["error_kind"] == "invalid_assurance_request"
    assert checked.provider.requests == []
