# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Project viewer role, PR 2a: setting a role and reporting the caller's access.

The members PUT sets a role per member (GK B R5, GK B3 V1/V2), a demotion
takes effect on the next request (V3), and the project list, GET and activate
report ``my_access`` from ``project_acl.project_role`` (V4).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from db.models import ProjectMembership
from services.project_write_gate import KIND
from tests.test_viewer_role_gate import (  # noqa: F401  (fixtures)
    _grant,
    _kind,
    _member,
    _org_of,
    _set_role,
    as_user,
    baseline,
    helpers_state,
    world,
)


def _stored(world, user_id):
    with world["session_local"]() as db:
        row = (
            db.query(ProjectMembership)
            .filter_by(project_id=uuid.UUID(world["project"]), user_id=user_id)
            .one_or_none()
        )
        return None if row is None else row.role


def _put(client, world, user_ids, roles=None):
    body = {"user_ids": [str(u) for u in user_ids]}
    if roles is not None:
        body["roles"] = {str(u): r for u, r in roles.items()}
    return client.put(f"/api/projects/{world['project']}/members", json=body)


# ── V1: the role is written explicitly ──────────────────────────────────────


def test_a_new_member_is_an_editor_unless_the_body_names_a_role(world, client):
    newcomer = _member(world["session_local"], _org_of(world))
    named = _member(world["session_local"], _org_of(world))
    members = [world["viewer"], world["editor"], newcomer, named]
    response = _put(client, world, members, {named: "viewer"})
    assert response.status_code == 200, response.text
    assert _stored(world, newcomer) == "editor"
    assert _stored(world, named) == "viewer"
    rows = {r["user_id"]: r for r in response.json()}
    assert rows[str(newcomer)]["role"] == "editor"
    assert rows[str(named)]["role"] == "viewer"
    assert rows[str(named)]["role_fixed"] is False


def test_an_existing_member_with_no_roles_entry_keeps_the_stored_role(world, client):
    response = _put(client, world, [world["viewer"], world["editor"]])
    assert response.status_code == 200, response.text
    assert _stored(world, world["viewer"]) == "viewer"
    assert _stored(world, world["editor"]) == "editor"


def test_promote_and_demote_in_one_put(world, client):
    response = _put(
        client,
        world,
        [world["viewer"], world["editor"]],
        {world["viewer"]: "editor", world["editor"]: "viewer"},
    )
    assert response.status_code == 200, response.text
    assert _stored(world, world["viewer"]) == "editor"
    assert _stored(world, world["editor"]) == "viewer"


def test_get_members_reports_each_role_and_the_fixed_ones(world, client, seeded_identity):
    admin = seeded_identity["user_id"]
    _grant(world["session_local"], world["project"], uuid.UUID(str(admin)), "viewer", by=admin)
    _set_role(world["session_local"], world["project"], world["editor"], None)
    rows = {r["user_id"]: r for r in client.get(f"/api/projects/{world['project']}/members").json()}
    assert rows[str(world["viewer"])]["role"] == "viewer"
    # A NULL row reads view-only, as `project_role` does.
    assert rows[str(world["editor"])]["role"] == "viewer"
    # The admin edits whatever its row says, and the dialog shows it fixed.
    assert rows[str(admin)]["role"] == "editor"
    assert rows[str(admin)]["role_fixed"] is True


# ── V2: roles are validated, never silently ignored ─────────────────────────


def test_an_unknown_role_value_is_422(world, client):
    response = _put(client, world, [world["viewer"]], {world["viewer"]: "owner"})
    assert response.status_code == 422, response.text
    assert _stored(world, world["viewer"]) == "viewer"


def test_a_roles_key_outside_user_ids_is_400(world, client):
    response = _put(client, world, [world["viewer"], world["editor"]], {uuid.uuid4(): "viewer"})
    assert response.status_code == 400, response.text


def test_a_roles_key_that_is_not_a_uuid_is_400(world, client):
    body = {"user_ids": [str(world["viewer"])], "roles": {"not-a-uuid": "viewer"}}
    response = client.put(f"/api/projects/{world['project']}/members", json=body)
    assert response.status_code == 400, response.text
    assert _stored(world, world["editor"]) == "editor"


# ── R5: who may set a role ──────────────────────────────────────────────────


def test_nobody_changes_their_own_role(world, client, seeded_identity):
    admin = seeded_identity["user_id"]
    members = [world["viewer"], world["editor"], admin]
    refused = _put(client, world, members, {admin: "viewer"})
    assert refused.status_code == 400, refused.text
    assert "own role" in refused.json()["detail"]
    assert _stored(world, uuid.UUID(str(admin))) is None
    # Restating the role one has (a new row defaults to editor) is not a change.
    assert _put(client, world, members, {admin: "editor"}).status_code == 200
    assert _put(client, world, members, {admin: "editor"}).status_code == 200


def test_a_member_who_cannot_manage_sets_no_role(world, as_user):
    editor = as_user(world["editor"])
    response = _put(
        editor, world, [world["viewer"], world["editor"]], {world["viewer"]: "editor"}
    )
    assert response.status_code == 403, response.text
    assert _stored(world, world["viewer"]) == "viewer"


# ── V3: a demotion takes effect on the next request ─────────────────────────


def test_a_demoted_editor_is_refused_on_the_next_write(world, client, as_user):
    editor = as_user(world["editor"])
    assert editor.post(f"/api/projects/{world['project']}/activate").status_code == 200
    before = editor.patch(f"/api/projects/{world['project']}/settings", json={})
    assert _kind(before) != KIND, before.text
    demoted = _put(client, world, [world["viewer"], world["editor"]], {world["editor"]: "viewer"})
    assert demoted.status_code == 200, demoted.text
    after = editor.patch(f"/api/projects/{world['project']}/settings", json={})
    assert after.status_code == 403 and _kind(after) == KIND, after.text


# ── V4: my_access on the list, GET and activate ─────────────────────────────


def _access(client, world) -> tuple:
    listed = {p["id"]: p for p in client.get("/api/projects/").json()}
    activated = client.post(f"/api/projects/{world['project']}/activate")
    assert activated.status_code == 200, activated.text
    loaded = client.get(f"/api/projects/{world['project']}")
    assert loaded.status_code == 200, loaded.text
    return (
        listed[world["project"]]["my_access"],
        activated.json()["my_access"],
        loaded.json()["my_access"],
    )


@pytest.mark.parametrize(
    "who, stored, expected",
    [
        ("editor", "editor", "edit"),
        ("viewer", "viewer", "view"),
        ("viewer", None, "view"),
        ("viewer", "Editor", "view"),
    ],
)
def test_my_access_follows_the_stored_role(world, as_user, who, stored, expected):
    _set_role(world["session_local"], world["project"], world[who], stored)
    assert _access(as_user(world[who]), world) == (expected,) * 3


def test_my_access_is_edit_for_an_admin_with_a_viewer_row(world, client, seeded_identity):
    admin = seeded_identity["user_id"]
    _grant(world["session_local"], world["project"], uuid.UUID(str(admin)), "viewer", by=admin)
    assert _access(client, world) == ("edit",) * 3


def test_my_access_is_edit_for_a_creator_with_a_viewer_row(world, as_user):
    # A non-admin member who created the root edits it whatever the row says.
    from db.models import Project

    with world["session_local"]() as db:
        db.get(Project, uuid.UUID(world["project"])).created_by = world["viewer"]
        db.commit()
    assert _access(as_user(world["viewer"]), world) == ("edit",) * 3


# ── mutant kills: the defaults and who reads as fixed ───────────────────────


def test_set_root_members_without_roles_keeps_every_stored_role(world, seeded_identity):
    from db.models import Project, User
    from services import project_registry

    with world["session_local"]() as db:
        actor = db.get(User, uuid.UUID(str(seeded_identity["user_id"])))
        project = db.get(Project, uuid.UUID(world["project"]))
        rows = project_registry.set_root_members(
            db, actor, project, [world["viewer"], world["editor"]])
    assert {r["user_id"]: r["role"] for r in rows} == {
        str(world["viewer"]): "viewer", str(world["editor"]): "editor"}


def test_role_is_fixed_for_an_org_admin_and_a_creator_only(world):
    from db.models import Project, User
    from services import project_acl

    other_admin = _member(world["session_local"], _org_of(world), org_role="admin")
    outsider = uuid.uuid4()
    with world["session_local"]() as db:
        db.add(User(id=outsider, email=f"lone-{outsider.hex}@example.com", password_hash="unused",
                    status="active", is_super_admin=False, created_at=datetime.now(UTC)))
        db.commit()
    with world["session_local"]() as db:
        project = db.get(Project, uuid.UUID(world["project"]))

        def fixed(uid):
            return project_acl.role_is_fixed(db, db.get(User, uid), project)

        assert fixed(other_admin) is True
        assert fixed(world["viewer"]) is False
        assert fixed(outsider) is False
        project.created_by = world["viewer"]
        db.flush()
        assert fixed(world["viewer"]) is True
