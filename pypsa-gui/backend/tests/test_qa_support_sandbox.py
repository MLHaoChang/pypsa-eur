"""
The sandbox `tests/qa_support.py` hands the standalone drivers must survive
being used from more than one thread.

Why this is not the same requirement the pytest suite has
---------------------------------------------------------
`make_auth_db()`'s default is one `:memory:` database behind a `StaticPool` —
i.e. ONE DBAPI connection shared by everything. That is deliberate and
load-bearing for the suite: `:memory:` gives each *connection* its own
database, so without the shared connection the seeded user is invisible to the
request that needs it. The suite drives the app through `TestClient`, one
request at a time, so nothing ever touches that connection concurrently.

`tests/qa_phase4_compare.py` broke that assumption. It boots a REAL uvicorn in
a daemon thread and then fires 20 concurrent requests at it, so its handlers
run on anyio worker threads while the driver's own thread queries too — several
threads on one `sqlite3.Connection`, with no serialisation above it.

That is not safe, and how unsafe depends on the interpreter. Measured on the
same SQLAlchemy (2.0.50), twelve threads doing mixed reads and writes through a
StaticPool `:memory:` engine:

    Python 3.11.15  ->  0 errors
    Python 3.12.3   ->  4 errors, `sqlite3.InterfaceError: bad parameter or
                        other API misuse`

which is exactly why the driver passed locally (a 3.11 venv) and failed in CI
(the pixi `test` environment is 3.12), there surfacing as

    ValueError: badly formed hexadecimal UUID string

raised inside `Uuid.result_processor` — a corrupted value reaching a UUID
column's result processor, the same corruption wearing a different hat.

So the fix is not to serialise the sandbox; it is to stop sharing one
connection. A FILE-backed SQLite database needs no StaticPool, because every
connection sees the same database — which is precisely how the product itself
runs on SQLite (`db/session.py::get_engine`, NullPool for a file URL).

These tests pin both halves: the suite's default is unchanged, and the drivers'
sandbox is file-backed.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile
import threading

from sqlalchemy import select
from sqlalchemy.pool import NullPool, StaticPool

from db import session as db_session_module
from db.models import Organization
from tests.conftest import make_auth_db

_BACKEND = pathlib.Path(__file__).resolve().parent.parent


def _restore(original):
    db_session_module.SessionLocal = original


def test_the_default_sandbox_is_still_the_shared_in_memory_database():
    """
    The suite's arrangement is unchanged by the drivers' needs. Miss this and
    ~2,600 tests lose the seeded user — which reads as an auth bug, not as a
    harness change.
    """
    engine, _session_local, original = make_auth_db()
    try:
        assert engine.url.database == ":memory:"
        assert isinstance(engine.pool, StaticPool)
    finally:
        _restore(original)
        engine.dispose()


def test_a_file_backed_sandbox_hands_each_thread_its_own_connection():
    """
    The property the self-hosting driver actually needs. `StaticPool` would
    return the SAME `sqlite3.Connection` object to all four threads here; a
    file-backed engine gives each its own, so no two threads can interleave
    calls on one connection's statement cache.
    """
    with tempfile.TemporaryDirectory() as tmp:
        url = f"sqlite+pysqlite:///{pathlib.Path(tmp) / 'qa.db'}"
        engine, session_local, original = make_auth_db(url)
        try:
            assert isinstance(engine.pool, NullPool)

            seen: list[int] = []
            lock = threading.Lock()

            def probe():
                with session_local() as db:
                    db.scalars(select(Organization)).all()
                    raw = db.connection().connection.dbapi_connection
                    with lock:
                        seen.append(id(raw))

            threads = [threading.Thread(target=probe) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert len(seen) == 4
            assert len(set(seen)) == 4, (
                "two threads were handed the same sqlite3 connection — that is "
                "the StaticPool arrangement again, and under Python 3.12 it "
                "corrupts results rather than raising cleanly"
            )
        finally:
            _restore(original)
            engine.dispose()


def test_the_driver_sandbox_is_file_backed():
    """
    End-to-end, and in a SUBPROCESS on purpose: importing `tests.qa_support`
    swaps `db.session.SessionLocal` for the whole process, so importing it into
    a pytest run would hand the rest of the suite the drivers' database.

    Asserted on the live engine rather than on the source text, because what
    matters is which database the drivers actually get.
    """
    probe = (
        "from tests import qa_support;"
        "e = qa_support._engine;"
        "print(type(e.pool).__name__, e.url.database)"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe], cwd=_BACKEND, capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"the probe failed:\n{proc.stdout}\n{proc.stderr[-2000:]}"
    pool, database = proc.stdout.strip().splitlines()[-1].split(None, 1)
    assert pool != "StaticPool", (
        "tests/qa_support.py is back on a single shared connection; "
        "qa_phase4_compare self-hosts a real server and will corrupt rows on 3.12"
    )
    assert database != ":memory:", "the drivers' sandbox is in-memory again"
