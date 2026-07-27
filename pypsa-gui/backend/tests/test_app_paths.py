"""
Per-user writable locations (spec workstream D).

Guards the invariant that nothing the application writes resolves inside the
source tree — which is what breaks once the backend is frozen into a read-only
app bundle.
"""
import sys
from pathlib import Path

import app_paths


def test_app_data_dir_is_absolute_and_outside_the_source_tree():
    d = app_paths.app_data_dir()
    assert d.is_absolute()
    backend = Path(app_paths.__file__).resolve().parent
    assert backend not in d.parents and d != backend


def test_app_data_dir_is_platform_correct():
    d = app_paths.app_data_dir()
    if sys.platform == "darwin":
        assert d.parts[-3:] == ("Library", "Application Support", "PyPSA GUI")
    elif sys.platform == "win32":
        assert d.name == "PyPSA GUI"
    else:
        assert "pypsa gui" in str(d).lower()


def test_projects_root_default_is_user_visible():
    r = app_paths.default_projects_root()
    assert r.is_absolute()
    assert r.parts[-2:] == ("PyPSA GUI", "Projects")


def test_flat_root_is_distinct_from_projects_root():
    """Different stores with different layouts — see Task 3."""
    assert app_paths.default_flat_projects_root() != app_paths.default_projects_root()


def test_database_url_is_absolute_sqlite():
    url = app_paths.default_database_url()
    assert url.startswith("sqlite+pysqlite:///")
    assert Path(url.removeprefix("sqlite+pysqlite:///")).is_absolute()


def test_env_overrides_win(monkeypatch, tmp_path):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "custom"))
    assert app_paths.app_data_dir() == (tmp_path / "custom").resolve()
