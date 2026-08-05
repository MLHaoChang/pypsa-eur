"""
Step 0a — the chat tools act with an explicit identity, or not at all.

`services/chat_tools.py` calls `routers/projects.py` handlers directly, in
process. Those handlers now resolve every project inside the caller's org and
ACL-gate it, so the tool layer has to supply the same identity an HTTP caller
would. Before this it supplied none: the unresolved FastAPI `Depends` sentinel
reached `user.id` and the tool died with
`AttributeError: 'Depends' object has no attribute 'id'` — a 500, from what is
really an authorization gap.

The conftest binds the seeded user for the rest of the suite (the whole suite
runs as that user), so these tests unbind explicitly to prove the closed case
still exists underneath.
"""
from __future__ import annotations

import contextlib

import pytest
from fastapi import HTTPException

from db.models import User
from services import chat_tools


@pytest.fixture
def unbound_identity():
    """Drop the acting identity for one test, then restore it."""
    previous = chat_tools.acting_user_id()
    chat_tools.set_acting_user(None)
    yield
    chat_tools.set_acting_user(previous)


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda: chat_tools.list_projects(), id="list_projects"),
        pytest.param(lambda: chat_tools.load_project("anything"), id="load_project"),
        pytest.param(lambda: chat_tools.save_project("anything"), id="save_project"),
        pytest.param(
            lambda: chat_tools.delete_project("anything"), id="delete_project"
        ),
        pytest.param(
            lambda: chat_tools.activate_project("anything"), id="activate_project"
        ),
    ],
)
def test_project_tools_refuse_without_an_acting_user(unbound_identity, call):
    with pytest.raises(HTTPException) as excinfo:
        call()
    assert excinfo.value.status_code == 401
    assert excinfo.value.detail["error_kind"] == "no_acting_user"


@contextlib.contextmanager
def _account_status(session_local, user_id, status: str):
    """Force `user_id`'s account status for the block, then put it back."""
    db = session_local()
    try:
        previous = db.get(User, user_id).status
        db.get(User, user_id).status = status
        db.commit()
    finally:
        db.close()
    try:
        yield
    finally:
        db = session_local()
        try:
            db.get(User, user_id).status = previous
            db.commit()
        finally:
            db.close()


# P-2 — the HTTP path drops a non-active user at `resolve_session`
# (`services/auth_service.py:83`), so a disabled account cannot make a request
# at all. `_acting()` looked the user up by id and checked only `is None`, so
# the tool layer had no equivalent gate: an SSE stream outlives the request that
# opened it, and every tool it dispatches re-enters `_acting()` long after the
# session check that admitted it. Disabling an account mid-turn therefore left
# that account acting with full authority until the stream ended.
#
# `invited` matters as much as `disabled`: a seeded-but-never-activated account
# is exactly the state `login` refuses (`routers/auth.py:166`), and the tool
# layer must refuse it for the same reason.
@pytest.mark.parametrize("status", ["disabled", "invited", "suspended"])
@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda: chat_tools.list_projects(), id="list_projects"),
        pytest.param(lambda: chat_tools.load_project("anything"), id="load_project"),
        pytest.param(lambda: chat_tools.save_project("anything"), id="save_project"),
    ],
)
def test_project_tools_refuse_a_non_active_account(
    _auth_db, seeded_identity, status, call
):
    _engine, session_local = _auth_db
    with _account_status(session_local, seeded_identity["user_id"], status):
        with pytest.raises(HTTPException) as excinfo:
            call()
    assert excinfo.value.status_code == 401
    assert excinfo.value.detail["error_kind"] == "inactive_acting_user"
    # The message must not be the "reload and retry" copy — reloading cannot fix
    # a disabled account, and telling the user to try again is a dead end.
    assert "reload" not in excinfo.value.detail["message"].lower()


def test_an_active_account_is_still_admitted(_auth_db, seeded_identity):
    """
    The negative control for the test above. Without it, a `_acting()` that
    refused EVERY user would pass the whole parametrised set — the status gate
    has to reject `disabled` and admit `active`, not merely reject.
    """
    _engine, session_local = _auth_db
    with _account_status(session_local, seeded_identity["user_id"], "active"):
        assert isinstance(chat_tools.list_projects(), list)


def test_bound_identity_is_readable_and_clearable(seeded_identity):
    chat_tools.set_acting_user(seeded_identity["user_id"])
    assert chat_tools.acting_user_id() == str(seeded_identity["user_id"])
    chat_tools.set_acting_user(None)
    assert chat_tools.acting_user_id() is None


def test_tools_see_only_the_acting_user_org(client, install_network, second_identity):
    """
    The identity is not decoration: switching it switches whose projects the
    tool layer can see. `list_projects` is the cheapest probe — it goes through
    `project_acl.list_accessible_projects`, the same gate the HTTP route uses.
    """
    from tests.conftest import build_network

    install_network(build_network(), name="OrgOneProject")
    assert client.post(
        "/api/projects/OrgOneProject", params={"force": True, "rebind": True}
    ).status_code == 200

    assert "OrgOneProject" in {p["name"] for p in chat_tools.list_projects()}

    chat_tools.set_acting_user(second_identity["user_id"])
    assert "OrgOneProject" not in {p["name"] for p in chat_tools.list_projects()}
