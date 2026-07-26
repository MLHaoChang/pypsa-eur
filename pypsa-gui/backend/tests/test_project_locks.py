from __future__ import annotations

import contextlib
import uuid
from datetime import datetime, timedelta, timezone

import pandas as pd
import pypsa
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

import main
from db import session as db_session_module
from db.models import Organization, OrgMembership, Project, ProjectLock, User
from services.auth_service import hash_password
from services.storage_paths import storage_path_for
from settings import get_settings

pytestmark = pytest.mark.auth_smoke


def _service():
    from services.project_locks import (
        acquire_lock,
        get_lock,
        heartbeat_lock,
        release_all_for_user,
        release_lock,
    )

    return acquire_lock, get_lock, heartbeat_lock, release_all_for_user, release_lock


@pytest.fixture
def lock_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSA_GUI_AUTH_ENABLED", "true")
    monkeypatch.setenv("PROJECTS_ROOT", str(tmp_path / "tenant-projects"))
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost:5173")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def session_local(db_engine, monkeypatch, lock_env):
    testing_session_local = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    monkeypatch.setattr(db_session_module, "SessionLocal", testing_session_local)
    return testing_session_local


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _create_org(session_local, *, name: str | None = None) -> Organization:
    with session_local() as db:
        org = Organization(name=name or f"Org {uuid.uuid4()}", created_at=_now())
        db.add(org)
        db.commit()
        db.refresh(org)
        return org


def _create_user(session_local, *, email: str | None = None) -> User:
    with session_local() as db:
        user = User(
            email=email or f"{uuid.uuid4()}@example.com",
            password_hash=hash_password("secret-pass"),
            status="active",
            is_super_admin=False,
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
    org: Organization,
    creator: User,
    name: str,
    parent: Project | None = None,
) -> Project:
    project_id = uuid.uuid4()
    storage_path = storage_path_for(org.id, project_id)
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


@contextlib.contextmanager
def _client_for(email: str):
    with TestClient(main.app) as client:
        response = client.post("/api/auth/login", json={"email": email, "password": "secret-pass"})
        assert response.status_code == 200, response.text
        yield client


def test_second_user_cannot_acquire_lock(session_local):
    acquire_lock, get_lock, _, _, _ = _service()
    org = _create_org(session_local)
    user_a = _create_user(session_local, email="a@example.com")
    user_b = _create_user(session_local, email="b@example.com")
    _add_membership(session_local, user_id=user_a.id, org_id=org.id, role="admin")
    _add_membership(session_local, user_id=user_b.id, org_id=org.id, role="admin")
    project = _create_project(session_local, org=org, creator=user_a, name="Alpha")

    with session_local() as db:
        lock = acquire_lock(db, project.id, user_a.id)
        assert lock is not None
        assert acquire_lock(db, project.id, user_b.id) is None
        current = get_lock(db, project.id)

    assert current is not None
    assert current.holder_user_id == user_a.id


def test_expired_lock_can_be_stolen(session_local):
    acquire_lock, get_lock, _, _, _ = _service()
    org = _create_org(session_local)
    user_a = _create_user(session_local, email="a@example.com")
    user_b = _create_user(session_local, email="b@example.com")
    _add_membership(session_local, user_id=user_a.id, org_id=org.id, role="admin")
    _add_membership(session_local, user_id=user_b.id, org_id=org.id, role="admin")
    project = _create_project(session_local, org=org, creator=user_a, name="Alpha")

    with session_local() as db:
        lock = acquire_lock(db, project.id, user_a.id, ttl_seconds=1)
        assert lock is not None
        lock.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
        stolen = acquire_lock(db, project.id, user_b.id)
        current = get_lock(db, project.id)

    assert stolen is not None
    assert current is not None
    assert current.holder_user_id == user_b.id


def test_locks_are_scoped_per_project_node(session_local):
    acquire_lock, _, _, _, _ = _service()
    org = _create_org(session_local)
    user_a = _create_user(session_local, email="a@example.com")
    user_b = _create_user(session_local, email="b@example.com")
    _add_membership(session_local, user_id=user_a.id, org_id=org.id, role="admin")
    _add_membership(session_local, user_id=user_b.id, org_id=org.id, role="admin")
    root = _create_project(session_local, org=org, creator=user_a, name="Root")
    child = _create_project(session_local, org=org, creator=user_a, name="Child", parent=root)

    with session_local() as db:
        root_lock = acquire_lock(db, root.id, user_a.id)
        child_lock = acquire_lock(db, child.id, user_b.id)
        assert root_lock is not None
        assert child_lock is not None
        assert root_lock.project_id == root.id
        assert child_lock.project_id == child.id

def test_lock_endpoints_surface_holder_and_allow_release(session_local):
    org = _create_org(session_local)
    user_a = _create_user(session_local, email="a@example.com")
    user_b = _create_user(session_local, email="b@example.com")
    _add_membership(session_local, user_id=user_a.id, org_id=org.id, role="admin")
    _add_membership(session_local, user_id=user_b.id, org_id=org.id, role="admin")
    project = _create_project(session_local, org=org, creator=user_a, name="Alpha")

    with _client_for(user_a.email) as client_a, _client_for(user_b.email) as client_b:
        acquire_response = client_a.post(f"/api/projects/{project.id}/lock")
        assert acquire_response.status_code == 200, acquire_response.text
        assert acquire_response.json()["lock"] == {
            "holder_email": user_a.email,
            "yours": True,
        }

        with session_local() as db:
            lock_before = db.scalar(select(ProjectLock).where(ProjectLock.project_id == project.id))
            assert lock_before is not None
            first_expiry = lock_before.expires_at

        heartbeat_response = client_a.post(f"/api/projects/{project.id}/lock/heartbeat")
        assert heartbeat_response.status_code == 200, heartbeat_response.text
        assert heartbeat_response.json()["lock"] == {
            "holder_email": user_a.email,
            "yours": True,
        }

        with session_local() as db:
            lock_after = db.scalar(select(ProjectLock).where(ProjectLock.project_id == project.id))
            assert lock_after is not None
            assert lock_after.expires_at > first_expiry

        conflict_response = client_b.post(f"/api/projects/{project.id}/lock")
        assert conflict_response.status_code == 409, conflict_response.text
        assert conflict_response.json()["detail"]["error_kind"] == "project_locked"
        assert conflict_response.json()["detail"]["lock"] == {
            "holder_email": user_a.email,
            "yours": False,
        }

        load_response = client_b.get(f"/api/projects/{project.id}")
        assert load_response.status_code == 200, load_response.text
        assert load_response.json()["lock"] == {
            "holder_email": user_a.email,
            "yours": False,
        }

        activate_response = client_b.post(f"/api/projects/{project.id}/activate")
        assert activate_response.status_code == 200, activate_response.text
        assert activate_response.json()["lock"] == {
            "holder_email": user_a.email,
            "yours": False,
        }

        release_response = client_a.delete(f"/api/projects/{project.id}/lock")
        assert release_response.status_code == 200, release_response.text
        assert release_response.json() == {"released": True}

        reacquire_response = client_b.post(f"/api/projects/{project.id}/lock")
        assert reacquire_response.status_code == 200, reacquire_response.text
        assert reacquire_response.json()["lock"] == {
            "holder_email": user_b.email,
            "yours": True,
        }


def test_logout_releases_all_project_locks(session_local):
    org = _create_org(session_local)
    user = _create_user(session_local, email="holder@example.com")
    _add_membership(session_local, user_id=user.id, org_id=org.id, role="admin")
    project_a = _create_project(session_local, org=org, creator=user, name="Alpha")
    project_b = _create_project(session_local, org=org, creator=user, name="Beta")

    with _client_for(user.email) as client:
        assert client.post(f"/api/projects/{project_a.id}/lock").status_code == 200
        assert client.post(f"/api/projects/{project_b.id}/lock").status_code == 200

        with session_local() as db:
            held_before = db.scalars(
                select(ProjectLock).where(ProjectLock.holder_user_id == user.id)
            ).all()
            assert {lock.project_id for lock in held_before} == {project_a.id, project_b.id}

        logout_response = client.post("/api/auth/logout")
        assert logout_response.status_code == 200, logout_response.text

    with session_local() as db:
        held_after = db.scalars(
            select(ProjectLock).where(ProjectLock.holder_user_id == user.id)
        ).all()

    assert held_after == []
