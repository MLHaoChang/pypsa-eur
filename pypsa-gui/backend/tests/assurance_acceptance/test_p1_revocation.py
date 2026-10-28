"""A1.2: access revoked between evidence capture and publish, or before a read.

Two revocations: the user's org membership is removed (project ACL), or the
user is disabled. Values a tool returned while access held may already be on
the stream; nothing may be published, certified or read back after it.
"""

import json
import uuid

import pytest

import app_paths
from db.models import OrgMembership, User
from harness.providers.fake import FakeProvider
from tests.assurance_acceptance.support import (
    approval,
    observed_draft,
    parse_sse,
    public_text,
    text_turn,
    tool_turn,
)


class RevokingProvider(FakeProvider):
    """Runs `revoke` once, just before the model's turn number `at` (from 0)."""

    def __init__(self, turns, revoke, at=1):
        super().__init__(turns)
        self._revoke, self._at, self._calls = revoke, at, 0

    def stream(self, request):
        if self._calls == self._at:
            self._revoke()
        self._calls += 1
        yield from super().stream(request)


@pytest.fixture
def revoke(_auth_db, seeded_identity):
    """Revocations of the seeded user's access; each is undone after the test."""
    _engine, session_local = _auth_db
    undo = []

    def membership():
        with session_local() as db:
            row = (
                db.query(OrgMembership)
                .filter_by(
                    user_id=seeded_identity["user_id"],
                    org_id=seeded_identity["org_id"],
                )
                .one()
            )
            saved = (row.id, row.role)
            db.delete(row)
            db.commit()
        undo.append(("membership", saved))

    def disable():
        with session_local() as db:
            db.get(User, uuid.UUID(str(seeded_identity["user_id"]))).status = "disabled"
            db.commit()
        undo.append(("disable", None))

    yield {"membership": membership, "disabled": disable}
    with session_local() as db:
        for kind, saved in reversed(undo):
            if kind == "membership":
                db.add(
                    OrgMembership(
                        id=saved[0],
                        user_id=seeded_identity["user_id"],
                        org_id=seeded_identity["org_id"],
                        role=saved[1],
                    )
                )
            else:
                db.get(
                    User, uuid.UUID(str(seeded_identity["user_id"]))
                ).status = "active"
        db.commit()


def answer_records(owner):
    folder = app_paths.app_data_dir() / "assistant_assurance" / owner
    found = set()
    for path in folder.glob("*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))["record"]
        if record.get("record_kind") == "answer":
            found.add(path.stem)
    return found


def get_component(call_id="read-load"):
    return tool_turn(
        "get_component", {"component_class": "Load", "name": "L1"}, call_id=call_id
    )


def list_loads():
    return tool_turn(
        "list_components", {"component_class": "Load"}, call_id="list-loads"
    )


PAGE_DRAFT = json.dumps(
    {
        "schema_version": 1,
        "blocks": [
            {
                "id": "page-input",
                "kind": "observed",
                "entity_id": "L1",
                "metric": "p_set",
                "value": 5,
                "unit": "MW",
                "source": {"tool_use_id": "list-loads", "path": "/L1/p_set"},
            }
        ],
    }
)


def assert_nothing_published(frames, owner, before):
    assert not any(event == "answer_approved" for event, _ in frames)
    assert public_text(frames) == ""
    errors = [d for event, d in frames if event == "error"]
    assert [e["error_kind"] for e in errors] == ["assurance_publication_failed"]
    assert "5" not in json.dumps(errors)
    done = [d for event, d in frames if event == "session_done"]
    assert done == [{"reason": "assurance_publication_failed"}]
    assert answer_records(owner) == before


KINDS = ["membership", "disabled"]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "read, draft",
    [(get_component, observed_draft()), (list_loads, PAGE_DRAFT)],
    ids=["get_component", "list_components"],
)
def test_access_revoked_after_capture_publishes_nothing(
    baseline, harness, revoke, seeded_identity, kind, read, draft
):
    owner = str(seeded_identity["user_id"])
    before = answer_records(owner)
    turns = [read(), text_turn(draft), text_turn(draft)]
    response = harness.request(RevokingProvider(turns, revoke[kind]))
    assert response.status_code == 200
    frames = parse_sse(response.text)
    # The capture itself held evidence: access was intact when the tool ran.
    result = next(d for event, d in frames if event == "tool_result")
    assert result["result"]["_assurance"]["evidence_ids"]
    assert_nothing_published(frames, owner, before)


@pytest.mark.parametrize("kind", KINDS)
def test_tool_called_after_revocation_returns_no_values(
    baseline, harness, revoke, seeded_identity, kind
):
    owner = str(seeded_identity["user_id"])
    before = answer_records(owner)
    draft = observed_draft()
    turns = [
        get_component(),
        get_component("again"),
        text_turn(draft),
        text_turn(draft),
    ]
    response = harness.request(RevokingProvider(turns, revoke[kind]))
    frames = parse_sse(response.text)
    late = [d for _, d in frames if d.get("tool_use_id") == "again"]
    assert late and not any("result" in d for d in late)
    refused = [d for event, d in frames if event == "tool_error"]
    assert [d["tool_use_id"] for d in refused] == ["again"]
    assert "p_set" not in json.dumps(late) and "5.0" not in json.dumps(late)
    assert_nothing_published(frames, owner, before)


@pytest.mark.parametrize("kind", KINDS)
def test_reads_after_revocation_return_no_values(baseline, harness, revoke, kind):
    approved = approval(harness.load_fact())
    session_id = harness.session_ids[-1]
    revoke[kind]()
    refused = 404 if kind == "membership" else 401
    for path in (
        f"/api/chat/answers/{approved['answer_id']}",
        f"/api/chat/answers/{approved['answer_id']}/export",
        f"/api/chat/evidence/{approved['receipt']['evidence_ids'][0]}",
    ):
        response = harness.client.get(path)
        assert response.status_code == refused, path
        assert "L1" not in response.text and "5 MW" not in response.text
    history = harness.client.get(f"/api/chat/history?session_id={session_id}")
    if kind == "disabled":
        assert history.status_code == 401
        return
    assert history.status_code == 200
    (turn,) = history.json()["turns"]
    assert turn["assurance_state"] == "unavailable"
    assert "5 MW" not in json.dumps(turn) and "receipt" not in turn
