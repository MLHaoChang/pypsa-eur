"""
The scaffolding a standalone ``qa_*.py`` driver needs to reach the backend the
way a real caller does: a sandboxed database, a seeded organisation, a signed-in
client, and project directories resolved through the registry.

Why this exists
---------------
``pytest.ini`` sets ``python_files = test_*.py``, so the ``qa_*.py`` drivers are
never collected — they run as ``python tests/qa_x.py``. That was fine until the
auth/tenancy migration, after which:

* every ``/api`` route requires a session, so an unauthenticated ``TestClient``
  gets ``401`` on every request; and
* calling a project handler as a plain function hands its
  ``db: DBSession = Depends(get_db)`` / ``user: User = Depends(optional_user)``
  parameters the *default* values — ``fastapi.params.Depends`` sentinels — which
  reach ``project_registry._org_id_or_none`` and raise
  ``AttributeError: 'Depends' object has no attribute 'id'``.

``tests/conftest.py`` already solves all of this for pytest. This module hands
the same thing to a script.

It deliberately **imports** ``tests.conftest`` rather than restating it. That
import is what pins ``DATABASE_URL``, ``PROJECTS_ROOT``, ``PYPSAGUI_APP_DATA_DIR``
and the rest at throwaway locations — and it must happen *before* ``main`` is
imported, because ``get_settings()`` is ``lru_cache``d and ``routers/projects.py``
reads ``PROJECTS_DIR`` at import time. So a driver imports this module FIRST,
ahead of its own ``from main import app``::

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

    from tests import qa_support          # ← pins the sandbox, before main
    from main import app                  # noqa: E402

Getting that order wrong is not a subtle failure: the driver writes into the
developer's real projects directory. That is the second reason this module
exists — before it, running one of these drivers by hand wrote into
``backend/projects/``.

What a driver gets
------------------
``client()``          an authenticated ``TestClient`` (session cookie + CSRF).
``anon_client()``     an unauthenticated one, for the 401 contrast.
``db_session()``      a context manager yielding a real ``Session``.
``user()``            the seeded ``User`` row, for direct handler calls.
``session_row(db)``   the client's live ``Session`` row, ditto.
``install_network(n)``  make ``n`` what the client's next request sees.
``save_project(...)`` save the live singleton through the real route.
``project_dir(name)`` the org-scoped directory a project's files live in.
``delete_project(...)`` remove row + directory, so a re-run does not 409.
``reset_backend()``   the same singleton reset the pytest suite does per test.

Calling a handler directly is still fine, but pass EVERY dependency it
declares — not just the ones you happen to know about::

    with qa_support.db_session() as db:
        projects_router.load_project(
            name, db=db, user=qa_support.user(),
            session=qa_support.session_row(db),
        )

which is what the ``Depends`` defaults stand in for. Miss one and the handler
gets the ``Depends`` object itself, which is not ``None`` — so a
``session is not None`` guard sails straight past it and the attribute error
lands somewhere else entirely. That is exactly how the ``session`` parameter
went unnoticed here: this example used to omit it.
"""
from __future__ import annotations

import atexit
import contextlib
import pathlib
import shutil
import sys
import tempfile

# The drivers add this themselves, but this module must survive being imported
# first, from anywhere.
_BACKEND = pathlib.Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# THE load-bearing import. Pins every environment variable at a throwaway
# location and then imports `main`. Nothing above may import `main`, `settings`
# or `routers.*`, or the pinning lands too late to matter.
from tests import conftest as _harness  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

import main  # noqa: E402

_EMAIL = "qa-driver@example.com"
_ORG = "QA Driver Org"

# A FILE, not `:memory:`. The suite's sandbox is one in-memory database behind a
# StaticPool — one `sqlite3.Connection` shared by everything — which is safe
# there because `TestClient` drives one request at a time. It is not safe here:
# `qa_phase4_compare` boots a real uvicorn and fires concurrent requests, so its
# handlers run on anyio worker threads. Python 3.12 does not tolerate that:
# a reduced repro raises `sqlite3.InterfaceError: bad parameter or other API
# misuse` where 3.11 comes through clean, and in CI it surfaced as
# `ValueError: badly formed hexadecimal UUID string` — a value from the wrong
# place reaching a UUID column's result processor — while the same driver passed
# on a local 3.11 interpreter.
#
# A file needs no shared connection to be one database, so every thread gets its
# own — which is how the product itself runs on SQLite. See
# `tests/test_qa_support_sandbox.py`.
_DB_DIR = pathlib.Path(tempfile.mkdtemp(prefix="pypsagui-qa-db-"))
_engine, _session_local, _ = _harness.make_auth_db(
    f"sqlite+pysqlite:///{_DB_DIR / 'qa.db'}"
)
_user_id, _org_id = _harness._seed_org_and_user(
    _session_local, email=_EMAIL, org_name=_ORG
)

_clients: list[TestClient] = []


@atexit.register
def _close_clients() -> None:
    for c in _clients:
        with contextlib.suppress(Exception):
            c.__exit__(None, None, None)
    with contextlib.suppress(Exception):
        _engine.dispose()
    shutil.rmtree(_DB_DIR, ignore_errors=True)


def _new_client(authenticate: bool) -> TestClient:
    """
    A TestClient with the app's lifespan actually started.

    The drivers used to do a bare ``TestClient(app)``, which never runs startup
    or shutdown. `conftest` uses ``with TestClient(app) as c`` for that reason;
    a script has no ``with`` block to hang it on, so the client is entered here
    and closed at exit.
    """
    client = TestClient(main.app)
    client.__enter__()
    _clients.append(client)
    if authenticate:
        _harness.attach_session(client, _session_local, _user_id)
    return client


_authed: TestClient | None = None
_anon: TestClient | None = None


def client() -> TestClient:
    """The signed-in client. One per process — sessions are server-side."""
    global _authed
    if _authed is None:
        _authed = _new_client(authenticate=True)
    return _authed


def anon_client() -> TestClient:
    """A client with no session, for asserting the 401 path deliberately."""
    global _anon
    if _anon is None:
        _anon = _new_client(authenticate=False)
    return _anon


@contextlib.contextmanager
def db_session():
    """A real ``Session`` against the sandbox database."""
    with _session_local() as db:
        yield db


def user():
    """The seeded ``User`` row — what ``Depends(optional_user)`` would supply."""
    from db.models import User

    with _session_local() as db:
        return db.get(User, _user_id)


def org_id():
    return _org_id


def session_row(db):
    """
    The signed-in client's live ``Session`` row, bound to ``db``.

    What ``Depends(current_session)`` supplies. Routes that move the per-session
    active-project pointer take it, and a driver calling such a route directly
    must hand over a real row: the ``Depends`` sentinel is not ``None``, so the
    ``if session is not None`` guards those routes use let it through and the
    failure surfaces as ``'Depends' object has no attribute 'active_project_id'``
    from two frames deeper.

    Takes ``db`` rather than opening its own so the row is attached to the SAME
    session the caller passes to the handler — a row loaded on another
    ``Session`` would be detached by the time the handler wrote to it.
    """
    from services.auth_service import resolve_session_row
    from settings import get_settings

    raw = client().cookies.get(get_settings().session_cookie_name)
    if not raw:
        raise RuntimeError("the client has no session cookie")
    row = resolve_session_row(db, raw)
    if row is None:
        raise RuntimeError("the client's session is not live")
    return row


def session_context():
    """
    The ``ProjectContext`` the signed-in client's own requests resolve to.

    Not the same thing as ``PyPSAService.get_network()`` read from the calling
    thread. The active project is per SESSION since the tenancy migration, so a
    driver reading the process foreground is looking at a DIFFERENT context from
    the one its HTTP requests just mutated — which reads as "the route didn't do
    anything". `conftest`'s ``session_ctx`` fixture exists for the same reason;
    this is that fixture, as a function.
    """
    from services import active_project

    with _session_local() as db:
        ctx, _slot = active_project.resolve_for_session(db, session_row(db))
        return ctx


def install_network(n, name: str | None = None):
    """
    Make ``n`` the network the client's next request will see.

    ``PyPSAService.set_network(n)`` alone is not enough and fails quietly: it
    writes the PROCESS foreground, which a session adopts exactly once, so the
    second install in a driver is invisible and the client keeps serving the
    first network. This is the suite's own `install_network`, which also drops
    resident scratch contexts and un-binds live sessions.
    """
    return _harness.install_network_into_backend(n, name)


def reset_backend() -> None:
    """The singleton/state reset the pytest suite performs around every test."""
    _harness._reset_backend_state()


def project_row(name: str):
    """The ``Project`` row for ``name`` inside the seeded org, or None."""
    from db.models import Project

    with _session_local() as db:
        return db.scalar(
            select(Project).where(Project.org_id == _org_id, Project.name == name)
        )


def project_dir(name: str) -> pathlib.Path | None:
    """
    Where ``name``'s files actually live, or None if there is no such project.

    Storage is org-scoped (``projects_root/<org>/<uuid>/``) since the tenancy
    migration, so ``PROJECTS_DIR / name`` — what these drivers all used to do —
    names a path that no longer exists even when the project does. Resolve
    through the registry, exactly as the routes do.
    """
    from services import project_registry

    row = project_row(name)
    return None if row is None else project_registry.project_dir(row)


def save_project(name: str, **params) -> None:
    """
    Save the live singleton network as ``name``, through the real route.

    ``force=True`` by default: a driver re-run must not 409 on the project it
    left behind last time. Raises on a non-200 so a broken save surfaces where
    it happened rather than as a puzzling assertion three steps later.
    """
    query = {"force": True, "clear_undo": False}
    query.update(params)
    resp = client().post(f"/api/projects/{name}", params=query)
    if resp.status_code != 200:
        raise RuntimeError(
            f"save of {name!r} failed: {resp.status_code} {resp.text[:300]}"
        )


def delete_project(*names: str) -> None:
    """
    Remove each project's row AND its directory, ignoring the ones absent.

    Both halves matter. A leftover DIRECTORY is merely stale, but a leftover
    ROW makes the next save of the same name collide — the same reason
    `conftest` truncates the project tables between tests.
    """
    from db.models import Project

    for name in names:
        directory = project_dir(name)
        if directory is not None and directory.exists():
            shutil.rmtree(directory, ignore_errors=True)
        with _session_local() as db:
            row = db.scalar(
                select(Project).where(Project.org_id == _org_id, Project.name == name)
            )
            if row is not None:
                db.delete(row)
                db.commit()
