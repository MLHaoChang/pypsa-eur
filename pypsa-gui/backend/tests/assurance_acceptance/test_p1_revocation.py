"""A1.2: access revoked between evidence capture and publish, or before a read.

Two revocations: the user's org membership is removed (project ACL), or the
user is disabled. Values a tool returned while access held may already be on
the stream; nothing may be published, certified or read back after it.
"""

import json
import logging
import uuid

import pytest

import app_paths
from db.models import OrgMembership, User
from harness.providers.fake import FakeProvider
from services import assurance_records as records
from tests.assurance_acceptance.test_p1_evidence import page_draft
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
    """Revocations of the seeded user's access; teardown restores and checks it.

    The identity is session-scoped, so a missed restore would leak into every
    later test module.
    """
    _engine, session_local = _auth_db
    user_id = uuid.UUID(str(seeded_identity["user_id"]))
    membership_of = {"user_id": user_id, "org_id": seeded_identity["org_id"]}
    removed = []  # (id, role) of the deleted OrgMembership, its only columns

    def membership():
        with session_local() as db:
            row = db.query(OrgMembership).filter_by(**membership_of).one()
            removed.append((row.id, row.role))
            db.delete(row)
            db.commit()

    def disable():
        with session_local() as db:
            db.get(User, user_id).status = "disabled"
            db.commit()

    yield {"membership": membership, "disabled": disable}
    with session_local() as db:
        for row_id, role in removed:
            db.add(OrgMembership(id=row_id, role=role, **membership_of))
        db.get(User, user_id).status = "active"
        db.commit()
    with session_local() as db:
        assert db.get(User, user_id).status == "active"
        assert db.query(OrgMembership).filter_by(**membership_of).one().role == "admin"


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


PAGE_DRAFT = page_draft()


def assert_nothing_published(frames, owner, before):
    assert not any(event == "answer_approved" for event, _ in frames)
    assert public_text(frames) == ""
    errors = [d for event, d in frames if event == "error"]
    assert [e["error_kind"] for e in errors] == ["assurance_publication_failed"]
    assert "5 MW" not in json.dumps(errors) and "p_set" not in json.dumps(errors)
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
@pytest.mark.parametrize(
    "read, draft",
    [(get_component, observed_draft()), (list_loads, PAGE_DRAFT)],
    ids=["get_component", "list_components"],
)
def test_access_revoked_during_commit_publishes_nothing(
    baseline, harness, revoke, seeded_identity, monkeypatch, kind, read, draft
):
    """REVOKE-TOCTOU: revoked after commit's last ACL check, as the record lands."""
    owner = str(seeded_identity["user_id"])
    before = answer_records(owner)
    save = records.save

    def save_then_revoke(record, record_id):
        save(record, record_id)
        if record["record_kind"] == "answer":
            revoke[kind]()

    monkeypatch.setattr(records, "save", save_then_revoke)
    turns = [read(), text_turn(draft), text_turn(draft)]
    response = harness.request(FakeProvider(turns))
    assert response.status_code == 200
    assert_nothing_published(parse_sse(response.text), owner, before)


@pytest.mark.parametrize("kind", KINDS)
def test_tool_called_after_revocation_returns_no_values(
    baseline, harness, revoke, seeded_identity, kind
):
    owner = str(seeded_identity["user_id"])
    draft = observed_draft()
    turns = [
        get_component(),
        get_component("again"),
        text_turn(draft),
        text_turn(draft),
    ]
    # Positive control: without revocation the same second call returns values.
    control = parse_sse(harness.request(RevokingProvider(turns, lambda: None)).text)
    assert [d["tool_use_id"] for event, d in control if event == "tool_result"] == [
        "read-load",
        "again",
    ]
    before = answer_records(owner)
    response = harness.request(RevokingProvider(turns, revoke[kind]))
    frames = parse_sse(response.text)
    late = [d for _, d in frames if d.get("tool_use_id") == "again"]
    assert late and not any("result" in d for d in late)
    refused = [d for event, d in frames if event == "tool_error"]
    assert [d["tool_use_id"] for d in refused] == ["again"]
    assert "p_set" not in json.dumps(late) and "5.0" not in json.dumps(late)
    assert_nothing_published(frames, owner, before)


READ_PATHS = (
    "/api/chat/answers/{answer}",
    "/api/chat/answers/{answer}/export",
    "/api/chat/evidence/{evidence}",
)


def read_paths(answer, evidence):
    return [p.format(answer=answer, evidence=evidence) for p in READ_PATHS]


@pytest.mark.parametrize("kind", KINDS)
def test_reads_after_revocation_look_like_unknown_records(
    baseline, harness, revoke, kind
):
    approved = approval(harness.load_fact())
    session_id = harness.session_ids[-1]
    owned = read_paths(approved["answer_id"], approved["receipt"]["evidence_ids"][0])
    unknown = read_paths(uuid.uuid4(), uuid.uuid4())
    # Positive control: the owner reads all three before revocation.
    assert [harness.client.get(p).status_code for p in owned] == [200, 200, 200]
    revoke[kind]()
    # A disabled user is refused by global auth before any record lookup;
    # removed membership reaches records.load and its project check.
    refused = {"membership": 404, "disabled": 401}[kind]
    for path, missing in zip(owned, unknown):
        response, absent = harness.client.get(path), harness.client.get(missing)
        assert response.status_code == refused, path
        # A revoked record is indistinguishable from one that never existed.
        assert (absent.status_code, absent.text) == (refused, response.text), path
        assert "L1" not in response.text and "5 MW" not in response.text
    history = harness.client.get(f"/api/chat/history?session_id={session_id}")
    if kind == "disabled":
        assert history.status_code == 401
        return
    assert history.status_code == 200
    (turn,) = history.json()["turns"]
    assert turn["assurance_state"] == "unavailable"
    assert "5 MW" not in json.dumps(turn) and "receipt" not in turn


@pytest.mark.parametrize("kind", KINDS)
def test_revocation_logs_carry_no_values(
    baseline, harness, revoke, seeded_identity, caplog, kind
):
    caplog.set_level(logging.DEBUG)
    draft = observed_draft()
    turns = [get_component(), text_turn(draft), text_turn(draft)]
    response = harness.request(RevokingProvider(turns, revoke[kind]))
    assert not any(e == "answer_approved" for e, _ in parse_sse(response.text))
    failure = [
        r
        for r in caplog.records
        if "checked answer could not be committed" in r.message
    ]
    assert len(failure) == 1, "the revocation path no longer logs its failure"
    formatter = logging.Formatter()
    for record in caplog.records:
        logged = formatter.format(record)
        for value in ("5 MW", "5.0", "p_set", draft):
            assert value not in logged, (value, record.name)


def test_revocations_are_undone_after_each_test(_auth_db, seeded_identity):
    # Runs last in this module: every revocation above was restored.
    _engine, session_local = _auth_db
    with session_local() as db:
        user = db.get(User, uuid.UUID(str(seeded_identity["user_id"])))
        assert user.status == "active"
        assert (
            db.query(OrgMembership)
            .filter_by(user_id=user.id, org_id=seeded_identity["org_id"])
            .one()
            .role
            == "admin"
        )
