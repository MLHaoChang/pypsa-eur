from __future__ import annotations

import pathlib
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from db.models import Organization, OrgMembership, Project, ProjectMembership, User
from services.project_acl import (
    can_access_project,
    can_delete_project,
    can_manage_membership,
    ensure_project_access,
    list_accessible_projects,
    resolve_tree_root,
)
from services.storage_paths import storage_path_for
from settings import get_settings


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


@pytest.fixture(name="db")
def db_fixture(db_session):
    return db_session


def _create_org(db, *, name: str | None = None) -> Organization:
    organization = Organization(
        name=name or f"Org {uuid.uuid4()}",
        created_at=_now_utc(),
    )
    db.add(organization)
    db.commit()
    db.refresh(organization)
    return organization


def _create_user(
    db,
    *,
    email: str | None = None,
    status: str = "active",
    is_super_admin: bool = False,
) -> User:
    user = User(
        email=email or f"{uuid.uuid4()}@example.com",
        password_hash=None,
        status=status,
        is_super_admin=is_super_admin,
        created_at=_now_utc(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _add_org_membership(db, *, user: User, org: Organization, role: str) -> OrgMembership:
    membership = OrgMembership(user_id=user.id, org_id=org.id, role=role)
    db.add(membership)
    db.commit()
    db.refresh(membership)
    return membership


def _create_project(
    db,
    *,
    org: Organization,
    creator: User,
    name: str,
    parent: Project | None = None,
) -> Project:
    project_id = uuid.uuid4()
    project = Project(
        id=project_id,
        org_id=org.id,
        name=name,
        created_by=creator.id,
        # Phase 1b: relative to `projects_root`, and never materialised — this
        # module only exercises the ACL, so nothing here reads the disk.
        storage_path=str(
            storage_path_for(org.id, project_id, name, taken=set(), org_segment=True)
        ),
        parent_project_id=parent.id if parent is not None else None,
        scenario_description=None,
        created_at=_now_utc(),
        updated_at=_now_utc(),
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def _assign(
    db,
    *,
    project: Project,
    user: User,
    assigned_by: User | None = None,
) -> ProjectMembership:
    membership = ProjectMembership(
        project_id=project.id,
        user_id=user.id,
        assigned_by=assigned_by.id if assigned_by is not None else project.created_by,
        assigned_at=_now_utc(),
    )
    db.add(membership)
    db.commit()
    db.refresh(membership)
    return membership


def test_storage_path_is_relative_and_rejoins_the_configured_root(tmp_path, monkeypatch) -> None:
    """
    Phase 1b (E2) inverted this. `storage_path_for` no longer returns an
    absolute path — it returns one relative to `projects_root`, and
    `project_registry.project_dir` is what rejoins them. An absolute value in
    the row bakes one machine's home directory into the database.
    """
    from db.models import Project as _Project
    from services.project_registry import project_dir

    monkeypatch.setenv("PROJECTS_ROOT", str(tmp_path / "tenant-projects"))
    get_settings.cache_clear()

    org_id = uuid.uuid4()
    project_id = uuid.uuid4()

    relative = storage_path_for(
        org_id, project_id, "Belgium Grid", taken=set(), org_segment=True
    )
    assert not relative.is_absolute()
    assert relative == pathlib.Path(str(org_id)) / "Belgium Grid"

    row = _Project(id=project_id, org_id=org_id, name="Belgium Grid",
                   storage_path=str(relative))
    assert project_dir(row) == tmp_path / "tenant-projects" / str(org_id) / "Belgium Grid"

    get_settings.cache_clear()


def test_resolve_tree_root_returns_topmost_ancestor(db) -> None:
    org = _create_org(db, name="Org")
    admin = _create_user(db, email="admin@example.com")
    _add_org_membership(db, user=admin, org=org, role="admin")
    root = _create_project(db, org=org, creator=admin, name="Root")
    child = _create_project(db, org=org, creator=admin, name="Child", parent=root)
    grandchild = _create_project(db, org=org, creator=admin, name="Grandchild", parent=child)

    assert resolve_tree_root(db, grandchild).id == root.id


def test_member_assigned_to_root_can_open_nested_scenario(db) -> None:
    org = _create_org(db, name="Org")
    admin = _create_user(db, email="admin@example.com")
    member = _create_user(db, email="member@example.com")
    _add_org_membership(db, user=admin, org=org, role="admin")
    _add_org_membership(db, user=member, org=org, role="member")
    root = _create_project(db, org=org, creator=admin, name="Root")
    child = _create_project(db, org=org, creator=admin, name="Child", parent=root)
    grandchild = _create_project(db, org=org, creator=admin, name="Grandchild", parent=child)
    _assign(db, project=root, user=member, assigned_by=admin)

    assert can_access_project(db, member, grandchild) is True


def test_scenario_creator_on_ancestor_can_access_descendants(db) -> None:
    org = _create_org(db, name="Org")
    admin = _create_user(db, email="admin@example.com")
    member = _create_user(db, email="member@example.com")
    _add_org_membership(db, user=admin, org=org, role="admin")
    _add_org_membership(db, user=member, org=org, role="member")
    root = _create_project(db, org=org, creator=admin, name="Root")
    child = _create_project(db, org=org, creator=member, name="Child", parent=root)
    grandchild = _create_project(db, org=org, creator=admin, name="Grandchild", parent=child)

    assert can_access_project(db, member, grandchild) is True


def test_other_org_user_gets_no_access(db) -> None:
    org_a = _create_org(db, name="Org A")
    org_b = _create_org(db, name="Org B")
    admin_a = _create_user(db, email="admin-a@example.com")
    user_b = _create_user(db, email="user-b@example.com")
    _add_org_membership(db, user=admin_a, org=org_a, role="admin")
    _add_org_membership(db, user=user_b, org=org_b, role="member")
    root_a = _create_project(db, org=org_a, creator=admin_a, name="Root")

    assert can_access_project(db, user_b, root_a) is False


def test_list_roots_only_hides_scenarios(db) -> None:
    org = _create_org(db, name="Org")
    admin = _create_user(db, email="admin@example.com")
    member = _create_user(db, email="member@example.com")
    _add_org_membership(db, user=admin, org=org, role="admin")
    _add_org_membership(db, user=member, org=org, role="member")
    root = _create_project(db, org=org, creator=admin, name="Root")
    _create_project(db, org=org, creator=admin, name="Child", parent=root)
    _assign(db, project=root, user=member, assigned_by=admin)

    names = {project.name for project in list_accessible_projects(db, member, roots_only=True)}

    assert names == {"Root"}


def test_root_creator_and_org_admin_can_manage_membership_on_descendants(db) -> None:
    org = _create_org(db, name="Org")
    root_creator = _create_user(db, email="creator@example.com")
    org_admin = _create_user(db, email="admin@example.com")
    member = _create_user(db, email="member@example.com")
    _add_org_membership(db, user=root_creator, org=org, role="member")
    _add_org_membership(db, user=org_admin, org=org, role="admin")
    _add_org_membership(db, user=member, org=org, role="member")
    root = _create_project(db, org=org, creator=root_creator, name="Root")
    child = _create_project(db, org=org, creator=member, name="Child", parent=root)

    assert can_manage_membership(db, root_creator, child) is True
    assert can_manage_membership(db, org_admin, child) is True
    assert can_manage_membership(db, member, child) is False


def test_delete_rules_differ_for_root_and_scenario(db) -> None:
    org = _create_org(db, name="Org")
    root_creator = _create_user(db, email="root@example.com")
    org_admin = _create_user(db, email="admin@example.com")
    scenario_creator = _create_user(db, email="scenario@example.com")
    plain_member = _create_user(db, email="member@example.com")
    _add_org_membership(db, user=root_creator, org=org, role="member")
    _add_org_membership(db, user=org_admin, org=org, role="admin")
    _add_org_membership(db, user=scenario_creator, org=org, role="member")
    _add_org_membership(db, user=plain_member, org=org, role="member")
    root = _create_project(db, org=org, creator=root_creator, name="Root")
    child = _create_project(db, org=org, creator=scenario_creator, name="Child", parent=root)

    assert can_delete_project(db, root_creator, root) is True
    assert can_delete_project(db, org_admin, root) is True
    assert can_delete_project(db, scenario_creator, root) is False
    assert can_delete_project(db, scenario_creator, child) is True
    assert can_delete_project(db, plain_member, child) is False


def test_ensure_project_access_raises_404_for_denied_user(db) -> None:
    org_a = _create_org(db, name="Org A")
    org_b = _create_org(db, name="Org B")
    admin_a = _create_user(db, email="admin-a@example.com")
    user_b = _create_user(db, email="user-b@example.com")
    _add_org_membership(db, user=admin_a, org=org_a, role="admin")
    _add_org_membership(db, user=user_b, org=org_b, role="member")
    root_a = _create_project(db, org=org_a, creator=admin_a, name="Root")

    with pytest.raises(HTTPException) as exc:
        ensure_project_access(db, user_b, root_a)

    assert exc.value.status_code == 404


# ── accessible_project_ids: the batch resolver must not drift ────────────────
# `routers/solve_queue.py:list_queue` is polled every 1.5s while a solve is
# active, so it cannot afford `can_access_project` per job. It calls
# `accessible_project_ids`, which answers the same question from three queries.
# "Same question" is only true if it is tested, so these do exactly that.


def _acl_matrix(db):
    """
    One org covering every grant `can_access_project` recognises.

    Returns (org, {label: user}, [projects]) — a two-level tree plus a sibling
    root, so lineage-inherited access and root-membership access are distinct.
    """
    org = _create_org(db, name="Matrix Org")
    users = {
        "admin": _create_user(db, email="m-admin@example.com"),
        "root_creator": _create_user(db, email="m-root@example.com"),
        "scenario_creator": _create_user(db, email="m-scenario@example.com"),
        "assigned": _create_user(db, email="m-assigned@example.com"),
        "stranger": _create_user(db, email="m-stranger@example.com"),
    }
    _add_org_membership(db, user=users["admin"], org=org, role="admin")
    for label in ("root_creator", "scenario_creator", "assigned", "stranger"):
        _add_org_membership(db, user=users[label], org=org, role="member")

    root = _create_project(db, org=org, creator=users["root_creator"], name="Root")
    child = _create_project(
        db, org=org, creator=users["scenario_creator"], name="Child", parent=root
    )
    grandchild = _create_project(
        db, org=org, creator=users["root_creator"], name="Grandchild", parent=child
    )
    other_root = _create_project(
        db, org=org, creator=users["scenario_creator"], name="Other", parent=None
    )
    _assign(db, project=root, user=users["assigned"], assigned_by=users["root_creator"])
    return org, users, [root, child, grandchild, other_root]


@pytest.mark.parametrize(
    "label",
    ["admin", "root_creator", "scenario_creator", "assigned", "stranger"],
)
def test_accessible_project_ids_agrees_with_can_access_project(db, label) -> None:
    from services.project_acl import accessible_project_ids

    _org, users, projects = _acl_matrix(db)
    user = users[label]

    expected = {p.id for p in projects if can_access_project(db, user, p)}
    actual = accessible_project_ids(db, user, [p.id for p in projects])

    assert actual == expected, (
        f"{label}: batch resolver disagrees with can_access_project "
        f"(missing {expected - actual}, extra {actual - expected})"
    )


def test_accessible_project_ids_never_leaves_the_callers_org(db) -> None:
    from services.project_acl import accessible_project_ids

    _org, users, projects = _acl_matrix(db)
    other_org = _create_org(db, name="Elsewhere")
    outsider = _create_user(db, email="outsider@example.com")
    _add_org_membership(db, user=outsider, org=other_org, role="admin")
    foreign = _create_project(db, org=other_org, creator=outsider, name="Foreign")

    # An org admin asking about a project in ANOTHER org gets nothing back,
    # even though `role == "admin"` short-circuits inside their own org.
    assert accessible_project_ids(db, users["admin"], [foreign.id]) == set()
    # …and the outsider learns nothing about this org's projects.
    assert accessible_project_ids(db, outsider, [p.id for p in projects]) == set()


def test_accessible_project_ids_returns_nothing_without_a_membership(db) -> None:
    from services.project_acl import accessible_project_ids

    _org, _users, projects = _acl_matrix(db)
    orphan = _create_user(db, email="no-org@example.com")

    assert accessible_project_ids(db, orphan, [p.id for p in projects]) == set()


def test_accessible_project_ids_ignores_ids_that_are_not_projects(db) -> None:
    from services.project_acl import accessible_project_ids

    _org, users, projects = _acl_matrix(db)
    deleted = uuid.uuid4()

    result = accessible_project_ids(
        db, users["admin"], [p.id for p in projects] + [deleted]
    )
    assert deleted not in result
    assert result == {p.id for p in projects}
    assert accessible_project_ids(db, users["admin"], []) == set()


def test_accessible_project_ids_survives_a_cyclic_parent_chain(db) -> None:
    """
    `resolve_tree_root` raises ValueError on a cycle, which would 500 a polled
    listing. The batch resolver stops at the repeat and fails the entry closed.
    """
    from services.project_acl import accessible_project_ids

    _org, users, projects = _acl_matrix(db)
    root, child = projects[0], projects[1]
    root.parent_project_id = child.id  # root -> child -> root
    db.commit()

    stranger = users["stranger"]
    assert accessible_project_ids(db, stranger, [root.id, child.id]) == set()
    # The creator still gets in — the lineage check hits before the walk loops.
    assert root.id in accessible_project_ids(db, users["root_creator"], [root.id])
