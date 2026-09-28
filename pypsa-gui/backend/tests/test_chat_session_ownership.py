"""
A chat session belongs to the user who created it.

`POST /api/chat/{session_id}/{confirm,rewind,abort}` declared no user dependency
and consulted no owner, org or project. `_SESSIONS` is a process-global dict and
`ChatSession` carried no owner field, so the only gate was the global `/api/*`
middleware — i.e. "be signed in as anyone". Verified before this fix: a user in a
DIFFERENT organization called `POST /api/chat/<victim>/rewind {"turns": 2}` and
got `200 {"dropped": 4}`, emptying the victim's conversation; `/abort` returned
200 with the victim's abort event set, killing an in-flight turn.

The session id is not a secret either: `GET /api/chat/history` returns
`last_session_id` read from the active project's `chat.jsonl`, so any co-member
who activates the project is handed it.

`/confirm` is the same family and the most serious of the three — it supplies the
approval for a destructive tool, which is the whole protection the safety tiers
give those tools. It was practically shielded by the token being a `uuid4`, which
is defence by accident rather than by design.

FAIL-CLOSED on purpose. An owner-less session is refused rather than allowed: if
a future creation path forgets to record the owner, the visible symptom is "I
cannot abort my own turn" — loud, immediate, and fixed in minutes — instead of a
silent reopening of this hole. `test_a_session_made_by_the_normal_path_records_an_owner`
is what keeps that from being a theoretical trade.

Found by an independent QA review, 2026-09-12. Server only: local mode has one
identity, so the owner always matches.
"""
import uuid as _uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import main
from services import chat_service
from tests.conftest import attach_session


def _other_org_user(auth_db):
    """A user in a DIFFERENT org — the strongest form of the attack."""
    from db.models import OrgMembership, Organization, User
    from services.auth_service import hash_password

    _engine, session_local = auth_db
    now = datetime.now(tz=timezone.utc)
    with session_local() as db:
        org = Organization(id=_uuid.uuid4(), name=f"Org-{_uuid.uuid4().hex[:6]}",
                           created_at=now)
        u = User(id=_uuid.uuid4(), email=f"out-{_uuid.uuid4().hex[:6]}@example.com",
                 password_hash=hash_password("irrelevant"), status="active",
                 is_super_admin=False, created_at=now)
        db.add_all([org, u])
        db.flush()
        db.add(OrgMembership(id=_uuid.uuid4(), user_id=u.id, org_id=org.id,
                            role="admin"))
        db.commit()
        return u.id, session_local


@pytest.fixture
def victim_session(seeded_identity):
    """A live session owned by the seeded user, with content to lose."""
    sid = f"victim-{_uuid.uuid4().hex[:8]}"
    sess = chat_service.get_or_create_session(
        sid, owner_user_id=str(seeded_identity["user_id"]),
    )
    sess.messages.clear()
    for i in range(4):
        sess.messages.append({"role": "user", "content": f"m{i}"})
    yield sess
    chat_service.drop_session(sid)


@pytest.fixture
def outsider(_auth_db):
    uid, session_local = _other_org_user(_auth_db)
    with TestClient(main.app) as c:
        yield attach_session(c, session_local, uid)


def test_an_outsider_cannot_rewind_someone_elses_session(outsider, victim_session):
    """
    The response must be INDISTINGUISHABLE from an unknown session, not a 404.
    `/rewind` and `/abort` deliberately never 404 — an unknown id returns a
    not-found-ish 200 so a double-click is harmless — so making a foreign session
    404 would both break that contract and confirm the session exists.
    """
    before = len(victim_session.messages)
    r = outsider.post(f"/api/chat/{victim_session.session_id}/rewind",
                      json={"turns": 2})
    unknown = outsider.post(f"/api/chat/definitely-no-such-session-{_uuid.uuid4().hex}/rewind",
                            json={"turns": 2})
    assert len(victim_session.messages) == before, (
        f"the outsider's rewind truncated the victim's conversation: {r.text[:200]}"
    )
    assert (r.status_code, r.json()) == (unknown.status_code, unknown.json()), (
        f"a foreign session is distinguishable from an unknown one: "
        f"{r.status_code} {r.text[:160]} vs {unknown.status_code} {unknown.text[:160]}"
    )


def test_an_outsider_cannot_abort_someone_elses_turn(outsider, victim_session):
    """Same contract as rewind: effect-free AND indistinguishable from unknown."""
    r = outsider.post(f"/api/chat/{victim_session.session_id}/abort")
    unknown = outsider.post(f"/api/chat/definitely-no-such-session-{_uuid.uuid4().hex}/abort")
    assert not victim_session.abort_event.is_set(), (
        f"the outsider's abort set the victim's abort event: {r.text[:200]}"
    )
    assert (r.status_code, r.json()) == (unknown.status_code, unknown.json()), (
        f"a foreign session is distinguishable from an unknown one: "
        f"{r.status_code} {r.text[:160]} vs {unknown.status_code} {unknown.text[:160]}"
    )


def test_an_outsider_cannot_confirm_someone_elses_destructive_tool(outsider,
                                                                   victim_session):
    """
    The most serious of the three: the confirmation IS the safety control for
    destructive tools, so supplying it for someone else's turn defeats the tier
    system regardless of how the token was obtained.
    """
    r = outsider.post(f"/api/chat/{victim_session.session_id}/confirm",
                      json={"token": _uuid.uuid4().hex, "decision": "approve"})
    assert r.status_code == 404, (
        f"an outsider reached another user's confirmation gate: "
        f"{r.status_code} {r.text[:200]}"
    )


def test_the_owner_can_still_rewind_and_abort(client, victim_session):
    """The guard must not lock a user out of their own session."""
    a = client.post(f"/api/chat/{victim_session.session_id}/abort")
    assert a.status_code == 200, f"owner refused on abort: {a.text[:200]}"
    r = client.post(f"/api/chat/{victim_session.session_id}/rewind",
                    json={"turns": 1})
    assert r.status_code == 200, f"owner refused on rewind: {r.text[:200]}"


def test_an_ownerless_session_is_refused_rather_than_shared(outsider):
    """
    Fail-closed. A session with no recorded owner must NOT be operable by an
    arbitrary caller — that is the pre-fix behaviour and the hole itself.
    """
    sid = f"ownerless-{_uuid.uuid4().hex[:8]}"
    chat_service.get_or_create_session(sid)  # no owner recorded
    try:
        r = outsider.post(f"/api/chat/{sid}/abort")
        assert r.json().get("ok") is not True, (
            f"an owner-less session was operable by any caller: {r.text[:200]}"
        )
    finally:
        chat_service.drop_session(sid)


def test_the_stream_path_records_an_owner(client, seeded_identity, monkeypatch):
    """
    Keeps fail-closed honest, and does it without depending on fixture state.

    Under fail-closed, a creation path that omits the owner locks every user out
    of their OWN session. The first version of this test went through
    GET /api/chat/history and SKIPPED when no session was minted — a guard that
    silently does not run is exactly the failure mode this branch keeps finding,
    so it is asserted on the /stream path by capturing what the creation call
    receives. The request is allowed to fail afterwards (no LLM is configured in
    the suite); the creation happens first, which is all this asserts.
    """
    captured: dict = {}
    real = chat_service.get_or_create_session

    def recording(session_id=None, **kwargs):
        captured.update(kwargs)
        return real(session_id, **kwargs)

    monkeypatch.setattr(chat_service, "get_or_create_session", recording)
    client.post("/api/chat/stream", json={"message": "hello",
                                          "session_id": f"own-{_uuid.uuid4().hex[:8]}"})

    assert "owner_user_id" in captured, (
        "POST /stream created a session without passing owner_user_id; under "
        "fail-closed that locks the real owner out of /abort and /rewind"
    )
    assert captured["owner_user_id"] == str(seeded_identity["user_id"]), (
        f"owner recorded as {captured['owner_user_id']!r}, expected the caller"
    )


def test_the_owner_is_never_reassigned_by_a_later_caller(seeded_identity):
    """
    An existing session's owner must not be overwritten. `get_or_create` is
    reached by both /stream and /history, so if the second caller could set the
    owner, anyone could adopt a live session just by naming its id — the hole
    this closes, reintroduced through the creation path.
    """
    sid = f"adopt-{_uuid.uuid4().hex[:8]}"
    first = chat_service.get_or_create_session(sid, owner_user_id="original-owner")
    assert first.owner_user_id == "original-owner"
    second = chat_service.get_or_create_session(sid, owner_user_id="attacker")
    assert second is first, "expected the same session object back"
    assert second.owner_user_id == "original-owner", (
        "a later caller reassigned the session owner"
    )
    chat_service.drop_session(sid)
