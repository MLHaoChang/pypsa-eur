"""A1.2: access revoked between evidence capture and publish, or before a read.

Two revocations: the user's org membership is removed (project ACL), or the
user is disabled. Values a tool returned while access held may already be on
the stream; nothing may be published, certified or read back after it.
"""

import contextlib
import json
import logging
import uuid

import pytest
from fastapi import HTTPException

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


def get_series():
    """Give L1 a p_set series, then read it (time-series evidence, issue 02)."""
    from services.pypsa_service import PyPSAService

    network = PyPSAService.get_active_context().network
    network.loads_t.p_set["L1"] = [5.0] * len(network.snapshots)
    return tool_turn(
        "get_timeseries",
        {"component": "loads", "name": "L1", "attribute": "p_set"},
        call_id="read-series",
    )


SERIES_DRAFT = json.dumps(
    {
        "schema_version": 1,
        "blocks": [
            {
                "id": "series-step",
                "kind": "observed",
                "entity_id": "L1",
                "metric": "p_set",
                "value": 5,
                "unit": "MW",
                "source": {
                    "tool_use_id": "read-series",
                    "path": "/L1/2026-01-01T00:00:00",
                },
            }
        ],
    }
)


def assert_nothing_published(frames, owner, before, kind="assurance_access_revoked"):
    assert not any(event == "answer_approved" for event, _ in frames)
    assert public_text(frames) == ""
    errors = [d for event, d in frames if event == "error"]
    assert [e["error_kind"] for e in errors] == [kind]
    assert "5 MW" not in json.dumps(errors) and "p_set" not in json.dumps(errors)
    done = [d for event, d in frames if event == "session_done"]
    assert done == [{"reason": kind}]
    assert answer_records(owner) == before


KINDS = ["membership", "disabled"]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "read, draft",
    [
        (get_component, observed_draft()),
        (list_loads, PAGE_DRAFT),
        (get_series, SERIES_DRAFT),
    ],
    ids=["get_component", "list_components", "get_timeseries"],
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


@pytest.mark.parametrize("removal", ["removed", "stuck"])
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "read, draft",
    [(get_component, observed_draft()), (list_loads, PAGE_DRAFT)],
    ids=["get_component", "list_components"],
)
def test_access_revoked_during_commit_publishes_nothing(
    baseline,
    harness,
    revoke,
    seeded_identity,
    monkeypatch,
    caplog,
    kind,
    read,
    draft,
    removal,
):
    """REVOKE-TOCTOU: revoked after commit's last ACL check, as the record lands.

    "stuck": the saved file cannot be removed; the refusal still ends the turn
    and load() keeps refusing the leftover record.
    """
    caplog.set_level(logging.DEBUG)
    owner = str(seeded_identity["user_id"])
    before = answer_records(owner)
    save, saved = records.save, []

    @contextlib.contextmanager
    def busy(directory):
        raise TimeoutError("assurance storage busy")
        yield

    def save_then_revoke(record, record_id):
        save(record, record_id)
        if record["record_kind"] == "answer":
            saved.append(record_id)
            revoke[kind]()
            if removal == "stuck":
                monkeypatch.setattr(records, "transaction", busy)

    monkeypatch.setattr(records, "save", save_then_revoke)
    turns = [read(), text_turn(draft), text_turn(draft)]
    response = harness.request(FakeProvider(turns))
    assert response.status_code == 200
    frames = parse_sse(response.text)
    # The answer reached disk; it must not stay (or, if stuck, stay unreadable).
    [answer_id] = saved
    if removal == "removed":
        assert answer_records(owner) == before
    else:
        assert answer_records(owner) == before | {answer_id}
        with pytest.raises(HTTPException) as refused:
            records.load(owner, answer_id, "answer")
        assert refused.value.status_code == {"membership": 404, "disabled": 401}[kind]
        stuck = [r for r in caplog.records if "could not be removed" in r.message]
        assert len(stuck) == 1 and answer_id in stuck[0].getMessage()
        records._path(owner, answer_id).unlink()
    assert_nothing_published(frames, owner, before)
    formatter = logging.Formatter()
    for record in caplog.records:
        logged = formatter.format(record)
        for value in ("5 MW", "5.0", "p_set", draft):
            assert value not in logged, (value, record.name)


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
        if kind == "membership":
            assert response.json() == {"detail": "Record not found"}, path
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
    # REVOKE-KIND: an expected refusal, logged once as a warning with no ids.
    failure = [r for r in caplog.records if r.name == "pypsa_gui.chat" and r.levelno >= logging.WARNING]
    assert [(r.levelname, r.getMessage()) for r in failure] == [
        ("WARNING", "checked answer withdrawn: project access was revoked")
    ]
    assert failure[0].exc_info is None
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


def in_commit_guard(monkeypatch, act):
    """A provider that runs `act` once commit takes the network guard.

    Armed when the model drafts its answer (turn 1), so the capture's own
    guard runs untouched; commit's evidence re-check then sees the change.
    """
    from harness import durable

    guard, armed = durable.network_guard, []

    @contextlib.contextmanager
    def acting_guard(*args, **kwargs):
        with guard(*args, **kwargs):
            if armed == [True]:
                armed.append("done")
                act()
            yield

    monkeypatch.setattr(durable, "network_guard", acting_guard)
    draft = observed_draft()
    return RevokingProvider(
        [get_component(), text_turn(draft), text_turn(draft)], lambda: armed.append(True)
    )


def resolve_refusing(status):
    def resolve(db, user, project_id, **kwargs):
        raise HTTPException(status, "refused")

    return resolve


@pytest.mark.parametrize("status", [409, 500])
def test_other_project_refusals_in_commit_stay_publication_failures(
    baseline, harness, seeded_identity, monkeypatch, caplog, status
):
    """REVOKE-KIND keeps its mapping narrow: only access refusals are revocations."""
    caplog.set_level(logging.DEBUG)
    owner = str(seeded_identity["user_id"])
    before = answer_records(owner)
    response = harness.request(in_commit_guard(monkeypatch, lambda: monkeypatch.setattr(
        records.project_registry, "resolve_project", resolve_refusing(status))))
    assert_nothing_published(parse_sse(response.text), owner, before, "assurance_publication_failed")
    failure = [r for r in caplog.records if "checked answer could not be committed" in r.message]
    assert len(failure) == 1 and failure[0].levelname == "ERROR"


def test_unavailable_evidence_in_commit_stays_a_publication_failure(
    baseline, harness, seeded_identity, monkeypatch
):
    owner = str(seeded_identity["user_id"])
    before = answer_records(owner)

    def gone(*args, **kwargs):
        records.unavailable("evidence_unavailable")

    response = harness.request(
        in_commit_guard(monkeypatch, lambda: monkeypatch.setattr(records, "evidence", gone)))
    assert_nothing_published(parse_sse(response.text), owner, before, "assurance_publication_failed")


@pytest.mark.parametrize("kind", KINDS)
def test_revocation_seen_by_the_evidence_recheck_in_commit_is_typed(
    baseline, harness, revoke, seeded_identity, monkeypatch, kind
):
    owner = str(seeded_identity["user_id"])
    before = answer_records(owner)
    response = harness.request(in_commit_guard(monkeypatch, revoke[kind]))
    assert_nothing_published(parse_sse(response.text), owner, before)

@pytest.mark.parametrize("status", [409, 500])
def test_authorize_passes_other_project_refusals_through(seeded_identity, monkeypatch, status):
    monkeypatch.setattr(records.project_registry, "resolve_project", resolve_refusing(status))
    with pytest.raises(HTTPException) as refused:
        records.authorize(str(seeded_identity["user_id"]), "Demo")
    assert refused.value.status_code == status
    assert not isinstance(refused.value, records.AccessRevoked)


@pytest.mark.parametrize("status", [403, 404])
def test_authorize_marks_project_access_refusals(seeded_identity, monkeypatch, status):
    def resolve(db, user, project_id):
        raise HTTPException(status, "Project not found")

    monkeypatch.setattr(records.project_registry, "resolve_project", resolve)
    with pytest.raises(records.AccessRevoked) as refused:
        records.authorize(str(seeded_identity["user_id"]), "Demo")
    assert (refused.value.status_code, refused.value.detail) == (status, "Project not found")
