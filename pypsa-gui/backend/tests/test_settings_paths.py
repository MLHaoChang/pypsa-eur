"""
Settings path defaults (spec workstream D).

Two things are load-bearing here and easy to get wrong:

  * The defaults must be `Field(default_factory=...)`, not class-body
    expressions. A bare `= app_paths.x()` is evaluated once at `import
    settings`, so anything that sets PYPSAGUI_APP_DATA_DIR afterwards — a
    test, or the desktop launcher — gets the stale value.
  * `settings.py` declares `env_file=<backend>/.env`, and pydantic-settings
    ranks dotenv ABOVE field defaults. Any test probing a DEFAULT must build
    `Settings(_env_file=None)`; `monkeypatch.delenv` alone does not reach it.
"""
from pathlib import Path

import pytest

import app_paths
import settings as settings_module


def test_projects_root_default_is_outside_the_source_tree(monkeypatch, tmp_path):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.delenv("PROJECTS_ROOT", raising=False)
    monkeypatch.setenv("PYPSAGUI_PROJECTS_ROOT", str(tmp_path / "projects"))
    settings_module.get_settings.cache_clear()
    try:
        s = settings_module.Settings(_env_file=None)
        backend = Path(app_paths.__file__).resolve().parent
        assert backend not in Path(s.projects_root).parents
    finally:
        settings_module.get_settings.cache_clear()


def test_legacy_root_is_env_overridable(monkeypatch, tmp_path):
    monkeypatch.setenv("LEGACY_ROOT", str(tmp_path / "legacy"))
    settings_module.get_settings.cache_clear()
    try:
        assert Path(settings_module.get_settings().legacy_root) == tmp_path / "legacy"
    finally:
        settings_module.get_settings.cache_clear()


def test_database_url_default_is_sqlite_not_postgres(monkeypatch, tmp_path):
    """
    Probes the FIELD DEFAULT, with the dotenv source disabled — `backend/.env`
    carries a CWD-relative DATABASE_URL that outranks it.
    """
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    url = settings_module.Settings(_env_file=None).database_url
    assert url.startswith("sqlite+pysqlite:///")
    assert "auth_dev.db" not in url


def test_path_defaults_read_the_env_per_instantiation(monkeypatch, tmp_path):
    """
    Regression guard for the class-body-vs-default_factory trap. A class-body
    default freezes at import; only default_factory re-reads the environment.
    """
    # FLAT_PROJECTS_ROOT is pinned by conftest, and an explicit env var outranks
    # the field default entirely — leave it set and this probes nothing.
    monkeypatch.delenv("FLAT_PROJECTS_ROOT", raising=False)
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "first"))
    first = settings_module.Settings(_env_file=None).flat_projects_root
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "second"))
    second = settings_module.Settings(_env_file=None).flat_projects_root
    assert first != second, (
        "flat_projects_root is frozen at import — declare it with "
        "Field(default_factory=...) so the env is read per instantiation"
    )


@pytest.mark.parametrize("var", ["PYPSAGUI_APP_DATA_DIR", "LEGACY_ROOT", "FLAT_PROJECTS_ROOT"])
def test_conftest_pins_the_app_data_paths(var):
    """Without these pins the suite writes into the developer's real app-data dir."""
    import os

    assert os.environ.get(var), (
        f"conftest must pin {var} now that its default lives in app-data"
    )
