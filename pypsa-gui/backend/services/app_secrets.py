"""
Operator-supplied secrets that the running app can set for itself (U-1).

WHY THIS EXISTS. `pypsa-gui/backend/.env` is deliberately kept OUT of the frozen
bundle (`pypsa-gui.spec`, asserted by `smoke/check_bundle.py`) because it holds
a real `ANTHROPIC_API_KEY` *and* the `SECRET_KEY` that signs sessions; shipping
it would hand every install the developer's secrets. Correct — but nothing
replaced it. The packaged app therefore had no supported way to receive an API
key at all: `_build_anthropic_client` returned `missing_api_key` on every turn
and the chat panel was permanently dead in the shipped artifact, with no
setting, no prompt and no documentation saying why.

WHAT REPLACES IT. A single `user.env` in app-data (`app_paths.user_env_file()`),
written by the app itself when a super-admin saves a key, and applied to
`os.environ` at startup. It survives app updates, and it belongs to the person
who typed the key rather than to whoever built the `.app`.

PRECEDENCE — shell env  >  user.env  >  backend/.env.

  * The launching shell wins outright. An operator who runs
    `ANTHROPIC_API_KEY=… pypsa-gui` means it, and `main.py` has documented that
    contract since the `.env` loader was added.
  * `user.env` beats `backend/.env` on purpose. The alternative traps a
    developer: they save a key through Settings, it works for the rest of the
    process because the route also sets `os.environ`, and then it silently
    reverts to the stale `.env` value on the next restart. A setting that
    un-sets itself overnight is worse than no setting.

ALLOWLIST. Only `MANAGED_KEYS` are ever read out of `user.env` or written into
it. This is a security boundary, not tidiness: without it, anything that could
write this file could set `SECRET_KEY` (forging session cookies) or
`PYPSAGUI_APP_DATA_DIR` (repointing the database), and the second is circular
besides — the file lives *inside* the directory that variable names.

STORAGE. Plaintext, mode 0600, created with `O_CREAT|O_EXCL`-style flags so the
key is never briefly world-readable. Not the OS keychain: `keyring` would need
bundling plus a platform backend on each of macOS and Windows, and the threat
model here is the same one `backend/.env` already accepts — an attacker who can
read files as this user has already lost the game. Documented rather than
hidden. On Windows `chmod` is close to a no-op; the file's protection there is
the per-user profile directory it sits in.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import app_paths

logger = logging.getLogger(__name__)

# The only names this module will read from, or write to, `user.env`.
MANAGED_KEYS: tuple[str, ...] = ("ANTHROPIC_API_KEY",)

# Longest value accepted. Anthropic keys are ~100 chars; this is a sanity bound
# against a paste of an entire file into the field, not a format assertion.
MAX_VALUE_LENGTH = 500

# Names that were already in the process environment when `bootstrap_environment`
# ran — i.e. set by the launching shell, before any file was loaded. Nothing in
# `user.env` overrides these. Empty until bootstrap runs, which is correct for
# tests: they get file-wins semantics unless they say otherwise.
_SHELL_NAMES: frozenset[str] = frozenset()


class SecretValueError(ValueError):
    """The supplied value cannot be stored as an environment value."""


def _parse(text: str) -> dict[str, str]:
    """
    Parse `KEY=VALUE` lines. Blank lines and `#` comments are skipped.

    Deliberately not `dotenv.load_dotenv`: that resolves `${VAR}` interpolation
    and executes `export` prefixes, neither of which should apply to a file
    whose values are opaque credentials. A key containing a literal `$` would
    otherwise be silently mangled into something that fails to authenticate,
    with no error anywhere to explain it.

    Surrounding single or double quotes are stripped, because a person editing
    this file by hand will reasonably add them.
    """
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if name:
            values[name] = value
    return values


def _read_managed() -> dict[str, str]:
    """Every allowlisted name currently in `user.env`, with a value set."""
    path = app_paths.user_env_file()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError:
        # Unreadable file is a degraded state, not a crash: the app must still
        # start, just without whatever the file would have supplied.
        logger.warning("user.env exists but could not be read", exc_info=True)
        return {}
    parsed = _parse(text)
    return {k: v for k, v in parsed.items() if k in MANAGED_KEYS and v}


def _write_managed(values: dict[str, str]) -> None:
    """Rewrite `user.env` from `values`, 0600, never world-readable."""
    path = app_paths.user_env_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(f"{name}={values[name]}\n" for name in MANAGED_KEYS if values.get(name))
    header = (
        "# PyPSA Studio — settings written by the app.\n"
        "# Edit by hand only if you know what you are doing; the app rewrites\n"
        "# this file whenever a key is saved or cleared from Settings.\n"
    )
    # Open with the mode up front rather than write-then-chmod: the latter
    # leaves a window where the key is on disk under the process umask.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(header + body)
    # O_CREAT only applies the mode to a file it CREATES, so a file that already
    # existed keeps whatever mode it had. Re-assert it — the common case is
    # overwriting, not creating.
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover — Windows / exotic filesystems
        logger.debug("could not chmod %s", path, exc_info=True)


def validate_value(value: str) -> str:
    """Return the storable form of `value`, or raise `SecretValueError`."""
    cleaned = value.strip()
    if not cleaned:
        raise SecretValueError("The key cannot be empty.")
    if len(cleaned) > MAX_VALUE_LENGTH:
        raise SecretValueError(
            f"The key is longer than {MAX_VALUE_LENGTH} characters — "
            "check you pasted only the key."
        )
    # A newline would end the line early and silently truncate the stored key;
    # any other control character would travel into an HTTP header. Both are
    # rejected rather than stripped, because silently altering a credential
    # produces an authentication failure with no visible cause.
    if any(ch < " " or ch == "\x7f" for ch in cleaned):
        raise SecretValueError("The key contains characters that cannot be stored.")
    return cleaned


def bootstrap_environment(backend_env: Path | None = None) -> None:
    """
    Populate `os.environ` from `backend/.env` then `user.env`, in that order.

    MUST run before anything reads configuration. `main.py` calls it as its
    first statement for exactly that reason — `settings.get_settings()` is
    cached on first call, so a later load would be read too late to matter.
    """
    global _SHELL_NAMES
    # Captured before any file is applied: this is the set the shell supplied,
    # and the one thing no file may override.
    _SHELL_NAMES = frozenset(os.environ)

    if backend_env is not None and backend_env.exists():
        try:
            from dotenv import load_dotenv  # noqa: PLC0415
        except ImportError:
            logger.debug("python-dotenv not installed; skipping %s", backend_env)
        else:
            # override=False so a shell-set variable still wins, matching the
            # contract this loader has had since it was introduced.
            load_dotenv(dotenv_path=backend_env, override=False)

    for name, value in _read_managed().items():
        if name in _SHELL_NAMES:
            # The shell set it explicitly. Leave it alone and say so, because
            # otherwise the Settings page shows a key that is not the one in use.
            logger.info("%s: using the value from the environment, not Settings.", name)
            continue
        os.environ[name] = value


def status(name: str = "ANTHROPIC_API_KEY") -> dict[str, object]:
    """
    Whether `name` is configured, where it came from, and a non-reversible hint.

    Never returns the value. The hint is the last four characters, which is
    enough for a person to tell two of their own keys apart and not enough to
    reconstruct either.
    """
    if name not in MANAGED_KEYS:
        raise SecretValueError(f"{name} is not a managed setting.")
    live = os.environ.get(name) or ""
    stored = _read_managed().get(name)
    if name in _SHELL_NAMES and live:
        source: str | None = "environment"
    elif stored and live == stored:
        source = "settings"
    elif live:
        # In `os.environ` but not from the shell and not matching the file:
        # `backend/.env`, or something set in-process since bootstrap.
        source = "environment"
    else:
        source = None
    return {
        "configured": bool(live),
        "source": source,
        "hint": f"…{live[-4:]}" if len(live) >= 4 else None,
        # True when a shell-set value is masking a stored one, so the UI can
        # explain why saving appeared to do nothing.
        "overridden_by_environment": bool(stored) and name in _SHELL_NAMES,
        "storage_path": str(app_paths.user_env_file()),
    }


def set_secret(name: str, value: str) -> dict[str, object]:
    """Persist `name` and apply it to this process immediately."""
    if name not in MANAGED_KEYS:
        raise SecretValueError(f"{name} is not a managed setting.")
    cleaned = validate_value(value)
    values = _read_managed()
    values[name] = cleaned
    _write_managed(values)
    if name in _SHELL_NAMES:
        # Persist it — the operator asked — but do NOT clobber the shell value
        # in this process, or a restart would change behaviour without anyone
        # touching a setting. `status()` reports the mask.
        logger.info("%s saved, but the environment value stays in effect.", name)
    else:
        # Applied in-process on purpose: every consumer reads `os.environ` at
        # call time (`chat_service._build_anthropic_client`,
        # `routers/chat.py:chat_health`), so the chat panel comes alive on the
        # next poll instead of after a restart the user has no way to trigger
        # from inside a packaged app.
        os.environ[name] = cleaned
    return status(name)


def clear_secret(name: str) -> dict[str, object]:
    """Forget `name` — remove it from the file and from this process."""
    if name not in MANAGED_KEYS:
        raise SecretValueError(f"{name} is not a managed setting.")
    values = _read_managed()
    values.pop(name, None)
    _write_managed(values)
    if name not in _SHELL_NAMES:
        os.environ.pop(name, None)
    return status(name)
