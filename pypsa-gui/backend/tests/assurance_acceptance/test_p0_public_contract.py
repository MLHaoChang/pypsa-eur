"""A0: strict negotiation and public surfaces, before implementation."""

import pytest

from tests.assurance_acceptance.support import parse_sse, text_turn


@pytest.mark.parametrize(
    "options,kind",
    [
        ({"version": 99, "mode": "strict"}, "unsupported_assurance_version"),
        ({"version": 0, "mode": "strict"}, "unsupported_assurance_version"),
        ({"version": True, "mode": "strict"}, "invalid_assurance_request"),
        ({"version": 1, "mode": "unchecked"}, "invalid_assurance_request"),
        ({"mode": "strict"}, "invalid_assurance_request"),
    ],
)
def test_invalid_negotiation_refuses_before_model_use(harness, options, kind):
    response = harness.request(
        [text_turn("This must not be generated.")], assurance=options
    )
    assert response.status_code == 422, "Assurance negotiation was silently ignored"
    assert response.json()["detail"]["error_kind"] == kind
    assert harness.provider.requests == []


def test_strict_session_identifies_checking_mode(harness):
    response = harness.request(
        [text_turn("Unsupported prose."), text_turn("Unsupported prose.")]
    )
    assert response.status_code == 200
    init = next(
        data for event, data in parse_sse(response.text) if event == "session_init"
    )
    assert init.get("assurance") == {
        "version": 1,
        "mode": "strict",
        "state": "checking",
    }


@pytest.mark.parametrize(
    "path,verb",
    [
        ("/api/chat/answers/{answer_id}", "get"),
        ("/api/chat/evidence/{evidence_id}", "get"),
        ("/api/chat/ui-ack", "post"),
    ],
)
def test_required_owned_public_routes_are_registered(client, path, verb):
    paths = client.get("/openapi.json").json()["paths"]
    assert verb in paths.get(path, {}), (
        f"Required assurance route missing: {verb} {path}"
    )
