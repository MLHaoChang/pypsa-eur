from __future__ import annotations

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
        storage_path=str(storage_path_for(org.id, project_id)),
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


def test_storage_path_uses_configured_projects_root(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PROJECTS_ROOT", str(tmp_path / "tenant-projects"))
    get_settings.cache_clear()

    org_id = uuid.uuid4()
    project_id = uuid.uuid4()

    assert storage_path_for(org_id, project_id) == tmp_path / "tenant-projects" / str(org_id) / str(project_id)

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
