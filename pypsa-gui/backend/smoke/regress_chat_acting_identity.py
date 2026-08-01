"""
Regression harness for the chat acting-identity defects (F1 + S9.1).

Standalone PASS/FAIL script — NOT a pytest module. It lives in `smoke/` because
`tests/` is shared with a concurrently-running pytest session whose database it
would cross-contaminate, and `pytest.ini` pins `testpaths = tests`, so nothing
here is ever collected.

Run it with the pixi environment, from anywhere:

    pixi run python pypsa-gui/backend/smoke/regress_chat_acting_identity.py

What it pins down:

F1 — `routers.chat.chat_stream` binds the acting user, but the SSE body is a
sync generator that Starlette drives through `iterate_in_threadpool`, which
copies the event-loop task's context ONCE PER YIELDED ITEM. When the endpoint
is a sync `def`, the handler itself also runs in a threadpool context copy, so
the binding dies with the handler and every project-scoped tool sees `None`.
Checks 1-3 observe the contextvar from inside the generator AFTER a yield has
already crossed that boundary, and from inside `chat_service._TOOL_EXECUTOR`
via the same `copy_context()` submit the real dispatcher uses.

S9.1 — seven chat tools called FastAPI route handlers directly, leaving their
`Depends` defaults unresolved (four of them additionally mis-bound a positional
onto the `Depends` parameter). Checks 4-10 call each one against a project that
does not exist: a resolved dependency answers `HTTPException(404)`, an
unresolved one dies with `AttributeError`.

The `script` stub path is deliberately NOT used as the probe — `_dispatch_stub_
call` replays canned results and never reaches a real handler. The probe
replaces `chat_service.run_turn` instead, so the router, Starlette's streaming
machinery and the tool executor are all the real ones.
"""
from __future__ import annotations

import contextvars
import os
import pathlib
import sys
import tempfile

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# Isolation must be pinned BEFORE `import main` — `get_settings()` is lru_cached
# and main reads it at import time.
_SCRATCH = pathlib.Path(tempfile.mkdtemp(prefix="pypsagui-f1-regress-"))
os.environ["PYPSAGUI_APP_DATA_DIR"] = str(_SCRATCH / "appdata")
os.environ["PYPSAGUI_PROJECTS_ROOT"] = str(_SCRATCH / "projects")
os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{_SCRATCH / 'auth.db'}"
os.environ.setdefault("SECRET_KEY", "f1-regression-not-a-real-key")
# Local mode injects a fixed seeded identity on every /api request, so the
# expected user id is a constant rather than a login round-trip.
os.environ["PYPSAGUI_LOCAL_MODE"] = "1"
for _bare in ("PROJECTS_ROOT", "FLAT_PROJECTS_ROOT", "LEGACY_ROOT"):
    os.environ.pop(_bare, None)

from smoke.isolation import require_isolated_environment  # noqa: E402

require_isolated_environment()

import local_mode  # noqa: E402
import main  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from services import chat_service, chat_tools  # noqa: E402

EXPECTED_USER = str(local_mode.LOCAL_USER_ID)
MISSING_PROJECT = "qa_e2e_f1_regress_no_such_project"

_results: list[tuple[str, bool, str]] = []
_observed: dict[str, object] = {}


def record(name: str, ok: bool, detail: str = "") -> None:
    """Append one PASS/FAIL line and echo it."""
    _results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail}")


def _probe_turn(session, message, attachment_file_ids=None):
    """
    Stand in for `chat_service.run_turn` and observe the acting identity.

    The first `yield` returns control to `iterate_in_threadpool`, which issues a
    fresh `to_thread.run_sync` (and therefore a fresh context copy) for the next
    item. Everything observed after it is observed under the same conditions a
    real tool execution runs under, since `_dispatch_tool_use` also yields
    `tool_request` / `tool_running` before it dispatches.
    """
    yield "token", {"text": "probe"}
    _observed["in_generator"] = chat_tools.acting_user_id()
    # Mirror chat_service.py's submit into _TOOL_EXECUTOR exactly.
    snapshot = contextvars.copy_context()
    future = chat_service._TOOL_EXECUTOR.submit(
        lambda: snapshot.run(chat_tools.acting_user_id)
    )
    _observed["in_tool_executor"] = future.result(timeout=30)
    dispatch_future = chat_service._TOOL_EXECUTOR.submit(
        lambda: snapshot.run(_dispatch_audit_log)
    )
    _observed["audit_log_via_dispatcher"] = dispatch_future.result(timeout=30)
    yield "done", {"ok": True}


def _dispatch_audit_log():
    """Run the real `audit_log` dispatcher and describe what came back."""
    handler = chat_tools.DISPATCHERS["audit_log"]
    try:
        result = handler()
    except HTTPException as exc:  # noqa: BLE001 — the 401 we are hunting
        return f"HTTPException({exc.status_code}): {exc.detail}"
    except Exception as exc:  # noqa: BLE001 — the crash we are hunting
        return f"{type(exc).__name__}: {exc}"
    return f"ok:{type(result).__name__}"


def _expect_404(name: str, call) -> None:
    """
    FAIL unless `call` raises HTTPException(404) for a nonexistent project.

    A resolved `Depends` reaches `require_project_access` (or the solve-queue
    registry lookup) and answers 404. An unresolved one dies inside the handler
    with AttributeError instead, which is the S9.1 defect.
    """
    try:
        call()
    except HTTPException as exc:
        record(
            name,
            exc.status_code == 404,
            f"HTTPException({exc.status_code})",
        )
    except Exception as exc:  # noqa: BLE001 — the unresolved-Depends crash
        record(name, False, f"{type(exc).__name__}: {exc}"[:110])
    else:
        record(name, False, "returned without raising — expected 404")


def run_f1() -> None:
    """Drive one real SSE turn and read the identity back out of it."""
    print("F1 — acting identity survives the SSE threadpool boundary")
    original = chat_service.run_turn
    chat_service.run_turn = _probe_turn
    # Unbind in THIS thread first: a value left bound here is the root context
    # every downstream copy descends from and would mask the defect.
    chat_tools.set_acting_user(None)
    try:
        with TestClient(main.app) as client:
            response = client.post(
                "/api/chat/stream",
                json={"session_id": "f1-regress", "message": "probe"},
            )
        record("F1.0 stream opened", response.status_code == 200,
               f"HTTP {response.status_code}")
    finally:
        chat_service.run_turn = original

    seen_gen = _observed.get("in_generator")
    record("F1.1 identity visible in the SSE generator",
           seen_gen == EXPECTED_USER, f"saw {seen_gen!r}")
    seen_exec = _observed.get("in_tool_executor")
    record("F1.2 identity visible in _TOOL_EXECUTOR",
           seen_exec == EXPECTED_USER, f"saw {seen_exec!r}")
    seen_dispatch = _observed.get("audit_log_via_dispatcher")
    record("F1.3 audit_log dispatches without 401/crash",
           isinstance(seen_dispatch, str) and seen_dispatch.startswith("ok:"),
           f"saw {seen_dispatch!r}")


def run_s9() -> None:
    """Call each of the seven tools with a bound identity."""
    print("S9.1 — the seven direct-handler tools resolve their Depends")
    chat_tools.set_acting_user(EXPECTED_USER)
    try:
        _expect_404(
            "S9.1.1 create_project_snapshot",
            lambda: chat_tools.create_project_snapshot(MISSING_PROJECT, "lbl"),
        )
        _expect_404(
            "S9.1.2 list_project_snapshots",
            lambda: chat_tools.list_project_snapshots(MISSING_PROJECT),
        )
        _expect_404(
            "S9.1.3 restore_project_snapshot",
            lambda: chat_tools.restore_project_snapshot(MISSING_PROJECT, "s1"),
        )
        _expect_404(
            "S9.1.4 delete_project_snapshot",
            lambda: chat_tools.delete_project_snapshot(MISSING_PROJECT, "s1"),
        )
        _expect_404(
            "S9.1.5 solve_queue_enqueue",
            lambda: chat_tools.solve_queue_enqueue(MISSING_PROJECT),
        )
        try:
            entries = chat_tools.audit_log()
        except Exception as exc:  # noqa: BLE001
            record("S9.1.6 audit_log", False, f"{type(exc).__name__}: {exc}"[:110])
        else:
            record("S9.1.6 audit_log", isinstance(entries, list),
                   f"{type(entries).__name__}")
        try:
            chat_tools.clear_audit_log()
        except Exception as exc:  # noqa: BLE001
            record("S9.1.7 clear_audit_log", False,
                   f"{type(exc).__name__}: {exc}"[:110])
        else:
            record("S9.1.7 clear_audit_log", True, "returned")
    finally:
        chat_tools.set_acting_user(None)


def main_() -> int:
    """Run both groups and return a process exit code."""
    run_f1()
    run_s9()
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = len(_results) - passed
    print(f"\nSUMMARY  PASS {passed}  FAIL {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main_())
