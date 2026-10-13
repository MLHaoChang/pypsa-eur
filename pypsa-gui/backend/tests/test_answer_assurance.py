"""Implemented publication boundary, enabled explicitly within these tests."""

# ruff: noqa: F811 -- Imported pytest fixtures are intentionally used as arguments.
import json
import uuid

import pytest

from harness import assurance
from services import assurance_records, chat_service, chat_tools
from tests.assurance_acceptance.support import (
    UNSAFE,
    Harness,
    approval,
    observed_draft,
    public_text,
    text_turn,
    tool_turn,
)
from tests.test_workflow_tools import baseline  # noqa: F401


@pytest.fixture
def checked(client, monkeypatch):
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_MODE", "strict")
    harness = Harness(client, monkeypatch)
    yield harness
    for session_id in harness.session_ids:
        chat_service.drop_session(session_id)


@pytest.mark.parametrize("case", ["first", "second"])
def test_checked_record_store_isolated_between_cases(baseline, checked, case):
    directory = assurance_records._path(
        assurance_records.actor_id(), "00000000-0000-0000-0000-000000000000"
    ).parent
    assert not list(directory.glob("*.json")), "An earlier test left owned records behind"
    frames = checked.load_fact()
    assert approval(frames)["verdict"] == "passed"
    assert len(list(directory.glob("*.json"))) >= 2


def test_default_full_record_store_still_withholds_publication(baseline, checked):
    from fastapi import HTTPException

    owner = assurance_records.actor_id()
    for _ in range(512):
        assurance_records.save(
            {"owner_id": owner, "record_kind": "evidence"}, str(uuid.uuid4())
        )
    with pytest.raises(HTTPException) as blocked:
        assurance_records.save(
            {"owner_id": owner, "record_kind": "evidence"}, str(uuid.uuid4())
        )
    assert blocked.value.detail["error_kind"] == "assurance_storage_full"
    frames = checked.load_fact()
    assert not public_text(frames)
    assert not any(event == "answer_approved" for event, _ in frames)


def test_disabled_server_refuses_explicit_strict_request(checked, monkeypatch):
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_MODE", "disabled")
    response = checked.request([text_turn(UNSAFE)])
    assert response.status_code == 503
    assert response.json()["detail"]["error_kind"] == "assurance_unavailable"
    assert not checked.provider.requests


def test_strict_server_cannot_be_bypassed_with_script(checked):
    response = checked.request([], script=[{"event": "token", "delta": UNSAFE}])
    assert response.status_code == 422
    assert not checked.provider.requests
    assert UNSAFE not in response.text


def test_server_strict_cannot_be_bypassed_by_omitting_options(checked):
    response = checked.request([text_turn(UNSAFE)], assurance=None)
    assert response.status_code == 422
    body = {"message": "Answer", "session_id": "strict-no-options"}
    checked.install([text_turn(UNSAFE)])
    response = checked.client.post("/api/chat/stream", json=body)
    from tests.assurance_acceptance.support import parse_sse

    assert response.status_code == 200
    assert UNSAFE not in public_text(parse_sse(response.text))
    assert approval(parse_sse(response.text))["verdict"] == "limited"
    chat_service.drop_session("strict-no-options")


def test_checked_input_has_useful_server_rendered_value(baseline, checked):
    frames = checked.load_fact()
    assert approval(frames)["verdict"] == "passed"
    assert "p_set = 5 MW" in public_text(frames)
    assert "Static inputs" in public_text(frames)


def test_checked_fractional_value_is_not_silently_rounded(baseline, checked):
    value = 5.123456789012345
    baseline[0].network.loads.at["L1", "p_set"] = value
    frames = checked.load_fact(value=value)
    assert approval(frames)["verdict"] == "passed"
    assert f"p_set = {value} MW" in public_text(frames)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_raw_input_is_not_promoted_to_zero(baseline, checked, value):
    baseline[0].network.loads.at["L1", "p_set"] = value
    frames = checked.load_fact(value=0)
    assert approval(frames)["verdict"] == "limited"
    assert "p_set = 0 MW" not in public_text(frames)


def test_disabling_identity_revokes_saved_answer_access(baseline, checked, _auth_db):
    import uuid
    from fastapi import HTTPException
    from db.models import User

    approved = approval(checked.load_fact())
    owner = assurance_records.actor_id()
    session_local = _auth_db[1]
    with session_local() as db:
        db.get(User, uuid.UUID(owner)).status = "disabled"
        db.commit()
    try:
        with pytest.raises(HTTPException) as caught:
            assurance_records.answer(owner, approved["answer_id"])
        assert caught.value.status_code == 401
    finally:
        with session_local() as db:
            db.get(User, uuid.UUID(owner)).status = "active"
            db.commit()


@pytest.mark.parametrize("fault", ["changed", "deleted", "corrupted"])
def test_invalidated_evidence_blocks_fetch_history_and_export(baseline, checked, fault):
    approved = approval(checked.load_fact())
    evidence_id = approved["receipt"]["evidence_ids"][0]
    owner = assurance_records.actor_id()
    if fault == "changed":
        baseline[0].network.loads.at["L1", "p_set"] = 75
    else:
        path = assurance_records._path(owner, evidence_id)
        if fault == "deleted":
            path.unlink()
        else:
            path.write_text('{"record": {}, "sha256": "forged"}')
    response = checked.client.get(f"/api/chat/answers/{approved['answer_id']}")
    assert response.status_code == 409
    assert response.json()["detail"]["error_kind"] == "answer_evidence_unavailable"
    for route in ("/api/chat/history", "/api/chat/export"):
        replay = checked.client.get(route).json()
        assert "p_set = 5 MW" not in json.dumps(replay)
        assert "receipt" not in replay["turns"][-1]


def test_commit_failure_never_publishes_or_persists_the_draft(
    baseline, checked, monkeypatch
):
    save = assurance_records.save

    def fail_answer(record, record_id):
        if record["record_kind"] == "answer":
            raise OSError("simulated storage failure")
        save(record, record_id)

    monkeypatch.setattr(assurance_records, "save", fail_answer)
    frames = checked.load_fact()
    assert not public_text(frames)
    assert not any(event == "answer_approved" for event, _ in frames)
    assert checked.client.get("/api/chat/history").json()["turns"] == []


def test_required_guard_exception_blocks_handler(baseline, checked, monkeypatch):
    calls = []
    monkeypatch.setitem(
        chat_tools.DISPATCHERS, "get_component", lambda **kw: calls.append(kw)
    )

    def fail_guard(*args):
        raise RuntimeError("required guard unavailable")

    monkeypatch.setattr(assurance.Turn, "tool_guard", fail_guard)
    frames = checked.load_fact()
    assert not calls
    assert approval(frames)["verdict"] == "limited"


def test_unsupported_effect_is_not_executed(baseline, checked, monkeypatch):
    calls = []
    monkeypatch.setitem(
        chat_tools.DISPATCHERS, "update_component", lambda **kw: calls.append(kw)
    )
    frames = checked.run(
        [
            tool_turn(
                "update_component",
                {"component_class": "Load", "name": "L1", "attrs": {"p_set": 75}},
            ),
            text_turn(UNSAFE),
        ]
    )
    assert not calls
    assert baseline[0].network.loads.at["L1", "p_set"] == 5
    assert approval(frames)["verdict"] == "limited"


def test_raw_preamble_and_thinking_do_not_survive_session_history(baseline, checked):
    frames = checked.run(
        [
            tool_turn(
                "get_component",
                {"component_class": "Load", "name": "L1"},
                preamble=UNSAFE,
            ),
            text_turn(observed_draft(), thinking=UNSAFE),
        ]
    )
    approval(frames)
    session = chat_service.get_session(checked.session_ids[-1])
    assert UNSAFE not in json.dumps(list(session.messages))


def test_incorrect_handler_value_cannot_certify_itself(baseline, checked, monkeypatch):
    monkeypatch.setitem(
        chat_tools.DISPATCHERS, "get_component", lambda **kw: {"p_set": 75}
    )
    frames = checked.load_fact(value=75)
    assert approval(frames)["verdict"] == "limited"
    assert "75 MW" not in public_text(frames)


def test_evidence_changed_after_history_commit_is_not_published(
    baseline, checked, monkeypatch
):
    from harness import loop

    append = loop.append_turn

    def change_input(ctx, record):
        append(ctx, record)
        baseline[0].network.loads.at["L1", "p_set"] = 75

    monkeypatch.setattr(loop, "append_turn", change_input)
    frames = checked.load_fact()
    assert not public_text(frames)
    assert not any(event == "answer_approved" for event, _ in frames)


def test_expired_records_are_reclaimed_before_quota(baseline, checked, monkeypatch):
    import os
    import time

    approved = approval(checked.load_fact())
    owner = assurance_records.actor_id()
    expired = assurance_records._path(owner, approved["answer_id"])
    old = time.time() - assurance_records.RETENTION_SECONDS - 1
    os.utime(expired, (old, old))
    count = len(list(expired.parent.glob("*.json")))
    monkeypatch.setattr(assurance_records, "MAX_RECORDS", count + 1)
    assert approval(checked.load_fact())["verdict"] == "passed"
    assert not expired.exists()


def test_strict_history_seed_revalidates_and_excludes_legacy_claims(baseline, checked):
    approved = approval(checked.load_fact())
    baseline[0].network.loads.at["L1", "p_set"] = 75
    session = chat_service.get_session(checked.session_ids[-1])
    gate = assurance.Turn(session, baseline[0])
    seed = gate.history_seed(
        [
            {"role": "assistant", "content": [{"type": "text", "text": UNSAFE}]},
            {
                "role": "assistant",
                "_approved_answer_id": approved["answer_id"],
                "content": [{"type": "text", "text": "p_set = 5 MW"}],
            },
        ]
    )
    assert UNSAFE not in json.dumps(seed)
    assert "p_set = 5 MW" not in json.dumps(seed)
    assert "unavailable" in json.dumps(seed)


@pytest.mark.parametrize(
    "change", ["boolean", "duplicate_field", "extra_trust", "duplicate_id"]
)
def test_schema_and_trust_spoofs_are_rejected(baseline, checked, change):
    data = json.loads(observed_draft())
    if change == "boolean":
        data["blocks"][0]["value"] = True
    elif change == "extra_trust":
        data["blocks"][0]["verified"] = True
    elif change == "duplicate_id":
        data["blocks"].append(data["blocks"][0])
    text = json.dumps(data)
    if change == "duplicate_field":
        text = text.replace(
            '"schema_version": 1', '"schema_version": 99, "schema_version": 1'
        )
    frames = checked.run(
        [
            tool_turn("get_component", {"component_class": "Load", "name": "L1"}),
            text_turn(text),
        ]
    )
    assert approval(frames)["verdict"] == "limited"
