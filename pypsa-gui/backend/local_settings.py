"""
Per-user local settings for the desktop app.

Imports only stdlib and `app_paths`, deliberately: `main.py` reads this module
at import time, before the router graph exists, and `app_paths` itself imports
nothing from this package to avoid exactly that cycle.

It holds one thing today — the Anthropic API key — and it exists because the
packaged app has no other way to receive one. `backend/.env` is excluded from
the bundle on purpose (`smoke/check_bundle.py`: it carries a real key and the
SECRET_KEY that signs sessions), and a `.app` launched from Finder sources no
shell profile, so ANTHROPIC_API_KEY is unset by construction.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import app_paths

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


def stored_api_key() -> str | None:
    """The stored key, or None. Blank and absent are the same answer."""
    key = read_settings().get(_API_KEY, "").strip()
    return key or None


def api_key_hint(key: str | None) -> str | None:
    """Last four characters, or None when that would disclose too much."""
    if not key or len(key) < _MIN_HINT_LENGTH:
        return None
    return key[-4:]


def write_api_key(key: str) -> None:
    """
    Persist the key; an empty string removes the entry.

    Two properties the tests pin, both about the same risk:
      * mode 0600 AT CREATION via `os.open`. A `chmod` after writing leaves a
        window in which a live key is world-readable.
      * atomic `os.replace`, so a crash mid-write cannot leave a truncated file
        that reads back as "no key configured".
    """
    data = read_settings()
    key = key.strip()
    if key:
        data[_API_KEY] = key
    else:
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
    # Adopts the temp file's 0600, so a pre-existing wider mode is corrected.
    os.replace(tmp, path)


def apply_to_environ() -> bool:
    """
    Publish the stored key as ANTHROPIC_API_KEY. Returns True if it set it.

    **The stored key never overrides a TRUTHY environment value.** Same
    intent as `load_dotenv(override=False)` at `main.py:23` — an
    operator-set value wins — but the check here is truthiness-based
    (`os.environ.get(...)` in a boolean context), not presence-based like
    dotenv's `if k in os.environ and not override: continue`. So
    `ANTHROPIC_API_KEY=""` is treated as absent and the stored key still
    applies. That is deliberate, not a gap: `chat_service._build_anthropic_client`
    makes the same truthiness check when deciding whether a key is
    configured, so an empty string is "missing" on the consuming side too —
    matching it here means the web deployment and a developer shell with a
    real key exported are still unaffected by a file only the desktop app
    ever writes.
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        return False
    key = stored_api_key()
    if not key:
        return False
    os.environ["ANTHROPIC_API_KEY"] = key
    return True
