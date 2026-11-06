"""Issue 09 close-out PR-A — resolving the reconcile list without replaying.

GK B (25) conditions A1-A7: a job is ``verified`` only by a queue read the
thread owner may make (no oracle between foreign, unknown and access-revoked
jobs); the owner resolves turns and tool calls, never jobs; a request applies
whole or not at all; generation, replica and lease refusals write nothing;
nothing on the route or the resume path dispatches a tool, enqueues a solve or
starts a turn.
"""

from __future__ import annotations

import contextlib
import json
import time
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from harness import eventlog
from harness import loop
from harness import runner
from services import chat_service
from services import durable_conversation as dc
from services import utterance_admission as ua


OWNER = "owner-aaa"
OTHER = "owner-bbb"
PROJECT = "11111111-1111-1111-1111-111111111111"

OWN_JOB = str(uuid.UUID(int=1))
FOREIGN_JOB = str(uuid.UUID(int=2))
UNKNOWN_JOB = str(uuid.UUID(int=3))
REVOKED_JOB = str(uuid.UUID(int=4))


@pytest.fixture
def queue(monkeypatch):
    """The solve queue as the thread owner sees it.

    ``visible`` is what the owner may read now (project ACL); ``queued_by``
    is who queued each job (resident or persisted).
    """
    import routers.solve_queue as queue_router
    from services import chat_tools, solve_job_store
    from services.solve_queue import solve_queue

    state = SimpleNamespace(
        visible={OWN_JOB: "running", FOREIGN_JOB: "finished"},
        queued_by={OWN_JOB: OWNER, FOREIGN_JOB: OTHER, REVOKED_JOB: OWNER},
        persisted=set(),
    )

    @contextlib.contextmanager
    def acting():
        yield None, SimpleNamespace(id=OWNER)

    def visible_job_or_404(db, user, job_id):
        if job_id not in state.visible:
            raise HTTPException(404, f"No solve job with id {job_id}.")
        return {"id": job_id, "status": state.visible[job_id]}

    def owners():
        return {k: v for k, v in state.queued_by.items() if k not in state.persisted}

    def load_job(parsed):
        key = str(parsed)
        if key in state.persisted:
            return {"id": parsed, "enqueued_by_user_id": state.queued_by[key]}
        return None

    monkeypatch.setattr(chat_tools, "_acting", acting)
    monkeypatch.setattr(queue_router, "_visible_job_or_404", visible_job_or_404)
    monkeypatch.setattr(solve_queue, "owners", owners)
    monkeypatch.setattr(solve_job_store, "load_job", load_job)
    return state


@pytest.fixture
def no_replay(monkeypatch):
    """A5: any tool dispatch, solve enqueue or turn start fails the test."""
    from services.solve_queue import SolveQueue

    calls: list[str] = []

    def spy(name):
        def record(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"{name} called during reconcile")

        return record

    monkeypatch.setattr(loop, "dispatch_tool_call", spy("dispatch_tool_call"))
    monkeypatch.setattr(loop, "run_turn", spy("run_turn"))
    monkeypatch.setattr(SolveQueue, "enqueue", spy("enqueue"))
    monkeypatch.setattr(SolveQueue, "enqueue_unique", spy("enqueue_unique"))
    return calls


@pytest.fixture(autouse=True)
def _reset(tmp_path, monkeypatch):
    import security

    dc._reset_for_tests()
    chat_service._reset_sessions_for_tests()
    ua._reset_for_tests()
    root = tmp_path / PROJECT
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(dc, "_authority", lambda: (root, PROJECT, OWNER))
    monkeypatch.setattr(eventlog, "_root", lambda: tmp_path / "chat-events")
    monkeypatch.setattr(security, "replica_id", lambda: "replica-alpha")
    yield
    dc._reset_for_tests()
    chat_service._reset_sessions_for_tests()
    ua._reset_for_tests()


def _thread_file(tid: str):
    root, _, _ = dc._authority()
    return root / "durable_conversations" / f"{tid}.json"


def _interrupted_turn(session):
    eventlog.turn_start(session, "Solve the base case")
    eventlog.message(
        session,
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_solve",
                    "name": "run_optimization",
                    "input": {"secret_like": "never copied"},
                },
            ],
        },
    )
    eventlog.flush(session)


def _restart(monkeypatch):
    import security

    chat_service._reset_sessions_for_tests()
    monkeypatch.setattr(security, "replica_id", lambda: "replica-beta")
    now = time.time() + runner.LEASE_SECONDS + 1
    monkeypatch.setattr(dc, "_wall_clock", lambda: now)


def _taken_over(monkeypatch, job_ids):
    """A thread whose worker died mid-turn, resumed on a new worker."""
    tid = dc.create_thread()["thread_id"]
    view, session = dc.resume_episode(tid, generation=1)
    _interrupted_turn(session)
    view = dc.checkpoint_thread(tid, generation=view["generation"], job_ids=job_ids)
    _restart(monkeypatch)
    resumed, _ = dc.resume_episode(tid, generation=view["generation"])
    return tid, resumed


def _entries(view, kind):
    return [e for e in view["reconcile"] if e["kind"] == kind]


# ── A1: the queue read verifies, without an oracle ──────────────────────────


def test_takeover_verifies_the_owners_visible_job(monkeypatch, queue, no_replay):
    tid, resumed = _taken_over(monkeypatch, [OWN_JOB])
    [job] = _entries(resumed, "job")
    assert job["status"] == "verified"
    assert job["observed_state"] == "running"
    assert job["key"] == f"job:{OWN_JOB}"
    assert no_replay == []


def test_foreign_unknown_and_revoked_jobs_are_indistinguishable(monkeypatch, queue):
    tid, resumed = _taken_over(monkeypatch, [FOREIGN_JOB, UNKNOWN_JOB, REVOKED_JOB])
    stored = json.loads(_thread_file(tid).read_text())

    for view in (resumed, stored):
        jobs = _entries(view, "job")
        assert [j["job_id"] for j in jobs] == [FOREIGN_JOB, UNKNOWN_JOB, REVOKED_JOB]
        shapes = [
            json.dumps(
                {k: v for k, v in j.items() if k not in ("job_id", "key")},
                sort_keys=True,
            )
            for j in jobs
        ]
        assert shapes[0] == shapes[1] == shapes[2]
        assert all(j["status"] == "unverified" for j in jobs)
        assert all("observed_state" not in j for j in jobs)


def test_a_persisted_job_is_verified_from_its_row(monkeypatch, queue):
    queue.persisted.add(OWN_JOB)
    _, resumed = _taken_over(monkeypatch, [OWN_JOB])
    assert _entries(resumed, "job")[0]["status"] == "verified"


def test_reconcile_rereads_jobs_once_access_returns(monkeypatch, queue, no_replay):
    tid, resumed = _taken_over(monkeypatch, [REVOKED_JOB])
    assert _entries(resumed, "job")[0]["status"] == "unverified"
    queue.visible[REVOKED_JOB] = "failed"
    after = dc.reconcile_effects(tid, generation=resumed["generation"], resolutions=[])
    [job] = _entries(after, "job")
    assert (job["status"], job["observed_state"]) == ("verified", "failed")
    assert no_replay == []


# ── owner resolutions for turns and tool calls ──────────────────────────────


def test_owner_resolves_turn_and_tool_call_without_replay(
    monkeypatch, queue, no_replay
):
    tid, resumed = _taken_over(monkeypatch, [])
    keys = {e["kind"]: e["key"] for e in resumed["reconcile"]}
    assert keys["tool_call"].endswith(":toolu_solve")

    after = dc.reconcile_effects(
        tid,
        generation=resumed["generation"],
        resolutions=[
            {"key": keys["turn"], "resolution": "dismissed"},
            {"key": keys["tool_call"], "resolution": "not_done"},
        ],
    )

    by_kind = {e["kind"]: e for e in after["reconcile"]}
    assert by_kind["turn"]["status"] == "resolved"
    assert by_kind["turn"]["resolution"] == "dismissed"
    assert by_kind["tool_call"]["resolution"] == "not_done"
    assert "never copied" not in json.dumps(after)
    assert after["generation"] == resumed["generation"]
    assert after["checkpoint_rev"] == resumed["checkpoint_rev"] + 1
    assert no_replay == []


# ── A2, A3, A7: whole-or-nothing typed refusals ─────────────────────────────


def _refusal(tid, generation, resolutions):
    before = _thread_file(tid).read_bytes()
    with pytest.raises(HTTPException) as failure:
        dc.reconcile_effects(tid, generation=generation, resolutions=resolutions)
    assert _thread_file(tid).read_bytes() == before
    return failure.value


def test_a_job_entry_cannot_be_resolved_by_the_client(monkeypatch, queue):
    tid, resumed = _taken_over(monkeypatch, [FOREIGN_JOB])
    refusal = _refusal(
        tid,
        resumed["generation"],
        [
            {"key": f"job:{FOREIGN_JOB}", "resolution": "done"},
        ],
    )
    assert refusal.status_code == 422
    assert refusal.detail["error_kind"] == "reconcile_job_not_resolvable"


@pytest.mark.parametrize(
    "bad",
    [
        {"key": "tool_call:nope:toolu_x", "resolution": "done"},
        {"key": "job:" + UNKNOWN_JOB, "resolution": "done"},
    ],
)
def test_an_unknown_key_is_refused_and_nothing_applies(monkeypatch, queue, bad):
    tid, resumed = _taken_over(monkeypatch, [])
    turn_key = _entries(resumed, "turn")[0]["key"]
    refusal = _refusal(
        tid,
        resumed["generation"],
        [
            {"key": turn_key, "resolution": "dismissed"},
            bad,
        ],
    )
    assert refusal.status_code == 422
    # A7: the exact kind, not a generic refusal.
    assert refusal.detail["error_kind"] == "reconcile_entry_unknown"


def test_a_resolved_entry_is_no_longer_open(monkeypatch, queue):
    tid, resumed = _taken_over(monkeypatch, [])
    turn_key = _entries(resumed, "turn")[0]["key"]
    after = dc.reconcile_effects(
        tid,
        generation=resumed["generation"],
        resolutions=[
            {"key": turn_key, "resolution": "done"},
        ],
    )
    refusal = _refusal(
        tid, after["generation"], [{"key": turn_key, "resolution": "not_done"}]
    )
    assert refusal.detail["error_kind"] == "reconcile_entry_unknown"


@pytest.mark.parametrize(
    "case", ["duplicate", "invalid_resolution", "over_cap", "not_a_list"]
)
def test_malformed_requests_are_refused_whole(monkeypatch, queue, case):
    tid, resumed = _taken_over(monkeypatch, [])
    turn_key = _entries(resumed, "turn")[0]["key"]
    resolutions = {
        "duplicate": [{"key": turn_key, "resolution": "done"}] * 2,
        "invalid_resolution": [{"key": turn_key, "resolution": "replay"}],
        "over_cap": [
            {"key": f"turn:{i}", "resolution": "done"}
            for i in range(dc.RECONCILE_MAX + 1)
        ],
        "not_a_list": {"key": turn_key, "resolution": "done"},
    }[case]
    refusal = _refusal(tid, resumed["generation"], resolutions)
    assert refusal.status_code == 422
    assert refusal.detail["error_kind"] == "reconcile_request_invalid"


# ── A4: generation, replica and lease refusals write nothing ────────────────


def test_stale_generation_writes_nothing(monkeypatch, queue):
    tid, resumed = _taken_over(monkeypatch, [])
    turn_key = _entries(resumed, "turn")[0]["key"]
    refusal = _refusal(
        tid, resumed["generation"] - 1, [{"key": turn_key, "resolution": "done"}]
    )
    assert refusal.detail["error_kind"] == "stale_generation"


@pytest.mark.parametrize("lapsed", [False, True])
def test_another_replica_writes_nothing_live_or_lapsed(monkeypatch, queue, lapsed):
    import security

    tid = dc.create_thread()["thread_id"]
    view, session = dc.resume_episode(tid, generation=1)
    _interrupted_turn(session)
    view, _ = dc.resume_episode(tid, generation=view["generation"])
    turn_key = _entries(view, "turn")[0]["key"]
    monkeypatch.setattr(security, "replica_id", lambda: "replica-beta")
    if lapsed:
        now = time.time() + runner.LEASE_SECONDS + 1
        monkeypatch.setattr(dc, "_wall_clock", lambda: now)
    refusal = _refusal(
        tid, view["generation"], [{"key": turn_key, "resolution": "done"}]
    )
    assert refusal.detail["error_kind"] == "wrong_replica"
    assert refusal.detail["resume_required"] is lapsed


def test_an_in_flight_turn_refuses_and_writes_nothing(monkeypatch, queue):
    tid, resumed = _taken_over(monkeypatch, [])
    chat_service.get_session(resumed["episode_id"])._turn_in_flight = True
    turn_key = _entries(resumed, "turn")[0]["key"]
    refusal = _refusal(
        tid, resumed["generation"], [{"key": turn_key, "resolution": "done"}]
    )
    assert refusal.detail["error_kind"] == "turn_already_in_flight"


# ── HTTP ────────────────────────────────────────────────────────────────────


def test_reconcile_route(client, monkeypatch, queue, no_replay):
    tid, resumed = _taken_over(monkeypatch, [OWN_JOB])
    turn_key = _entries(resumed, "turn")[0]["key"]
    url = f"/api/durable-conversations/{tid}/reconcile"

    bad = client.post(
        url,
        json={
            "generation": resumed["generation"],
            "resolutions": [{"key": turn_key, "resolution": "replay"}],
        },
    )
    assert bad.status_code == 422
    assert bad.json()["detail"]["error_kind"] == "reconcile_request_invalid"
    over_cap = client.post(
        url,
        json={
            "generation": resumed["generation"],
            "resolutions": [
                {"key": f"turn:{i}", "resolution": "done"}
                for i in range(dc.RECONCILE_MAX + 1)
            ],
        },
    )
    assert over_cap.status_code == 422
    assert over_cap.json()["detail"]["error_kind"] == "reconcile_request_invalid"

    ok = client.post(
        url,
        json={
            "generation": resumed["generation"],
            "resolutions": [{"key": turn_key, "resolution": "done"}],
        },
    )
    assert ok.status_code == 200, ok.text
    assert _entries(ok.json(), "turn")[0]["resolution"] == "done"
    assert _entries(ok.json(), "job")[0]["status"] == "verified"
    assert no_replay == []


# ── A1 against the real queue ACL (no fakes for the read path) ─────────────


def test_job_state_reads_the_real_queue_acl(
    api_project, project_row, registry_key_for, seeded_identity, second_identity
):
    """The real ``_acting`` / ``_visible_job_or_404`` / ``owners`` path."""
    from services.solve_queue import SolveJob, solve_queue

    name = api_project("lv09-reconcile")
    row = project_row(name)
    owner = str(seeded_identity["user_id"])
    parked: list[uuid.UUID] = []

    def park(project_id, project_key, queued_by):
        jid = uuid.uuid4()
        with solve_queue._lock:
            solve_queue._jobs[jid] = SolveJob(
                id=jid,
                project_id=project_id,
                project_key=project_key,
                enqueued_at=time.time(),
                enqueued_by_user_id=queued_by,
            )
            solve_queue._order.append(jid)
        parked.append(jid)
        return str(jid)

    try:
        own = park(str(row.id), registry_key_for(name), seeded_identity["user_id"])
        foreign = park(str(row.id), registry_key_for(name), second_identity["user_id"])
        other_org = park(
            str(uuid.uuid4()),
            f"{second_identity['org_id']}:{uuid.uuid4()}",
            seeded_identity["user_id"],
        )
        assert (
            dc._job_state(own, owner) == solve_queue.get_job(uuid.UUID(own))["status"]
        )
        assert dc._job_state(foreign, owner) is None
        assert dc._job_state(other_org, owner) is None
        assert dc._job_state(str(uuid.uuid4()), owner) is None
        assert dc._job_state("not-a-uuid", owner) is None
        assert dc._job_state(own, str(second_identity["user_id"])) is None
    finally:
        with solve_queue._lock:
            for jid in parked:
                solve_queue._jobs.pop(jid, None)
                if jid in solve_queue._order:
                    solve_queue._order.remove(jid)
