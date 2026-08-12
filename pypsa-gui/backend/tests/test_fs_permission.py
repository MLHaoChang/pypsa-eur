"""
Turning a blocked filesystem permission into an answer instead of a hang.

MEASURED, not theorised. On this machine, with a freshly built bundle:

  * `GET /api/projects/` never returned — 60s and 25s attempts both ended
    `http=000`, on two separate installs.
  * `sample` on the process: 2293 of 2293 stacks in `open()` -> `__open`
    (libsystem_kernel). Every other route answered in 5-24ms throughout.
  * A screenshot showed why: the macOS consent dialog for the Documents
    folder, waiting. `projects_root` is `~/Documents/PyPSA GUI/Projects`.
  * The same code, running as a dev process started from a shell that already
    holds the grant, answered the same route in 6ms.

CLAUDE.md already records the cause — "grants are keyed to the app binary, so
every rebuild of an ad-hoc-signed build can reset them" — so this is not an
edge case. It is EVERY install, and it lasts until someone clicks Allow.

Two things were wrong, and only the second is fixable in software:

  1. The route hangs rather than answering. A blocking syscall cannot be
     cancelled from Python, but we do not have to WAIT on it: a probe thread
     absorbs the block, and the request answers 503 with a reason.
  2. The chat tool blamed the wrong thing. `list_projects` inherited the
     30s per-tool deadline and reported "tool 'list_projects' exceeded the 30s
     execution deadline" — which sent me, and would send any user, to debug a
     tool that was working perfectly.

The design rule this file pins: ON A HEALTHY MACHINE NOTHING CHANGES. The
probe finishes in microseconds, `wait_for_access` returns immediately, and no
request pays anything measurable. The 503 only exists on a machine where the
alternative was an infinite hang.
"""
from __future__ import annotations

import threading
import time

import pytest
from fastapi import HTTPException

from services import fs_permission


@pytest.fixture(autouse=True)
def _reset():
    fs_permission.reset_for_tests()
    yield
    fs_permission.reset_for_tests()


def test_unprobed_access_is_unknown():
    assert fs_permission.access_state() == "unknown"


def test_a_successful_probe_reports_granted():
    fs_permission.start_probe(lambda: None)
    assert fs_permission.wait_for_access(timeout=2.0) == "granted"
    assert fs_permission.access_state() == "granted"


def test_a_refused_probe_reports_denied():
    def refuse() -> None:
        raise PermissionError(1, "Operation not permitted")

    fs_permission.start_probe(refuse)
    assert fs_permission.wait_for_access(timeout=2.0) == "denied"


# The case that matters. The probe thread is parked in the kernel exactly as
# the real one is while the consent dialog waits; the REQUEST must not park
# with it.
def test_a_probe_still_blocked_reports_blocked_without_waiting_for_it():
    release = threading.Event()
    fs_permission.start_probe(lambda: release.wait(30))

    t0 = time.monotonic()
    state = fs_permission.wait_for_access(timeout=0.3)
    elapsed = time.monotonic() - t0

    assert state == "blocked"
    # Bounded by the caller's timeout, not by the blocked syscall.
    assert elapsed < 2.0
    release.set()


# What happens when the user finally clicks Allow: the probe completes and the
# app recovers on its own, with no restart.
def test_granting_afterwards_recovers_without_a_restart():
    release = threading.Event()
    fs_permission.start_probe(lambda: release.wait(30))
    assert fs_permission.wait_for_access(timeout=0.3) == "blocked"

    release.set()

    assert fs_permission.wait_for_access(timeout=2.0) == "granted"


# On a machine that already holds the grant this must cost nothing, or the
# cure is worse than a bug most users will never see.
def test_a_healthy_machine_pays_no_measurable_wait():
    fs_permission.start_probe(lambda: None)
    fs_permission.wait_for_access(timeout=2.0)

    t0 = time.monotonic()
    for _ in range(1000):
        fs_permission.wait_for_access(timeout=5.0)
    assert time.monotonic() - t0 < 0.5


def test_starting_twice_does_not_start_two_probes():
    calls: list[int] = []
    fs_permission.start_probe(lambda: calls.append(1))
    fs_permission.start_probe(lambda: calls.append(1))
    fs_permission.wait_for_access(timeout=2.0)
    assert len(calls) == 1


# An unexpected OSError is NOT a permission problem, and pretending it is
# would mask a real failure (a missing volume, a bad symlink) behind a
# misleading "grant access" message. Let the real handler raise the real
# error.
def test_an_unrelated_oserror_does_not_masquerade_as_a_permission_problem():
    def explode() -> None:
        raise OSError(28, "No space left on device")

    fs_permission.start_probe(explode)
    assert fs_permission.wait_for_access(timeout=2.0) == "granted"


# ── the guard ───────────────────────────────────────────────────────────────

def test_the_guard_passes_when_access_is_granted():
    fs_permission.start_probe(lambda: None)
    fs_permission.require_file_access()  # must not raise


def test_the_guard_answers_503_while_blocked():
    release = threading.Event()
    fs_permission.start_probe(lambda: release.wait(30))

    with pytest.raises(HTTPException) as exc:
        fs_permission.require_file_access(timeout=0.3)

    assert exc.value.status_code == 503
    detail = exc.value.detail
    # The payload has to name the CAUSE. The whole reason this module exists
    # is that the previous symptom ("tool exceeded its deadline") pointed at
    # innocent code.
    assert detail["error"] == "awaiting_file_permission"
    assert "Documents" in detail["message"] or "permission" in detail["message"].lower()
    release.set()


def test_the_guard_says_denied_differently_from_pending():
    # Pending means "click Allow". Denied means "you clicked Don't Allow, and
    # the fix is in System Settings" — different sentence, different action.
    def refuse() -> None:
        raise PermissionError(1, "Operation not permitted")

    fs_permission.start_probe(refuse)
    with pytest.raises(HTTPException) as exc:
        fs_permission.require_file_access(timeout=1.0)

    assert exc.value.detail["error"] == "file_permission_denied"
