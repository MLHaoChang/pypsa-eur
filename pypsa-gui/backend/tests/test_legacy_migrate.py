from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from db.models import Organization, OrgMembership, Project, ProjectMembership, User
from services.auth_service import hash_password
from services.legacy_migrate import claim_legacy_project
from settings import get_settings


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


@pytest.fixture
def legacy_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSA_GUI_AUTH_ENABLED", "true")
    monkeypatch.setenv("PROJECTS_ROOT", str(tmp_path / "tenant-projects"))
    monkeypatch.setenv("LEGACY_ROOT", str(tmp_path / "legacy-unclaimed"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def session_local(db_engine, legacy_env):
    return sessionmaker(autocommit=False, autoflush=False, bind=db_engine)


@pytest.fixture
def legacy_root_dir():
    legacy_root = get_settings().legacy_root
    legacy_root.mkdir(parents=True, exist_ok=True)
    return legacy_root


@pytest.fixture
def org(session_local) -> Organization:
    with session_local() as db:
        organization = Organization(name=f"Org {uuid.uuid4()}", created_at=_now())
        db.add(organization)
        db.commit()
        db.refresh(organization)
        return organization


@pytest.fixture
def admin(session_local, org) -> User:
    with session_local() as db:
        user = User(
            email="admin@example.com",
            password_hash=hash_password("secret-pass"),
            status="active",
            is_super_admin=False,
            created_at=_now(),
        )
        db.add(user)
        db.flush()
        db.add(OrgMembership(user_id=user.id, org_id=org.id, role="admin"))
        db.commit()
        db.refresh(user)
        return user


@pytest.fixture
def member(session_local, org) -> User:
    with session_local() as db:
        user = User(
            email="member@example.com",
            password_hash=hash_password("secret-pass"),
            status="active",
            is_super_admin=False,
            created_at=_now(),
        )
        db.add(user)
        db.flush()
        db.add(OrgMembership(user_id=user.id, org_id=org.id, role="member"))
        db.commit()
        db.refresh(user)
        return user


def _write_legacy_project(root_dir, name: str, *, parent_project: str | None = None) -> None:
    project_dir = root_dir / name
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "metadata.json").write_text(
        json.dumps(
            {
                "name": name,
                "parent_project": parent_project,
                "scenario_description": None,
            }
        ),
        encoding="utf-8",
    )
    (project_dir / "network.nc").write_text(f"network:{name}", encoding="utf-8")
    (project_dir / "payload.txt").write_text(f"payload:{name}", encoding="utf-8")


def test_claim_tree_relinks_parent(session_local, org, admin, legacy_root_dir) -> None:
    _write_legacy_project(legacy_root_dir, "Root")
    _write_legacy_project(legacy_root_dir, "Child", parent_project="Root")

    with session_local() as db:
        result = claim_legacy_project(db, "Root", org.id, admin.id, [], include_descendants=True)
        child = db.scalar(select(Project).where(Project.name == "Child"))

        assert result.root.name == "Root"
        assert child is not None
        assert child.parent_project_id == result.root.id
        assert child.org_id == org.id


def test_claim_assigns_owner_and_members_on_root_only(
    session_local,
    org,
    admin,
    member,
    legacy_root_dir,
) -> None:
    _write_legacy_project(legacy_root_dir, "Root")
    _write_legacy_project(legacy_root_dir, "Child", parent_project="Root")

    with session_local() as db:
        result = claim_legacy_project(
            db,
            "Root",
            org.id,
            admin.id,
            [member.id],
            include_descendants=True,
        )

        memberships = db.scalars(
            select(ProjectMembership).where(ProjectMembership.project_id == result.root.id)
        ).all()
        child = db.scalar(select(Project).where(Project.name == "Child"))

        assert child is not None
        child_memberships = db.scalars(
            select(ProjectMembership).where(ProjectMembership.project_id == child.id)
        ).all()
        assert {row.user_id for row in memberships} == {admin.id, member.id}
        assert child_memberships == []
        assert not (legacy_root_dir / "Root").exists()
        assert not (legacy_root_dir / "Child").exists()
        assert (get_settings().projects_root / str(org.id) / str(result.root.id) / "payload.txt").exists()
