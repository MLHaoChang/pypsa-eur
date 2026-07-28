"""
The whole launch chain, headless (phase 2a, Tasks 1-3 together).

Tasks 1, 2 and 3 are each unit-tested in isolation, and until Task 5 nothing
runs them as a sequence. This closes that gap two tasks early, without a
window:

    lock -> bind -> environment -> import main -> serve -> health -> stop -> exit

Every one of these is a subprocess, and not only because `apply_environment`
refuses once `conftest` has imported `main`. The last assertion is that the
process EXITS — which cannot be observed from inside itself. `chat_service`
creates a module-level `ThreadPoolExecutor` that is never shut down, and
CPython's atexit joiner blocks interpreter exit on it, so "the server thread
returned" and "the app quit" are genuinely different claims. Task 7 Step 7
asserts this by hand against a real window; here it is automatic.

The storage variables are pinned by the HARNESS, never by the launcher — the
shell deliberately leaves those to `app_paths` (see Task 2). Pinning them here
is what keeps a test run out of the developer's real app-data directory.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parent.parent
_DIST = _BACKEND.parent / "frontend" / "dist"


def _run_chain(tmp_path: Path, body: str, timeout: int = 240):
    """
    One interpreter, real backend, real socket. `timeout` expiring IS a
    failure — it means the process did not exit.
    """
    appdata = tmp_path / "appdata"
    env = {
        **os.environ,
        "PYPSAGUI_APP_DATA_DIR": str(appdata),
        "DATABASE_URL": f"sqlite+pysqlite:///{(appdata / 'test.db').as_posix()}",
        "PROJECTS_ROOT": str(tmp_path / "projects"),
        "FLAT_PROJECTS_ROOT": str(tmp_path / "flat"),
        "LEGACY_ROOT": str(tmp_path / "legacy_unclaimed"),
    }
    env.pop("PYPSAGUI_LEGACY_IMPORT_ROOT", None)
    appdata.mkdir(parents=True, exist_ok=True)

    script = f"import sys; sys.path.insert(0, {str(_BACKEND)!r})\n" + textwrap.dedent(body)
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=timeout, env=env,
    )


# The chain itself, shared by the tests below so they cannot drift apart.
# `__LOCKDIR__` is substituted rather than %-formatted: the body is Python
# source full of `%` operators, and escaping them all is a trap that shows up
# as an IndentationError three frames away from the cause.
_CHAIN = """
import json
from pathlib import Path

from desktop.single_instance import SingleInstance
from desktop import launcher

LOCKDIR = Path(__LOCKDIR__)
result = {}

lock = SingleInstance(LOCKDIR / "single.lock")
lock.acquire()
result["lock_held"] = lock.held

sock = launcher.bind_socket()
port = sock.getsockname()[1]
result["port"] = port

# Before `import main`: the allowlist is frozen at import and the settings are
# lru_cached. This is the ordering the whole task exists to enforce.
launcher.apply_environment(launcher.build_environment(port, None))

import main
import security
result["allowlist_follows_the_port"] = security.is_allowed_origin(
    "http://127.0.0.1:{}".format(port)
)
result["local_mode"] = main.local_mode.is_local_mode()

server = launcher.DesktopServer(main.app, sock)
server.start()
result["healthy"] = server.wait_healthy(180)
"""


def _chain(lockdir: Path, tail: str) -> str:
    return _CHAIN.replace("__LOCKDIR__", repr(str(lockdir))) + textwrap.dedent(tail)


def test_the_whole_chain_starts_serves_stops_and_the_process_exits(tmp_path):
    """
    The end-to-end claim. `wait_healthy` gets a long budget on purpose: the
    first-run import runs synchronously inside `lifespan` and uvicorn accepts
    no connection until it returns, so a short timeout here would be a flaky
    test that looked like a broken launcher.
    """
    result = _run_chain(tmp_path, _chain(tmp_path, """
        import urllib.request
        with urllib.request.urlopen(
            "http://127.0.0.1:{}/api/health".format(server.port), timeout=30
        ) as response:
            body = json.loads(response.read())
        result["health"] = body["status"]
        # local mode is what turns the login gate off; the SPA reads this field.
        result["auth_enabled"] = body["auth_enabled"]

        result["stage"] = server.stop()
        lock.release()
        print("RESULT " + json.dumps(result))
    """))

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(
        [ln for ln in result.stdout.splitlines() if ln.startswith("RESULT ")][-1][7:]
    )
    assert payload["lock_held"] is True
    assert payload["healthy"] is True, "the backend never came up"
    assert payload["health"] == "ok"
    assert payload["local_mode"] is True
    assert payload["auth_enabled"] is False, "the desktop build must not gate on login"
    assert payload["allowlist_follows_the_port"] is True
    assert payload["stage"] == "clean", payload["stage"]


def test_the_chain_stops_within_its_bound_with_the_REAL_log_stream_open(tmp_path):
    """
    The production shape of the hang, not a stand-in.

    `/api/simulation/log_stream` needs BOTH conditions to stay open: a
    `log_queue` in `_state`, and `status == "running"`. With only the queue the
    generator's `except` branch breaks out after one 0.5 s poll and the stream
    closes on its own — so a test that seeded just the queue would pass against
    an unbounded shutdown. Both are seeded, and the first chunk is read back
    before `stop()` is called, so the connection is provably live.
    """
    result = _run_chain(tmp_path, _chain(tmp_path, """
        import http.client, time
        from routers import simulation as sim

        queue = sim.BufferedLogQueue()
        queue.put("[PHASE] seeded so the stream has something to replay")
        sim._state["log_queue"] = queue
        sim._state_update(status="running")

        conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=30)
        conn.request("GET", "/api/simulation/log_stream")
        response = conn.getresponse()
        result["stream_status"] = response.status
        # `response.readline()` decodes the chunked body; `response.fp` would
        # hand back the hex chunk-size line instead.
        result["stream_first_line"] = response.readline().decode().strip()

        started = time.monotonic()
        result["stage"] = server.stop()
        result["elapsed"] = time.monotonic() - started

        conn.close()
        lock.release()
        print("RESULT " + json.dumps(result))
    """))

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(
        [ln for ln in result.stdout.splitlines() if ln.startswith("RESULT ")][-1][7:]
    )
    assert payload["healthy"] is True
    assert payload["stream_status"] == 200
    assert payload["stream_first_line"].startswith("data:"), payload["stream_first_line"]
    # See the unit test: accepting "forced" would pass against an unbounded
    # graceful timeout, because the ladder rescues it either way.
    assert payload["stage"] == "clean", payload["stage"]
    assert payload["elapsed"] < 8, (
        f"stop() took {payload['elapsed']:.1f}s with the real SSE stream open"
    )


def test_the_process_still_exits_if_the_shell_never_calls_stop(tmp_path):
    """
    The path nobody plans for: the GUI dies, or the bootstrap raises after the
    server is up, and the ordered shutdown never runs. A serving thread must
    not be able to keep the process alive on its own — an invisible process
    holding the single-instance lock means the user cannot relaunch the app and
    has no window to close.

    So this script starts the server and simply ENDS. Nothing is stopped and
    nothing is released. `subprocess.run`'s timeout expiring is the failure.

    Caught by mutation: with a non-daemon server thread every other test in
    this file still passed.
    """
    result = _run_chain(tmp_path, _chain(tmp_path, """
        print("RESULT " + json.dumps(result))
        # deliberately no server.stop(), no lock.release()
    """), timeout=180)

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(
        [ln for ln in result.stdout.splitlines() if ln.startswith("RESULT ")][-1][7:]
    )
    assert payload["healthy"] is True, "the backend never came up, so nothing was proven"


def test_a_second_launch_is_refused_while_the_first_is_serving(tmp_path):
    """
    D11 against a running backend rather than a bare lock file — the state the
    user is actually in when they double-click the icon twice. Task 7 Step 6
    asserts the same thing through the window.
    """
    result = _run_chain(tmp_path, _chain(tmp_path, """
        from desktop.single_instance import AlreadyRunning

        try:
            SingleInstance(LOCKDIR / "single.lock").acquire()
        except AlreadyRunning:
            result["second_launch"] = "refused"
        else:
            result["second_launch"] = "ADMITTED"

        result["stage"] = server.stop()
        lock.release()
        print("RESULT " + json.dumps(result))
    """))

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(
        [ln for ln in result.stdout.splitlines() if ln.startswith("RESULT ")][-1][7:]
    )
    assert payload["healthy"] is True
    assert payload["second_launch"] == "refused"


@pytest.mark.skipif(
    not (_DIST / "index.html").exists(),
    reason="SPA not built — run `npm --prefix pypsa-gui/frontend run build`",
)
def test_the_first_window_would_have_a_page_to_load(tmp_path):
    """
    Constraint #18: the SPA route returns 503 "Frontend not built" when `dist/`
    is missing, and `dist/` is gitignored. That makes the build a prerequisite
    of the FIRST WINDOW, not of acceptance — without this the shell opens on a
    503 JSON page and the failure looks like a broken backend.

    Skipped rather than failed when the SPA is absent: a checkout that has not
    run the build is not a broken launcher.
    """
    result = _run_chain(tmp_path, _chain(tmp_path, """
        import urllib.request
        with urllib.request.urlopen(launcher.app_url(server.port), timeout=30) as page:
            result["spa_status"] = page.status
            result["spa_is_html"] = b"<html" in page.read(2048).lower()

        result["stage"] = server.stop()
        lock.release()
        print("RESULT " + json.dumps(result))
    """))

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(
        [ln for ln in result.stdout.splitlines() if ln.startswith("RESULT ")][-1][7:]
    )
    assert payload["spa_status"] == 200
    assert payload["spa_is_html"] is True
