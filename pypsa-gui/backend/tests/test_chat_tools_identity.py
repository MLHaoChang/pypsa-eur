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

import pytest
from fastapi import HTTPException

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
