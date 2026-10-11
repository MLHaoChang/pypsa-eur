"""A5: authenticated exact-generation view acknowledgements, not URLs."""

import pytest


def acknowledgement(**overrides):
    return {
        "version": 1,
        "action_id": "unknown-action",
        "turn_id": "unknown-turn",
        "binding_generation": 1,
        "target": {"panel_id": "results", "results_tab": "loadflow"},
        **overrides,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"binding_generation": 999},
        {"turn_id": "older-turn"},
        {"target": {"panel_id": "results", "results_tab": "economics"}},
    ],
)
def test_unissued_or_mismatched_ack_does_not_create_navigation_success(client, changes):
    response = client.post("/api/chat/ui-ack", json=acknowledgement(**changes))
    assert response.status_code == 409, (
        "No expected-action acknowledgement implementation"
    )
    assert response.json()["detail"]["error_kind"] == "ui_ack_mismatch"


def test_missing_csrf_is_rejected_before_ack_processing(client):
    from settings import get_settings

    name = get_settings().csrf_header_name
    client.headers.pop(name, None)
    response = client.post("/api/chat/ui-ack", json=acknowledgement())
    assert response.status_code == 403
