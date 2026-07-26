"""
Auth-mode (multi-user tenancy) integration tests for the projects router.

These exercise the DB-registry + tree-aware ACL + org-scoped storage wiring
added in Task 7. They run with ``PYPSA_GUI_AUTH_ENABLED=true`` and a temp
``PROJECTS_ROOT`` so nothing touches the operator's real projects. The legacy
(auth-disabled) project tests continue to pass unchanged because the router
only takes the DB path when auth is enabled AND a user is resolved.
"""
from __future__ import annotations

import contextlib
import uuid
from datetime import datetime, timezone

import pandas as pd
import pypsa
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

import main
from db import session as db_session_module
from db.models import Organization, OrgMembership, Project, ProjectMembership, User
from services.auth_service import hash_password
from services.storage_paths import storage_path_for
from settings import get_settings


# ── Environment / session fixtures ───────────────────────────────────────────

@pytest.fixture
def tenancy_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSA_GUI_AUTH_ENABLED", "true")
    monkeypatch.setenv("PROJECTS_ROOT", str(tmp_path / "tenant-projects"))
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost:5173")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def session_local(db_engine, monkeypatch, tenancy_env):
    testing_session_local = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    monkeypatch.setattr(db_session_module, "SessionLocal", testing_session_local)
    return testing_session_local


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


# ── DB seeding helpers ───────────────────────────────────────────────────────

def _create_org(session_local, *, name: str | None = None) -> Organization:
    with session_local() as db:
        org = Organization(name=name or f"Org {uuid.uuid4()}", created_at=_now())
        db.add(org)
        db.commit()
        db.refresh(org)
        return org


def _create_user(session_local, *, email: str | None = None, is_super_admin: bool = False) -> User:
    with session_local() as db:
        user = User(
            email=email or f"{uuid.uuid4()}@example.com",
            password_hash=hash_password("secret-pass"),
            status="active",
            is_super_admin=is_super_admin,
            created_at=_now(),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def _add_membership(session_local, *, user_id, org_id, role: str) -> None:
    with session_local() as db:
        db.add(OrgMembership(user_id=user_id, org_id=org_id, role=role))
        db.commit()


def _seed_network(directory) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=3, freq="h"))
    n.add("Bus", "B1")
    n.add("Load", "L1", bus="B1", p_set=10.0)
    n.export_to_netcdf(str(directory / "network.nc"))


def _create_project(
    session_local,
    *,
    org,
    creator,
    name,
    parent=None,
    seed_disk=True,
) -> Project:
    project_id = uuid.uuid4()
    storage_path = storage_path_for(org.id, project_id)
    if seed_disk:
        _seed_network(storage_path)
    with session_local() as db:
        project = Project(
            id=project_id,
            org_id=org.id,
            name=name,
            created_by=creator.id,
            storage_path=str(storage_path),
            parent_project_id=parent.id if parent is not None else None,
            scenario_description=None,
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(project)
        db.commit()
        db.refresh(project)
        return project


def _assign(session_local, *, project, user, assigned_by) -> None:
    with session_local() as db:
        db.add(
            ProjectMembership(
                project_id=project.id,
                user_id=user.id,
                assigned_by=assigned_by.id,
                assigned_at=_now(),
            )
        )
        db.commit()


@contextlib.contextmanager
def _client_for(email: str):
    with TestClient(main.app) as client:
        resp = client.post("/api/auth/login", json={"email": email, "password": "secret-pass"})
        assert resp.status_code == 200, resp.text
        yield client


# ── Tests ────────────────────────────────────────────────────────────────────

def test_user_b_cannot_list_user_a_project(session_local):
    org_a = _create_org(session_local, name="Org A")
    org_b = _create_org(session_local, name="Org B")
    user_a = _create_user(session_local, email="a@example.com")
    user_b = _create_user(session_local, email="b@example.com")
    _add_membership(session_local, user_id=user_a.id, org_id=org_a.id, role="admin")
    _add_membership(session_local, user_id=user_b.id, org_id=org_b.id, role="admin")
    project_a = _create_project(session_local, org=org_a, creator=user_a, name="Alpha")

    with _client_for("a@example.com") as client_a:
        names_a = {p["name"] for p in client_a.get("/api/projects/").json()}
        ids_a = {p["id"] for p in client_a.get("/api/projects/").json()}
    with _client_for("b@example.com") as client_b:
        names_b = {p["name"] for p in client_b.get("/api/projects/").json()}

    assert project_a.name in names_a
    assert str(project_a.id) in ids_a
    assert project_a.name not in names_b


def test_user_b_cannot_load_or_activate_user_a_project(session_local):
    org_a = _create_org(session_local, name="Org A")
    org_b = _create_org(session_local, name="Org B")
    user_a = _create_user(session_local, email="a@example.com")
    user_b = _create_user(session_local, email="b@example.com")
    _add_membership(session_local, user_id=user_a.id, org_id=org_a.id, role="admin")
    _add_membership(session_local, user_id=user_b.id, org_id=org_b.id, role="admin")
    project_a = _create_project(session_local, org=org_a, creator=user_a, name="Alpha")

    with _client_for("b@example.com") as client_b:
        assert client_b.get(f"/api/projects/{project_a.id}").status_code == 404
        assert client_b.post(f"/api/projects/{project_a.id}/activate").status_code == 404
        assert client_b.get(f"/api/projects/{project_a.id}/bundle").status_code == 404

    with _client_for("a@example.com") as client_a:
        assert client_a.get(f"/api/projects/{project_a.id}").status_code == 200
        assert client_a.post(f"/api/projects/{project_a.id}/activate").status_code == 200


def test_create_scenario_inherits_tree_access(session_local):
    org = _create_org(session_local, name="Org")
    admin = _create_user(session_local, email="admin@example.com")
    member = _create_user(session_local, email="member@example.com")
    _add_membership(session_local, user_id=admin.id, org_id=org.id, role="admin")
    _add_membership(session_local, user_id=member.id, org_id=org.id, role="member")
    root = _create_project(session_local, org=org, creator=admin, name="Root")
    _assign(session_local, project=root, user=member, assigned_by=admin)

    with _client_for("member@example.com") as client_member:
        resp = client_member.post(
            f"/api/projects/{root.id}/scenarios",
            json={"name": "S1", "description": "[scenario] x"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["parent_project"] == root.name
        assert body["id"] is not None and body["id"] != str(root.id)
        assert body["scenario_description"] == "[scenario] x"

        # Member has tree-inherited access to the freshly-created child.
        assert client_member.get(f"/api/projects/{body['id']}").status_code == 200
        assert client_member.post(f"/api/projects/{body['id']}/activate").status_code == 200
        assert client_member.get(f"/api/projects/{body['id']}/bundle").status_code in (200, 404)

    # The child row carries the DB parent pointer.
    with session_local() as db:
        child = db.get(Project, uuid.UUID(body["id"]))
        assert child is not None
        assert child.parent_project_id == root.id
        assert child.org_id == org.id


def test_scenario_child_metadata_parent_name_in_sync(session_local):
    org = _create_org(session_local, name="Org")
    admin = _create_user(session_local, email="admin@example.com")
    _add_membership(session_local, user_id=admin.id, org_id=org.id, role="admin")
    root = _create_project(session_local, org=org, creator=admin, name="Root")

    with _client_for("admin@example.com") as client:
        resp = client.post(
            f"/api/projects/{root.id}/scenarios",
            json={"name": "Branch", "description": "d"},
        )
        assert resp.status_code == 201, resp.text
        child_id = resp.json()["id"]

    # metadata.json on the child's storage dir points at the base NAME.
    import json
    with session_local() as db:
        child = db.get(Project, uuid.UUID(child_id))
    meta = json.loads((storage_path_for(org.id, child.id) / "metadata.json").read_text())
    assert meta["parent_project"] == "Root"


def test_member_without_assignment_cannot_see_root(session_local):
    org = _create_org(session_local, name="Org")
    admin = _create_user(session_local, email="admin@example.com")
    member = _create_user(session_local, email="member@example.com")
    _add_membership(session_local, user_id=admin.id, org_id=org.id, role="admin")
    _add_membership(session_local, user_id=member.id, org_id=org.id, role="member")
    root = _create_project(session_local, org=org, creator=admin, name="Root")

    with _client_for("member@example.com") as client_member:
        names = {p["name"] for p in client_member.get("/api/projects/").json()}
        assert root.name not in names
        assert client_member.get(f"/api/projects/{root.id}").status_code == 404


def test_delete_permission_enforced(session_local):
    org = _create_org(session_local, name="Org")
    admin = _create_user(session_local, email="admin@example.com")
    member = _create_user(session_local, email="member@example.com")
    _add_membership(session_local, user_id=admin.id, org_id=org.id, role="admin")
    _add_membership(session_local, user_id=member.id, org_id=org.id, role="member")
    root = _create_project(session_local, org=org, creator=admin, name="Root")
    _assign(session_local, project=root, user=member, assigned_by=admin)

    # Member can access but cannot delete a root they didn't create.
    with _client_for("member@example.com") as client_member:
        assert client_member.delete(f"/api/projects/{root.id}").status_code == 403

    with _client_for("admin@example.com") as client_admin:
        assert client_admin.delete(f"/api/projects/{root.id}").status_code == 200

    with session_local() as db:
        assert db.get(Project, root.id) is None


def test_rename_updates_registry(session_local):
    org = _create_org(session_local, name="Org")
    admin = _create_user(session_local, email="admin@example.com")
    _add_membership(session_local, user_id=admin.id, org_id=org.id, role="admin")
    root = _create_project(session_local, org=org, creator=admin, name="OldName")

    with _client_for("admin@example.com") as client:
        resp = client.post(f"/api/projects/{root.id}/rename", json={"new_name": "NewName"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["name"] == "NewName"
        assert resp.json()["id"] == str(root.id)

    with session_local() as db:
        assert db.get(Project, root.id).name == "NewName"


def test_members_get_and_put(session_local):
    org = _create_org(session_local, name="Org")
    admin = _create_user(session_local, email="admin@example.com")
    member = _create_user(session_local, email="member@example.com")
    _add_membership(session_local, user_id=admin.id, org_id=org.id, role="admin")
    _add_membership(session_local, user_id=member.id, org_id=org.id, role="member")
    root = _create_project(session_local, org=org, creator=admin, name="Root")

    with _client_for("admin@example.com") as client:
        assert client.get(f"/api/projects/{root.id}/members").json() == []
        resp = client.put(
            f"/api/projects/{root.id}/members",
            json={"user_ids": [str(member.id)]},
        )
        assert resp.status_code == 200, resp.text
        emails = {m["email"] for m in resp.json()}
        assert member.email in emails

    # Now the assigned member can see the root in their list.
    with _client_for("member@example.com") as client_member:
        names = {p["name"] for p in client_member.get("/api/projects/").json()}
        assert root.name in names


def test_member_cannot_manage_members(session_local):
    org = _create_org(session_local, name="Org")
    admin = _create_user(session_local, email="admin@example.com")
    member = _create_user(session_local, email="member@example.com")
    _add_membership(session_local, user_id=admin.id, org_id=org.id, role="admin")
    _add_membership(session_local, user_id=member.id, org_id=org.id, role="member")
    root = _create_project(session_local, org=org, creator=admin, name="Root")
    _assign(session_local, project=root, user=member, assigned_by=admin)

    with _client_for("member@example.com") as client_member:
        resp = client_member.put(
            f"/api/projects/{root.id}/members",
            json={"user_ids": [str(member.id)]},
        )
        assert resp.status_code == 403


# ── Read-only sub-resource ACL gating (Task 7 review — must-fix #1) ───────────
# layout / statistics / results_bundle used to resolve a project via the flat
# `_safe_project_dir(name)` alone, bypassing the org-scoped registry + ACL. In
# auth mode they must now 404 for another org/user's project and succeed for an
# authorized one — the same resolution as every other project-scoped route.


def test_layout_get_isolation(session_local):
    org_a = _create_org(session_local, name="Org A")
    org_b = _create_org(session_local, name="Org B")
    user_a = _create_user(session_local, email="a@example.com")
    user_b = _create_user(session_local, email="b@example.com")
    _add_membership(session_local, user_id=user_a.id, org_id=org_a.id, role="admin")
    _add_membership(session_local, user_id=user_b.id, org_id=org_b.id, role="admin")
    project_a = _create_project(session_local, org=org_a, creator=user_a, name="Alpha")

    # Another org's user cannot read the layout — 404 (existence not leaked).
    with _client_for("b@example.com") as client_b:
        assert client_b.get(f"/api/projects/{project_a.id}/layout").status_code == 404

    # The owner reads the layout fine — empty ({}) since none was saved.
    with _client_for("a@example.com") as client_a:
        resp = client_a.get(f"/api/projects/{project_a.id}/layout")
        assert resp.status_code == 200, resp.text
        assert resp.json() == {}


def test_layout_put_then_get_roundtrip_for_owner(session_local):
    org = _create_org(session_local, name="Org")
    admin = _create_user(session_local, email="admin@example.com")
    _add_membership(session_local, user_id=admin.id, org_id=org.id, role="admin")
    project = _create_project(session_local, org=org, creator=admin, name="Root")

    with _client_for("admin@example.com") as client:
        put = client.put(
            f"/api/projects/{project.id}/layout", json={"nodes": {"B1": [1, 2]}}
        )
        assert put.status_code == 200, put.text
        got = client.get(f"/api/projects/{project.id}/layout")
        assert got.status_code == 200
        assert got.json() == {"nodes": {"B1": [1, 2]}}

    # The layout landed in the org-scoped storage dir, not a flat path.
    layout_file = storage_path_for(org.id, project.id) / "layout.json"
    assert layout_file.exists()


def test_layout_put_isolation(session_local):
    org_a = _create_org(session_local, name="Org A")
    org_b = _create_org(session_local, name="Org B")
    user_a = _create_user(session_local, email="a@example.com")
    user_b = _create_user(session_local, email="b@example.com")
    _add_membership(session_local, user_id=user_a.id, org_id=org_a.id, role="admin")
    _add_membership(session_local, user_id=user_b.id, org_id=org_b.id, role="admin")
    project_a = _create_project(session_local, org=org_a, creator=user_a, name="Alpha")

    with _client_for("b@example.com") as client_b:
        resp = client_b.put(
            f"/api/projects/{project_a.id}/layout", json={"nodes": {}}
        )
        assert resp.status_code == 404


def test_statistics_isolation(session_local):
    org_a = _create_org(session_local, name="Org A")
    org_b = _create_org(session_local, name="Org B")
    user_a = _create_user(session_local, email="a@example.com")
    user_b = _create_user(session_local, email="b@example.com")
    _add_membership(session_local, user_id=user_a.id, org_id=org_a.id, role="admin")
    _add_membership(session_local, user_id=user_b.id, org_id=org_b.id, role="admin")
    project_a = _create_project(session_local, org=org_a, creator=user_a, name="Alpha")

    # Another org's user cannot read statistics — 404.
    with _client_for("b@example.com") as client_b:
        assert client_b.get(f"/api/projects/{project_a.id}/statistics").status_code == 404

    # Owner gets real statistics from the seeded network (1 bus, 3 snapshots).
    with _client_for("a@example.com") as client_a:
        resp = client_a.get(f"/api/projects/{project_a.id}/statistics")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["buses"] == 1
        assert body["snapshots"] == 3
        assert body["name"] == "Alpha"


def test_results_bundle_isolation(session_local):
    org_a = _create_org(session_local, name="Org A")
    org_b = _create_org(session_local, name="Org B")
    user_a = _create_user(session_local, email="a@example.com")
    user_b = _create_user(session_local, email="b@example.com")
    _add_membership(session_local, user_id=user_a.id, org_id=org_a.id, role="admin")
    _add_membership(session_local, user_id=user_b.id, org_id=org_b.id, role="admin")
    project_a = _create_project(session_local, org=org_a, creator=user_a, name="Alpha")

    # Another org's user cannot read the results bundle — 404, not a leak.
    with _client_for("b@example.com") as client_b:
        assert client_b.get(f"/api/projects/{project_a.id}/results_bundle").status_code == 404

    # Owner is authorized; the seeded network was never solved so the bundle is
    # empty → 204 (authorization succeeded, there's just no dispatch to return).
    with _client_for("a@example.com") as client_a:
        assert client_a.get(f"/api/projects/{project_a.id}/results_bundle").status_code == 204


# ── UUID-shaped names (Task 7 review — must-fix #3) ───────────────────────────


def test_uuid_shaped_name_resolves_by_name(session_local):
    """A project *named* like a UUID (that is NOT any project's id) must still
    resolve by name — the resolver falls back to a name lookup when the UUID
    parse succeeds but matches no id in the org."""
    org = _create_org(session_local, name="Org")
    admin = _create_user(session_local, email="admin@example.com")
    _add_membership(session_local, user_id=admin.id, org_id=org.id, role="admin")

    uuid_name = str(uuid.uuid4())  # a valid UUID string used as the NAME
    project = _create_project(session_local, org=org, creator=admin, name=uuid_name)
    assert str(project.id) != uuid_name  # distinct from its own id

    with _client_for("admin@example.com") as client:
        # Addressed by the UUID-shaped NAME (no row has this as an id).
        assert client.get(f"/api/projects/{uuid_name}").status_code == 200
        assert client.get(f"/api/projects/{uuid_name}/statistics").status_code == 200
        # The real id still resolves too.
        assert client.get(f"/api/projects/{project.id}").status_code == 200


def test_find_project_uuid_fallback_unit(session_local):
    """Unit-level: find_project returns the row when addressed by a UUID-shaped
    name with no matching id, and None for a genuinely absent UUID."""
    from services import project_registry

    org = _create_org(session_local, name="Org")
    admin = _create_user(session_local, email="admin@example.com")
    _add_membership(session_local, user_id=admin.id, org_id=org.id, role="admin")

    uuid_name = str(uuid.uuid4())
    project = _create_project(session_local, org=org, creator=admin, name=uuid_name)

    with session_local() as db:
        db_user = db.get(User, admin.id)
        # UUID-shaped name, no id match → falls back to name lookup.
        found = project_registry.find_project(db, db_user, uuid_name)
        assert found is not None
        assert found.id == project.id
        # A UUID that matches neither an id nor a name → None.
        assert project_registry.find_project(db, db_user, str(uuid.uuid4())) is None
        # The real id resolves directly.
        assert project_registry.find_project(db, db_user, str(project.id)).id == project.id


# ── Save-As copies uploads from the auth storage path (review — must-fix #2) ──


def test_save_as_copies_uploads_from_auth_storage(session_local):
    """Save-As (save the active project under a new name) must copy the
    `uploads/` dir from the SOURCE project's org-scoped storage_path into the
    new project's storage_path — not from a flat `_safe_project_dir(loaded)`."""
    org = _create_org(session_local, name="Org")
    admin = _create_user(session_local, email="admin@example.com")
    _add_membership(session_local, user_id=admin.id, org_id=org.id, role="admin")
    source = _create_project(session_local, org=org, creator=admin, name="Source")

    # Seed an upload under the SOURCE project's org-scoped storage dir.
    uploads = storage_path_for(org.id, source.id) / "uploads" / "file-1"
    uploads.mkdir(parents=True, exist_ok=True)
    (uploads / "ref.csv").write_text("a,b\n1,2\n")

    with _client_for("admin@example.com") as client:
        # Load the source so the active singleton is bound to "Source".
        assert client.get(f"/api/projects/{source.id}").status_code == 200
        # Save-As to a brand-new name → create the root row + copy the bundle.
        resp = client.post("/api/projects/Copy", params={"rebind": "true"})
        assert resp.status_code == 200, resp.text

    # The new project's org-scoped storage dir received the uploads copy.
    with session_local() as db:
        copy_row = db.scalar(
            select(Project).where(Project.org_id == org.id, Project.name == "Copy")
        )
    assert copy_row is not None
    copied = storage_path_for(org.id, copy_row.id) / "uploads" / "file-1" / "ref.csv"
    assert copied.exists(), "uploads/ was not copied from the source storage_path"
    assert copied.read_text() == "a,b\n1,2\n"
