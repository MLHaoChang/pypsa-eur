"""
A fresh dev boot with no `.env` creates the sign-in AGENTS.md describes:
the app-data directory, the generated password file and `admin@example.com`
with a personal workspace.
"""
from __future__ import annotations

import stat

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app_paths
import local_bootstrap
from db.models import OrgMembership, User
from services.auth_service import verify_password
from settings import get_settings


@pytest.fixture
def fresh_app_data(tmp_path, monkeypatch):
    data_dir = tmp_path / "PyPSA Studio"  # not created: a fresh machine
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(data_dir))
    monkeypatch.setenv("DATABASE_URL", app_paths.default_database_url())
    get_settings.cache_clear()
    engine = create_engine(app_paths.default_database_url())
    yield data_dir, sessionmaker(bind=engine, autoflush=False)
    engine.dispose()
    get_settings.cache_clear()


def test_is_default_sqlite(fresh_app_data):
    assert local_bootstrap.is_default_sqlite(app_paths.default_database_url())
    assert not local_bootstrap.is_default_sqlite("postgresql://u:p@h/db")
    assert not local_bootstrap.is_default_sqlite("sqlite+pysqlite:///:memory:")


def test_fresh_boot_creates_dir_password_and_workspace(fresh_app_data):
    data_dir, factory = fresh_app_data
    assert not data_dir.exists()

    pw_file = local_bootstrap.ensure_dev_admin(factory)

    assert pw_file == data_dir / "dev-admin-password"
    assert stat.S_IMODE(pw_file.stat().st_mode) == 0o600
    password = pw_file.read_text().strip()
    assert password and "\n" not in password
    with factory() as db:
        user = db.scalar(select(User).where(User.email == "admin@example.com"))
        assert user.is_super_admin and user.status == "active"
        assert verify_password(password, user.password_hash)
        assert db.scalar(select(OrgMembership).where(OrgMembership.user_id == user.id))


def test_second_boot_keeps_password_and_workspace(fresh_app_data):
    _, factory = fresh_app_data
    pw_file = local_bootstrap.ensure_dev_admin(factory)
    first = pw_file.read_text()
    local_bootstrap.ensure_dev_admin(factory)

    assert pw_file.read_text() == first
    with factory() as db:
        assert len(db.scalars(select(OrgMembership)).all()) == 1
