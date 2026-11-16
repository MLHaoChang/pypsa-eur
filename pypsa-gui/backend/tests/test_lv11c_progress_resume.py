"""live-voice 11c-1: versioned progress events, Last-Event-ID resume, job_snapshot.

GK B plan GO conditions C1, C2 (server half), E1–E4 and Q1
(`.scratch/live-voice/issues/11-nonblocking-monitoring.md`, ## Comments).
"""

from __future__ import annotations

import json
import uuid

import pytest

from harness import events, progress_ids
from harness import job_adapters as ja
from services import chat_service, chat_tools
from services.pypsa_service import PyPSAService
from tests.test_lv11a_job_contract import _start, _stop_all
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401


def _frames(body: str) -> list[tuple[str | None, str, dict]]:
    """(id, event, data) per SSE frame."""
    out = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        fid, event, data = None, "message", []
        for line in block.split("\n"):
            if line.startswith("id: "):
                fid = line[4:]
            elif line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data.append(line[6:])
        out.append((fid, event, json.loads("\n".join(data))))
    return out


@pytest.fixture
def owned(seeded_identity):
    chat_service._reset_sessions_for_tests()
    ja._reset_for_tests()
    session = chat_service.get_or_create_session(
        owner_user_id=str(seeded_identity["user_id"])
    )
    yield session
    chat_service._reset_sessions_for_tests()
    ja._reset_for_tests()


def _get(client, session, last_event_id=None, cursor=None):
    headers = {} if last_event_id is None else {"Last-Event-ID": last_event_id}
    url = f"/api/chat/{session.session_id}/progress"
    if cursor is not None:
        url += f"?cursor={cursor}"
    return client.get(url, headers=headers)


def _lines(session, *names):
    for name in names:
        session.append_detached_progress({"tool_use_id": "tu", "line": name})


# ── ids ──────────────────────────────────────────────────────────────────────


def test_every_progress_frame_carries_a_monotonic_id_in_one_epoch(client, owned):
    _lines(owned, "a", "b", "c")
    frames = _frames(_get(client, owned).text)
    ids = [progress_ids.parse_event_id(f[0]) for f in frames]
    assert [e for e, _ in ids] == [owned.progress_epoch()] * 3
    assert [s for _, s in ids] == [1, 2, 3]
    assert {f[1] for f in frames} <= events.PROGRESS_FRAMES


def test_an_epoch_verifies_only_for_its_own_session():
    epoch = progress_ids.mint_epoch("s1")
    assert progress_ids.epoch_belongs_to("s1", epoch)
    assert not progress_ids.epoch_belongs_to("s2", epoch)
    nonce, mac = epoch.split(".")
    flipped = mac[:-1] + ("0" if mac[-1] != "0" else "1")
    assert not progress_ids.epoch_belongs_to("s1", f"{nonce}.{flipped}")
    assert not progress_ids.epoch_belongs_to("s1", "not-an-epoch")


@pytest.mark.parametrize(
    "raw",
    ["", "7", "abc:1", "zzzzzzzzzzzz.0123456789abcdef:1", ":1", "{e}", "{e}:", "{e}:-1",
     "{e}:1.5", "{e}:١", "{e}:" + "9" * 19, "{e}:1:2"],
)
def test_a_malformed_id_is_refused_by_the_parser(raw):
    with pytest.raises(ValueError):
        progress_ids.parse_event_id(raw.format(e=progress_ids.mint_epoch("s")))


def test_the_parser_reads_back_what_was_formatted():
    epoch = progress_ids.mint_epoch("s")
    assert progress_ids.parse_event_id(progress_ids.format_event_id(epoch, 0)) == (epoch, 0)
    assert progress_ids.parse_event_id(f"{epoch}:" + "9" * 18) == (epoch, 10**18 - 1)


# ── resume ───────────────────────────────────────────────────────────────────


def test_last_event_id_replays_only_what_follows_it(client, owned):
    _lines(owned, "a", "b", "c")
    _get(client, owned)  # the first reader drained the ring
    epoch = owned.progress_epoch()
    frames = _frames(_get(client, owned, f"{epoch}:1").text)
    assert [(f[1], f[2]["line"]) for f in frames] == [
        ("tool_progress", "b"),
        ("tool_progress", "c"),
    ]


def test_a_resume_at_the_head_replays_nothing(client, owned):
    _lines(owned, "a")
    _get(client, owned)
    assert _frames(_get(client, owned, f"{owned.progress_epoch()}:1").text) == []


def test_a_gap_gives_a_snapshot_then_only_newer_frames(client, owned, monkeypatch):
    import collections

    monkeypatch.setattr(owned, "_progress_log", collections.deque(maxlen=3))
    _lines(owned, "a", "b", "c", "d", "e")  # the log keeps 3..5
    epoch = owned.progress_epoch()
    # seq 2 is the oldest id whose successor (3) the log still holds.
    replay = _frames(_get(client, owned, f"{epoch}:2").text)
    assert [f[1] for f in replay] == ["tool_progress"] * 3
    gap = _frames(_get(client, owned, f"{epoch}:1").text)
    assert [f[1] for f in gap] == ["job_snapshot"]
    assert gap[0][2]["seq"] == 5 and gap[0][0] == f"{epoch}:5"
    _lines(owned, "f")
    later = _frames(_get(client, owned, f"{epoch}:5").text)
    assert [f[2]["line"] for f in later] == ["f"]


def test_a_seq_beyond_the_head_gives_a_snapshot(client, owned):
    _lines(owned, "a")
    frames = _frames(_get(client, owned, f"{owned.progress_epoch()}:9").text)
    assert [f[1] for f in frames] == ["job_snapshot"]


def test_an_epoch_from_before_a_restart_gives_a_snapshot_not_a_replay_from_0(
    client, owned, seeded_identity
):
    """E1: the rehydrated session is a new object with a new epoch; the old
    epoch still verifies for its session id, so the client gets a snapshot."""
    _lines(owned, "a", "b")
    old = f"{owned.progress_epoch()}:2"
    chat_service._reset_sessions_for_tests()  # the process restarted
    again = chat_service.get_or_create_session(
        owned.session_id, owner_user_id=str(seeded_identity["user_id"])
    )
    assert again is not owned and again.progress_epoch() != owned.progress_epoch()
    _lines(again, "x", "y")
    frames = _frames(_get(client, again, old).text)
    assert [f[1] for f in frames] == ["job_snapshot"]
    assert frames[0][2]["epoch"] == again.progress_epoch()
    assert frames[0][2]["seq"] == 2


def test_another_sessions_id_is_409_and_streams_nothing(client, owned, seeded_identity):
    other = chat_service.get_or_create_session(
        owner_user_id=str(seeded_identity["user_id"])
    )
    _lines(owned, "a")
    response = _get(client, owned, f"{other.progress_epoch()}:0")
    assert response.status_code == 409
    assert response.json()["detail"]["error_kind"] == "progress_cursor_foreign"
    assert owned.drain_detached_progress() != []  # nothing was drained


@pytest.mark.parametrize("raw", ["garbage", "abc:1", "{e}:-1"])
def test_a_malformed_last_event_id_is_400_and_streams_nothing(client, owned, raw):
    _lines(owned, "a")
    response = _get(client, owned, raw.format(e=owned.progress_epoch()))
    assert response.status_code == 400
    assert response.json()["detail"]["error_kind"] == "invalid_progress_cursor"
    assert owned.drain_detached_progress() != []


def test_last_event_id_wins_over_the_cursor_query(client, owned):
    """E3: both present, the header decides."""
    _lines(owned, "a", "b", "c")
    _get(client, owned)
    frames = _frames(_get(client, owned, f"{owned.progress_epoch()}:2", cursor=0).text)
    assert [f[2]["line"] for f in frames] == ["c"]


def test_the_old_cursor_client_misses_nothing(client, owned):
    """Q1: no header, `?cursor=` as before; the payloads keep their keys."""
    _lines(owned, "a", "b")
    _get(client, owned)
    frames = _frames(_get(client, owned, cursor=0).text)
    assert [(f[1], f[2]["line"], f[2]["cursor"]) for f in frames] == [
        ("tool_progress", "a", 1),
        ("tool_progress", "b", 2),
    ]


def test_stop_monitoring_does_not_move_the_epoch(owned):
    """S3: the epoch is not 11b's monitor_generation."""
    epoch = owned.progress_epoch()
    owned.stop_monitoring()
    assert owned.progress_epoch() == epoch


# ── C1 / E2: what a snapshot and a terminal frame may show ───────────────────


def _project(ctx):
    return str(ctx.project_uuid)


def test_a_snapshot_shows_the_actors_jobs_in_this_project_only(baseline):
    from routers import chat as chat_router

    ctx, *_ = baseline
    me = chat_tools.acting_user_id()
    started = [_start(ctx, "ad"), _start(ctx, "rpt")]
    mine, report = started[0][0], started[1][0]
    other_project = ja.register_study_job(
        study_key="fmea_sweep",
        tool_name="run_fmea_sweep",
        tool_use_id="tu-x",
        owner_user_id=me,
        project_uuid=str(uuid.uuid4()),
    )
    try:
        snap = chat_router._progress_snapshot(
            chat_service.get_or_create_session(owner_user_id=me), me, "e", 0
        )
        ids = {j["id"] for j in snap["jobs"]}
        assert {mine, report} <= ids and other_project not in ids
        assert all(j["project_id"] == _project(ctx) for j in snap["jobs"])
        stranger = chat_router._progress_snapshot(
            chat_service.get_or_create_session(owner_user_id=me),
            str(uuid.uuid4()),
            "e",
            0,
        )
        assert stranger["jobs"] == []
    finally:
        _stop_all(started)


def test_a_job_view_is_refused_to_another_actor_and_another_project(
    baseline, monkeypatch
):
    from routers import chat as chat_router

    ctx, *_ = baseline
    me = chat_tools.acting_user_id()
    started = [_start(ctx, "ad")]
    job = started[0][0]
    try:
        assert chat_router._progress_job_view(job, me)["id"] == job
        assert chat_router._progress_job_view(job, str(uuid.uuid4())) is None
        monkeypatch.setattr(
            chat_router, "_progress_project_id", lambda: str(uuid.uuid4())
        )
        assert chat_router._progress_job_view(job, me) is None
    finally:
        _stop_all(started)


def test_a_replayed_terminal_frame_is_read_for_the_reader_at_replay_time(
    client, owned, baseline, seeded_identity, monkeypatch
):
    """E2: access revoked after the terminal item was logged; the resume
    serves the frame without the job's view."""
    from routers import chat as chat_router

    owner = str(seeded_identity["user_id"])
    ctx, *_ = baseline
    job = ja.register_study_job(
        study_key="fmea_sweep",
        tool_name="run_fmea_sweep",
        tool_use_id="tu-t",
        owner_user_id=owner,
        project_uuid=_project(ctx),
    )
    owned.append_detached_progress(
        {"frame": "job_terminal", "job_id": job, "tool_use_id": "tu-t"}
    )
    first = _frames(_get(client, owned).text)
    assert [f[1] for f in first] == ["job_terminal"]
    assert first[0][2]["job"]["id"] == job
    monkeypatch.setattr(chat_router, "_progress_project_id", lambda: str(uuid.uuid4()))
    again = _frames(_get(client, owned, f"{owned.progress_epoch()}:0").text)
    assert [f[1] for f in again] == ["job_terminal"]
    assert again[0][2]["job"] is None and again[0][2]["job_id"] == job


def test_the_detached_monitor_logs_one_terminal_item_when_its_solve_ends(baseline):
    import threading

    from harness.solver_bridge import start_detached_solver_monitor

    ctx, *_ = baseline
    session = chat_service.get_or_create_session(owner_user_id=chat_tools.acting_user_id())
    started = [_start(ctx, "fg")]
    job = started[0][0]
    settled = threading.Event()
    start_detached_solver_monitor(
        session,
        PyPSAService.get_active_context(),
        tool_use_id="tu-fg",
        tool_name="run_simulation",
        on_terminal=settled.set,
        job_id=job,
    )
    _stop_all(started)
    assert settled.wait(10)
    import time

    deadline = time.monotonic() + 5
    while session.detached_monitor_active() and time.monotonic() < deadline:
        time.sleep(0.02)
    items = [i for i in session.drain_detached_progress(after_cursor=0)
             if i.get("frame") == "job_terminal"]
    assert [(i["job_id"], i["tool_use_id"]) for i in items] == [(job, "tu-fg")]


def test_a_monitor_without_a_job_id_logs_no_terminal_item(baseline):
    import threading
    import time

    from harness.solver_bridge import start_detached_solver_monitor

    ctx, *_ = baseline
    session = chat_service.get_or_create_session(owner_user_id=chat_tools.acting_user_id())
    started = [_start(ctx, "fg")]
    settled = threading.Event()
    start_detached_solver_monitor(
        session,
        PyPSAService.get_active_context(),
        tool_use_id="tu-fg",
        tool_name="run_simulation",
        on_terminal=settled.set,
    )
    _stop_all(started)
    assert settled.wait(10)
    deadline = time.monotonic() + 5
    while session.detached_monitor_active() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not [i for i in session.drain_detached_progress(after_cursor=0)
                if i.get("frame") == "job_terminal"]


def test_no_actor_or_no_project_shows_no_job(baseline, monkeypatch):
    """Fail closed: an ownerless job is never matched by a missing actor, and
    a project-less job never by a missing project."""
    from routers import chat as chat_router

    ctx, *_ = baseline
    session = chat_service.get_or_create_session(owner_user_id=chat_tools.acting_user_id())
    ownerless = ja.register_study_job(
        study_key="fmea_sweep",
        tool_name="run_fmea_sweep",
        tool_use_id="tu-o",
        owner_user_id=None,
        project_uuid=_project(ctx),
    )
    assert chat_router._progress_snapshot(session, None, "e", 0)["jobs"] == []
    assert chat_router._progress_job_view(ownerless, None) is None
    me = chat_tools.acting_user_id()
    unbound = ja.register_study_job(
        study_key="fmea_sweep",
        tool_name="run_fmea_sweep",
        tool_use_id="tu-u",
        owner_user_id=me,
        project_uuid=None,
    )
    monkeypatch.setattr(chat_router, "_progress_project_id", lambda: None)
    assert chat_router._progress_snapshot(session, me, "e", 0)["jobs"] == []
    assert chat_router._progress_job_view(unbound, me) is None


def _plan_step_monitor():
    """The plan step's monitor: no on_terminal (the step settles elsewhere)."""
    from harness.solver_bridge import start_detached_solver_monitor
    from tests.test_lv11b_cancel_stop import _Ctx

    ctx = _Ctx()
    session = chat_service.get_or_create_session(owner_user_id="u1")
    start_detached_solver_monitor(
        session, ctx, tool_use_id="tu-fg", tool_name="run_simulation", job_id="fg:x"
    )
    return session, ctx


def _terminal_items(session):
    """Once the monitor thread has exited (not merely been marked inactive)."""
    import threading

    for thread in threading.enumerate():
        if thread.name == "detached-solver-tu-fg":
            thread.join(5)
            assert not thread.is_alive()
    return [i for i in session.drain_detached_progress(after_cursor=0)
            if i.get("frame") == "job_terminal"]


def test_a_plan_step_monitor_logs_the_end_of_its_solve():
    import time

    session, ctx = _plan_step_monitor()
    time.sleep(0.3)
    assert session.detached_monitor_active()
    ctx.solver_state["status"] = "completed"
    assert [i["job_id"] for i in _terminal_items(session)] == ["fg:x"]


def test_a_plan_step_monitor_stopped_mid_run_logs_no_end():
    import time

    session, ctx = _plan_step_monitor()
    time.sleep(0.3)
    assert session.detached_monitor_active()
    session.stop_monitoring()
    assert _terminal_items(session) == []  # the solve still runs
    ctx.solver_state["status"] = "completed"
    assert _terminal_items(session) == []  # and its end is not watched


def test_a_plan_step_monitor_logs_the_end_after_a_turn_abort():
    """Review F3: a turn abort ends the bridge, not the watch for the end."""
    import threading
    import time

    session, ctx = _plan_step_monitor()
    time.sleep(0.3)
    session.abort_event.set()
    time.sleep(0.3)
    assert not session.detached_monitor_active()
    time.sleep(0.7)  # past one wait poll: the running solve logs no end yet
    assert not [i for i in session._progress_log if i.get("frame") == "job_terminal"]
    ctx.solver_state["status"] = "completed"
    assert [i["job_id"] for i in _terminal_items(session)] == ["fg:x"]


def test_a_monitor_without_a_job_id_does_not_wait_out_a_turn_abort():
    import threading
    import time

    from harness.solver_bridge import start_detached_solver_monitor
    from tests.test_lv11b_cancel_stop import _Ctx

    ctx = _Ctx()
    session = chat_service.get_or_create_session(owner_user_id="u1")
    start_detached_solver_monitor(
        session, ctx, tool_use_id="tu-fg", tool_name="run_simulation"
    )
    time.sleep(0.3)
    session.abort_event.set()
    threads = [t for t in threading.enumerate() if t.name == "detached-solver-tu-fg"]
    for thread in threads:
        thread.join(2)
    assert threads and not any(t.is_alive() for t in threads)  # the solve still runs


def test_a_header_resume_follows_the_log_and_leaves_the_shared_ring(client, owned):
    """Review F1: a resumed stream never drains the unread ring, so a half-open
    older stream or a second tab cannot take its frames (nor it theirs)."""
    _lines(owned, "a", "b")
    _get(client, owned)  # the ring is drained
    _lines(owned, "c")
    frames = _frames(_get(client, owned, f"{owned.progress_epoch()}:2").text)
    assert [f[2]["line"] for f in frames] == ["c"]
    assert [i["line"] for i in owned.drain_detached_progress()] == ["c"]


def test_a_gap_while_following_gives_a_snapshot(client, owned, monkeypatch):
    """Review F2: the log outran the reader between two polls."""
    import collections

    monkeypatch.setattr(owned, "_progress_log", collections.deque(maxlen=3))
    monkeypatch.setattr("routers.chat.PROGRESS_POLL_SECONDS", 0.01, raising=False)
    calls = []

    def active(_tid=None):
        calls.append(1)
        if len(calls) == 1:
            _lines(owned, "a", "b", "c", "d", "e")  # the log keeps 3..5
            return True
        return False

    monkeypatch.setattr(owned, "detached_monitor_active", active)
    frames = _frames(_get(client, owned, f"{owned.progress_epoch()}:0").text)
    assert [(f[1], f[2].get("seq")) for f in frames] == [("job_snapshot", 5)]


def test_progress_since_decides_and_collects_in_one_call(owned):
    _lines(owned, "a", "b", "c")
    epoch = owned.progress_epoch()
    point, items, head = owned.progress_since(epoch, 1)
    assert (point, [i["line"] for i in items], head) == ("replay", ["b", "c"], 3)
    assert owned.progress_since(epoch, 3) == ("replay", [], 3)
    assert owned.progress_since(epoch, 4)[0] == "snapshot"
    other = progress_ids.mint_epoch(owned.session_id)
    assert owned.progress_since(other, 1) == ("snapshot", [], 3)
    assert owned.progress_since(progress_ids.mint_epoch("x"), 1)[0] == "foreign"


def test_an_adapter_fault_serves_no_view(baseline, monkeypatch):
    """Review F6: fail closed, and the stream goes on."""
    from routers import chat as chat_router

    def broken(job_id, *, actor_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(ja, "job_status", broken)
    assert chat_router._progress_job_view("ad:x", chat_tools.acting_user_id()) is None
