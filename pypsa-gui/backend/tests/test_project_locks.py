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
    # Phase 1b: `storage_path_for` returns a path RELATIVE to `projects_root`,
    # so the row stores the relative form and anything that writes must rejoin
    # it with the root first — otherwise `_seed_network` lands in
    # `pypsa-gui/backend/<org>/…`, inside the checkout (pixi runs the suite
    # with that cwd) and outside `.gitignore`'s reach.
    #
    # `taken=set()` is sound here and only here: `UniqueConstraint(org_id,
    # name)` already makes every project in one org distinctly named, and no
    # two names in this module sanitise alike.
    relative = storage_path_for(
        org.id, project_id, name, taken=set(), org_segment=True
    )
    storage_path = get_settings().projects_root / relative
    _seed_network(storage_path)
    with session_local() as db:
        project = Project(
            id=project_id,
            org_id=org.id,
            name=name,
            created_by=creator.id,
            storage_path=str(relative),
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
        # Step 0a: state-changing routes require the double-submit CSRF token.
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
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


def test_acquire_lock_returns_none_on_insert_race(session_local, monkeypatch):
    """A concurrent insert (IntegrityError) maps to contention (None), not a 500."""
    from services import project_locks

    org = _create_org(session_local)
    user_a = _create_user(session_local, email="a@example.com")
    user_b = _create_user(session_local, email="b@example.com")
    _add_membership(session_local, user_id=user_a.id, org_id=org.id, role="admin")
    _add_membership(session_local, user_id=user_b.id, org_id=org.id, role="admin")
    project = _create_project(session_local, org=org, creator=user_a, name="Alpha")

    with session_local() as db:
        # Simulate the lost-update race: another worker inserts the lock row
        # after our prune returns None but before our own insert commits.
        db.add(
            ProjectLock(
                project_id=project.id,
                holder_user_id=user_a.id,
                acquired_at=_now(),
                expires_at=project_locks._expires_at(120),
            )
        )
        db.commit()

    monkeypatch.setattr(project_locks, "_prune_expired", lambda db, project_id: None)

    with session_local() as db:
        result = project_locks.acquire_lock(db, project.id, user_b.id)

    assert result is None

    with session_local() as db:
        current = db.scalar(select(ProjectLock).where(ProjectLock.project_id == project.id))
        assert current is not None
        assert current.holder_user_id == user_a.id


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


# ── Task 4: _enforce_project_lock (write-edge lock-enforcement helper) ─────
#
# Fixtures here mirror this file's own construction style (session_local +
# _create_org/_create_user/_add_membership/_create_project) rather than the
# `db`/`user_a`/`project_row` fixture names sketched in the task brief, since
# this file has no such fixtures and the brief says to borrow the inline
# construction style used elsewhere instead of adding a conftest.


def test_enforce_allows_free_and_makes_caller_holder(session_local):
    from routers.projects import _enforce_project_lock
    from services import project_locks

    org = _create_org(session_local)
    user_a = _create_user(session_local, email="a@example.com")
    _add_membership(session_local, user_id=user_a.id, org_id=org.id, role="admin")
    project = _create_project(session_local, org=org, creator=user_a, name="Alpha")

    with session_local() as db:
        db_project = db.get(Project, project.id)
        _enforce_project_lock(db, db_project, user_a)  # no raise
        lock = project_locks.get_lock(db, project.id)
        assert lock is not None and lock.holder_user_id == user_a.id


def test_enforce_409_when_foreign_holder(session_local):
    from fastapi import HTTPException

    from routers.projects import _enforce_project_lock
    from services import project_locks

    org = _create_org(session_local)
    user_a = _create_user(session_local, email="a@example.com")
    user_b = _create_user(session_local, email="b@example.com")
    _add_membership(session_local, user_id=user_a.id, org_id=org.id, role="admin")
    _add_membership(session_local, user_id=user_b.id, org_id=org.id, role="admin")
    project = _create_project(session_local, org=org, creator=user_a, name="Alpha")

    with session_local() as db:
        assert project_locks.acquire_lock(db, project.id, user_b.id) is not None
        db_project = db.get(Project, project.id)
        with pytest.raises(HTTPException) as exc:
            _enforce_project_lock(db, db_project, user_a)
        assert exc.value.status_code == 409
        assert exc.value.detail["error_kind"] == "project_locked"
        assert "lock" in exc.value.detail


def test_enforce_reacquires_after_expiry(session_local):
    from routers.projects import _enforce_project_lock
    from services import project_locks

    org = _create_org(session_local)
    user_a = _create_user(session_local, email="a@example.com")
    user_b = _create_user(session_local, email="b@example.com")
    _add_membership(session_local, user_id=user_a.id, org_id=org.id, role="admin")
    _add_membership(session_local, user_id=user_b.id, org_id=org.id, role="admin")
    project = _create_project(session_local, org=org, creator=user_a, name="Alpha")

    with session_local() as db:
        project_locks.acquire_lock(db, project.id, user_b.id)
        row = db.get(ProjectLock, project.id)
        row.expires_at = datetime.now(tz=timezone.utc) - timedelta(seconds=1)
        db.commit()
        db_project = db.get(Project, project.id)
        _enforce_project_lock(db, db_project, user_a)  # expired lock pruned, A takes over
        assert project_locks.get_lock(db, project.id).holder_user_id == user_a.id


def test_enforce_noops_in_local_mode(session_local, monkeypatch):
    import routers.projects as projects_router
    from services import project_locks

    org = _create_org(session_local)
    user_a = _create_user(session_local, email="a@example.com")
    user_b = _create_user(session_local, email="b@example.com")
    _add_membership(session_local, user_id=user_a.id, org_id=org.id, role="admin")
    _add_membership(session_local, user_id=user_b.id, org_id=org.id, role="admin")
    project = _create_project(session_local, org=org, creator=user_a, name="Alpha")

    with session_local() as db:
        project_locks.acquire_lock(db, project.id, user_b.id)
        db_project = db.get(Project, project.id)
        monkeypatch.setattr(projects_router.local_mode, "is_local_mode", lambda: True)
        projects_router._enforce_project_lock(db, db_project, user_a)  # no raise


# ── I2: acquisition tiers at the write edges ───────────────────────────────
#
# D8 keeps acquire-on-write for save/rename/delete/scenario/members/snapshots
# — the writer becoming the holder for the TTL is what un-strands a holder
# whose heartbeat lapsed. Two edges are deliberately NOT in that set:
#
#   * `put_layout` is a CHECK. The canvas PUTs a layout on every drag-settle
#     and on remount, so acquiring there would let a passive viewer's autosaved
#     layout take an idle project's lock away from nobody's benefit.
#   * `update_scenario_metadata` with an EMPTY body is a documented no-op that
#     must not stamp `updated_at` — and must therefore not take a lock either.


def test_put_layout_checks_the_lock_without_acquiring_it(session_local):
    from routers.projects import put_layout
    from services import project_locks

    org = _create_org(session_local)
    user_a = _create_user(session_local, email="a@example.com")
    _add_membership(session_local, user_id=user_a.id, org_id=org.id, role="admin")
    project = _create_project(session_local, org=org, creator=user_a, name="Alpha")

    with session_local() as db:
        assert project_locks.get_lock(db, project.id) is None
        put_layout(project.name, {"nodes": []}, db=db, user=db.get(User, user_a.id))
        assert project_locks.get_lock(db, project.id) is None, (
            "put_layout must not become the lock holder"
        )


def test_put_layout_409s_under_a_foreign_lock(session_local):
    from fastapi import HTTPException

    from routers.projects import put_layout
    from services import project_locks

    org = _create_org(session_local)
    user_a = _create_user(session_local, email="a@example.com")
    user_b = _create_user(session_local, email="b@example.com")
    _add_membership(session_local, user_id=user_a.id, org_id=org.id, role="admin")
    _add_membership(session_local, user_id=user_b.id, org_id=org.id, role="admin")
    project = _create_project(session_local, org=org, creator=user_a, name="Alpha")

    with session_local() as db:
        assert project_locks.acquire_lock(db, project.id, user_b.id) is not None
        with pytest.raises(HTTPException) as exc:
            put_layout(project.name, {"nodes": []}, db=db, user=db.get(User, user_a.id))
        assert exc.value.status_code == 409
        assert exc.value.detail["error_kind"] == "project_locked"
        assert "lock" in exc.value.detail


def test_empty_scenario_metadata_patch_does_not_take_the_lock(session_local):
    from models.schemas import UpdateScenarioRequest
    from routers.projects import update_scenario_metadata
    from services import project_locks

    org = _create_org(session_local)
    user_a = _create_user(session_local, email="a@example.com")
    _add_membership(session_local, user_id=user_a.id, org_id=org.id, role="admin")
    project = _create_project(session_local, org=org, creator=user_a, name="Alpha")

    with session_local() as db:
        assert project_locks.get_lock(db, project.id) is None
        update_scenario_metadata(
            project.name,
            UpdateScenarioRequest(),
            db=db,
            user=db.get(User, user_a.id),
        )
        assert project_locks.get_lock(db, project.id) is None, (
            "a no-op PATCH must not gate, and must not acquire"
        )


def test_non_empty_scenario_metadata_patch_still_gates(session_local):
    from fastapi import HTTPException

    from models.schemas import UpdateScenarioRequest
    from routers.projects import update_scenario_metadata
    from services import project_locks

    org = _create_org(session_local)
    user_a = _create_user(session_local, email="a@example.com")
    user_b = _create_user(session_local, email="b@example.com")
    _add_membership(session_local, user_id=user_a.id, org_id=org.id, role="admin")
    _add_membership(session_local, user_id=user_b.id, org_id=org.id, role="admin")
    project = _create_project(session_local, org=org, creator=user_a, name="Alpha")

    with session_local() as db:
        assert project_locks.acquire_lock(db, project.id, user_b.id) is not None
        with pytest.raises(HTTPException) as exc:
            update_scenario_metadata(
                project.name,
                UpdateScenarioRequest(description="new"),
                db=db,
                user=db.get(User, user_a.id),
            )
        assert exc.value.status_code == 409
        assert exc.value.detail["error_kind"] == "project_locked"


# ── M1: a lock serialisation failure must not turn a 409 into a 500 ────────


def test_serialize_project_lock_returns_none_on_db_error(session_local, monkeypatch):
    from routers.projects import _serialize_project_lock

    org = _create_org(session_local)
    user_a = _create_user(session_local, email="a@example.com")
    _add_membership(session_local, user_id=user_a.id, org_id=org.id, role="admin")
    project = _create_project(session_local, org=org, creator=user_a, name="Alpha")

    def _raise(*_args, **_kwargs):
        raise RuntimeError("simulated DB error (e.g. StaleDataError on prune)")

    monkeypatch.setattr("services.project_locks.get_lock", _raise)

    with session_local() as db:
        assert _serialize_project_lock(db, project.id, db.get(User, user_a.id)) is None


# ── `_check_project_lock`: the check-only sibling (D8) ──────────────────────
#
# Landed with the uploads write edges but shipped without a test. The
# distinction it encodes is the whole point and is invisible to a suite that
# only asserts the 409: `_enforce_project_lock` ACQUIRES on write, which is
# right for edges that ARE the edit (save/rename/delete), and wrong for
# incidental ones (an attachment upload, a canvas layout flush) where it lets
# a passive caller take an idle project's lock just by touching it and leaves
# a 120 s claim behind. So the assertion that matters is the NEGATIVE one:
# after a permitted call, no lock exists.

def test_check_project_lock_does_not_acquire_on_a_free_project(session_local):
    """The sibling-path assertion: permitted, and NO lock created."""
    from routers.projects import _check_project_lock

    _, get_lock, _, _, _ = _service()
    org = _create_org(session_local)
    user_a = _create_user(session_local, email="checkfree@example.com")
    _add_membership(session_local, user_id=user_a.id, org_id=org.id, role="admin")
    project = _create_project(session_local, org=org, creator=user_a, name="FreeCheck")

    with session_local() as db:
        _check_project_lock(db, project, user_a)   # must not raise
        assert get_lock(db, project.id) is None, (
            "check-only must not ACQUIRE — acquiring here would let a passive "
            "upload or layout flush claim an idle project and keep renewing it"
        )


def test_check_project_lock_refuses_a_live_foreign_lock(session_local):
    from fastapi import HTTPException

    from routers.projects import _check_project_lock

    acquire_lock, _, _, _, _ = _service()
    org = _create_org(session_local)
    holder = _create_user(session_local, email="holder@example.com")
    other = _create_user(session_local, email="other@example.com")
    for u in (holder, other):
        _add_membership(session_local, user_id=u.id, org_id=org.id, role="admin")
    project = _create_project(session_local, org=org, creator=holder, name="HeldCheck")

    with session_local() as db:
        assert acquire_lock(db, project.id, holder.id) is not None
        with pytest.raises(HTTPException) as ei:
            _check_project_lock(db, project, other)
        assert ei.value.status_code == 409
        assert ei.value.detail["error_kind"] == "project_locked"


def test_check_project_lock_passes_the_holders_own_lock(session_local):
    """The other sibling: your own lock must not refuse your own upload."""
    from routers.projects import _check_project_lock

    acquire_lock, _, _, _, _ = _service()
    org = _create_org(session_local)
    holder = _create_user(session_local, email="ownlock@example.com")
    _add_membership(session_local, user_id=holder.id, org_id=org.id, role="admin")
    project = _create_project(session_local, org=org, creator=holder, name="OwnCheck")

    with session_local() as db:
        assert acquire_lock(db, project.id, holder.id) is not None
        _check_project_lock(db, project, holder)   # must not raise
