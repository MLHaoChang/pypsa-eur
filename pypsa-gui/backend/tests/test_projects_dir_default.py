"""
The flat project root (spec workstream D).

TWO ROOTS, DELIBERATELY. Do not merge them:

  routers.projects.PROJECTS_DIR   flat,       <root>/<display-name>/network.nc
  settings.projects_root          org-scoped, <root>/<org_uuid>/<project_uuid>/

`conftest.py` pins PROJECTS_ROOT to one tmpdir and separately monkeypatches
PROJECTS_DIR to a different one — they are not interchangeable. Pointing
PROJECTS_DIR at projects_root makes every `PROJECTS_DIR / <display-name>`
lookup address an org-UUID directory instead: `_safe_project_dir`, the
legacy-mode fallback in `_resolve_project_src`, and `_unique_project_name`
all resolve against the wrong tree.

PROJECTS_DIR must also stay a settable module ATTRIBUTE: `conftest.py`'s
`tmp_projects_dir` fixture does `monkeypatch.setattr(projects_router,
"PROJECTS_DIR", d)`, which raises AttributeError if the name is gone. Nine
test files depend on that, directly or via the fixture.

Only the DEFAULT changes here — it was `__file__`-relative, which lands inside
a read-only app bundle once the backend is frozen.
"""
from pathlib import Path

import app_paths
import settings as settings_module


def test_flat_root_default_is_outside_the_source_tree(monkeypatch, tmp_path):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.delenv("FLAT_PROJECTS_ROOT", raising=False)
    settings_module.get_settings.cache_clear()
    try:
        root = Path(settings_module.Settings(_env_file=None).flat_projects_root)
        backend = Path(app_paths.__file__).resolve().parent
        assert backend not in root.parents and root != backend
    finally:
        settings_module.get_settings.cache_clear()


def test_flat_root_is_env_overridable(monkeypatch, tmp_path):
    monkeypatch.setenv("FLAT_PROJECTS_ROOT", str(tmp_path / "flat"))
    settings_module.get_settings.cache_clear()
    try:
        assert Path(settings_module.get_settings().flat_projects_root) == tmp_path / "flat"
    finally:
        settings_module.get_settings.cache_clear()


def test_projects_dir_attribute_still_exists_and_is_settable(monkeypatch, tmp_path):
    """conftest.py monkeypatches this attribute; nine test files depend on it."""
    from routers import projects as projects_router

    assert hasattr(projects_router, "PROJECTS_DIR")
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_path / "patched")
    assert projects_router.PROJECTS_DIR == tmp_path / "patched"


def test_projects_dir_default_is_outside_the_source_tree():
    """The actual defect being fixed: the default was __file__-relative."""
    from routers import projects as projects_router

    backend = Path(app_paths.__file__).resolve().parent
    resolved = Path(projects_router.PROJECTS_DIR).resolve()
    assert backend not in resolved.parents and resolved != backend


def test_flat_root_is_not_the_org_scoped_root(monkeypatch, tmp_path):
    """Different stores, different layouts. Merging them misdirects every flat lookup."""
    monkeypatch.setenv("PROJECTS_ROOT", str(tmp_path / "org"))
    monkeypatch.setenv("FLAT_PROJECTS_ROOT", str(tmp_path / "flat"))
    settings_module.get_settings.cache_clear()
    try:
        s = settings_module.get_settings()
        assert Path(s.projects_root) != Path(s.flat_projects_root)
    finally:
        settings_module.get_settings.cache_clear()
