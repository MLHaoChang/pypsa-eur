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
from tests import test_viewer_role_gate as gate
from tests.test_viewer_role_gate import _grant, _kind, _member, _org_of, _set_role

# The viewer-role gate suite's fixtures, shared as module attributes.
as_user = gate.as_user
baseline = gate.baseline
helpers_state = gate.helpers_state
world = gate.world


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


def test_get_members_reports_each_role_and_the_fixed_ones(
    world, client, seeded_identity
):
    admin = seeded_identity["user_id"]
    _grant(
        world["session_local"],
        world["project"],
        uuid.UUID(str(admin)),
        "viewer",
        by=admin,
    )
    _set_role(world["session_local"], world["project"], world["editor"], None)
    rows = {
        r["user_id"]: r
        for r in client.get(f"/api/projects/{world['project']}/members").json()
    }
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
    response = _put(
        client, world, [world["viewer"], world["editor"]], {uuid.uuid4(): "viewer"}
    )
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
    demoted = _put(
        client, world, [world["viewer"], world["editor"]], {world["editor"]: "viewer"}
    )
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


def test_my_access_is_edit_for_an_admin_with_a_viewer_row(
    world, client, seeded_identity
):
    admin = seeded_identity["user_id"]
    _grant(
        world["session_local"],
        world["project"],
        uuid.UUID(str(admin)),
        "viewer",
        by=admin,
    )
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
            db, actor, project, [world["viewer"], world["editor"]]
        )
    assert {r["user_id"]: r["role"] for r in rows} == {
        str(world["viewer"]): "viewer",
        str(world["editor"]): "editor",
    }


def test_role_is_fixed_for_an_org_admin_and_a_creator_only(world):
    from db.models import Project, User
    from services import project_acl

    other_admin = _member(world["session_local"], _org_of(world), org_role="admin")
    outsider = uuid.uuid4()
    with world["session_local"]() as db:
        db.add(
            User(
                id=outsider,
                email=f"lone-{outsider.hex}@example.com",
                password_hash="unused",
                status="active",
                is_super_admin=False,
                created_at=datetime.now(UTC),
            )
        )
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


def test_a_stale_editor_row_grants_nothing_after_leaving_the_org(
    world, seeded_identity
):
    # A user who left the org keeps no access through an old membership row.
    from db.models import OrgMembership, Project, User
    from services import project_acl

    with world["session_local"]() as db:
        db.query(OrgMembership).filter_by(user_id=world["editor"]).delete()
        db.commit()
        project = db.get(Project, uuid.UUID(world["project"]))
        assert (
            project_acl.project_role(db, db.get(User, world["editor"]), project) is None
        )


def test_a_new_row_is_written_with_its_role_not_left_to_the_column_default(
    world, client
):
    # NULL reads view-only, so the role must be in the INSERT itself (GK B3 V1).
    from sqlalchemy import event

    inserted = []

    def capture(mapper, connection, target):
        inserted.append((target.user_id, target.role))

    newcomer = _member(world["session_local"], _org_of(world))
    event.listen(ProjectMembership, "before_insert", capture)
    try:
        response = _put(client, world, [world["viewer"], world["editor"], newcomer])
    finally:
        event.remove(ProjectMembership, "before_insert", capture)
    assert response.status_code == 200, response.text
    assert inserted == [(newcomer, "editor")]


def test_the_same_user_twice_in_roles_is_400(world, client):
    viewer = str(world["viewer"])
    body = {
        "user_ids": [viewer, str(world["editor"])],
        "roles": {viewer: "editor", viewer.upper(): "viewer"},
    }
    response = client.put(f"/api/projects/{world['project']}/members", json=body)
    assert response.status_code == 400, response.text
    assert _stored(world, world["viewer"]) == "viewer"


def test_share_facts_follow_the_role(world, as_user):
    """`can_manage_members` and `owner_email` ride with `my_access` (05)."""
    viewer = as_user(world["viewer"]).get(f"/api/projects/{world['project']}")
    assert viewer.status_code == 200, viewer.text
    body = viewer.json()
    assert body["can_manage_members"] is False
    assert body["owner_email"]


def test_refusal_cause_sentence_matches_the_frontend_wording():
    """The viewer line the GUI builds and the refusal share one cause sentence."""
    import pathlib

    from services.project_write_gate import MESSAGE

    src = (
        pathlib.Path(__file__).resolve().parents[2]
        / "frontend/src/utils/viewerAccess.ts"
    ).read_text()
    assert MESSAGE.split(". ")[0] + "." in src
    assert MESSAGE == "You can view this project. Ask its owner for edit access."


def test_refusal_names_the_owner_like_the_banner():
    from services.project_write_gate import MESSAGE, refusal

    named = refusal("o@x.com").detail["message"]
    assert named == "You can view this project. Ask o@x.com for edit access."
    assert refusal().detail["message"] == MESSAGE


# ── 05 B1: can_manage_members and owner_email for every kind of caller ───────


def _facts(world, user_id, project_id=None):
    from db.models import Project, User
    from services import project_acl

    with world["session_local"]() as db:
        project = db.get(Project, uuid.UUID(str(project_id or world["project"])))
        return project_acl.access_fields(db, db.get(User, user_id), project)


def _add_child(world, creator_id):
    from datetime import UTC, datetime

    from db.models import Project

    child_id = uuid.uuid4()
    with world["session_local"]() as db:
        root = db.get(Project, uuid.UUID(str(world["project"])))
        db.add(
            Project(
                id=child_id,
                org_id=root.org_id,
                name=f"child-{child_id.hex[:8]}",
                created_by=creator_id,
                storage_path=f"child-{child_id.hex}",
                parent_project_id=root.id,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        db.commit()
    return child_id


def test_can_manage_members_for_every_role(world, seeded_identity):
    from db.models import Project

    admin = uuid.UUID(str(seeded_identity["user_id"]))
    # Org admin who created the root: may manage; viewer, editor may not.
    got = _facts(world, admin)
    assert got["can_manage_members"] is True and got["my_access"] == "edit"
    assert _facts(world, world["editor"])["can_manage_members"] is False
    assert _facts(world, world["viewer"])["can_manage_members"] is False

    # A non-admin who is the root creator may manage (the members route's rule).
    creator = _member(world["session_local"], _org_of(world))
    with world["session_local"]() as db:
        db.get(Project, uuid.UUID(str(world["project"]))).created_by = creator
        db.commit()
    got = _facts(world, creator)
    assert got["can_manage_members"] is True and got["my_access"] == "edit"
    # The admin is still an org admin, so still manages; a lineage creator
    # (made a child, not the root) edits but may not manage.
    assert _facts(world, admin)["can_manage_members"] is True
    lineage = _member(world["session_local"], _org_of(world))
    child = _add_child(world, lineage)
    got = _facts(world, lineage, child)
    assert got["my_access"] == "edit" and got["can_manage_members"] is False


def test_owner_email_is_the_root_creators_even_on_a_child(world, seeded_identity):
    from db.models import Project, User

    creator = _member(world["session_local"], _org_of(world))
    with world["session_local"]() as db:
        db.get(Project, uuid.UUID(str(world["project"]))).created_by = creator
        creator_email = db.get(User, creator).email
        db.commit()
    other = _member(world["session_local"], _org_of(world))
    child = _add_child(world, other)
    assert _facts(world, world["viewer"])["owner_email"] == creator_email
    assert _facts(world, world["viewer"], child)["owner_email"] == creator_email
