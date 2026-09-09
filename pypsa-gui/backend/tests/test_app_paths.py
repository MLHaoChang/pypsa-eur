"""
Per-user writable locations (spec workstream D).

Guards the invariant that nothing the application writes resolves inside the
source tree — which is what breaks once the backend is frozen into a read-only
app bundle.
"""
import sys
from pathlib import Path

import pytest

import app_paths


def _app_data_base(home: Path) -> Path:
    """
    Where app-data lives on THIS platform, given `home`.

    The two rename tests below used to build a macOS
    `Library/Application Support` tree and assert against `app_data_dir()`,
    so on Linux and Windows they compared the macOS layout with the XDG or
    LOCALAPPDATA one and failed — not because the migration logic was wrong,
    but because the test was written on a Mac. They were red in CI for as
    long as anyone has looked, and a permanently red summary line is how a
    real failure hides.

    This mirrors `app_paths.app_data_dir`'s platform branch. That the two
    agree is pinned INDEPENDENTLY by
    `test_app_data_dir_is_platform_correct` above, so the duplication cannot
    quietly drift into agreeing with a broken implementation. And the
    preference rule those tests are really about — new wins unless only
    legacy exists — is now asserted directly against `_preferred`, with no
    platform knowledge at all.

    Callers must clear `XDG_DATA_HOME` / `LOCALAPPDATA` (see
    `_home_relative_app_data`) or the real environment escapes `home`.
    """
    if sys.platform == "darwin":
        return home / "Library" / "Application Support"
    if sys.platform == "win32":
        return home / "AppData" / "Local"
    return home / ".local" / "share"


@pytest.fixture()
def _home_relative_app_data(monkeypatch, tmp_path):
    """Pin `home` to a tmpdir and force the home-relative branch everywhere."""
    monkeypatch.delenv("PYPSAGUI_APP_DATA_DIR", raising=False)
    monkeypatch.delenv("PYPSAGUI_PROJECTS_ROOT", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(app_paths.Path, "home", staticmethod(lambda: tmp_path))
    return tmp_path



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


def test_a_fresh_install_uses_the_new_name(monkeypatch, tmp_path):
    """The product is PyPSA Studio; a machine with no history says so."""
    import app_paths

    monkeypatch.delenv("PYPSAGUI_APP_DATA_DIR", raising=False)
    monkeypatch.setattr(app_paths.Path, "home", staticmethod(lambda: tmp_path))

    assert app_paths.app_data_dir().name == "PyPSA Studio"
    assert app_paths.default_projects_root().parent.name == "PyPSA Studio"


def test_an_EXISTING_install_keeps_its_data_after_the_rename(_home_relative_app_data):
    """
    `APP_NAME` is not a label — it is the directory the user's projects live in.
    Renaming it outright points a working install at empty folders: the app
    opens, lists nothing, and the projects are still on disk under the old name
    with nothing saying so. That is indistinguishable from data loss to the
    person it happens to.

    So the old location wins whenever it exists and the new one does not.
    """
    import app_paths

    tmp_path = _home_relative_app_data

    legacy_data = _app_data_base(tmp_path) / "PyPSA GUI"
    legacy_data.mkdir(parents=True)
    (legacy_data / "pypsa-gui.db").write_text("")
    legacy_projects = tmp_path / "Documents" / "PyPSA GUI" / "Projects"
    legacy_projects.mkdir(parents=True)

    assert app_paths.app_data_dir() == legacy_data.resolve()
    assert app_paths.default_projects_root() == legacy_projects.resolve()


def test_the_new_location_wins_once_it_exists(_home_relative_app_data):
    """
    Otherwise a stale empty "PyPSA GUI" folder — one `mkdir` from any earlier
    launch — would pin every future install to the old name forever.
    """
    import app_paths

    tmp_path = _home_relative_app_data

    (_app_data_base(tmp_path) / "PyPSA GUI").mkdir(parents=True)
    new = _app_data_base(tmp_path) / "PyPSA Studio"
    new.mkdir(parents=True)

    assert app_paths.app_data_dir() == new.resolve()


def test_the_env_override_still_beats_both(monkeypatch, tmp_path):
    """The harnesses and the run-books depend on this."""
    import app_paths

    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "chosen"))
    (tmp_path / "Library" / "Application Support" / "PyPSA GUI").mkdir(parents=True)

    assert app_paths.app_data_dir() == (tmp_path / "chosen").resolve()


# ─────────────────────────────────────────────────────────────────────────
# `_preferred` is the whole rename rule, and it has no platform in it. The
# two tests above reach it through `app_data_dir`, which is worth keeping —
# that is how production calls it — but a bug in the rule itself should not
# need a platform-shaped fixture to show up.
# ─────────────────────────────────────────────────────────────────────────


def test_preferred_takes_the_new_name_when_both_exist(tmp_path):
    """A stale empty legacy folder must not pin the install to the old name."""
    (tmp_path / app_paths.LEGACY_APP_NAME).mkdir()
    (tmp_path / app_paths.APP_NAME).mkdir()
    assert app_paths._preferred(tmp_path) == (tmp_path / app_paths.APP_NAME).resolve()


def test_preferred_falls_back_to_the_legacy_name_when_only_it_exists(tmp_path):
    """
    `APP_NAME` is not a label — it is the directory the user's data is in.
    Taking the new name here points a working install at empty folders: the
    app opens, lists nothing, and the projects are still on disk under the
    old name with nothing saying so.
    """
    (tmp_path / app_paths.LEGACY_APP_NAME).mkdir()
    assert app_paths._preferred(tmp_path) == (tmp_path / app_paths.LEGACY_APP_NAME).resolve()


def test_preferred_takes_the_new_name_on_a_fresh_machine(tmp_path):
    """Neither exists: a first run must not be born under the legacy name."""
    assert app_paths._preferred(tmp_path) == (tmp_path / app_paths.APP_NAME).resolve()
