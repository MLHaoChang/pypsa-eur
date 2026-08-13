"""
Answer instead of hanging when macOS has not granted access to the projects
root yet.

WHY THIS EXISTS, measured rather than assumed. With a freshly built bundle,
`GET /api/projects/` never returned — 60s and 25s attempts both ended
`http=000`, across two separate installs. `sample` on the process put 2293 of
2293 stacks in `open()` -> `__open` (libsystem_kernel) while every other route
answered in 5-24ms, and a screenshot showed the macOS consent dialog for the
Documents folder sitting there, waiting. The same code as a dev process
started from a shell that already held the grant answered in 6ms.

`projects_root` defaults to `~/Documents/PyPSA GUI/Projects`, which is
TCC-gated, and CLAUDE.md already records the sharp edge: "grants are keyed to
the app binary, so every rebuild of an ad-hoc-signed build can reset them."
So this is not a rare state. It is EVERY install of a new build, for as long
as it takes someone to notice the dialog.

The blocking `open()` cannot be cancelled from Python — but nothing says the
REQUEST has to wait on it. A probe thread absorbs the block; requests consult
the probe and answer 503 with a cause.

DESIGN RULE, and the thing to preserve if this is ever edited: ON A MACHINE
THAT ALREADY HOLDS THE GRANT, NOTHING CHANGES. The probe finishes in
microseconds, `wait_for_access` returns on an already-set Event, and no
request pays anything measurable. The 503 exists only where the alternative
was an unbounded hang.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any, Literal

from fastapi import HTTPException

logger = logging.getLogger(__name__)

AccessState = Literal["unknown", "granted", "blocked", "denied"]

# How long a request will wait for a pending probe before answering 503.
# Generous next to a granted machine's microseconds, short next to forever.
DEFAULT_WAIT_SECONDS = 2.0

_lock = threading.Lock()
_done = threading.Event()
_state: AccessState = "unknown"
_started = False


def reset_for_tests() -> None:
    """Return the module to its pre-probe state. Tests only."""
    global _state, _started, _done
    with _lock:
        _state = "unknown"
        _started = False
        _done = threading.Event()


def start_probe(check: Callable[[], None]) -> None:
    """
    Touch the projects root once, on a daemon thread, and record what happened.

    `check` is injected rather than built here so the caller owns the path (it
    is a settings lookup, and importing settings into this module would drag
    the whole config graph into a file that must stay importable from
    anywhere) — and so tests can supply a probe that blocks on command, which
    is the state that actually matters.

    Idempotent: a second call is ignored, so a reload or a double-registered
    startup hook cannot stack threads against a dialog that only needs one.
    """
    global _started
    with _lock:
        if _started:
            return
        _started = True
        done = _done

    def _run() -> None:
        global _state
        try:
            check()
            _state = "granted"
        except PermissionError:
            # macOS reports a TCC refusal as EPERM on a file whose mode, owner
            # and ACL are ordinary — see the CLAUDE.md note. The user's fix is
            # System Settings, not chmod.
            _state = "denied"
            logger.warning("fs_permission: access to the projects root was refused")
        except OSError:
            # A missing volume, a bad symlink, a full disk. NOT a permission
            # problem, and dressing it as one would hide a real failure behind
            # a "grant access" message that cannot fix it. Let the real
            # handler raise the real error.
            _state = "granted"
            logger.exception("fs_permission: probe hit a non-permission OSError")
        finally:
            done.set()

    threading.Thread(target=_run, name="fs-permission-probe", daemon=True).start()


def access_state() -> AccessState:
    """The last known state, without waiting."""
    if _done.is_set():
        return _state
    return "unknown" if not _started else "blocked"


def wait_for_access(timeout: float = DEFAULT_WAIT_SECONDS) -> AccessState:
    """
    Resolve the state, waiting at most `timeout` for a probe still in flight.

    Returns "blocked" when the probe has not come back — meaning the consent
    dialog is almost certainly on screen. Crucially this bounds the CALLER;
    the probe thread stays parked in the kernel until the user answers, and
    then this returns "granted" on the next call with no restart needed.
    """
    if not _started:
        return "unknown"
    if _done.wait(timeout):
        return _state
    return "blocked"


def require_file_access(timeout: float = DEFAULT_WAIT_SECONDS) -> None:
    """
    FastAPI dependency: 503 rather than hang when the projects root is
    unreachable.

    503 (not 500) because it is genuinely temporary and self-healing — the
    moment the user clicks Allow, the very next request succeeds.

    The two states get DIFFERENT messages on purpose. Pending means "a dialog
    is waiting, click Allow". Denied means "you already said no, and the fix
    is in System Settings" — a different sentence prompting a different
    action. Collapsing them would reproduce, in a nicer wrapper, the original
    sin here: a message that points somewhere unhelpful.
    """
    state = wait_for_access(timeout)
    if state in ("unknown", "granted"):
        return
    detail: dict[str, Any]
    if state == "denied":
        detail = {
            "error": "file_permission_denied",
            "message": (
                "macOS is blocking access to your projects folder. Open System "
                "Settings → Privacy & Security → Files and Folders and allow "
                "PyPSA Studio access to your Documents folder, then try again."
            ),
        }
    else:
        detail = {
            "error": "awaiting_file_permission",
            "message": (
                "macOS is asking whether PyPSA Studio may open files in your "
                "Documents folder. Click Allow on that dialog and this will "
                "work immediately — no restart needed."
            ),
        }
    raise HTTPException(status_code=503, detail=detail)
