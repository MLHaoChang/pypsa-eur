"""Issue 09 close-out PR-B — evidence and run refs revalidated under the ACL.

GK B (25) conditions B1-B4: on resume, takeover, profile switch and rebind
each ``evidence_revisions`` entry and the current/discussed run ref is read
again through ``run_evidence.get_run_evidence`` as the acting user may read
it. The result is a separate ``revalidation`` stamp
(``current|changed|unanchored|unreadable``); the entries and refs are never rewritten.
An ``unreadable`` stamp carries no fingerprint, revision or reason, so a
missing run and a forbidden run leave byte-equal stamps. Reads are bounded,
run under ``_LOCK`` after the lease and generation checks, and a rebind
refreshes the episode's discussed refs only from its own stamp.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from harness import eventlog
from harness import runner
from services import chat_service
from services import durable_conversation as dc
from services import run_evidence as re_
from services import utterance_admission as ua

OWNER = "owner-aaa"
PROJECT = "11111111-1111-1111-1111-111111111111"
OTHER_PROJECT = "22222222-2222-2222-2222-222222222222"
FP = "fp-0123456789abcdef"


@pytest.fixture
def acl(tmp_path, monkeypatch):
    """``run_ok`` False: the run routes refuse the actor; the thread still reads."""
    import security

    state = SimpleNamespace(run_ok=True, root=tmp_path / PROJECT)
    state.root.mkdir(parents=True, exist_ok=True)

    def run_authority():
        if not state.run_ok:
            re_._refuse(
                "project_unavailable",
                "This project is not available to you: your access was removed.",
                404,
            )
        return state.root, PROJECT, OWNER

    monkeypatch.setattr(dc, "_authority", lambda: (state.root, PROJECT, OWNER))
    monkeypatch.setattr(re_, "_authority", run_authority)
    monkeypatch.setattr(eventlog, "_root", lambda: tmp_path / "chat-events")
    monkeypatch.setattr(security, "replica_id", lambda: "replica-alpha")
    dc._reset_for_tests()
    chat_service._reset_sessions_for_tests()
    ua._reset_for_tests()
    yield state
    dc._reset_for_tests()
    chat_service._reset_sessions_for_tests()
    ua._reset_for_tests()


@pytest.fixture
def reads(monkeypatch):
    """Every run read, with whether ``_LOCK`` was held at the time."""
    calls = []
    real = re_.get_run_evidence

    def spy(run_id, section=None):
        calls.append((run_id, dc._LOCK._is_owned()))
        return real(run_id, section)

    monkeypatch.setattr(re_, "get_run_evidence", spy)
    return calls


def _publish(root, run_id, *, fingerprint=FP, project=PROJECT):
    re_.publish_run(
        root,
        project,
        run_id=run_id,
        kind="simulation",
        status="completed",
        started_at="2026-10-01T10:00:00+00:00",
        finished_at="2026-10-01T10:05:00+00:00",
        mode="lopf",
        fingerprint=fingerprint,
    )


def _thread_file(acl, tid):
    return acl.root / "durable_conversations" / f"{tid}.json"


def _with_facts(evidence, refs):
    tid = dc.create_thread()["thread_id"]
    view = dc.checkpoint_thread(
        tid,
        generation=1,
        evidence_revisions=evidence,
        refs=refs,
    )
    return tid, view["generation"]


def _restart(monkeypatch):
    import security

    chat_service._reset_sessions_for_tests()
    monkeypatch.setattr(security, "replica_id", lambda: "replica-beta")
    now = time.time() + runner.LEASE_SECONDS + 1
    monkeypatch.setattr(dc, "_wall_clock", lambda: now)


# ── B1: unreadable says nothing about why ───────────────────────────────────


def _unreadable_outcome(acl, *, publish, run_ok):
    if publish:
        _publish(acl.root, "run-x")
    acl.run_ok = run_ok
    entry = {"run_id": "run-x", "revision": 3, "fingerprint": FP}
    tid, gen = _with_facts([entry], {"current": {}, "discussed": {"run_id": "run-x"}})
    view, session = dc.resume_episode(tid, generation=gen)
    stored = json.loads(_thread_file(acl, tid).read_text())
    stamps = [
        view["revalidation"]["evidence"],
        view["revalidation"]["refs"],
        stored["revalidation"]["evidence"],
        stored["revalidation"]["refs"],
    ]
    return json.dumps(stamps, sort_keys=True), json.dumps(
        session.discussed_refs, sort_keys=True
    )


def test_missing_and_forbidden_runs_leave_byte_equal_stamps(acl):
    missing = _unreadable_outcome(acl, publish=False, run_ok=True)
    dc._reset_for_tests()
    chat_service._reset_sessions_for_tests()
    forbidden = _unreadable_outcome(acl, publish=True, run_ok=False)
    assert missing == forbidden
    stamps = json.loads(missing[0])
    assert stamps[0] == [{"run_id": "run-x", "state": "unreadable"}]
    assert stamps[1] == {"discussed": {"run_id": "run-x", "state": "unreadable"}}


def test_a_ref_to_another_project_is_not_read(acl, reads):
    _publish(acl.root, "run-x")
    tid, gen = _with_facts(
        [{"run_id": "run-x", "project_id": OTHER_PROJECT}],
        {"current": {"run_id": "run-x", "project_id": OTHER_PROJECT}, "discussed": {}},
    )
    view, _ = dc.resume_episode(tid, generation=gen)
    assert view["revalidation"]["evidence"] == [
        {"run_id": "run-x", "state": "unreadable"}
    ]
    assert view["revalidation"]["refs"] == {
        "current": {"run_id": "run-x", "state": "unreadable"}
    }
    assert reads == []


# ── B2: changed compares in full; the refs are never rewritten ──────────────


def test_changed_compares_the_full_stored_fingerprint(acl):
    _publish(acl.root, "run-a")
    evidence = [
        {"run_id": "run-a", "fingerprint": FP},
        {"run_id": "run-a", "fingerprint": FP[:-1]},  # a prefix is not a match
        {"run_id": "run-a", "fingerprint": FP, "status": "running"},
    ]
    tid, gen = _with_facts(evidence, {"current": {}, "discussed": {}})
    view, _ = dc.resume_episode(tid, generation=gen)
    states = [s["state"] for s in view["revalidation"]["evidence"]]
    assert states == ["current", "changed", "changed"]
    observed = view["revalidation"]["evidence"][0]["observed"]
    assert observed["fingerprint"] == FP and observed["status"] == "completed"


def test_only_anchors_the_read_supplies_are_compared(acl):
    """N1/N2: nothing compared is ``unanchored``; a revision the read lacks is named."""
    _publish(acl.root, "run-a")
    evidence = [
        {"run_id": "run-a"},
        {"run_id": "run-a", "revision": 4},
        {"run_id": "run-a", "fingerprint": FP, "revision": 4},
        {"run_id": "run-a", "fingerprint": "fp-other", "revision": 4},
    ]
    tid, gen = _with_facts(evidence, {"current": {}, "discussed": {}})
    view, _ = dc.resume_episode(tid, generation=gen)
    stamps = view["revalidation"]["evidence"]
    assert [(s["state"], s["uncompared"]) for s in stamps] == [
        ("unanchored", []),
        ("unanchored", ["revision"]),
        ("current", ["revision"]),
        ("changed", ["revision"]),
    ]
    assert all(s["observed"]["fingerprint"] == FP for s in stamps)
    assert "revision" not in stamps[2]["observed"]


def test_entries_and_refs_are_byte_equal_after_many_cycles(acl, monkeypatch):
    _publish(acl.root, "run-a")
    evidence = [
        {"run_id": "run-a", "fingerprint": "fp-old", "revision": 4},
        {"run_id": "run-gone", "revision": 1},
    ]
    refs = {
        "current": {"project": "P1", "run_id": "run-a", "asset": "Battery-1"},
        "discussed": {
            "run_id": "run-gone",
            "project_id": PROJECT,
            "section": "summary",
        },
    }
    tid, gen = _with_facts(evidence, refs)
    original = json.loads(_thread_file(acl, tid).read_text())
    profile = SimpleNamespace(id="gpt", wire="openai", model="gpt-x")
    for cycle in range(6):
        if cycle % 3 == 0:
            view, _ = dc.resume_episode(tid, generation=gen)
        elif cycle % 3 == 1:
            view = dc.rebind_scope(tid, generation=gen)
        else:
            view, _ = dc.switch_profile(tid, generation=gen, profile=profile)
        gen = view["generation"]
    _restart(monkeypatch)
    view, _ = dc.resume_episode(tid, generation=gen)

    stored = json.loads(_thread_file(acl, tid).read_text())
    for snapshot in (view, stored):
        assert snapshot["evidence_revisions"] == original["evidence_revisions"]
        assert snapshot["refs"] == original["refs"]
        assert [s["state"] for s in snapshot["revalidation"]["evidence"]] == [
            "changed",
            "unreadable",
        ]
        assert snapshot["revalidation"]["trigger"] == "takeover"
        assert snapshot["revalidation"]["generation"] == snapshot["generation"]


# ── B3: bounded, under the lock, after the CAS ──────────────────────────────


def test_reads_are_capped_and_held_under_the_lock(acl, reads, monkeypatch):
    monkeypatch.setattr(dc, "REVALIDATE_MAX", 4)
    evidence = [{"run_id": f"run-{i}"} for i in range(7)]
    tid, gen = _with_facts(
        evidence, {"current": {"run_id": "run-c"}, "discussed": {"run_id": "run-d"}}
    )
    view, _ = dc.resume_episode(tid, generation=gen)
    assert len(view["revalidation"]["evidence"]) == 4
    assert view["revalidation"]["evidence_unchecked"] == 3
    assert sorted(r for r, _ in reads) == sorted(
        ["run-0", "run-1", "run-2", "run-3", "run-c", "run-d"],
    )
    assert all(held for _, held in reads)


def test_one_run_is_read_once_per_pass(acl, reads):
    _publish(acl.root, "run-a")
    tid, gen = _with_facts(
        [{"run_id": "run-a"}] * 5,
        {"current": {"run_id": "run-a"}, "discussed": {"run_id": "run-a"}},
    )
    dc.resume_episode(tid, generation=gen)
    assert [r for r, _ in reads] == ["run-a"]


@pytest.mark.parametrize("control", ["resume", "rebind"])
def test_stale_generation_reads_and_writes_nothing(acl, reads, control):
    tid, gen = _with_facts([{"run_id": "run-a"}], {"current": {}, "discussed": {}})
    before = _thread_file(acl, tid).read_bytes()
    with pytest.raises(HTTPException) as refused:
        if control == "resume":
            dc.resume_episode(tid, generation=gen - 1)
        else:
            dc.rebind_scope(tid, generation=gen - 1)
    assert refused.value.status_code == 409
    assert reads == []
    assert _thread_file(acl, tid).read_bytes() == before


def test_a_foreign_live_lease_reads_and_writes_nothing(acl, reads, monkeypatch):
    import security

    tid, gen = _with_facts([{"run_id": "run-a"}], {"current": {}, "discussed": {}})
    before = _thread_file(acl, tid).read_bytes()
    monkeypatch.setattr(security, "replica_id", lambda: "replica-beta")
    with pytest.raises(HTTPException) as refused:
        dc.rebind_scope(tid, generation=gen)
    assert refused.value.detail["error_kind"] == "wrong_replica"
    assert reads == []
    assert _thread_file(acl, tid).read_bytes() == before


@pytest.mark.parametrize("control", ["resume", "rebind"])
def test_an_evidence_outage_changes_nothing(acl, monkeypatch, control):
    from services import live_speech

    tid, gen = _with_facts([{"run_id": "run-a"}], {"current": {}, "discussed": {}})
    before = _thread_file(acl, tid).read_bytes()
    closed = []
    monkeypatch.setattr(live_speech, "close_in", lambda *a, **k: closed.append(a))

    def offline(run_id, section=None):
        raise RuntimeError("evidence store offline")

    monkeypatch.setattr(re_, "get_run_evidence", offline)
    with pytest.raises(RuntimeError, match="offline"):
        if control == "resume":
            dc.resume_episode(tid, generation=gen)
        else:
            dc.rebind_scope(tid, generation=gen)
    assert closed == []
    assert _thread_file(acl, tid).read_bytes() == before


# ── B4: rebind revalidates before the episode's refs change ─────────────────


def _discussed_episode(acl):
    _publish(acl.root, "run-a")
    tid = dc.create_thread()["thread_id"]
    view = dc.set_discussed_run(tid, generation=1, run_id="run-a", section="summary")
    view, session = dc.resume_episode(tid, generation=view["generation"])
    return tid, view, session


def test_resume_hydrates_live_fields_from_a_readable_stamp(acl):
    _, view, session = _discussed_episode(acl)
    assert view["revalidation"]["refs"]["discussed"]["state"] == "unanchored"
    refs = session.discussed_refs
    assert (refs["run_id"], refs["revalidation"], refs["fingerprint"]) == (
        "run-a",
        "unanchored",
        FP,
    )
    assert refs["status"] == "completed" and refs["section"] == "summary"


def test_rebind_after_access_is_lost_strips_the_episodes_live_fields(acl):
    tid, view, session = _discussed_episode(acl)
    with session._lock:
        session.discussed_refs = dict(
            session.discussed_refs,
            section_status="not_kept",
            section_reason="too_large",
        )
    acl.run_ok = False
    out = dc.rebind_scope(tid, generation=view["generation"])
    assert out["revalidation"]["refs"]["discussed"] == {
        "run_id": "run-a",
        "state": "unreadable",
    }
    assert out["revalidation"]["trigger"] == "rebind"
    refs = session.discussed_refs
    assert refs["run_id"] == "run-a" and refs["section"] == "summary"
    assert refs["revalidation"] == "unreadable"
    for live in (
        "status",
        "mode",
        "fingerprint",
        "evidence_available",
        "section_status",
        "section_reason",
    ):
        assert live not in refs

    acl.run_ok = True
    again = dc.rebind_scope(tid, generation=out["generation"])
    assert again["revalidation"]["refs"]["discussed"]["state"] == "unanchored"
    assert session.discussed_refs["fingerprint"] == FP


def test_rebind_leaves_an_episode_discussing_another_run_alone(acl):
    tid, view, session = _discussed_episode(acl)
    with session._lock:
        session.discussed_refs = {"run_id": "run-other", "project_id": PROJECT}
    acl.run_ok = False
    dc.rebind_scope(tid, generation=view["generation"])
    assert session.discussed_refs == {"run_id": "run-other", "project_id": PROJECT}


def test_hydration_honours_an_unreadable_stamp_without_reading(acl, reads):
    _, view, _ = _discussed_episode(acl)
    reads.clear()
    thread = dict(
        view,
        revalidation={
            "generation": view["generation"],
            "refs": {"discussed": {"run_id": "run-a", "state": "unreadable"}},
        },
    )
    target = SimpleNamespace(discussed_refs=None)
    dc._hydrate_discussed_refs(target, thread)
    assert target.discussed_refs == {
        "project_id": PROJECT,
        "run_id": "run-a",
        "section": "summary",
        "revalidation": "unreadable",
    }
    assert reads == []


def test_hydration_never_trusts_a_stamp_from_another_generation(acl):
    _, view, _ = _discussed_episode(acl)
    stale = dict(view["revalidation"], generation=view["generation"] - 1)
    target = SimpleNamespace(discussed_refs=None)
    dc._hydrate_discussed_refs(target, dict(view, revalidation=stale))
    assert target.discussed_refs["revalidation"] == "unreadable"
    assert "fingerprint" not in target.discussed_refs
