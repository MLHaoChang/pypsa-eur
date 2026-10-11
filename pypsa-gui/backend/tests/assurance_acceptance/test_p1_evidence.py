"""A1: real configured inputs, scoped evidence and anonymous refusal."""

import pytest

from tests.assurance_acceptance.support import approval


def test_real_input_fact_has_resolvable_project_entity_unit_evidence(baseline, harness):
    approved = approval(harness.load_fact())
    assert approved["verdict"] == "passed"
    ids = approved["receipt"]["evidence_ids"]
    assert ids, "An observed fact has no server-owned evidence"
    response = harness.client.get(f"/api/chat/evidence/{ids[0]}")
    assert response.status_code == 200
    record = response.json()
    assert record["project_id"] == baseline[1]
    assert record["source_kind"] == "configured_input"
    assert any(
        fact["entity_id"] == "L1"
        and fact["metric"] == "p_set"
        and fact["value"] == 5
        and fact["unit"] == "MW"
        for fact in record["facts"]
    )


def test_another_organization_cannot_read_an_approved_fact(
    baseline, harness, other_org_client
):
    approved = approval(harness.load_fact())
    for path in (
        f"/api/chat/answers/{approved['answer_id']}",
        f"/api/chat/evidence/{approved['receipt']['evidence_ids'][0]}",
    ):
        response = other_org_client.get(path)
        assert response.status_code == 404
        assert "L1" not in response.text and "p_set" not in response.text


@pytest.mark.parametrize(
    "path", ["/api/chat/answers/unknown", "/api/chat/evidence/unknown"]
)
def test_anonymous_requests_are_refused_by_global_auth(anon_client, path):
    assert anon_client.get(path).status_code == 401


def test_evidence_capture_is_attached_to_real_tool_result(baseline, harness):
    frames = harness.load_fact()
    result = next(data["result"] for event, data in frames if event == "tool_result")
    assert result["p_set"] == 5
    assert result.get("_assurance", {}).get("evidence_ids"), (
        "Raw result lacks captured evidence IDs"
    )
