"""
Regression harness for the chat acting-identity defects (F1 + S9.1 + F3).

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

S9.1 pins the RESOLUTION half only, and cannot pin the mis-binding half.
`_authorized_project(MISSING_PROJECT)` raises 404 while the argument list is
still being EVALUATED, so the handler is never entered and a call with its
arguments swapped is indistinguishable from a correct one — all three
two-parameter checks were observed PASSing against deliberately swapped tools.
S9.2 is the half that does pin it: every check there aims at a project that
EXISTS, which moves the 404 (or the success) inside the handler body, where the
result is reachable only if each resolved value landed on the parameter that
was meant to receive it. `list_snapshots(project)` takes a single parameter, so
no argument-order defect is expressible for it; S9.2.2 pins that the handler is
entered and answers for the right project, which is all that shape admits.

F3 — `_route` injected only `db=` and `user=`, but `save_project` and
`activate_project` declare a THIRD dependency, `session: SessionRow | None =
Depends(current_session)`, and move the session's active-project pointer with
it. They received the raw `Depends` sentinel and died on `.active_project_id`.
Checks 12-16 pin BOTH halves of the fix: that a real `SessionRow` arrives (a
constant `None` would stop the crash but silently leave the browser pointing at
the old project after a chat-driven Save-As), and that handlers declaring no
`session` are still called without the keyword.

The F3 group runs with LOCAL MODE OFF. Local mode injects the seeded user and
issues no session cookie at all, so `current_session` is None there for the UI
and the chat path alike — a session pointer only exists when auth is on, and
"not crashing" is all that could be observed under it.

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
import uuid

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
import security  # noqa: E402
from fastapi import Depends, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import Session as DBSession  # noqa: E402

from db.models import Project, Session as SessionRow, User  # noqa: E402
from db.session import SessionLocal, get_db  # noqa: E402
from deps import current_session, optional_user  # noqa: E402
from services import chat_service, chat_tools  # noqa: E402
from settings import get_settings  # noqa: E402

EXPECTED_USER = str(local_mode.LOCAL_USER_ID)
MISSING_PROJECT = "qa_e2e_f1_regress_no_such_project"
# Destructive-call hazard 1: every project this harness writes carries the
# `qa_e2e_` prefix, inside the scratch PYPSAGUI_PROJECTS_ROOT pinned above.
F3_PROJECT_A = "qa_e2e_f3_regress_alpha"
F3_PROJECT_B = "qa_e2e_f3_regress_beta"
# S9.2 needs a project that EXISTS, so its 404s come from inside the handler.
S9_PROJECT = "qa_e2e_s9_regress_binding"
S9_LABEL = "qa-e2e-binding-probe"
# Matches `_SNAPSHOT_ID_RE` (snapshots.py:67), so the handler answers 404 for a
# missing snapshot rather than 400 for a malformed id.
S9_MISSING_SNAPSHOT = "qa-e2e-no-such-snapshot"

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


def _expect_inner_404(name: str, call) -> None:
    """
    FAIL unless `call` 404s from INSIDE the handler, naming the bogus snapshot.

    The project exists, so `_authorized_project` returns rather than raising and
    the handler really runs. Reaching the snapshot-missing 404 requires BOTH
    arguments to have landed correctly: `project` must be the AuthorizedProject
    (a swapped call dies on `project.name` before any 404) and `snapshot_id`
    must be the string, since the detail text interpolates it. A project-level
    404 would carry the project name instead and still FAIL here.
    """
    try:
        call()
    except HTTPException as exc:
        detail = str(exc.detail)
        record(
            name,
            exc.status_code == 404 and S9_MISSING_SNAPSHOT in detail,
            f"HTTPException({exc.status_code}): {detail}"[:110],
        )
    except Exception as exc:  # noqa: BLE001 — the mis-binding crash
        record(name, False, f"{type(exc).__name__}: {exc}"[:110])
    else:
        record(name, False, "returned without raising — expected an inner 404")


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


def run_s9_binding() -> None:
    """
    Pin the ARGUMENT-ORDER half of S9.1, which `run_s9` structurally cannot.

    Runs LAST, and unbinds the acting session first, because it must not leak
    state into another group. Both directions were observed: saving this
    group's project binds `PyPSAService.get_active_context().loaded_project`,
    and `save_project` only moves a session pointer when the context was
    unbound (`projects.py:1226`) — so running this group any earlier turned
    F3.3 red, and leaving F3's session bound would let this group move a
    pointer F3 asserts on. Destructive-call hazard 1: `S9_PROJECT` carries the
    `qa_e2e_` prefix and lives under the scratch PYPSAGUI_PROJECTS_ROOT pinned
    at import time.
    """
    print("S9.2 — the snapshot tools bind their arguments to the right parameters")
    chat_tools.set_acting_user(EXPECTED_USER)
    chat_tools.set_acting_session(None)
    try:
        # An existing project on disk is the whole point of this group: it is
        # what lets a 404 originate in the handler instead of in the argument
        # list. `save_project` writes network.nc, which every check below needs.
        chat_tools.save_project(S9_PROJECT)
        info = None
        try:
            info = chat_tools.create_project_snapshot(S9_PROJECT, S9_LABEL)
        except Exception as exc:  # noqa: BLE001 — the mis-binding crash
            record("S9.2.1 create_project_snapshot binds (req, project)", False,
                   f"{type(exc).__name__}: {exc}"[:110])
        else:
            # The label only round-trips if `req` got the CreateSnapshotRequest;
            # swapped, the handler reads `.name` off that model and dies.
            seen_label = getattr(info, "label", None)
            record("S9.2.1 create_project_snapshot binds (req, project)",
                   seen_label == S9_LABEL, f"label={seen_label!r}")
        snapshot_id = getattr(info, "id", None)
        try:
            rows = chat_tools.list_project_snapshots(S9_PROJECT)
        except Exception as exc:  # noqa: BLE001
            record("S9.2.2 list_project_snapshots answers for that project", False,
                   f"{type(exc).__name__}: {exc}"[:110])
        else:
            ids = [getattr(r, "id", None) for r in rows] if isinstance(rows, list) else []
            # Finding the id here also proves S9.2.1's snapshot landed under the
            # right project's directory, not merely that the call returned.
            record("S9.2.2 list_project_snapshots answers for that project",
                   snapshot_id is not None and snapshot_id in ids,
                   f"{len(ids)} snapshot(s), created id present={snapshot_id in ids}")
        _expect_inner_404(
            "S9.2.3 restore_project_snapshot binds (snapshot_id, project)",
            lambda: chat_tools.restore_project_snapshot(
                S9_PROJECT, S9_MISSING_SNAPSHOT
            ),
        )
        _expect_inner_404(
            "S9.2.4 delete_project_snapshot binds (snapshot_id, project)",
            lambda: chat_tools.delete_project_snapshot(
                S9_PROJECT, S9_MISSING_SNAPSHOT
            ),
        )
    finally:
        chat_tools.set_acting_user(None)


def _probe_handler_with_session(
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
    session: SessionRow | None = Depends(current_session),
):
    """
    Stand in for `save_project`'s THREE-dependency shape.

    Reports the injected session's id, so the check can tell a resolved
    `SessionRow` from `None` and from an unresolved `Depends` sentinel. Read
    HERE, inside `_acting()`'s DB session, rather than by the caller — that is
    the discipline the fix itself follows.
    """
    return str(session.id) if session is not None else repr(session)


def _probe_handler_without_session(
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """
    Stand in for the majority of handlers `_route` serves.

    They declare no `session`, so passing the keyword unconditionally would
    TypeError every one of them. This is the check that keeps the fix keyed on
    the target's signature.
    """
    return "ok"


def _pointer_project_name(session_id: str) -> str | None:
    """The display name of the project `session_id` currently points at."""
    with SessionLocal() as db:
        row = db.get(SessionRow, uuid.UUID(session_id))
        if row is None or row.active_project_id is None:
            return None
        project = db.get(Project, row.active_project_id)
        return None if project is None else project.name


def _describe(call) -> str:
    """Run `call` and render its outcome as one short string."""
    try:
        return f"ok:{call()}"
    except HTTPException as exc:
        return f"HTTPException({exc.status_code}): {exc.detail}"
    except Exception as exc:  # noqa: BLE001 — the unresolved-Depends crash
        return f"{type(exc).__name__}: {exc}"


def _probe_turn_f3(session, message, attachment_file_ids=None):
    """
    Stand in for `run_turn` and exercise the session-bound tools from a tool thread.

    Everything after the first `yield` runs under a fresh `iterate_in_threadpool`
    context copy, and each probe is submitted into `chat_service._TOOL_EXECUTOR`
    through the same `copy_context()` snapshot the real dispatcher uses — so the
    acting SESSION is observed under exactly the conditions a real tool call
    sees, not merely in the handler that bound it.
    """
    yield "token", {"text": "probe"}
    snapshot = contextvars.copy_context()
    sid = str(_observed["f3_session_id"])

    def _run(fn):
        return chat_service._TOOL_EXECUTOR.submit(lambda: snapshot.run(fn)).result(
            timeout=120
        )

    def _tool(call):
        """One tool's outcome, plus where it left the session's pointer."""
        outcome = _describe(call)
        return f"{outcome} pointer={_pointer_project_name(sid)!r}"

    _observed["f3_session_row"] = _run(
        lambda: _describe(lambda: chat_tools._route(_probe_handler_with_session))
    )
    _observed["f3_no_session_kwarg"] = _run(
        lambda: _describe(lambda: chat_tools._route(_probe_handler_without_session))
    )
    _observed["f3_save"] = _run(
        lambda: _tool(lambda: bool(chat_tools.save_project(F3_PROJECT_A)))
    )
    _observed["f3_save_as"] = _run(
        lambda: _tool(lambda: bool(chat_tools.save_project_as(F3_PROJECT_B)))
    )
    _observed["f3_activate"] = _run(
        lambda: _tool(lambda: bool(chat_tools.activate_project(F3_PROJECT_A)))
    )
    yield "done", {"ok": True}


def run_f3() -> None:
    """Drive one cookie-authenticated turn and prove the session really resolves."""
    print("F3 — _route resolves the acting SESSION, not just the user")
    settings = get_settings()
    # Auth ON for this group: local mode issues no session cookie, so
    # `current_session` is None there by design and neither the fix nor the
    # rejected `session=None` shortcut would be distinguishable under it.
    os.environ["PYPSAGUI_LOCAL_MODE"] = "0"
    original = chat_service.run_turn
    chat_service.run_turn = _probe_turn_f3
    try:
        # The identity itself is the one `lifespan` seeded during run_f1 — it
        # already has an org, a membership and status="active". All this adds is
        # a real session row to carry a cookie, which local mode never mints.
        from services.auth_service import create_session
        with SessionLocal() as db:
            raw_token, row = create_session(db, local_mode.LOCAL_USER_ID)
            session_id = str(row.id)
        _observed["f3_session_id"] = session_id
        csrf_token = security.new_csrf_token()
        # Unbind in THIS thread first, exactly as run_f1 does: a value left
        # bound here is the root context every downstream copy descends from.
        # Nothing here binds the SESSION — the router must do that, or the
        # check would be testing the harness rather than the fix.
        chat_tools.set_acting_user(None)
        with TestClient(main.app) as client:
            client.cookies.set(settings.session_cookie_name, raw_token)
            client.cookies.set(settings.csrf_cookie_name, csrf_token)
            client.headers[settings.csrf_header_name] = csrf_token
            response = client.post(
                "/api/chat/stream",
                json={"session_id": "f3-regress", "message": "probe"},
            )
        record("F3.0 authenticated stream opened", response.status_code == 200,
               f"HTTP {response.status_code}")
    finally:
        chat_service.run_turn = original
        os.environ["PYPSAGUI_LOCAL_MODE"] = "1"
        chat_tools.set_acting_user(None)

    seen_row = _observed.get("f3_session_row")
    record(
        "F3.1 _route injects the resolved SessionRow",
        seen_row == f"ok:{session_id}",
        f"saw {seen_row!r}"[:150],
    )
    seen_plain = _observed.get("f3_no_session_kwarg")
    record(
        "F3.2 handlers without `session` keep their signature",
        seen_plain == "ok:ok",
        f"saw {seen_plain!r}"[:150],
    )
    # The three checks the human's ruling turns on. A `session=None` shortcut
    # stops the AttributeError and leaves every one of these pointers None.
    for name, key, expected in (
        ("F3.3 save_project moves the session pointer", "f3_save", F3_PROJECT_A),
        ("F3.4 save_project_as rebinds the session pointer", "f3_save_as", F3_PROJECT_B),
        ("F3.5 activate_project moves the session pointer", "f3_activate", F3_PROJECT_A),
    ):
        seen = _observed.get(key)
        record(name, seen == f"ok:True pointer={expected!r}", f"saw {seen!r}"[:150])


def main_() -> int:
    """Run all three groups and return a process exit code."""
    run_f1()
    run_s9()
    run_f3()
    run_s9_binding()
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = len(_results) - passed
    print(f"\nSUMMARY  PASS {passed}  FAIL {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main_())
