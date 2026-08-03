"""
The desktop app's own settings surface: the Anthropic key and the log path.

Every route is gated by `local_mode.reject_unless_local_mode`, whose docstring
already carries the reasoning: the gate is not "admin only", it is "this
deployment has exactly one tenant, and they own the disk". On a web deployment
the server's API key is not something an authenticated user may replace, and
the server's app-data path is not theirs to learn — so these routes 404 there.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel

import app_paths
import local_mode
import local_settings

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(local_mode.reject_unless_local_mode)])

# Duplicates `desktop.bootstrap._LOG_FILENAME` deliberately. `desktop/__init__.py`
# states the rule: "Nothing here may be imported by `main` — the hosted
# deployment must not acquire a dependency on the desktop shell." One shared
# string is the cheaper price.
LOG_FILENAME = "pypsa-gui.log"


class ApiKeyBody(BaseModel):
    api_key: str


def _state() -> dict:
    key = local_settings.stored_api_key()
    return {
        "key_set": key is not None,
        "key_hint": local_settings.api_key_hint(key),
        "log_path": str(app_paths.app_data_dir() / LOG_FILENAME),
    }


def probe_api_key() -> tuple[str, str]:
    """
    Ask Anthropic whether the key works. Returns `(status, detail)`.

    `models.list` is the cheapest possible auth probe: it returns model
    metadata and bills no tokens.

    NEVER raises, and the three failure modes stay DISTINCT. A key we could not
    check is not a key that works and must not render as one — the same rule
    the economics surfaces follow for an unresolvable cost.

    **SDK exception text never reaches the response or the log.** The detail
    strings below are fixed, and only the exception CLASS NAME is logged — a
    class name cannot contain an API key. This is stronger than scrubbing:
    there is no formatting step for a key to survive.
    """
    try:
        import anthropic
    except ImportError:
        return "sdk_not_installed", "The anthropic package is missing from this build."

    try:
        anthropic.Anthropic().models.list(limit=1)
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
        logger.warning("local settings: key probe rejected (%s)", type(exc).__name__)
        return "rejected", "Anthropic rejected this key."
    except Exception as exc:  # noqa: BLE001 — every other failure is "unknown"
        logger.warning("local settings: key probe failed (%s)", type(exc).__name__)
        return "unreachable", "Could not reach Anthropic to verify the key."
    return "valid", "Key accepted."


@router.get("")
def get_local_settings() -> dict:
    """Presence and a hint. The key itself is never returned."""
    return _state()


@router.put("/anthropic-key")
def put_anthropic_key(body: ApiKeyBody) -> dict:
    """
    Store the key, publish it, then report what Anthropic said about it.

    Persist BEFORE probing: a network failure must not discard what the user
    just typed. The probe result is reported, never conflated with success.
    """
    key = body.api_key.strip()
    local_settings.write_api_key(key)

    if key:
        os.environ["ANTHROPIC_API_KEY"] = key
        status, detail = probe_api_key()
    else:
        os.environ.pop("ANTHROPIC_API_KEY", None)
        status, detail = "cleared", "Key removed. Chat is disabled."

    logger.info("local settings: anthropic key updated, probe=%s", status)
    return {"status": status, "detail": detail, **_state()}


def _reveal_argv(path: Path) -> list[str]:
    """
    The platform's "show this file" command.

    Linux has no portable reveal-and-select, so it opens the containing
    directory instead — the honest degradation, rather than pretending.
    """
    if sys.platform == "darwin":
        return ["open", "-R", str(path)]
    if sys.platform == "win32":
        # No space after the comma: explorer parses `/select,<path>` as ONE
        # token. It also exits non-zero on success, which is why check=False
        # below is load-bearing rather than lazy.
        return ["explorer", f"/select,{path}"]
    return ["xdg-open", str(path.parent)]


@router.post("/reveal-log")
def reveal_log() -> dict:
    """
    Show the log file in the platform file manager.

    This is the only `subprocess` invocation in the application. It is
    acceptable for one specific reason: NOTHING from the request reaches the
    command. This route takes no parameters and the path is computed here from
    `app_paths`. There is no argument to inject into because there is no
    argument — and `test_reveal_runs_a_fixed_command_with_no_request_input`
    exists to keep it that way.
    """
    path = app_paths.app_data_dir() / LOG_FILENAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        subprocess.run(_reveal_argv(path), shell=False, check=False, timeout=10)
    except Exception as exc:  # noqa: BLE001 — reported, never fatal
        logger.warning("local settings: reveal-log failed: %s", exc)
        return {"revealed": False, "detail": str(exc), "log_path": str(path)}
    return {"revealed": True, "log_path": str(path)}
