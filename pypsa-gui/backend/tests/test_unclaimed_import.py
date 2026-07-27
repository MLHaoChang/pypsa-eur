"""
Pre-auth ("unclaimed") project discovery + one-click import.

A single-user install saves projects as flat ``projects/<name>/`` directories.
Once ``PYPSA_GUI_AUTH_ENABLED=true`` the project list comes from the DB
registry and those directories vanish from the UI. These tests cover the
recovery path: the flat directories inside ``projects_root`` are surfaced by
``GET /api/projects/unclaimed`` and adopted into the caller's organization by
``POST /api/projects/unclaimed/{name}/import`` — which provisions a personal
organization when the caller has none (the bootstrapped super-admin case).
"""
from __future__ import annotations

import contextlib
import json
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

import main
from db import session as db_session_module
from db.models import Organization, OrgMembership, Project, User
from services import project_registry
from services.auth_service import hash_password
from settings import get_settings

pytestmark = pytest.mark.auth_smoke


# ── Environment / session fixtures ───────────────────────────────────────────

@pytest.fixture
def unclaimed_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSA_GUI_AUTH_ENABLED", "true")
    monkeypatch.setenv("PROJECTS_ROOT", str(tmp_path / "tenant-projects"))
    monkeypatch.setenv("LEGACY_ROOT", str(tmp_path / "legacy-unclaimed"))
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost:5173")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def session_local(db_engine, monkeypatch, unclaimed_env):
    testing_session_local = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    monkeypatch.setattr(db_session_module, "SessionLocal", testing_session_local)
    return testing_session_local


@pytest.fixture
def projects_root(unclaimed_env):
    root = get_settings().projects_root
    root.mkdir(parents=True, exist_ok=True)
    return root


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


# ── Seeding helpers ──────────────────────────────────────────────────────────

def _write_flat_project(root, name: str, *, parent_project: str | None = None):
    """Write a pre-auth project directory the way the single-user app did."""
    project_dir = root / name
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "metadata.json").write_text(
        json.dumps(
            {
                "name": name,
                "parent_project": parent_project,
                "scenario_description": "[scenario] variant" if parent_project else None,
                "bus_count": 3,
            }
        ),
        encoding="utf-8",
    )
    (project_dir / "network.nc").write_text(f"network:{name}", encoding="utf-8")
    (project_dir / "payload.txt").write_text(f"payload:{name}", encoding="utf-8")
    return project_dir


def _create_user(session_local, *, email: str, is_super_admin: bool = False) -> User:
    with session_local() as db:
        user = User(
            email=email,
            password_hash=hash_password("secret-pass"),
            status="active",
            is_super_admin=is_super_admin,
            created_at=_now(),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def _create_org_member(session_local, *, email: str, role: str = "admin") -> tuple[User, Organization]:
    user = _create_user(session_local, email=email)
    with session_local() as db:
        org = Organization(name=f"Org {uuid.uuid4()}", created_at=_now())
        db.add(org)
        db.flush()
        db.add(OrgMembership(user_id=user.id, org_id=org.id, role=role))
        db.commit()
        db.refresh(org)
        return user, org


@contextlib.contextmanager
def _client_for(email: str):
    with TestClient(main.app) as client:
        resp = client.post("/api/auth/login", json={"email": email, "password": "secret-pass"})
        assert resp.status_code == 200, resp.text
        # Step 0a: state-changing routes require the double-submit CSRF
        # token. The login response carries it in the body precisely so a
        # non-browser client can echo it back without parsing cookies.
        client.headers["X-CSRF-Token"] = resp.json()["csrf_token"]
        yield client


# ── Discovery ────────────────────────────────────────────────────────────────

def test_flat_dir_in_projects_root_is_listed(session_local, projects_root):
    _create_org_member(session_local, email="member@example.com", role="member")
    _write_flat_project(projects_root, "Old Study")

    with _client_for("member@example.com") as client:
        resp = client.get("/api/projects/unclaimed")

    assert resp.status_code == 200, resp.text
    rows = {row["name"]: row for row in resp.json()}
    assert "Old Study" in rows
    assert rows["Old Study"] == {
        "name": "Old Study",
        "parent_project": None,
        "scenario_description": None,
        "has_network": True,
        "descendant_names": [],
    }


def test_uuid_named_dir_is_not_listed(session_local, projects_root):
    _create_org_member(session_local, email="admin@example.com")
    # An org storage root: <org_uuid>/<project_uuid>/… — never a legacy project.
    org_storage = projects_root / str(uuid.uuid4()) / str(uuid.uuid4())
    org_storage.mkdir(parents=True)
    (org_storage / "network.nc").write_text("network", encoding="utf-8")
    _write_flat_project(projects_root, "Flat One")

    with _client_for("admin@example.com") as client:
        resp = client.get("/api/projects/unclaimed")

    assert resp.status_code == 200, resp.text
    assert [row["name"] for row in resp.json()] == ["Flat One"]


def test_unclaimed_requires_authentication(anon_client):
    """
    Replaces `test_unclaimed_requires_auth_enabled` (Step 0a).

    That test asserted the routes 404 when `PYPSA_GUI_AUTH_ENABLED=false`. The
    flag is deleted and single-user mode with it, so the property worth keeping
    is the one that still means something: an ANONYMOUS caller cannot enumerate
    or claim pre-auth projects.
    """
    assert anon_client.get("/api/projects/unclaimed").status_code == 401
    assert anon_client.post("/api/projects/unclaimed/Whatever/import").status_code == 401


# ── Import ───────────────────────────────────────────────────────────────────

def test_import_auto_creates_workspace_for_orgless_super_admin(session_local, projects_root):
    _create_user(session_local, email="alice@example.com", is_super_admin=True)
    _write_flat_project(projects_root, "Old Study")

    with _client_for("alice@example.com") as client:
        # No organization yet, so the pre-auth project is the only thing visible.
        assert client.get("/api/projects/").json() == []

        resp = client.post("/api/projects/unclaimed/Old Study/import")
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["root"]["name"] == "Old Study"
        assert body["root"]["parent_project_id"] is None
        assert [row["name"] for row in body["claimed"]] == ["Old Study"]
        assert body["warnings"] == []

        listed = client.get("/api/projects/").json()
        assert [row["name"] for row in listed] == ["Old Study"]
        assert listed[0]["id"] == body["root"]["id"]

        # It is no longer offered for import.
        assert client.get("/api/projects/unclaimed").json() == []

    with session_local() as db:
        org = db.get(Organization, uuid.UUID(body["root"]["org_id"]))
        assert org is not None
        assert org.name == "alice's workspace"
        membership = db.scalar(select(OrgMembership).where(OrgMembership.org_id == org.id))
        assert membership is not None
        assert membership.role == "admin"


def test_import_moves_directory_into_org_storage(session_local, projects_root):
    user, org = _create_org_member(session_local, email="admin@example.com")
    _write_flat_project(projects_root, "Old Study")

    with _client_for("admin@example.com") as client:
        resp = client.post("/api/projects/unclaimed/Old Study/import")
        assert resp.status_code == 201, resp.text
        project_id = resp.json()["root"]["id"]

    # Phase 1b (E1/E2): the destination is `<org>/<readable name>`, relative in
    # the row and rejoined by the resolver — not `<org>/<project uuid>`
    # absolute. Ask the resolver rather than rebuilding the path by hand, or
    # this assertion re-encodes a layout decision that lives in one place.
    with session_local() as db:
        project = db.get(Project, uuid.UUID(project_id))
        destination = project_registry.project_dir(project)
        assert project.storage_path == f"{org.id}/Old Study"

    assert destination.is_dir()
    assert (destination / "payload.txt").read_text() == "payload:Old Study"
    assert not (projects_root / "Old Study").exists()


def test_import_relinks_scenario_child_to_root(session_local, projects_root):
    _create_org_member(session_local, email="admin@example.com")
    _write_flat_project(projects_root, "Base")
    _write_flat_project(projects_root, "Variant", parent_project="Base")

    with _client_for("admin@example.com") as client:
        assert {row["name"] for row in client.get("/api/projects/unclaimed").json()} == {
            "Base",
            "Variant",
        }
        resp = client.post("/api/projects/unclaimed/Base/import")
        assert resp.status_code == 201, resp.text
        body = resp.json()

    claimed = {row["name"]: row for row in body["claimed"]}
    assert set(claimed) == {"Base", "Variant"}
    assert claimed["Variant"]["parent_project_id"] == body["root"]["id"]
    assert body["warnings"] == []

    with session_local() as db:
        child = db.scalar(select(Project).where(Project.name == "Variant"))
        assert child.parent_project_id == uuid.UUID(body["root"]["id"])
        assert child.scenario_description == "[scenario] variant"


def test_importing_same_name_twice_conflicts(session_local, projects_root):
    _create_org_member(session_local, email="admin@example.com")
    _write_flat_project(projects_root, "Old Study")

    with _client_for("admin@example.com") as client:
        assert client.post("/api/projects/unclaimed/Old Study/import").status_code == 201

        # A stale copy of the same pre-auth directory reappears (restored from a
        # backup, say) — importing it must not collide with the claimed row.
        _write_flat_project(projects_root, "Old Study")
        resp = client.post("/api/projects/unclaimed/Old Study/import")

    assert resp.status_code == 409, resp.text
    assert "already exists" in resp.json()["detail"]
    # The stale directory is left untouched for the operator to deal with.
    assert (projects_root / "Old Study").exists()


def test_import_unknown_name_is_400(session_local, projects_root):
    _create_org_member(session_local, email="admin@example.com")

    with _client_for("admin@example.com") as client:
        resp = client.post("/api/projects/unclaimed/Nope/import")

    assert resp.status_code == 400, resp.text
