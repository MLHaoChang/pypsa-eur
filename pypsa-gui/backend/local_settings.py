"""
Per-user local settings for the desktop app.

Imports only stdlib, `app_paths` and `services.app_secrets`, deliberately:
`main.py` reads this module at import time, before the router graph exists.
`app_secrets` is safe to add to that list because it imports nothing from this
package either — the no-cycle property is preserved transitively, not
abandoned.

THE KEY IS NOT STORED HERE. It used to be, in `local-settings.json`, and
`master` grew a second store for the same secret in `services/app_secrets.py`
(`<app-data>/user.env`) while this branch was being written. The merge stacked
both — see `docs/superpowers/findings/2026-08-05-api-key-store-collision.md`.
`app_secrets` won because it owns the things a second store cannot have on its
own: an allowlist of writable names, knowledge of which variables the launching
shell supplied, and therefore the precedence rule. This module keeps its API
and its Settings pane, and delegates the storage.

`local-settings.json` survives only as a migration source — see
`migrate_api_key_to_app_secrets`.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import app_paths
from services import app_secrets

logger = logging.getLogger(__name__)

_FILENAME = "local-settings.json"
_API_KEY = "anthropic_api_key"

# Below this length "the last four characters" discloses most of the value.
_MIN_HINT_LENGTH = 8


def settings_path() -> Path:
    return app_paths.app_data_dir() / _FILENAME


def read_settings() -> dict[str, str]:
    """
    The stored settings, or `{}`. NEVER raises.

    A missing file is the normal first-run state. An unreadable or malformed
    one is a warning, not a launch failure — the same rule
    `desktop.bootstrap.install_file_logging` follows, and for the same reason:
    an app-data problem must never be why the app will not start.
    """
    path = settings_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError:
        logger.warning("local settings: %s could not be read; ignoring it", path)
        return {}
    except UnicodeDecodeError:
        # UnicodeDecodeError is a ValueError subclass, not an OSError subclass,
        # so it is NOT caught by the `except OSError` above — a corrupted or
        # tampered file would otherwise raise out of a function documented as
        # "NEVER raises", crashing `apply_to_environ()` at main.py import time.
        logger.warning("local settings: %s is not valid UTF-8; ignoring it", path)
        return {}

    try:
        data = json.loads(raw)
    except ValueError:
        logger.warning("local settings: %s is not valid JSON; ignoring it", path)
        return {}
    if not isinstance(data, dict):
        logger.warning("local settings: %s is not a JSON object; ignoring it", path)
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str)}


def legacy_stored_api_key() -> str | None:
    """The key still sitting in `local-settings.json`, if any. Migration only."""
    key = read_settings().get(_API_KEY, "").strip()
    return key or None


def stored_api_key() -> str | None:
    """The stored key, or None. Blank and absent are the same answer."""
    return app_secrets.get_stored("ANTHROPIC_API_KEY")


def api_key_hint(key: str | None) -> str | None:
    """Last four characters, or None when that would disclose too much."""
    if not key or len(key) < _MIN_HINT_LENGTH:
        return None
    return key[-4:]


def write_api_key(key: str) -> None:
    """
    Persist the key; an empty string removes it.

    Delegates to `app_secrets`, which writes 0600 at creation via `os.open` and
    applies the value to this process — the two properties this function used
    to own and the tests still pin. Raises `app_secrets.SecretValueError` on a
    value that cannot be stored (empty after strip is handled here as "clear",
    not as an error); `routers/local_settings.py` turns that into a 400.
    """
    key = key.strip()
    if key:
        app_secrets.set_secret("ANTHROPIC_API_KEY", key)
    else:
        app_secrets.clear_secret("ANTHROPIC_API_KEY")


def _remove_legacy_key() -> None:
    """Drop `anthropic_api_key` from `local-settings.json`, keeping the rest."""
    data = read_settings()
    if _API_KEY not in data:
        return
    data.pop(_API_KEY, None)
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    # Atomic: a crash mid-write cannot leave a truncated file that reads back
    # as "no settings at all".
    os.replace(tmp, path)


def migrate_api_key_to_app_secrets() -> bool:
    """
    Move a key out of `local-settings.json` into `user.env`. True if it moved.

    Runs at import time from `main.py`, BEFORE `bootstrap_environment`, so a
    migrated key is published in the same startup rather than one launch later.

    Idempotent, and it never overwrites: a key already in `user.env` wins and
    the legacy entry is dropped. That direction is deliberate. `user.env` is
    what the live process has been reading since the merge (the finding
    documents why), so it is the value the user has been successfully using;
    preferring the JSON file would silently swap a working key for one that has
    been inert, possibly for weeks.

    NEVER raises. A migration that fails leaves both files untouched and logs —
    an app-data problem must not be why the app will not start, the same rule
    `read_settings` follows.
    """
    try:
        legacy = legacy_stored_api_key()
        if not legacy:
            return False
        if app_secrets.get_stored("ANTHROPIC_API_KEY"):
            # Already migrated, or the user set one through the other pane.
            _remove_legacy_key()
            logger.info("local settings: dropped the legacy key; user.env already has one")
            return False
        app_secrets.set_secret("ANTHROPIC_API_KEY", legacy)
        _remove_legacy_key()
        logger.info("local settings: migrated the stored API key into user.env")
        return True
    except Exception:  # noqa: BLE001 — logged, never fatal
        logger.warning("local settings: could not migrate the stored API key", exc_info=True)
        return False
