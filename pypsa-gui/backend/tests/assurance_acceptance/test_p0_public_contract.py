"""A0: strict negotiation and public surfaces, before implementation."""

import json

import pytest

from tests.assurance_acceptance.support import (
    OMIT,
    approval,
    observed_draft,
    parse_sse,
    text_turn,
    tool_turn,
)


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


# A0.1: exact draft bounds, duplicate IDs, forged trust fields and block kinds.


def _draft(**changes):
    data = json.loads(observed_draft())
    block = data["blocks"][0]
    if "count" in changes:
        data["blocks"] = [{**block, "id": f"b{i}"} for i in range(changes["count"])]
    if "block_id" in changes:
        block["id"] = changes["block_id"]
    for key, value in changes.get("top", {}).items():
        data[key] = value
    for key, value in changes.get("block", {}).items():
        block[key] = value
    for key, value in changes.get("source", {}).items():
        block["source"][key] = value
    text = json.dumps(data)
    if "size" in changes:
        # JSON permits trailing whitespace, so the size bound is the only difference.
        text += " " * (changes["size"] - len(text.encode()))
    return text


def _verdict(harness, draft):
    frames = harness.run(
        [
            tool_turn("get_component", {"component_class": "Load", "name": "L1"}),
            text_turn(draft),
            text_turn(draft),
        ]
    )
    approved = approval(frames)
    return approved["verdict"], approved["receipt"]["checked_block_ids"]


@pytest.mark.parametrize(
    "changes,passes",
    [
        ({"count": 20}, True),
        ({"count": 21}, False),
        ({"block_id": "x" * 64}, True),
        ({"block_id": "x" * 65}, False),
        ({"size": 64 * 1024}, True),
        ({"size": 64 * 1024 + 1}, False),
    ],
    ids=["20-blocks", "21-blocks", "id-64", "id-65", "64KiB", "64KiB+1"],
)
def test_draft_bounds_are_exact(baseline, harness, changes, passes):
    verdict, checked = _verdict(harness, _draft(**changes))
    assert (verdict == "passed") is passes, (changes, verdict)
    assert bool(checked) is passes


@pytest.mark.parametrize(
    "changes",
    [
        {"top": {"verdict": "passed"}},
        {"top": {"receipt": {"checked_block_ids": ["load-input"]}}},
        {"top": {"answer_id": "forged"}},
        {"block": {"checked": True}},
        {"block": {"evidence_owner": "someone-else"}},
        {"source": {"trusted": True}},
        {"source": {"project_id": "other-project"}},
    ],
    ids=["verdict", "receipt", "answer-id", "checked", "owner", "trusted-source", "project"],
)
def test_forged_trust_fields_anywhere_in_the_draft_are_refused(baseline, harness, changes):
    verdict, checked = _verdict(harness, _draft(**changes))
    assert verdict == "limited" and checked == []


@pytest.mark.parametrize(
    "kind", ["computed", "action", "proposal", "limitation", "clarification", "interpretation", "magic"]
)
def test_only_admitted_block_kinds_reach_a_checked_label(baseline, harness, kind):
    # v1 admits observed blocks here; other kinds need a reviewed additive schema.
    verdict, checked = _verdict(harness, _draft(block={"kind": kind}))
    assert verdict == "limited" and checked == []


def test_duplicate_ids_across_otherwise_valid_blocks_are_refused(baseline, harness):
    data = json.loads(_draft(count=2))
    data["blocks"][1]["id"] = data["blocks"][0]["id"]
    verdict, checked = _verdict(harness, json.dumps(data))
    assert verdict == "limited" and checked == []


# A0.3: unchecked turns and history are explicitly unvalidated; never "checking".


@pytest.mark.parametrize("mode", ["disabled", "shadow", "pilot"])
def test_unchecked_turn_states_unvalidated_and_never_advertises_checking(
    harness, monkeypatch, mode
):
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_MODE", mode)
    frames = harness.run([text_turn("Ordinary answer.")], assurance=OMIT)
    init = next(data for event, data in frames if event == "session_init")
    assert init["assurance"] == {"version": 1, "mode": "legacy", "state": "unvalidated"}
    assert not any(event == "answer_approved" for event, _ in frames)


def test_old_client_without_assurance_field_still_gets_the_checked_gate_on_strict(harness):
    frames = harness.run(
        [text_turn("Unsupported prose."), text_turn("Unsupported prose.")],
        assurance=OMIT,
    )
    init = next(data for event, data in frames if event == "session_init")
    assert init["assurance"]["state"] == "checking"
    assert approval(frames)["verdict"] == "limited"


@pytest.mark.parametrize("mode", ["disabled", "strict"])
def test_legacy_history_is_explicitly_unvalidated_and_drops_imported_trust(
    baseline, monkeypatch, mode
):
    from routers import chat as chat_router
    from services import chat_service
    from services.pypsa_service import PyPSAService

    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_MODE", mode)
    chat_service.append_turn(
        PyPSAService.get_active_context(),
        {
            "ts": 1.0,
            "session_id": "legacy-" + mode,
            "model": chat_service.DEFAULT_MODEL,
            "user": "What is L1?",
            "assistant": [{"type": "text", "text": "L1 is 75 MW."}],
            "receipt": {"checked_block_ids": ["load-input"]},
            "requires_checked": True,
        },
    )
    turn = chat_router.chat_history(limit=200, actor=None)["turns"][-1]
    assert turn["assurance_state"] == "unvalidated"
    assert "receipt" not in turn and "requires_checked" not in turn
    assert ("L1 is 75 MW." in str(turn["assistant"])) is (mode == "disabled")
