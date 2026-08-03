"""
The store behind the desktop Settings pane.

`app_paths.app_data_dir()` reads PYPSAGUI_APP_DATA_DIR on every call and caches
nothing, so a monkeypatched environment is all the isolation these tests need —
no `get_settings.cache_clear()`, no module reloading.
"""
import json
import logging
import os
import stat
import sys

import pytest

import local_settings


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
    stored = json.loads(local_settings.settings_path().read_text(encoding="utf-8"))
    assert "anthropic_api_key" not in stored, (
        "an empty string must remove the entry, not store an empty value — "
        "otherwise absence has two representations"
    )


def test_surrounding_whitespace_is_stripped(appdata):
    local_settings.write_api_key("  sk-ant-abc123def456\n")

    assert local_settings.stored_api_key() == "sk-ant-abc123def456"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_file_is_created_with_mode_600(appdata):
    """
    Set at creation via os.open, never by a chmod afterwards — a chmod leaves a
    window in which a live API key is world-readable.
    """
    local_settings.write_api_key("sk-ant-abc123def456")

    mode = stat.S_IMODE(local_settings.settings_path().stat().st_mode)
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_rewrite_keeps_mode_600(appdata):
    """os.replace adopts the temp file's mode; a second write must not widen it."""
    local_settings.write_api_key("sk-ant-first-key-value")
    local_settings.write_api_key("sk-ant-second-key-value")

    mode = stat.S_IMODE(local_settings.settings_path().stat().st_mode)
    assert mode == 0o600, f"expected 0o600 after rewrite, got {oct(mode)}"


def test_a_failed_replace_leaves_the_original_file_intact(appdata, monkeypatch):
    """
    Pins write-to-temp-then-`os.replace`, not write-in-place.

    Catches: a regression that opens `settings_path()` directly (in place of
    the temp file) instead of swapping it in via `os.replace`. Under such a
    regression the OLD value would already be destroyed by the time this test
    could observe a failure — and if the regression also drops the call to
    `os.replace` entirely, this monkeypatch never fires and the write silently
    "succeeds", which is caught below by the missing `pytest.raises`. Also
    catches a swap done via something other than `os.replace` (e.g.
    `shutil.move`), which this patch would likewise not intercept.
    """
    local_settings.write_api_key("sk-ant-original-value")

    def boom(*_args, **_kwargs):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", boom)

    with pytest.raises(OSError):
        local_settings.write_api_key("sk-ant-new-value")

    assert local_settings.stored_api_key() == "sk-ant-original-value"


def test_a_failed_write_leaves_no_tmp_file_and_original_untouched(appdata, monkeypatch):
    """
    Covers the `except Exception: tmp.unlink(missing_ok=True); raise` cleanup
    branch in `write_api_key`, which nothing else in the suite exercises.

    Catches: removing that cleanup (or swallowing the exception instead of
    re-raising it) would leave a stray `<file>.tmp` on disk after a failed
    write — this test fails on that stray file's presence even though
    `stored_api_key()` alone couldn't tell the difference.
    """
    local_settings.write_api_key("sk-ant-original-value")

    def boom(*_args, **_kwargs):
        raise ValueError("simulated dump failure")

    monkeypatch.setattr(json, "dump", boom)

    with pytest.raises(ValueError):
        local_settings.write_api_key("sk-ant-new-value")

    assert local_settings.stored_api_key() == "sk-ant-original-value"
    path = local_settings.settings_path()
    tmp = path.with_name(path.name + ".tmp")
    assert not tmp.exists()


def test_malformed_json_is_ignored_rather_than_raised(appdata, caplog):
    path = local_settings.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not json", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        assert local_settings.stored_api_key() is None

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


def test_apply_to_environ_sets_an_unset_variable(appdata, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    local_settings.write_api_key("sk-ant-from-the-file")

    assert local_settings.apply_to_environ() is True
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-from-the-file"


def test_apply_to_environ_never_overrides_the_environment(appdata, monkeypatch):
    """
    Mirrors `load_dotenv(override=False)` at main.py:23. This is what keeps a
    web deployment, and a developer shell with the key exported, unaffected by
    a file only the desktop app ever writes.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-the-shell")
    local_settings.write_api_key("sk-ant-from-the-file")

    assert local_settings.apply_to_environ() is False
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-from-the-shell"


def test_apply_to_environ_is_a_no_op_with_no_stored_key(appdata, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    assert local_settings.apply_to_environ() is False
    assert "ANTHROPIC_API_KEY" not in os.environ
