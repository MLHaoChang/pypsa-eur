"""A1.2: a stateful view acknowledgement needs its owner and a valid CSRF token.

Issue 02 (P1). `POST /api/chat/ui-ack` changes an issued navigation action from
submitted to completed. A request carrying a CSRF header that does not match
the session's token is refused before the acknowledgement is read, and another
member of the same organization cannot acknowledge, or read, the owner's
action. Neither attempt changes the stored outcome; the owner's own
acknowledgement still completes it afterwards.
"""

# ruff: noqa: F811 -- imported shared fixtures.

from settings import get_settings
from tests.assurance_acceptance.test_p1_evidence import same_org_client  # noqa: F401
from tests.test_harness_reliability import issued_view


def outcome(client, action_id):
    return client.get(f"/api/chat/navigation/{action_id}").json()["outcome"]


def test_a_wrong_csrf_token_is_refused_before_ack_processing(baseline, client):
    _turn, body = issued_view(baseline[0])
    name = get_settings().csrf_header_name
    valid = client.headers[name]
    client.headers[name] = valid[:-1] + ("0" if valid[-1] != "0" else "1")
    response = client.post("/api/chat/ui-ack", json=body)
    assert response.status_code == 403, response.text
    assert "csrf_token_invalid" in response.text
    assert "ui_ack" not in response.text
    client.headers[name] = valid
    assert outcome(client, body["action_id"]) == "submitted"
    ok = client.post("/api/chat/ui-ack", json=body)
    assert ok.status_code == 200, ok.text
    assert ok.json()["outcome"] == "completed"


def test_another_member_cannot_acknowledge_or_read_the_action(
    baseline, client, same_org_client
):
    _turn, body = issued_view(baseline[0])
    response = same_org_client.post("/api/chat/ui-ack", json=body)
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == {"error_kind": "ui_ack_mismatch"}
    unknown = same_org_client.get(f"/api/chat/navigation/{body['action_id']}")
    assert unknown.status_code == 404
    assert (
        unknown.json()
        == same_org_client.get(
            "/api/chat/navigation/00000000-0000-0000-0000-000000000000"
        ).json()
    )
    assert outcome(client, body["action_id"]) == "submitted"
    ok = client.post("/api/chat/ui-ack", json=body)
    assert ok.status_code == 200, ok.text
    assert ok.json()["outcome"] == "completed"
