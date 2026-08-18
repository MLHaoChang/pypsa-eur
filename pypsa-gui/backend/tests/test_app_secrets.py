"""
U-1 — the packaged app can be given an `ANTHROPIC_API_KEY`.

The frozen bundle deliberately ships no `backend/.env` (it carries a real key
AND the session-signing `SECRET_KEY`), and nothing replaced it: the chat panel
was permanently disabled in the shipped artifact with no setting, no prompt and
no documentation. `services/app_secrets` is the replacement — a 0600 `user.env`
in app-data, written by the app and applied to `os.environ` at startup.

These tests pin the three properties that make it safe to ship:

  * the ALLOWLIST holds — nothing but `MANAGED_KEYS` is ever read from or
    written to the file, so this surface cannot be used to set `SECRET_KEY`
    (forging session cookies) or `PYPSAGUI_APP_DATA_DIR` (repointing the DB);
  * the SHELL still wins — a value the operator exported before launch is never
    overwritten by the file, and the UI is told when it is being masked;
  * the VALUE never comes back out — `status()` returns a four-character hint
    and nothing more.
"""
from __future__ import annotations

import os
import stat

import pytest

import app_paths
from services import app_secrets

KEY = "ANTHROPIC_API_KEY"
SAMPLE = "sk-ant-api03-EXAMPLE-not-a-real-key-abcd"


@pytest.fixture(autouse=True)
def isolated_app_data(tmp_path, monkeypatch):
    """
    Point app-data at a tmp dir and restore the process env afterwards.

    `set_secret` writes `os.environ` directly (that is its whole point — the
    chat panel must come alive without a restart), so monkeypatch's own
    bookkeeping does not cover it. The explicit save/restore below does, and
    matters: `test_chat_metrics.py` and `test_chat_e2e.py` both assert on the
    ABSENCE of this variable and would fail behind a leak from here.
    """
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    previous_value = os.environ.get(KEY)
    previous_shell = app_secrets._SHELL_NAMES
    os.environ.pop(KEY, None)
    app_secrets._SHELL_NAMES = frozenset()
    yield
    app_secrets._SHELL_NAMES = previous_shell
    if previous_value is None:
        os.environ.pop(KEY, None)
    else:
        os.environ[KEY] = previous_value


def test_set_secret_persists_and_takes_effect_immediately():
    result = app_secrets.set_secret(KEY, SAMPLE)

    # In-process, because every consumer reads os.environ at call time:
    # `chat_service._build_anthropic_client` and `routers/chat.py:chat_health`
    # both do. Without this the user would have to restart a packaged app they
    # have no way to restart from inside.
    assert os.environ[KEY] == SAMPLE
    assert app_paths.user_env_file().read_text(encoding="utf-8").count(SAMPLE) == 1
    assert result["configured"] is True
    assert result["source"] == "settings"


def test_the_stored_file_is_not_readable_by_anyone_else():
    app_secrets.set_secret(KEY, SAMPLE)
    mode = stat.S_IMODE(app_paths.user_env_file().stat().st_mode)
    assert mode == 0o600, f"user.env is {oct(mode)}, expected 0o600"


def test_rewriting_an_existing_file_re_asserts_the_mode():
    """`O_CREAT` applies its mode only to a file it CREATES."""
    path = app_paths.user_env_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# pre-existing, world-readable\n", encoding="utf-8")
    os.chmod(path, 0o644)

    app_secrets.set_secret(KEY, SAMPLE)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_status_never_returns_the_key():
    app_secrets.set_secret(KEY, SAMPLE)
    result = app_secrets.status(KEY)
    assert SAMPLE not in repr(result)
    # Enough to tell two of your own keys apart, not enough to reconstruct one.
    assert result["hint"] == "…" + SAMPLE[-4:]


def test_status_is_empty_before_anything_is_set():
    result = app_secrets.status(KEY)
    assert result == {
        "configured": False,
        "source": None,
        "hint": None,
        "overridden_by_environment": False,
        "storage_path": str(app_paths.user_env_file()),
    }


def test_clear_secret_forgets_it_from_both_places():
    app_secrets.set_secret(KEY, SAMPLE)
    result = app_secrets.clear_secret(KEY)
    assert KEY not in os.environ
    assert SAMPLE not in app_paths.user_env_file().read_text(encoding="utf-8")
    assert result["configured"] is False


# ── the allowlist is a security boundary ────────────────────────────────────
@pytest.mark.parametrize("name", ["SECRET_KEY", "PYPSAGUI_APP_DATA_DIR", "DATABASE_URL"])
def test_unmanaged_names_are_refused(name):
    with pytest.raises(app_secrets.SecretValueError):
        app_secrets.set_secret(name, "anything")
    with pytest.raises(app_secrets.SecretValueError):
        app_secrets.clear_secret(name)
    with pytest.raises(app_secrets.SecretValueError):
        app_secrets.status(name)


def test_an_unmanaged_line_in_the_file_is_never_applied_or_kept():
    """
    Hand-editing `SECRET_KEY` into the file must not forge session cookies.

    `_read_managed` filters on the way in, so bootstrap ignores it; the next
    write rebuilds the file from `MANAGED_KEYS` only, so it does not survive.
    """
    path = app_paths.user_env_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"SECRET_KEY=forged\n{KEY}={SAMPLE}\n", encoding="utf-8")

    app_secrets.bootstrap_environment()
    assert "SECRET_KEY" not in os.environ or os.environ["SECRET_KEY"] != "forged"
    assert os.environ[KEY] == SAMPLE

    app_secrets.set_secret(KEY, SAMPLE + "2")
    assert "forged" not in path.read_text(encoding="utf-8")


# ── precedence: shell > user.env ────────────────────────────────────────────
def test_bootstrap_applies_the_file_when_the_shell_is_silent():
    app_secrets.set_secret(KEY, SAMPLE)
    os.environ.pop(KEY, None)

    app_secrets.bootstrap_environment()

    assert os.environ[KEY] == SAMPLE


def test_a_shell_supplied_value_is_never_overwritten():
    app_secrets.set_secret(KEY, SAMPLE)
    os.environ[KEY] = "sk-ant-from-the-shell"

    app_secrets.bootstrap_environment()

    assert os.environ[KEY] == "sk-ant-from-the-shell"
    reported = app_secrets.status(KEY)
    assert reported["source"] == "environment"
    # The UI needs this to explain why Save appeared to do nothing.
    assert reported["overridden_by_environment"] is True


def test_user_env_beats_a_developer_backend_dotenv(tmp_path):
    """
    The stated precedence, and the trap it exists to avoid.

    If `backend/.env` won, a developer would save a key through Settings, watch
    it work for the rest of the process (the route also sets `os.environ`), and
    find it silently reverted on the next restart. A setting that un-sets itself
    overnight is worse than no setting.
    """
    backend_env = tmp_path / "backend.env"
    backend_env.write_text(f"{KEY}=sk-ant-from-backend-dotenv\n", encoding="utf-8")
    app_secrets.set_secret(KEY, SAMPLE)
    os.environ.pop(KEY, None)

    app_secrets.bootstrap_environment(backend_env=backend_env)

    assert os.environ[KEY] == SAMPLE
    assert app_secrets.status(KEY)["source"] == "settings"


def test_a_backend_dotenv_still_wins_when_nothing_is_stored(tmp_path):
    """
    The negative control.

    `user.env` taking precedence must not mean the developer's `.env` quietly
    stopped being loaded at all — which is what a bootstrap that skipped it
    would look like from the test above.
    """
    backend_env = tmp_path / "backend.env"
    backend_env.write_text(f"{KEY}=sk-ant-from-backend-dotenv\n", encoding="utf-8")

    app_secrets.bootstrap_environment(backend_env=backend_env)

    assert os.environ[KEY] == "sk-ant-from-backend-dotenv"
    assert app_secrets.status(KEY)["source"] == "environment"


def test_saving_under_a_shell_override_persists_without_changing_this_process():
    """
    Otherwise a restart silently changes behaviour with nobody touching a
    setting — the save "takes effect" hours later, which is worse than not
    taking effect at all.
    """
    os.environ[KEY] = "sk-ant-from-the-shell"
    app_secrets.bootstrap_environment()

    app_secrets.set_secret(KEY, SAMPLE)

    assert os.environ[KEY] == "sk-ant-from-the-shell"
    assert SAMPLE in app_paths.user_env_file().read_text(encoding="utf-8")


# ── value validation ────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "value, reason",
    [
        ("", "empty"),
        ("   ", "whitespace only"),
        ("sk-ant-with\nnewline", "a newline would truncate the stored line"),
        ("sk-ant-with\ttab", "control characters travel into an HTTP header"),
        ("x" * (app_secrets.MAX_VALUE_LENGTH + 1), "over the length bound"),
    ],
)
def test_unusable_values_are_refused(value, reason):
    with pytest.raises(app_secrets.SecretValueError):
        app_secrets.validate_value(value)


def test_surrounding_whitespace_is_trimmed_not_rejected():
    """A pasted key routinely carries a trailing newline from the clipboard."""
    app_secrets.set_secret(KEY, f"  {SAMPLE}\n")
    assert os.environ[KEY] == SAMPLE


def test_a_dollar_sign_in_a_key_survives_verbatim():
    """
    `dotenv` resolves `${VAR}` interpolation. A credential must not be
    rewritten by its own storage format — the failure that produces is an
    authentication error with nothing anywhere to explain it.
    """
    tricky = "sk-ant-${HOME}-literal"
    app_secrets.set_secret(KEY, tricky)
    os.environ.pop(KEY, None)
    app_secrets.bootstrap_environment()
    assert os.environ[KEY] == tricky


def test_hand_written_quotes_are_stripped():
    path = app_paths.user_env_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'{KEY}="{SAMPLE}"\n', encoding="utf-8")

    app_secrets.bootstrap_environment()

    assert os.environ[KEY] == SAMPLE


def test_is_managed_key_rule():
    from services import app_secrets as s
    assert s.is_managed_key("OPENAI_API_KEY")
    assert s.is_managed_key("PYPSA_GUI_LLM_KEY__OLLAMA_LOCAL")
    assert not s.is_managed_key("SECRET_KEY")
    assert not s.is_managed_key("PYPSAGUI_APP_DATA_DIR")
    assert not s.is_managed_key("DATABASE_URL")
    assert not s.is_managed_key("PYPSA_GUI_LLM_KEY__bad-lower")
    assert not s.is_managed_key("PYPSA_GUI_LLM_KEY__")


def test_saving_key_a_does_not_erase_key_b(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path))
    from services import app_secrets as s
    s.set_secret("PYPSA_GUI_LLM_KEY__A1", "value-aaaa-1234")
    s.set_secret("OPENAI_API_KEY", "value-bbbb-5678")
    assert s.get_stored("PYPSA_GUI_LLM_KEY__A1") == "value-aaaa-1234"
    assert s.get_stored("OPENAI_API_KEY") == "value-bbbb-5678"


def test_live_secret_values_covers_shell_only_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "shell-only-value-99")
    from services import app_secrets as s
    s.set_secret("PYPSA_GUI_LLM_KEY__F1", "file-value-1234")
    vals = s.live_secret_values()
    assert "shell-only-value-99" in vals      # env-only, never in user.env
    assert "file-value-1234" in vals
