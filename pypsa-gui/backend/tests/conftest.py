"""
Pytest harness for the pypsa-gui FastAPI backend.

Run with (from the repo root, using the pixi env's python):
    pixi run python -m pytest pypsa-gui/backend/tests
or from the backend dir:
    cd pypsa-gui/backend && <pixi-python> -m pytest

The existing `qa_*.py` files in this directory are standalone PASS/FAIL scripts;
`pytest.ini`'s `python_files = test_*.py` means pytest never collects them.

Isolation: the backend holds ONE shared in-memory `pypsa.Network` singleton plus
a module-level `_state` dict (in routers.simulation). The autouse `reset_backend`
fixture resets both before AND after every test so state can't bleed between tests.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import sys

# Make `main`, `routers`, `services` importable (mirrors the qa_*.py header).
_BACKEND = pathlib.Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# Pin the environment BEFORE `import main`, which is what triggers
# python-dotenv to read `backend/.env`.
#
# `PYPSA_GUI_AUTH_ENABLED` is GONE (Step 0a): single-user mode is deleted, not
# defaulted, so there is no flag to pin and no auth-off branch to exercise. The
# suite used to pin it `"false"`, which meant every one of its ~1100 tests ran
# in the mode that no longer exists. The replacement is below: one shared
# SQLite database, one seeded org + user, and a `client` fixture that is
# authenticated. Tests keep calling `client.get("/api/...")` unchanged and now
# exercise the shipping configuration.
#
# The DB is pinned here too — without it, `db/session.py` reads the developer's
# real `DATABASE_URL` from `.env` and the suite would write to their Postgres.
# dotenv loads with `override=False`, so an already-present value wins.
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret-not-a-real-key")
# `testserver` is TestClient's synthetic host. The CSRF Origin check only
# rejects a PRESENT-and-not-allowlisted origin, and TestClient sends none, but
# the tests that assert the reject path need a known-good origin to contrast
# with — so allowlist it explicitly rather than relying on absence.
os.environ["CORS_ALLOWED_ORIGINS"] = (
    "http://localhost:5173,http://127.0.0.1:5173,http://testserver"
)
# Org-scoped project storage now resolves under `settings.projects_root`. Point
# it at a throwaway directory for the whole session so a test that saves a
# project cannot land in the developer's real `backend/projects/`. Set before
# `import main`, because `get_settings()` is `lru_cache`d and main reads it at
# import time.
import tempfile as _tempfile

_TEST_PROJECTS_ROOT = _tempfile.mkdtemp(prefix="pypsa-gui-test-projects-")
os.environ["PROJECTS_ROOT"] = _TEST_PROJECTS_ROOT

# Same reason, for the three paths that now default into the per-user app-data
# dir instead of next to the source. Without all three, `test_legacy_migrate` /
# `test_tenancy_api` and anything that calls `ensure_app_dirs()` accumulate
# directories in the developer's real ~/Library/Application Support/PyPSA GUI/
# and become history-dependent. Pinning only one moves the problem to the others.
os.environ["PYPSAGUI_APP_DATA_DIR"] = _tempfile.mkdtemp(prefix="pypsa-gui-test-appdata-")
os.environ["LEGACY_ROOT"] = _tempfile.mkdtemp(prefix="pypsa-gui-test-legacy-")
os.environ["FLAT_PROJECTS_ROOT"] = _tempfile.mkdtemp(prefix="pypsa-gui-test-flat-")
# Same reasoning, one step further: this one is UNSET rather than redirected.
# A developer who exported it — exactly what the importer's rehearsal
# instructions teach — would otherwise have every local-mode test walk their
# real legacy tree, and the first-run import fire against it.
os.environ.pop("PYPSAGUI_LEGACY_IMPORT_ROOT", None)

import pandas as pd
import pypsa
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool, StaticPool

from db.base import Base
import main
from routers import network as net_router
from routers import projects as projects_router
from routers import simulation as sim_router
from routers.projects import _RESULTS_STATE_KEYS
from services import project_registry, undo_service
from services.pypsa_service import PyPSAService
from services.solve_queue import solve_queue
from services.solver_service import SolverConfig


def _reset_backend_state() -> None:
    """Fresh singleton network (unbound) + cleared lifecycle/result state."""
    # `allow_during_study=True` is exactly the internal caller the opt-out
    # exists for (Phase 11 review, finding 9): a test that leaves a live
    # study thread in the process foreground would otherwise raise 409 OUT
    # of this autouse fixture, skipping every cleanup below it — the
    # registry clear, `_user_ts`, undo and the solve queue — so state bled
    # into the next test and the failure surfaced far from its cause.
    PyPSAService.reset_network(allow_during_study=True)
    PyPSAService._contexts.clear()  # B2 registry: no resident ctxs bleed across tests
    # `_user_ts` is a PROCESS-GLOBAL time-series store in routers.network (keyed
    # by (component, attr, name)), NOT a per-context field — so reset_network()
    # doesn't touch it. Only POST /network/reset clears it (which tests don't
    # hit). Without this, a profile captured by one test (e.g. test_solve_queue's
    # Load "L1" p_set) reapplies into the next test's same-named component and
    # silently overrides its static value — making an intended-infeasible/shed
    # network feasible. Mirror the route handler's clear.
    with net_router._user_ts_lock:
        net_router._user_ts.clear()
    # The campaign budget lives in the context's `solver_state`, beside the
    # study records it gates — and `reset_network` CARRIES solver_state
    # forward (see ProjectContext), while the sweep below only clears
    # _RESULTS_STATE_KEYS, which this key is not one of. So without this a
    # campaign opened by one test keeps spending in the next.
    from services.adequacy import campaign as _campaign
    _campaign.reset()
    sim_router._state["solver_config"] = SolverConfig()
    sim_router._state_update(**{k: None for k in _RESULTS_STATE_KEYS})
    sim_router._state_update(status="idle", condition=None, objective=None, solve_time=None)
    # Clear solver-worker handles too, so a FUTURE test that exercises
    # POST /api/simulation/run can't observe a stale in-flight worker via
    # `_solver_in_flight()` (the current suite doesn't run a solve through the
    # API, but the harness is meant to be extended). Also drop the undo stack
    # so undo/changelog state can't bleed across tests.
    sim_router._state_update(thread=None, stop_event=None, log_queue=None)
    undo_service.clear()
    # Drain the multi-project solve queue so jobs (and the dispatcher's view of
    # "current") can't bleed across tests. The daemon dispatcher thread stays
    # parked on the now-empty queue — it's reused by the next test that enqueues.
    solve_queue.reset_for_tests()


@pytest.fixture(autouse=True)
def reset_backend():
    """Reset shared backend state around every test (the singleton leaks otherwise)."""
    import security

    # The login throttle is process-global and counts across tests. Without this
    # reset, a module that exercises bad credentials burns the shared budget and
    # every later `client` fixture fails to sign in with a 429 — a failure that
    # depends on test ORDER, which is the worst kind to debug.
    security.reset_login_throttle_for_tests()
    _reset_backend_state()
    yield
    _reset_backend_state()


@pytest.fixture(autouse=True)
def _clean_llm_profiles():
    """
    Task 9 — `llm-profiles.json` must not survive past the test that wrote it.

    `PYPSAGUI_APP_DATA_DIR` is pinned ONCE, above, for the whole session — a
    deliberate choice for most app-data files (a throwaway dir per test would
    multiply tempdirs for no benefit). But `llm_config.profiles_path()`
    resolves against that SAME session-wide directory for any test that
    doesn't redirect it per-test via `monkeypatch.setenv(...)`, and
    `load_profiles()` re-reads the file on every call with no caching — so a
    profile written by one test is immediately visible to every OTHER test in
    the session that reads profiles without its own redirect, including ones
    that assert on a specific zero-config/"no profiles file" starting state.
    Unlinking after every test (not before) means a test that crashes
    mid-body still leaves a clean slate for the next one.
    """
    from services import llm_config

    yield
    llm_config.profiles_path().unlink(missing_ok=True)


# ── Auth harness (Step 0a) ──────────────────────────────────────────────────
# Single-user mode is gone, so EVERY /api route now requires a session and every
# state-changing one requires a CSRF token. Rather than touch ~60 test modules,
# the shared `client` fixture below signs in and carries both. A test that wants
# the anonymous or cross-tenant view asks for `anon_client` / `second_org` and
# gets a clean, explicit contrast.

_SEED = {"password": "test-password-123"}

def pytest_addoption(parser):
    """
    `--record-chat-frames` writes tests/golden/chat_turn_frames.json.

    An option rather than an env var so it shows in `pytest --help`, and
    off by default so an ordinary suite run cannot overwrite the recording it
    is being gated against. See tests/test_chat_turn_frame_contract.py.
    """
    parser.addoption(
        "--record-chat-frames", action="store_true", default=False,
        help="re-record the chat turn frame contract (deliberate; read the diff)",
    )




def make_auth_db(url: str | None = None):
    """
    Build the SQLite database the harness runs against, and install its
    sessionmaker as `db.session.SessionLocal`.

    Returns `(engine, session_local, previous_session_local)` — the caller owns
    teardown, because the two callers own it differently: the `_auth_db`
    fixture restores and disposes at end of session, while `tests/qa_support.py`
    (the standalone `qa_*.py` drivers) holds it for the life of the process.

    Extracted from the fixture body so the drivers get THIS database rather
    than a second hand-rolled copy of it. A copy would drift, and the way it
    would drift is silent: miss StaticPool below and the seeded user simply
    isn't there for the request that needs it, which reads as an auth bug.

    **Default (`url=None`) — one `:memory:` database behind a `StaticPool`.**
    StaticPool is load-bearing there: `:memory:` gives each *connection* its own
    database, and the app opens connections from several places (the auth
    middleware, `get_db`, the solve dispatcher). Without a single pooled
    connection the seeded user would be invisible to the request that needs it.
    The suite drives the app through `TestClient`, one request at a time, so
    nothing ever uses that one connection from two threads at once.

    **A file URL — a normal connection per checkout, no StaticPool.**
    For a caller that DOES touch the database from several threads:
    `tests/qa_phase4_compare.py` boots a real uvicorn and fires concurrent
    requests, so its handlers run on anyio worker threads. Handing those threads
    one shared `sqlite3.Connection` is unsafe, and how it fails depends on the
    interpreter — measured on SQLAlchemy 2.0.50, twelve threads mixing reads and
    writes: Python 3.11 came through clean, Python 3.12 raised
    `sqlite3.InterfaceError: bad parameter or other API misuse`. A file needs no
    shared connection to be one database, which is also how the product runs on
    SQLite (`db/session.py::get_engine`).

    See `tests/test_qa_support_sandbox.py`.
    """
    from db import session as db_session_module

    if url is None:
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    else:
        # Mirrors `db/session.py::get_engine`'s file-backed branch. NullPool so
        # a connection is opened and closed per checkout rather than parked in a
        # pool; `check_same_thread=False` because FastAPI serves sync handlers
        # from a worker thread, which is safe now that no two of them share one
        # connection.
        engine = create_engine(
            url,
            connect_args={"check_same_thread": False, "timeout": 30},
            poolclass=NullPool,
        )
    db_session_module.enable_sqlite_foreign_keys(engine)
    Base.metadata.create_all(engine)
    testing_session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    original = db_session_module.SessionLocal
    db_session_module.SessionLocal = testing_session_local
    return engine, testing_session_local, original


@pytest.fixture(scope="session")
def _auth_db():
    """One FILE-BACKED SQLite database shared by every test in the session.

    A file, not `:memory:` + `StaticPool`, and the reason is thread safety
    rather than taste. The old justification for the pool ("`:memory:` gives
    each *connection* its own database, so every caller must be routed through
    ONE shared pooled connection or the seeded user is invisible to it") stops
    applying the moment the database is a real file: every connection then
    opens the same file.

    That funnelling was a latent hazard, not just an optimisation.
    `test_hydrate_or_adopt_cold_paths.py` races real OS threads — a
    `threading.Barrier`-synchronised `Session` per thread — and several
    `Session`s issuing overlapping statements through ONE physical
    `sqlite3.Connection` produced genuine corruption: `ValueError: badly formed
    hexadecimal UUID string` reading back a UUID column, and a `User` row
    committed session-scopes earlier intermittently reading back as absent
    (`404 Project not found` / `401 Authentication required` from inside the
    race). Neither was a bug in the code under test. `StaticPool` is the
    textbook-safe pattern for a shared engine used SEQUENTIALLY across threads;
    it was never safe for the genuinely CONCURRENT access those lock tests
    deliberately drive.

    This is the same conclusion `make_auth_db`'s file branch already reached
    for `qa_phase4_compare.py`, applied to the suite itself — so both callers
    now take the branch that matches how the product runs on SQLite
    (`db/session.py::get_engine`). `enable_sqlite_foreign_keys` meanwhile
    becomes meaningful per connection on a real file, giving reader/writer
    concurrency arbitrated by SQLite's own file locking.

    Lives in a session-scoped temp directory, removed on teardown.
    """
    from db import session as db_session_module

    tmp_dir = _tempfile.mkdtemp(prefix="pypsa-gui-test-authdb-")
    db_path = pathlib.Path(tmp_dir) / "auth.db"
    engine, testing_session_local, original = make_auth_db(f"sqlite+pysqlite:///{db_path}")
    try:
        yield engine, testing_session_local
    finally:
        db_session_module.SessionLocal = original
        engine.dispose()
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _seed_org_and_user(session_local, *, email: str, org_name: str):
    """Create an active org + admin member and return (user_id, org_id)."""
    import uuid as _uuid
    from datetime import datetime, timezone

    from db.models import OrgMembership, Organization, User
    from services.auth_service import hash_password

    with session_local() as db:
        org = Organization(id=_uuid.uuid4(), name=org_name,
                           created_at=datetime.now(tz=timezone.utc))
        user = User(
            id=_uuid.uuid4(),
            email=email,
            password_hash=hash_password(_SEED["password"]),
            status="active",
            is_super_admin=False,
            created_at=datetime.now(tz=timezone.utc),
        )
        db.add_all([org, user])
        db.flush()
        db.add(OrgMembership(id=_uuid.uuid4(), user_id=user.id,
                             org_id=org.id, role="admin"))
        db.commit()
        return user.id, org.id


@pytest.fixture(scope="session")
def seeded_identity(_auth_db):
    _engine, session_local = _auth_db
    user_id, org_id = _seed_org_and_user(
        session_local, email="tester@example.com", org_name="Test Org"
    )
    return {"email": "tester@example.com", "user_id": user_id, "org_id": org_id}


@pytest.fixture(scope="session")
def second_identity(_auth_db):
    """A SECOND org + user, for cross-tenant assertions (S0.1 / S0.3 / S0.5)."""
    _engine, session_local = _auth_db
    user_id, org_id = _seed_org_and_user(
        session_local, email="other@example.com", org_name="Other Org"
    )
    return {"email": "other@example.com", "user_id": user_id, "org_id": org_id}


def sign_in(test_client, email: str) -> TestClient:
    """
    Log `test_client` in over the REAL login route and pin the CSRF header.

    Used by the auth-journey tests that must exercise the password path. The
    default `client` fixture takes the cheaper `attach_session` route below —
    the password hasher is Argon2, and paying ~100 ms per test to re-prove a
    code path that has its own dedicated tests is not a good trade.

    The token is read from the login RESPONSE BODY rather than the cookie jar
    on purpose: that proves the body channel works for non-browser clients,
    which is the contract native/CLI callers depend on.
    """
    resp = test_client.post(
        "/api/auth/login", json={"email": email, "password": _SEED["password"]}
    )
    assert resp.status_code == 200, resp.text
    test_client.headers["X-CSRF-Token"] = resp.json()["csrf_token"]
    return test_client


def attach_session(test_client, session_local, user_id) -> TestClient:
    """Mint a real session row for `user_id` and put its cookies on the client."""
    import security
    from services.auth_service import create_session
    from settings import get_settings

    settings = get_settings()
    with session_local() as db:
        raw_token, _session = create_session(db, user_id)
    csrf_token = security.new_csrf_token()
    test_client.cookies.set(settings.session_cookie_name, raw_token)
    test_client.cookies.set(settings.csrf_cookie_name, csrf_token)
    test_client.headers["X-CSRF-Token"] = csrf_token
    return test_client


@pytest.fixture(autouse=True)
def _reset_tenant_tables(_auth_db):
    """
    Truncate the per-test tenant tables between tests.

    The engine and the seeded org/user are SESSION-scoped (Argon2 hashing is
    too slow to repeat 1000×), but project rows must not survive: dozens of
    tests save a project called `A`, and a leftover row makes the second one
    409 on a name collision. Users, orgs and memberships deliberately persist.

    `solve_jobs` joined this list with Task 15's boot reconciliation. The
    `client` fixture below is FUNCTION-scoped and enters `TestClient(main.app)`
    as a context manager, so `lifespan` — and with it
    `solve_job_store.reconcile_on_boot()` — runs on every single test that
    requests `client`, not once per process the way it does in production. A
    `queued` or `running` row a PRIOR test left behind (the in-memory queue is
    cleared by `reset_backend` below, but that never touches the table) would
    otherwise be resurrected into the freshly-reset in-memory queue at the
    START of an unrelated later test — observed as extra jobs a queue-listing
    test never enqueued itself. Cheap and always safe to drop: nothing else
    reads solve_jobs rows across a test boundary.
    """
    engine, _session_local = _auth_db
    from sqlalchemy import text

    yield
    with engine.begin() as conn:
        for table in ("project_locks", "project_memberships", "projects", "solve_jobs"):
            conn.execute(text(f"DELETE FROM {table}"))

@pytest.fixture(autouse=True)
def _acting_user(_auth_db, seeded_identity):
    """
    Bind the seeded user as the chat tools' acting identity.

    `chat_tools` calls the project routers in-process, so it needs the same
    identity an HTTP caller would carry; without it every project tool raises
    401 `no_acting_user`. Autouse because the whole suite already runs as this
    user through the `client` fixture — the tool layer should not be the one
    place that runs anonymously. `test_chat_tools_identity.py` covers the
    genuinely-unbound case explicitly, so autouse does not hide it.
    """
    from services import chat_tools

    chat_tools.set_acting_user(seeded_identity["user_id"])
    yield
    chat_tools.set_acting_user(None)


@pytest.fixture
def anon_client(_auth_db):
    """TestClient with NO session — for 401 / public-path assertions."""
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def client(_auth_db, seeded_identity):
    """
    Authenticated FastAPI TestClient over the real app.

    Carries the primary seeded user's session and CSRF token, so the hundreds
    of pre-existing `client.post("/api/network/...")` calls keep working — and
    now run THROUGH the auth + CSRF middleware they will meet in production
    rather than around it.
    """
    _engine, session_local = _auth_db
    with TestClient(main.app) as c:
        yield attach_session(c, session_local, seeded_identity["user_id"])


@pytest.fixture
def other_org_client(_auth_db, second_identity):
    """Authenticated client belonging to a DIFFERENT organization."""
    _engine, session_local = _auth_db
    with TestClient(main.app) as c:
        yield attach_session(c, session_local, second_identity["user_id"])


# ── Tenant-aware lookups for assertions ─────────────────────────────────────
# Project storage is org-scoped (`projects_root/<org>/<uuid>/`) and the resident
# registry is keyed `org:uuid`, so a test can no longer assert on a name-derived
# path or registry key. These resolve a name the way the app does.

# ── Reading a client's OWN context (Step 0b) ────────────────────────────────
# The active project is per SESSION now, so `PyPSAService.get_active_id()` read
# from the TEST thread reports the process foreground — a different context from
# the one the client's requests resolve to. Tests that used to assert on the
# process global assert through these instead. This is not a workaround: it is
# the same resolution the app performs, so an assertion here fails for exactly
# the reasons a user would notice.

@pytest.fixture
def session_ctx(_auth_db):
    """Factory: the `ProjectContext` a given test client's session resolves to."""
    _engine, session_local = _auth_db

    def _ctx(test_client):
        from services import active_project
        from services.auth_service import resolve_session_row
        from settings import get_settings

        raw = test_client.cookies.get(get_settings().session_cookie_name)
        assert raw, "client has no session cookie"
        with session_local() as db:
            row = resolve_session_row(db, raw)
            assert row is not None, "client's session is not live"
            ctx, _slot = active_project.resolve_for_session(db, row)
            return ctx
    return _ctx


@pytest.fixture
def session_state(session_ctx):
    """Factory: the solver-lifecycle dict for a client's own context."""
    def _state(test_client):
        return session_ctx(test_client).solver_state
    return _state


@pytest.fixture
def api_project(client, install_network):
    """
    Factory: create a REAL project (DB row + org-scoped storage) and return its
    name.

    Route tests need this since Step 0a — a hand-made directory under the
    projects root is no longer a project, because the routes resolve names
    through the registry inside the caller's org rather than by path join.
    """
    def _make(name: str = "demo") -> str:
        install_network(build_network(), name=name)
        resp = client.post(
            f"/api/projects/{name}", params={"force": True, "rebind": True}
        )
        assert resp.status_code == 200, resp.text
        return name
    return _make


@pytest.fixture
def project_row(_auth_db, seeded_identity):
    from db.models import Project
    from sqlalchemy import select

    _engine, session_local = _auth_db

    def _row(name: str, org_id=None):
        with session_local() as db:
            return db.scalar(
                select(Project).where(
                    Project.org_id == (org_id or seeded_identity["org_id"]),
                    Project.name == name,
                )
            )
    return _row


@pytest.fixture
def registry_key_for(project_row):
    """Name → the `org:uuid` key the resident registry uses."""
    def _key(name: str, org_id=None) -> str:
        row = project_row(name, org_id)
        assert row is not None, f"no project row named {name!r}"
        return f"{row.org_id}:{row.id}"
    return _key


@pytest.fixture
def project_storage_dir(project_row):
    """Name → the org-scoped directory the project's files actually live in."""
    def _dir(name: str, org_id=None) -> pathlib.Path:
        row = project_row(name, org_id)
        assert row is not None, f"no project row named {name!r}"
        # Through the resolver, not the raw column. 19 tests use this fixture,
        # and once `storage_path` is relative the raw value resolves against
        # pytest's CWD — `pypsa-gui/backend`, inside the checkout.
        return project_registry.project_dir(row)
    return _dir


@pytest.fixture
def tmp_projects_dir(tmp_path, monkeypatch):
    """
    Redirect routers.projects.PROJECTS_DIR to a temp dir so save/load tests
    never read or write the user's real projects. The functions read the module
    global at call time, so monkeypatching the attribute is sufficient.

    NOTE: intentionally NOT autouse. Making it autouse interacts badly with the
    module-local autouse fixtures in test_chat_sse.py (their setup resolves
    PROJECTS_DIR at a different point in fixture ordering, which only diverges
    during full-suite collection) and introduces fresh cross-contamination.
    The suite is deterministically green run cleanly; the flaky chat-tools
    failures originate from a CONCURRENT process touching backend/tests during a
    run (see the QA-agent / no-scratch-tests memory), not an in-process leak.
    """
    d = tmp_path / "projects"
    d.mkdir()
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", d)
    return d


@pytest.fixture
def db_engine():
    """
    Disposable SQLAlchemy engine for model tests.

    Defaults to in-memory SQLite because cloud agents do not have a live
    PostgreSQL service; callers may override with PYPSA_GUI_TEST_DATABASE_URL.
    """
    url = os.environ.get("PYPSA_GUI_TEST_DATABASE_URL", "sqlite+pysqlite:///:memory:")
    engine_kwargs = {}
    if url == "sqlite+pysqlite:///:memory:":
        engine_kwargs["connect_args"] = {"check_same_thread": False}
        engine_kwargs["poolclass"] = StaticPool

    engine = create_engine(url, **engine_kwargs)
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def db_session(db_engine):
    """Transaction-scoped SQLAlchemy session that rolls back after each test."""
    connection = db_engine.connect()
    transaction = connection.begin()
    testing_session_local = sessionmaker(autocommit=False, autoflush=False, bind=connection)
    session = testing_session_local()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def build_network(*, solve: bool = False, gens_weight=None, obj_weight=None) -> pypsa.Network:
    """
    Tiny single-bus network. `solve=True` runs HiGHS so `_dispatch_ready` is
    True and the /results endpoints return data. `gens_weight` / `obj_weight`
    set DIFFERENT snapshot_weightings columns so the energy(generators)-vs-
    cost(objective) basis is observable; the per-snapshot dispatch is identical
    regardless (no global constraints / storage), so the weighting only changes
    the post-hoc weighted sums.
    """
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=4, freq="h"))
    n.add("Bus", "B1")
    n.add("Load", "L1", bus="B1", p_set=100.0)
    n.add("Generator", "gas", bus="B1", carrier="gas",
          p_nom=200.0, marginal_cost=50.0, capital_cost=100_000.0)
    n.add("Generator", "solar", bus="B1", carrier="solar",
          p_nom=50.0, marginal_cost=0.0, capital_cost=60_000.0, p_max_pu=0.6)
    if gens_weight is not None:
        n.snapshot_weightings["generators"] = float(gens_weight)
    if obj_weight is not None:
        n.snapshot_weightings["objective"] = float(obj_weight)
    if solve:
        n.optimize(solver_name="highs")
    return n


def install_network_into_backend(n: pypsa.Network, name: str | None = None) -> pypsa.Network:
    """
    Install a network as the live singleton, optionally binding it to a project
    name (mimics a load: sets n.name + _loaded_project). Without `name` the
    network is left UNBOUND (_loaded_project stays None) — the first-save case.

    A plain function so `tests/qa_support.py` — the standalone `qa_*.py`
    drivers — installs a network the same way the suite does. A bare
    `PyPSAService.set_network()` is NOT the same way, and the difference is
    silent: the process foreground it writes is adopted by a session exactly
    once, so a driver that calls it twice keeps saving the FIRST network while
    believing it swapped.
    """
    PyPSAService.set_network(n)
    sim_router._state["solver_config"] = SolverConfig()
    if name is not None:
        n.name = name
        PyPSAService.set_loaded_project(name)
    # Step 0b: drop every resident SCRATCH context so the next request
    # re-adopts what was just installed.
    #
    # `set_network` writes the PROCESS foreground, which a session adopts
    # exactly once (`adopt_process_foreground`). Without this, the second
    # `install_network` in a test would be invisible: the session already
    # holds a scratch context and would keep serving the FIRST network
    # while the test believed it had swapped it. Bound project contexts are
    # deliberately left alone — those mirror real on-disk projects.
    with PyPSAService._registry_lock:
        for key in [k for k in PyPSAService._contexts if k.startswith("scratch:")]:
            PyPSAService._contexts.pop(key, None)
    # …and un-bind every live session, so the next request resolves the
    # freshly-installed network instead of re-hydrating whatever project the
    # session was last pointed at. `install_network` means "this is what the
    # client is now looking at"; leaving the pointer set would silently
    # discard the install one request later.
    try:
        from sqlalchemy import update

        from db import session as _db
        from db.models import Session as _SessionRow

        with _db.SessionLocal() as db:
            db.execute(update(_SessionRow).values(active_project_id=None))
            db.commit()
    except Exception:  # noqa: BLE001 — no DB in pure-service tests
        pass
    return n


@pytest.fixture
def install_network():
    """The function above, as a fixture."""
    return install_network_into_backend


# ── Mid-run source-change watcher (Improvement #10) ─────────────────────────
#
# The suite's real flake is not parallel-worker contamination — pytest-xdist
# is not installed and there are no workers. It is a run racing an EDITOR:
# `inspect.getsource` (nine call sites across five files) reads from disk at
# the line numbers recorded at import, so a mid-run edit makes it return text
# that was never in the function and the assertion fails as though the code
# were wrong. In this repo the editor is a second agent session on the same
# worktree, which CLAUDE.md documents as normal.
#
# This cannot prevent that. It stops the failure from lying about its cause.
from tests import _source_watch as _sw  # noqa: E402

_SOURCE_SNAPSHOT: dict[str, float] = {}
_WATCH_ROOTS = [pathlib.Path(__file__).resolve().parent.parent / r
                for r in _sw.WATCHED_ROOTS]


def pytest_sessionstart(session):  # noqa: ARG001
    global _SOURCE_SNAPSHOT
    try:
        _SOURCE_SNAPSHOT = _sw.snapshot_mtimes(_WATCH_ROOTS)
    except Exception:  # noqa: BLE001 — never let the watcher break a run
        _SOURCE_SNAPSHOT = {}


def pytest_terminal_summary(terminalreporter, exitstatus, config):  # noqa: ARG001
    if not _SOURCE_SNAPSHOT:
        return
    try:
        msg = _sw.format_warning(_sw.changed_since(_SOURCE_SNAPSHOT, _WATCH_ROOTS))
    except Exception:  # noqa: BLE001
        return
    if msg:
        terminalreporter.write_sep("=", "source changed mid-run", yellow=True)
        terminalreporter.write_line(msg)
