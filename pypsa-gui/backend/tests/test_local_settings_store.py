"""
The store behind the desktop Settings pane.

`app_paths.app_data_dir()` reads PYPSAGUI_APP_DATA_DIR on every call and caches
nothing, so a monkeypatched environment is all the isolation these tests need —
no `get_settings.cache_clear()`, no module reloading.

THE KEY LIVES IN `user.env` NOW, not in `local-settings.json`. This module
delegates storage to `services.app_secrets` after the two stores collided in
the master merge (docs/superpowers/findings/2026-08-05-api-key-store-collision.md).
The tests below therefore assert against `app_paths.user_env_file()` wherever
they used to assert against `settings_path()`. `local-settings.json` is still
read — as a migration source, and for any future non-secret setting — so the
robustness tests on `read_settings` stay exactly as they were.
"""
import json
import logging
import os
import stat
import sys

import pytest

import app_paths
import local_settings
from services import app_secrets


@pytest.fixture(autouse=True)
def _forget_shell_names(monkeypatch):
    """
    Reset `app_secrets._SHELL_NAMES` between tests.

    It is module-level state captured by `bootstrap_environment`, and conftest
    imports `main` once per session — so without this, whatever the environment
    looked like at that first import leaks into every test here and decides
    whether `set_secret` is allowed to touch `os.environ`.
    """
    monkeypatch.setattr(app_secrets, "_SHELL_NAMES", frozenset())


@pytest.fixture
def appdata(tmp_path, monkeypatch):
    """Point app-data at a temp dir. MANDATORY: without it these tests write
    into the developer's real ~/Library/Application Support/PyPSA Studio/."""
    target = tmp_path / "appdata"
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(target))
    return target


def test_write_then_read_round_trips(appdata):
    local_settings.write_api_key("sk-ant-abc123def456")

    assert local_settings.stored_api_key() == "sk-ant-abc123def456"


def test_empty_string_removes_the_key_entirely(appdata):
    local_settings.write_api_key("sk-ant-abc123def456")

    local_settings.write_api_key("")

    assert local_settings.stored_api_key() is None
    body = app_paths.user_env_file().read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY" not in body, (
        "an empty string must remove the entry, not store an empty value — "
        "otherwise absence has two representations"
    )


def test_surrounding_whitespace_is_stripped(appdata):
    local_settings.write_api_key("  sk-ant-abc123def456\n")

    assert local_settings.stored_api_key() == "sk-ant-abc123def456"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_the_key_file_is_created_with_mode_600(appdata):
    """
    Set at creation via os.open, never by a chmod afterwards — a chmod leaves a
    window in which a live API key is world-readable.

    `app_secrets` owns the write now, and pins this itself. Kept here too
    because it is the property this module's callers depend on, and a future
    change that routed `write_api_key` back to a hand-rolled writer would pass
    every other test in this file.
    """
    local_settings.write_api_key("sk-ant-abc123def456")

    mode = stat.S_IMODE(app_paths.user_env_file().stat().st_mode)
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_rewrite_keeps_mode_600(appdata):
    """O_CREAT applies a mode only when it CREATES; a rewrite must re-assert it."""
    local_settings.write_api_key("sk-ant-first-key-value")
    local_settings.write_api_key("sk-ant-second-key-value")

    mode = stat.S_IMODE(app_paths.user_env_file().stat().st_mode)
    assert mode == 0o600, f"expected 0o600 after rewrite, got {oct(mode)}"


def test_a_failed_replace_leaves_the_legacy_file_intact(appdata, monkeypatch):
    """
    Pins write-to-temp-then-`os.replace` in `_remove_legacy_key`, not
    write-in-place.

    This is the module's remaining atomic write. Catches a regression that
    opens `settings_path()` directly instead of swapping a temp file in: under
    it, the old contents would already be destroyed by the time this test could
    observe a failure. If the regression drops `os.replace` altogether, this
    monkeypatch never fires and the write silently "succeeds" — caught by the
    missing `pytest.raises`.
    """
    path = local_settings.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"anthropic_api_key": "sk-ant-legacy", "other": "keep"}))

    def boom(*_args, **_kwargs):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", boom)

    with pytest.raises(OSError):
        local_settings._remove_legacy_key()

    assert local_settings.legacy_stored_api_key() == "sk-ant-legacy"
    assert local_settings.read_settings()["other"] == "keep"


def test_a_failed_write_leaves_no_tmp_file_and_original_untouched(appdata, monkeypatch):
    """
    Covers the `except Exception: tmp.unlink(missing_ok=True); raise` cleanup
    branch, which nothing else in the suite exercises.

    Catches: removing that cleanup (or swallowing the exception instead of
    re-raising) would leave a stray `<file>.tmp` on disk — this test fails on
    that file's presence even though reading the settings back could not tell
    the difference.
    """
    path = local_settings.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"anthropic_api_key": "sk-ant-legacy"}))

    def boom(*_args, **_kwargs):
        raise ValueError("simulated dump failure")

    monkeypatch.setattr(json, "dump", boom)

    with pytest.raises(ValueError):
        local_settings._remove_legacy_key()

    assert local_settings.legacy_stored_api_key() == "sk-ant-legacy"
    tmp = path.with_name(path.name + ".tmp")
    assert not tmp.exists()


def test_malformed_json_is_ignored_rather_than_raised(appdata, caplog):
    path = local_settings.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not json", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        assert local_settings.legacy_stored_api_key() is None

    assert "not valid JSON" in caplog.text


def test_invalid_utf8_is_ignored_rather_than_raised(appdata, caplog):
    """
    `UnicodeDecodeError` is a `ValueError` subclass, not an `OSError` subclass,
    so it slips past `except OSError` in `read_settings` unless caught
    explicitly. Left unhandled, a corrupted/tampered settings file would raise
    out of a function documented as "NEVER raises" — and since Task 2 calls
    `apply_to_environ()` at `main.py` import time, that would stop the whole
    app from launching.
    """
    path = local_settings.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xfe\x00 not utf-8")

    with caplog.at_level(logging.WARNING):
        assert local_settings.read_settings() == {}

    assert "not valid UTF-8" in caplog.text


def test_a_json_array_is_ignored_rather_than_raised(appdata):
    path = local_settings.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('["not", "an", "object"]', encoding="utf-8")

    assert local_settings.read_settings() == {}


def test_missing_app_data_directory_is_created(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "no" / "such" / "dir"))

    local_settings.write_api_key("sk-ant-abc123def456")

    assert local_settings.stored_api_key() == "sk-ant-abc123def456"


def test_hint_is_the_last_four_characters():
    assert local_settings.api_key_hint("sk-ant-abcd1234") == "1234"


def test_hint_is_none_for_a_short_key():
    """Four of seven characters would disclose most of the value."""
    assert local_settings.api_key_hint("sk-ant") is None
    assert local_settings.api_key_hint(None) is None


def _write_legacy(key: str) -> None:
    path = local_settings.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"anthropic_api_key": key}), encoding="utf-8")


def test_migration_moves_a_legacy_key_and_publishes_it(appdata, monkeypatch):
    """The upgrade path: a user whose key predates `user.env` keeps working."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _write_legacy("sk-ant-from-the-json-file")

    assert local_settings.migrate_api_key_to_app_secrets() is True

    assert local_settings.stored_api_key() == "sk-ant-from-the-json-file"
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-from-the-json-file"
    assert local_settings.legacy_stored_api_key() is None, "the legacy entry must be dropped"


def test_migration_never_overwrites_a_key_already_in_user_env(appdata, monkeypatch):
    """
    The collision case, and the direction matters.

    `user.env` is what the process has actually been reading since the merge,
    so it is the key the user has been successfully using. Preferring the JSON
    file would swap a working key for one that has been inert.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    local_settings.write_api_key("sk-ant-already-in-user-env")
    _write_legacy("sk-ant-stale-in-the-json-file")

    assert local_settings.migrate_api_key_to_app_secrets() is False

    assert local_settings.stored_api_key() == "sk-ant-already-in-user-env"
    assert local_settings.legacy_stored_api_key() is None, (
        "the losing entry must still be dropped, or the Settings pane goes on "
        "reading a store nothing consumes — the defect this replaced"
    )


def test_migration_is_idempotent(appdata, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _write_legacy("sk-ant-from-the-json-file")

    assert local_settings.migrate_api_key_to_app_secrets() is True
    assert local_settings.migrate_api_key_to_app_secrets() is False
    assert local_settings.stored_api_key() == "sk-ant-from-the-json-file"


def test_migration_is_a_no_op_with_nothing_to_migrate(appdata, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    assert local_settings.migrate_api_key_to_app_secrets() is False
    assert "ANTHROPIC_API_KEY" not in os.environ


def test_migration_never_raises(appdata, monkeypatch, caplog):
    """
    It runs at `main.py` import time. An app-data problem must never be the
    reason the app will not start.
    """
    _write_legacy("sk-ant-from-the-json-file")

    def boom(*_args, **_kwargs):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(app_secrets, "set_secret", boom)

    with caplog.at_level(logging.WARNING):
        assert local_settings.migrate_api_key_to_app_secrets() is False

    assert "could not migrate" in caplog.text
    assert local_settings.legacy_stored_api_key() == "sk-ant-from-the-json-file", (
        "a failed migration must leave the source intact, or the key is lost"
    )
