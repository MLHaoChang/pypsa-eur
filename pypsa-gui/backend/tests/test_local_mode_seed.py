"""
Single local identity (spec workstream B).

The desktop build has no login. Rather than delete the tenancy layer — 68
require_user/optional_user sites, org-scoped storage, a large suite — local
mode seeds ONE org + user + membership and injects that user on every request,
so every downstream check passes for the reason it was written to pass.

Constraints the seed has to satisfy, all verified in db/models.py:
  Organization.created_at:17   NOT NULL, no Python default
  User.password_hash:25        nullable — there is no login to perform
  User.status:26               defaults "invited"; auth_service rejects that
  User.is_super_admin:27       defaults False; /api/admin/* needs True
  User.created_at:28           NOT NULL, no Python default
  OrgMembership.role:38        NOT NULL, no default; "admin" is the only
                               see-everything short-circuit in project_acl
  OrgMembership:33             UniqueConstraint("user_id") — one org per user
"""
import uuid

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import local_mode
from db.models import Base, OrgMembership, Organization, User


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'x.db').as_posix()}")
    Base.metadata.create_all(bind=engine)
    with sessionmaker(bind=engine)() as s:
        yield s
    engine.dispose()


@pytest.mark.parametrize(
    "value,expected",
    [
        ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
        ("0", False), ("false", False), ("", False), ("  ", False),
    ],
)
def test_is_local_mode_reads_env(monkeypatch, value, expected):
    monkeypatch.setenv("PYPSAGUI_LOCAL_MODE", value)
    assert local_mode.is_local_mode() is expected


def test_is_local_mode_defaults_off(monkeypatch):
    monkeypatch.delenv("PYPSAGUI_LOCAL_MODE", raising=False)
    assert local_mode.is_local_mode() is False


def test_is_local_mode_is_read_per_call(monkeypatch):
    """
    Load-bearing: the whole test strategy for tasks 8+ depends on there being
    no caching, so the one app object conftest imported serves both modes and
    nothing has to be re-imported.
    """
    monkeypatch.delenv("PYPSAGUI_LOCAL_MODE", raising=False)
    assert local_mode.is_local_mode() is False
    monkeypatch.setenv("PYPSAGUI_LOCAL_MODE", "1")
    assert local_mode.is_local_mode() is True


def test_seed_creates_org_user_and_membership(db):
    user = local_mode.ensure_local_identity(db)
    assert user.id == local_mode.LOCAL_USER_ID
    assert user.status == "active"
    assert user.is_super_admin is True
    assert user.password_hash is None
    assert db.get(Organization, local_mode.LOCAL_ORG_ID) is not None
    m = db.scalar(select(OrgMembership).where(OrgMembership.user_id == user.id))
    assert m is not None
    assert m.role == "admin"
    assert m.org_id == local_mode.LOCAL_ORG_ID


def test_seed_is_idempotent(db):
    a = local_mode.ensure_local_identity(db)
    b = local_mode.ensure_local_identity(db)
    assert a.id == b.id
    assert len(db.scalars(select(User)).all()) == 1
    assert len(db.scalars(select(OrgMembership)).all()) == 1


def test_get_local_user_returns_none_on_an_unseeded_db(db):
    assert local_mode.get_local_user(db) is None


def test_remove_local_identity_is_a_clean_round_trip(db):
    """
    Test fixtures for tasks 8+ rely on this: conftest's shared database
    persists users and orgs across the whole session by design, so a fixture
    that seeds without removing leaks a super-admin into every later test.
    """
    local_mode.ensure_local_identity(db)
    local_mode.remove_local_identity(db)
    assert local_mode.get_local_user(db) is None
    assert db.get(Organization, local_mode.LOCAL_ORG_ID) is None
    assert db.scalars(select(OrgMembership)).all() == []
    local_mode.ensure_local_identity(db)  # must be re-seedable
    assert local_mode.get_local_user(db) is not None


def test_remove_is_safe_on_an_unseeded_db(db):
    local_mode.remove_local_identity(db)  # must not raise


def test_ids_are_stable_constants():
    """
    projects_root/<org_id>/<project_id>/ embeds the org id, so a regenerated
    id would orphan every project directory on reinstall.
    """
    assert isinstance(local_mode.LOCAL_ORG_ID, uuid.UUID)
    assert isinstance(local_mode.LOCAL_USER_ID, uuid.UUID)
