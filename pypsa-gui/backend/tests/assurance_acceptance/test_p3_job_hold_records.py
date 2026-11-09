"""Job holds across chats (assistant-assurance issue 16, slice S1).

GK B (35) plan GO with changes H1-H9 (issue 16, Comments): a hold on a saved
project is written as an `assistant_job_holds` row and its evidence is kept
in a process registry, so a new chat or another tab of this process is denied
the same operation until the evidence that clears the hold reads terminal.
The row goes exactly when the hold clears (H5); every read failure denies
(H6); only the user who made the call sees its ids (H7); a failed write
leaves the hold on its chat and says so (H4). Rows of another process are
slice S2 (test_p3_job_hold_leases.py).
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, UTC

import pytest

from harness import job_hold_store, job_holds
from harness.session import ChatSession
from services import chat_tools
from tests.assurance_acceptance.test_p3_job_holds import _call, _error

QUEUE = "solve_queue_enqueue"


class _NeverDone:
    def done(self):
        return False


class _Future:
    def __init__(self):
        self.finished = False

    def done(self):
        return self.finished

    def cancelled(self):
        return False

    def exception(self):
        return None

    def result(self):
        return {}


class _Thread:
    def __init__(self, alive=True):
        self.alive = alive

    def is_alive(self):
        return self.alive


def _make_project(seeded_identity, name="alpha"):
    from db.models import Project
    from db.session import SessionLocal

    now = datetime.now(tz=UTC)
    with SessionLocal() as db:
        project = Project(
            id=uuid.uuid4(),
            org_id=seeded_identity["org_id"],
            name=name,
            created_by=seeded_identity["user_id"],
            storage_path=name,
            created_at=now,
            updated_at=now,
        )
        db.add(project)
        db.commit()
        return str(project.id)


@pytest.fixture
def project(monkeypatch, seeded_identity):
    """A saved project the active context is bound to."""
    project_id = _make_project(seeded_identity)
    monkeypatch.setattr(job_holds, "_binding", lambda: (project_id, "alpha"))
    # S2: no renewer thread sweeps these tests' rows; its tests drive it.
    monkeypatch.setattr(job_holds, "ensure_renewer", lambda: None)
    yield project_id
    with job_holds._REGISTRY_LOCK:
        job_holds._REGISTRY.clear()


def _rows(project_id, tool=QUEUE):
    return job_hold_store.active(project_id, tool)


def _held_by_record(session, tool=QUEUE, fingerprint="F"):
    return job_holds.check(session, tool, {}, fingerprint)


# ── The record ───────────────────────────────────────────────────────────────


def test_a_hold_on_a_saved_project_writes_its_record(project, seeded_identity):
    import security

    hold = job_holds.open_on_timeout(ChatSession(), QUEUE, "F", "tu", _NeverDone())
    (row,) = _rows(project)
    assert (row.id, row.tool, row.fingerprint, row.opened) == (
        hold.id,
        QUEUE,
        "F",
        "tool_timeout",
    )
    assert row.opened_by == str(seeded_identity["user_id"])
    assert row.owner_replica_id == security.replica_id()
    assert hold.shared and job_holds._REGISTRY[hold.id] is hold
    assert job_holds.unshared_note(hold) is None


def test_a_hold_on_an_unsaved_project_stays_on_its_chat(monkeypatch):
    monkeypatch.setattr(job_holds, "_binding", lambda: (None, "scratch"))
    hold = job_holds.open_on_timeout(ChatSession(), QUEUE, "F", "tu", _NeverDone())
    assert not hold.shared and hold.share_error == "no_saved_project"
    assert job_holds.unshared_note(hold) == job_holds.UNSHARED_NOTE
    assert hold.id not in job_holds._REGISTRY
    assert job_holds.check(ChatSession(), QUEUE, {}, "F") is None


# ── A new chat of the same process (S1) ─────────────────────────────────────


def test_a_new_chat_is_denied_the_same_call_as_pending(project):
    job_holds.open_on_timeout(ChatSession(), QUEUE, "F", "tu", _NeverDone())
    kind, message = _held_by_record(ChatSession())
    assert kind == job_holds.PENDING
    assert "call it again with the same settings" in message


def test_a_new_chat_is_denied_other_args_as_conflict(project):
    job_holds.open_on_timeout(ChatSession(), QUEUE, "F", "tu", _NeverDone())
    kind, message = _held_by_record(ChatSession(), fingerprint="G")
    assert kind == job_holds.CONFLICT
    assert "F" not in message.split()  # H7: never the other call's fingerprint


def test_the_record_is_read_only_for_its_tool_and_project(
    project, seeded_identity, monkeypatch
):
    job_holds.open_on_timeout(ChatSession(), QUEUE, "F", "tu", _NeverDone())
    assert job_holds.check(ChatSession(), "generate_report", {}, "F") is None
    other = _make_project(seeded_identity, "beta")
    monkeypatch.setattr(job_holds, "_binding", lambda: (other, "beta"))
    assert _held_by_record(ChatSession()) is None


def test_the_new_chat_clears_the_hold_on_the_same_evidence(project):
    """N3: the worker future ending clears it, read from the other chat; the
    row and the registry entry go with it (H5)."""
    future = _Future()
    hold = job_holds.open_on_timeout(ChatSession(), QUEUE, "F", "tu", future)
    later = ChatSession()
    assert _held_by_record(later)[0] == job_holds.PENDING
    future.finished = True
    assert _held_by_record(later) is None
    assert _held_by_record(ChatSession()) is None
    assert _rows(project) == []
    assert hold.id not in job_holds._REGISTRY


def test_the_record_outlives_the_chat_that_opened_it(project):
    import gc

    session = ChatSession()
    job_holds.open_on_timeout(session, QUEUE, "F", "tu", _NeverDone())
    del session
    gc.collect()
    assert _held_by_record(ChatSession())[0] == job_holds.PENDING
    assert len(_rows(project)) == 1


def test_time_alone_never_clears_a_record(project, monkeypatch):
    job_holds.open_on_timeout(ChatSession(), QUEUE, "F", "tu", _NeverDone())
    real = job_holds.time.time
    monkeypatch.setattr(job_holds.time, "time", lambda: real() + 30 * 86400)
    assert _held_by_record(ChatSession())[0] == job_holds.PENDING


def test_the_clear_in_the_opening_chat_deletes_the_record(project):
    future = _Future()
    session = ChatSession()
    hold = job_holds.open_on_timeout(session, QUEUE, "F", "tu", future)
    future.finished = True
    assert job_holds.check(session, QUEUE, {}, "F") is None
    assert _rows(project) == [] and hold.id not in job_holds._REGISTRY


def test_a_failed_delete_keeps_the_entry_and_the_next_reader_deletes(
    project, monkeypatch
):
    future = _Future()
    hold = job_holds.open_on_timeout(ChatSession(), QUEUE, "F", "tu", future)
    future.finished = True
    real = job_hold_store.delete
    monkeypatch.setattr(
        job_hold_store, "delete", lambda _id: (_ for _ in ()).throw(OSError())
    )
    assert _held_by_record(ChatSession()) is None
    assert hold.id in job_holds._REGISTRY and len(_rows(project)) == 1
    monkeypatch.setattr(job_hold_store, "delete", real)
    assert _held_by_record(ChatSession()) is None
    assert _rows(project) == [] and hold.id not in job_holds._REGISTRY


def test_a_late_result_moves_the_record_onto_its_job(project, monkeypatch):
    future = _Future()
    future.result = lambda: {"id": "q-1", "status": "queued"}
    job_holds.open_on_timeout(ChatSession(), QUEUE, "F", "tu", future)
    future.finished = True
    monkeypatch.setattr(job_holds, "job_terminal", lambda job_id: False)
    later = ChatSession()
    kind, message = _held_by_record(later)
    assert kind == job_holds.PENDING and "job q-1" in message
    assert _rows(project)[0].job_id == "q-1"
    monkeypatch.setattr(job_holds, "job_terminal", lambda job_id: True)
    assert _held_by_record(later) is None and _rows(project) == []


def test_an_unreadable_job_status_keeps_the_record(project, monkeypatch):
    hold = job_holds.open_on_outcome(
        ChatSession(),
        "run_simulation",
        "F",
        "tu",
        {"outcome": "uncertain", "job_id": "fg:1"},
    )

    def broken(_job_id):
        raise OSError("status unreadable")

    monkeypatch.setattr(job_holds, "job_terminal", broken)
    assert (
        job_holds.check(ChatSession(), "run_simulation", {}, "F")[0]
        == job_holds.PENDING
    )
    assert _rows(project, "run_simulation")[0].id == hold.id


def test_an_owned_record_with_no_registry_entry_holds(project):
    """H9: a row this process owns without its evidence is never read as clear."""
    hold = job_holds.open_on_timeout(ChatSession(), QUEUE, "F", "tu", _Future())
    with job_holds._REGISTRY_LOCK:
        job_holds._REGISTRY.clear()
    hold.future.finished = True  # the evidence is gone with the entry
    later = ChatSession()
    assert _held_by_record(later)[0] == job_holds.PENDING
    assert _held_by_record(later, fingerprint="G")[0] == job_holds.CONFLICT
    assert len(_rows(project)) == 1


def test_a_record_of_another_process_holds_while_its_lease_runs(project, monkeypatch):
    """S2 (H3): another process's row, read under another replica id, holds
    until its owner's lease lapses (test_p3_job_hold_leases.py)."""
    import security

    job_holds.open_on_timeout(ChatSession(), QUEUE, "F", "tu", _NeverDone())
    monkeypatch.setattr(security, "replica_id", lambda: "another-replica")
    assert _held_by_record(ChatSession())[0] == job_holds.PENDING


# ── The repeat card across chats (N5/N6, H5) ────────────────────────────────


def test_a_repeat_card_in_the_new_chat_names_the_record_and_removes_it(project):
    hold = job_holds.open_on_timeout(ChatSession(), QUEUE, "F", "tu", _NeverDone())
    later = ChatSession()
    assert job_holds.repeat_card(later, QUEUE, {}, "F") is None
    assert _held_by_record(later)[0] == job_holds.PENDING
    card = job_holds.repeat_card(later, QUEUE, {}, "F")
    assert card["hold_id"] == hold.id and card["tool_use_id"] == "tu"
    # The opening chat's own told state is its own.
    assert job_holds.repeat_card(ChatSession(), QUEUE, {}, "F") is None
    assert job_holds.check(later, QUEUE, {}, "F", card=card) is None
    assert _rows(project) == [] and hold.id not in job_holds._REGISTRY


def test_a_repeat_card_bounds_a_future_that_never_ends(project):
    """H5: an entry whose future never ends stays until its card is approved;
    the approval removes the entry and the row."""
    session = ChatSession()
    hold = job_holds.open_on_timeout(session, QUEUE, "F", "tu", _NeverDone())
    assert job_holds.check(session, QUEUE, {}, "F")[0] == job_holds.PENDING
    card = job_holds.repeat_card(session, QUEUE, {}, "F")
    assert job_holds.check(session, QUEUE, {}, "F", card=card) is None
    assert _rows(project) == [] and hold.id not in job_holds._REGISTRY


def test_an_orphan_record_can_be_released_by_its_repeat_card(project):
    job_holds.open_on_timeout(ChatSession(), QUEUE, "F", "tu", _NeverDone())
    with job_holds._REGISTRY_LOCK:
        job_holds._REGISTRY.clear()
    later = ChatSession()
    assert _held_by_record(later)[0] == job_holds.PENDING
    card = job_holds.repeat_card(later, QUEUE, {}, "F")
    assert card is not None and card["tool_use_id"] == ""
    assert job_holds.check(later, QUEUE, {}, "F", card=card) is None
    assert _rows(project) == []


# ── Read failures deny (N4, H6) ─────────────────────────────────────────────


def _assert_unverified(result):
    kind, message = result
    assert kind == job_holds.PENDING
    assert "could not be checked" in message


@pytest.mark.parametrize(
    "error", [OSError("disk"), RuntimeError("driver"), Exception("base class")]
)
def test_a_record_read_that_raises_denies(project, monkeypatch, error):
    """H6, at the catching site: any exception, the base class included."""

    def broken(*_a, **_k):
        raise error

    monkeypatch.setattr(job_hold_store, "active", broken)
    _assert_unverified(_held_by_record(ChatSession()))


def test_a_missing_table_denies(project, monkeypatch):
    from sqlalchemy.exc import OperationalError

    def missing(*_a, **_k):
        raise OperationalError(
            "SELECT", {}, Exception("no such table: assistant_job_holds")
        )

    monkeypatch.setattr(job_hold_store, "active", missing)
    _assert_unverified(_held_by_record(ChatSession()))


def test_a_missing_table_in_the_real_database_denies(project, _auth_db):
    from sqlalchemy import text

    engine, _ = _auth_db
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE assistant_job_holds RENAME TO ajh_moved"))
    try:
        _assert_unverified(_held_by_record(ChatSession()))
    finally:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE ajh_moved RENAME TO assistant_job_holds"))


def test_a_malformed_row_denies(project, _auth_db):
    from sqlalchemy import text

    job_holds.open_on_timeout(ChatSession(), QUEUE, "F", "tu", _NeverDone())
    engine, _ = _auth_db
    with engine.begin() as conn:
        conn.execute(text("UPDATE assistant_job_holds SET fingerprint = ''"))
    with pytest.raises(job_hold_store.MalformedRecord):
        _rows(project)
    _assert_unverified(_held_by_record(ChatSession()))


def test_an_unreadable_binding_denies_a_chat_with_no_hold(monkeypatch):
    def broken():
        raise OSError("no context")

    monkeypatch.setattr(job_holds, "_binding", broken)
    _assert_unverified(_held_by_record(ChatSession()))


# ── A failed write (H4) ─────────────────────────────────────────────────────


def test_a_failed_write_holds_in_its_chat_says_so_and_is_not_shared(
    project, monkeypatch, caplog
):
    def broken(**_kw):
        raise OSError("database is locked")

    monkeypatch.setattr(job_hold_store, "write", broken)
    session = ChatSession()
    with caplog.at_level("WARNING", logger="harness.job_holds"):
        hold = job_holds.open_on_timeout(session, QUEUE, "F", "tu", _NeverDone())
    assert "OSError" in caplog.text
    assert not hold.shared and hold.share_error == "OSError"
    assert job_holds.unshared_note(hold) == job_holds.UNSHARED_NOTE
    assert job_holds.check(session, QUEUE, {}, "F")[0] == job_holds.PENDING
    # Pinned limit: another chat is not told it is held.
    assert _held_by_record(ChatSession()) is None
    assert hold.id not in job_holds._REGISTRY


def test_a_failed_write_reaches_the_model_through_the_loop(project, monkeypatch):
    from harness import budget as harness_budget

    release = threading.Event()
    monkeypatch.setitem(
        chat_tools.DISPATCHERS, QUEUE, lambda **kw: release.wait(10) or {}
    )
    monkeypatch.setattr(harness_budget, "PER_TOOL_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(
        job_hold_store, "write", lambda **kw: (_ for _ in ()).throw(OSError())
    )
    collector: list = []
    from harness.loop import dispatch_tool_call

    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_MODE", "disabled")
    session = ChatSession()
    try:
        frames = []
        for event, data in dispatch_tool_call(
            session,
            {"id": "tu1", "name": QUEUE, "input": {"project_id": "alpha"}},
            collector,
            guided=True,
        ):
            frames.append((event, data))
            if event == "tool_pending_confirmation":
                session.record_decision(data["confirmation_token"], "approve")
    finally:
        release.set()
    error = _error(frames)
    assert error["error_kind"] == "tool_timeout"
    assert error["hold_note"] == job_holds.UNSHARED_NOTE
    assert job_holds.UNSHARED_NOTE in collector[-1]["content"]


# ── Other users (H7) ────────────────────────────────────────────────────────


def test_only_the_user_who_made_the_call_sees_its_ids(
    project, second_identity, monkeypatch
):
    future = _Future()
    future.result = lambda: {"id": "q-7", "status": "queued"}
    job_holds.open_on_timeout(ChatSession(), QUEUE, "F", "tu-secret", future)
    future.finished = True
    monkeypatch.setattr(job_holds, "job_terminal", lambda job_id: False)

    chat_tools.set_acting_user(second_identity["user_id"])
    other = ChatSession()
    kind, message = _held_by_record(other)
    assert kind == job_holds.PENDING and "q-7" not in message
    card = job_holds.repeat_card(other, QUEUE, {}, "F")
    assert card["job_id"] is None and card["tool_use_id"] == ""
    kind, message = _held_by_record(ChatSession(), fingerprint="G")
    assert (
        kind == job_holds.CONFLICT
        and "q-7" not in message
        and "F" not in message.split()
    )


def test_the_user_who_made_the_call_sees_its_job(project, seeded_identity, monkeypatch):
    future = _Future()
    future.result = lambda: {"id": "q-7", "status": "queued"}
    job_holds.open_on_timeout(ChatSession(), QUEUE, "F", "tu", future)
    future.finished = True
    monkeypatch.setattr(job_holds, "job_terminal", lambda job_id: False)
    assert "job q-7" in _held_by_record(ChatSession())[1]


# ── The project going away (H8) ─────────────────────────────────────────────


def test_deleting_the_project_deletes_its_records(project):
    from db.models import Project
    from db.session import SessionLocal

    job_holds.open_on_timeout(ChatSession(), QUEUE, "F", "tu", _NeverDone())
    assert len(_rows(project)) == 1
    with SessionLocal() as db:
        db.delete(db.get(Project, uuid.UUID(project)))
        db.commit()
    assert _rows(project) == []


# ── Each job-tool family, through the loop, from a new chat ─────────────────

POOL_FAMILIES = [
    # (tool, args): worker-pool tools whose call times out with the worker alive.
    (QUEUE, {"project_id": "alpha"}),  # solve queue
    ("run_mc_study", {}),  # adequacy studies (ad:)
    ("run_fmea_sweep", {}),  # sweeps
    ("generate_report", {}),  # reports (rpt:)
    ("generate_renewable_profile", {}),  # profile generation
    ("run_investment_case", {}),  # investment (execution_long_running)
    ("campus_run_study", {}),  # campus (execution_long_running)
    ("gridspine_run_pipeline", {}),  # gridspine (queue-backed)
]


@pytest.mark.parametrize("tool,args", POOL_FAMILIES, ids=[t for t, _ in POOL_FAMILIES])
def test_a_timed_out_job_call_denies_a_new_chat(project, monkeypatch, tool, args):
    from harness import budget as harness_budget

    release = threading.Event()
    calls: list = []

    def handler(**kw):
        calls.append(kw)
        if len(calls) == 1:
            release.wait(10)
        return {}

    monkeypatch.setitem(chat_tools.DISPATCHERS, tool, handler)
    monkeypatch.setattr(harness_budget, "PER_TOOL_TIMEOUT_SECONDS", 0.2)
    try:
        first = _error(_call(monkeypatch, ChatSession(), tool, args))
        assert first and first["error_kind"] == "tool_timeout", first
        error = _error(_call(monkeypatch, ChatSession(), tool, args))
        assert error and error["error_kind"] == job_holds.PENDING, error
        assert len(calls) == 1
    finally:
        release.set()


def test_a_solve_raise_that_left_a_thread_alive_denies_a_new_chat(project, monkeypatch):
    from services.pypsa_service import PyPSAService

    ctx = PyPSAService.get_active_context()
    started = _Thread()

    def raising(**_kw):
        with ctx.solver_state_lock:
            ctx.solver_state["thread"] = started
        raise RuntimeError("after the thread started")

    with ctx.solver_state_lock:
        prior = ctx.solver_state.get("thread")
    monkeypatch.setitem(chat_tools.DISPATCHERS, "run_simulation", raising)
    try:
        assert (
            _error(_call(monkeypatch, ChatSession(), "run_simulation", {}))[
                "error_kind"
            ]
            == "tool_error"
        )
        error = _error(_call(monkeypatch, ChatSession(), "run_simulation", {}))
        assert error["error_kind"] == job_holds.PENDING
        started.alive = False
        assert job_holds.check(ChatSession(), "run_simulation", {}, "x") is None
        assert _rows(project, "run_simulation") == []
    finally:
        with ctx.solver_state_lock:
            ctx.solver_state["thread"] = prior


def test_a_returned_solve_with_an_unknown_outcome_denies_a_new_chat(
    project, monkeypatch
):
    hold = job_holds.open_on_outcome(
        ChatSession(),
        "run_ac_pf_stage",
        "F",
        "tu",
        {"outcome": "uncertain", "job_id": "fg:9"},
    )
    state = {"terminal": False}
    monkeypatch.setattr(job_holds, "job_terminal", lambda job_id: state["terminal"])
    later = ChatSession()
    assert job_holds.check(later, "run_ac_pf_stage", {}, "F")[0] == job_holds.PENDING
    state["terminal"] = True
    assert job_holds.check(later, "run_ac_pf_stage", {}, "F") is None
    assert hold.id not in job_holds._REGISTRY


def test_a_solve_queue_record_holds_before_the_store_is_read(project, monkeypatch):
    from services import solve_job_store

    def must_not_read(*_a, **_k):
        raise AssertionError("the record answered first")

    job_holds.open_on_timeout(ChatSession(), QUEUE, "F", "tu", _NeverDone())
    monkeypatch.setattr(solve_job_store, "load_by_status", must_not_read)
    assert job_holds.check(ChatSession(), QUEUE, {"project_id": "alpha"}, "F")[0] == (
        job_holds.PENDING
    )


# ── Mutant-killing edges ────────────────────────────────────────────────────


def test_an_orphan_solve_record_holds_with_no_live_solve_thread(project, monkeypatch):
    """An orphan is never cleared by the live solver record: that thread is
    not the evidence the record's call left."""
    job_holds.open_on_raise(
        ChatSession(), "run_simulation", "F", "tu", _BrokenCtx(), None
    )
    with job_holds._REGISTRY_LOCK:
        job_holds._REGISTRY.clear()
    monkeypatch.setattr(job_holds, "_live_solve_thread", lambda: None)
    later = ChatSession()
    for _ in range(2):  # the second call reads the adopted hold's evidence
        assert job_holds.check(later, "run_simulation", {}, "F")[0] == job_holds.PENDING


class _BrokenCtx:
    solver_state_lock = threading.Lock()

    @property
    def solver_state(self):
        raise OSError("unreadable")


def test_a_chats_own_unshared_hold_names_its_job(monkeypatch):
    monkeypatch.setattr(job_holds, "_binding", lambda: (None, "scratch"))
    session = ChatSession()
    job_holds.open_on_outcome(
        session,
        "run_simulation",
        "F",
        "tu",
        {"outcome": "uncertain", "job_id": "fg:3"},
    )
    monkeypatch.setattr(job_holds, "job_terminal", lambda job_id: False)
    assert "job fg:3" in job_holds.check(session, "run_simulation", {}, "F")[1]


class _Row:
    def __init__(self, **over):
        values = dict(
            id="r",
            tool=QUEUE,
            fingerprint="F",
            opened="tool_timeout",
            owner_replica_id="me",
            job_id=None,
            opened_at=1.0,
            opened_by_user_id=None,
            project_id="p",
            lease_until=None,
        )
        values.update(over)
        self.__dict__.update(values)


@pytest.mark.parametrize(
    "over",
    [{"job_id": ""}, {"job_id": 7}, {"opened_at": float("nan")}, {"opened_at": "1"}],
)
def test_a_row_with_a_bad_job_id_or_time_is_malformed(over):
    assert job_hold_store._record(_Row()).id == "r"
    with pytest.raises(job_hold_store.MalformedRecord):
        job_hold_store._record(_Row(**over))


def test_a_hold_opened_on_an_unreadable_binding_stays_on_its_chat(monkeypatch):
    def broken():
        raise OSError("no context")

    monkeypatch.setattr(job_holds, "_binding", broken)
    session = ChatSession()
    hold = job_holds.open_on_timeout(session, QUEUE, "F", "tu", _NeverDone())
    assert not hold.shared and hold.share_error == "no_saved_project"
    assert job_holds.check(session, QUEUE, {}, "F")[0] == job_holds.PENDING


def test_a_shared_hold_whose_maker_is_unknown_shows_its_ids_to_no_one(
    project, seeded_identity, monkeypatch
):
    """H7, fail closed: a record written with no acting user names no job."""
    future = _Future()
    future.result = lambda: {"id": "q-8", "status": "queued"}
    chat_tools.set_acting_user(None)
    session = ChatSession()
    job_holds.open_on_timeout(session, QUEUE, "F", "tu", future)
    chat_tools.set_acting_user(seeded_identity["user_id"])
    future.finished = True
    monkeypatch.setattr(job_holds, "job_terminal", lambda job_id: False)
    assert _rows(project)[0].opened_by is None
    for chat in (session, ChatSession()):
        kind, message = job_holds.check(chat, QUEUE, {}, "F")
        assert kind == job_holds.PENDING and "q-8" not in message


def test_an_adopted_orphan_clears_once_another_chats_card_deleted_its_record(project):
    job_holds.open_on_timeout(ChatSession(), QUEUE, "F", "tu", _NeverDone())
    with job_holds._REGISTRY_LOCK:
        job_holds._REGISTRY.clear()
    first, second = ChatSession(), ChatSession()
    assert _held_by_record(first)[0] == job_holds.PENDING
    assert _held_by_record(second)[0] == job_holds.PENDING
    card = job_holds.repeat_card(second, QUEUE, {}, "F")
    assert job_holds.check(second, QUEUE, {}, "F", card=card) is None
    assert _rows(project) == []
    assert _held_by_record(first) is None


def test_an_orphan_whose_record_cannot_be_read_holds(project, monkeypatch):
    job_holds.open_on_timeout(ChatSession(), QUEUE, "F", "tu", _NeverDone())
    with job_holds._REGISTRY_LOCK:
        job_holds._REGISTRY.clear()
    later = ChatSession()
    assert _held_by_record(later)[0] == job_holds.PENDING

    def broken(_id):
        raise OSError("unreadable")

    monkeypatch.setattr(job_hold_store, "exists", broken)
    assert _held_by_record(later)[0] == job_holds.PENDING
