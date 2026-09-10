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


def test_app_data_dir_is_platform_correct(monkeypatch, tmp_path):
    """
    `home` is pinned to a tmpdir, not just the env override.

    These two asserted the literal "PyPSA GUI" and kept passing after the
    rename — because the DEVELOPER'S machine has a legacy
    `~/Library/Application Support/PyPSA GUI` from earlier runs, so the
    compatibility fallback returned it. Green here, red on a clean checkout and
    in CI: the test was reading the machine, not the code.
    """
    monkeypatch.delenv("PYPSAGUI_APP_DATA_DIR", raising=False)
    monkeypatch.setattr(app_paths.Path, "home", staticmethod(lambda: tmp_path))

    d = app_paths.app_data_dir()
    if sys.platform == "darwin":
        assert d.parts[-3:] == ("Library", "Application Support", "PyPSA Studio")
    elif sys.platform == "win32":
        assert d.name == "PyPSA Studio"
    else:
        assert "pypsa studio" in str(d).lower()


def test_projects_root_default_is_user_visible(monkeypatch, tmp_path):
    monkeypatch.delenv("PYPSAGUI_PROJECTS_ROOT", raising=False)
    monkeypatch.setattr(app_paths.Path, "home", staticmethod(lambda: tmp_path))

    r = app_paths.default_projects_root()

    assert r.is_absolute()
    assert r.parts[-2:] == ("PyPSA Studio", "Projects")


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


# ── the rename to "PyPSA Studio" ────────────────────────────────────────────


def _app_data_base(tmp_path, monkeypatch) -> Path:
    """
    The platform's app-data PARENT under a pinned `home`, mirroring
    `app_paths.app_data_dir()`'s own `sys.platform` branch.

    The two rename tests below are about `_preferred()` — old location wins
    while only it exists, new location wins as soon as it appears — and that
    logic is platform-independent. They used to hard-code macOS's
    `Library/Application Support`, so they could only ever pass on a Mac: red
    on Linux, and therefore red in any CI that ran this suite. Deriving the
    base the way the code derives it keeps exactly what they assert and lets
    them assert it everywhere, which is the same correction
    `test_app_data_dir_is_platform_correct` already documents ("the test was
    reading the machine, not the code").

    `default_projects_root()` needs no equivalent: it is `home/Documents` on
    every platform.
    """
    if sys.platform == "darwin":
        return tmp_path / "Library" / "Application Support"
    if sys.platform == "win32":
        base = tmp_path / "AppData" / "Local"
        monkeypatch.setenv("LOCALAPPDATA", str(base))
        return base
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    return tmp_path / ".local" / "share"


def test_a_fresh_install_uses_the_new_name(monkeypatch, tmp_path):
    """The product is PyPSA Studio; a machine with no history says so."""
    import app_paths

    monkeypatch.delenv("PYPSAGUI_APP_DATA_DIR", raising=False)
    monkeypatch.setattr(app_paths.Path, "home", staticmethod(lambda: tmp_path))

    assert app_paths.app_data_dir().name == "PyPSA Studio"
    assert app_paths.default_projects_root().parent.name == "PyPSA Studio"


def test_an_EXISTING_install_keeps_its_data_after_the_rename(monkeypatch, tmp_path):
    """
    `APP_NAME` is not a label — it is the directory the user's projects live in.
    Renaming it outright points a working install at empty folders: the app
    opens, lists nothing, and the projects are still on disk under the old name
    with nothing saying so. That is indistinguishable from data loss to the
    person it happens to.

    So the old location wins whenever it exists and the new one does not.
    """
    import app_paths

    monkeypatch.delenv("PYPSAGUI_APP_DATA_DIR", raising=False)
    monkeypatch.setattr(app_paths.Path, "home", staticmethod(lambda: tmp_path))

    legacy_data = _app_data_base(tmp_path, monkeypatch) / "PyPSA GUI"
    legacy_data.mkdir(parents=True)
    (legacy_data / "pypsa-gui.db").write_text("")
    legacy_projects = tmp_path / "Documents" / "PyPSA GUI" / "Projects"
    legacy_projects.mkdir(parents=True)

    assert app_paths.app_data_dir() == legacy_data.resolve()
    assert app_paths.default_projects_root() == legacy_projects.resolve()


def test_the_new_location_wins_once_it_exists(monkeypatch, tmp_path):
    """
    Otherwise a stale empty "PyPSA GUI" folder — one `mkdir` from any earlier
    launch — would pin every future install to the old name forever.
    """
    import app_paths

    monkeypatch.delenv("PYPSAGUI_APP_DATA_DIR", raising=False)
    monkeypatch.setattr(app_paths.Path, "home", staticmethod(lambda: tmp_path))

    base = _app_data_base(tmp_path, monkeypatch)
    (base / "PyPSA GUI").mkdir(parents=True)
    new = base / "PyPSA Studio"
    new.mkdir(parents=True)

    assert app_paths.app_data_dir() == new.resolve()


def test_the_env_override_still_beats_both(monkeypatch, tmp_path):
    """The harnesses and the run-books depend on this."""
    import app_paths

    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "chosen"))
    (tmp_path / "Library" / "Application Support" / "PyPSA GUI").mkdir(parents=True)

    assert app_paths.app_data_dir() == (tmp_path / "chosen").resolve()
