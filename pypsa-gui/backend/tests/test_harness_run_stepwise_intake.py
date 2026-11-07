# ruff: noqa: F811  (pytest fixtures imported from sibling test modules)
"""Issue 07 B3b, GK B (33) A with S1-S6: a step-by-step verdict on an intake
run creates the project through the same hand-over as the envelope path, on
the card's digest, and grants nothing; every later write and run keeps its
own card (COP-1). A plain run's step-by-step verdict is unchanged (S4)."""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi import HTTPException

from harness import approvals, confirm
from harness import session as harness_session
from services import run_envelope, run_handover
from tests.test_assistant_tasks_study_brief import (  # noqa: F401
    baseline,
    helpers_state,
    session,
)
from tests.test_harness_envelope import on_run, profile  # noqa: F401
from tests.test_harness_run_eh_site_v2 import (  # noqa: F401
    CAPTURE_FIELDS,
    PILOT_CAPTURE_FILES,
    _start_v2,
    _tick_to,
    capture_fields,
    goal_host,
    spy_capture,
)
from tests.test_harness_run_report_handoff import fake_report  # noqa: F401
from tests.test_harness_run_skeleton import RUN, site, start  # noqa: F401
from tests.test_harness_runner import task_path
from tests.test_run_intake_grant import _count, _new_task


def _stepwise(session, view, digest=None):
    return run_envelope.decide(
        session,
        view["task_id"],
        "stepwise",
        intake_digest=view["run"]["intake"]["digest"] if digest is None else digest,
    )


def test_a_stepwise_intake_verdict_names_the_project_and_digest_and_creates_it(
    goal_host, session, site
):
    name = f"Step {uuid.uuid4().hex[:6]}"
    view = _start_v2([*goal_host["citations"], goal_host["goal"]], name)
    digest = view["run"]["intake"]["digest"]
    message = _stepwise(session, view)
    assert f"creates project `{name}` from this intake (digest {digest[:8]})" in message
    assert "every write and run keeps its own card" in message
    assert _count(name) == 1
    intent = run_handover.read_intent(task_path(site[1], view))
    assert intent["digest"] == digest and intent["decision_id"].startswith("stepwise-")


def test_a_stepwise_intake_verdict_on_a_drifted_digest_is_refused_and_creates_nothing(
    goal_host, session
):
    name = f"Step {uuid.uuid4().hex[:6]}"
    view = _start_v2([*goal_host["citations"], goal_host["goal"]], name)
    for digest in ("0" * 64, ""):
        with pytest.raises(HTTPException) as exc:
            _stepwise(session, view, digest)
        assert exc.value.detail["error_kind"] == "intake_stale"
    assert _count(name) == 0


def test_a_stepwise_intake_verdict_without_a_goal_is_refused_goal_missing(
    goal_host, session
):
    name = f"Step {uuid.uuid4().hex[:6]}"
    view = _start_v2(goal_host["citations"], name)
    with pytest.raises(HTTPException) as exc:
        _stepwise(session, view)
    assert exc.value.detail["error_kind"] == "goal_missing"
    assert _count(name) == 0


def test_a_stepwise_setup_grants_nothing(goal_host, session, site):
    view = _start_v2(
        [*goal_host["citations"], goal_host["goal"]], f"Step {uuid.uuid4().hex[:6]}"
    )
    _stepwise(session, view)
    path, task = _new_task(run_handover.read_intent(task_path(site[1], view)))
    envelope_file = run_envelope.path_for(path)
    if envelope_file.exists():
        assert json.loads(envelope_file.read_text())["state"] != "granted"
    with on_run(task, path) as run:
        records = approvals.read(run)
        assert not any(str(r.get("by", "")).startswith("envelope") for r in records)
        assert (
            run_envelope.admit(
                run,
                call_id="call-solve",
                tool="solve_queue_enqueue",
                args={},
                tier=confirm._safety_tier_for("solve_queue_enqueue"),
                external=None,
                edit_card=None,
            )
            is None
        )
    # The host's decision is the user's card decision, never an envelope grant.
    decided = [r for r in approvals.read(session) if r.get("tool") == "grant_envelope"]
    assert decided == []
    assert any(
        r.get("tool") == "create_from_intake" and r.get("by") == "user"
        for r in approvals.read(session)
    )


def test_a_stepwise_verdict_on_a_plain_run_runs_no_setup(site, session, monkeypatch):
    def setup(*a, **k):
        raise AssertionError("a plain run has no setup to run")

    monkeypatch.setattr(run_envelope, "_grant_setup", setup)
    view = start(request_id=f"plain-{uuid.uuid4().hex[:6]}", run=RUN)
    message = run_envelope.decide(session, view["task_id"], "stepwise")
    assert message.endswith("every write and run keeps its own card.")
    assert "creates project" not in message


# ── The pilot, step by step (issue 07 acceptance; COP-1) ─────────────────────


def test_pilot_stepwise_mode_reaches_the_hand_off_with_one_card_per_write_or_run(
    goal_host, session, site, fake_report, monkeypatch
):
    """From one intake, decided step by step, the run goes intake -> hand-off
    on the new eh_site project; each write or run step is the user's card
    exactly once, and nothing runs inside an envelope (GK B (33) S2)."""
    from tests.test_harness_run_report_handoff import runner as run_runner
    from tests.test_harness_runner import Clicks, mail

    mail(monkeypatch)
    captured = spy_capture(monkeypatch)
    view = _start_v2(
        [*goal_host["citations"], goal_host["goal"]], f"Pilot {uuid.uuid4().hex[:6]}"
    )
    _stepwise(session, view)
    path, task = _new_task(run_handover.read_intent(task_path(site[1], view)))
    clicks = Clicks()
    record = _tick_to(
        run_runner(clicks),
        path,
        task["task_id"],
        lambda r: r["run"]["status"] in ("completed", "needs_user", "failed"),
    )
    assert record["run"]["status"] == "completed", record["run"]
    stages = record["run"]["stages"]
    assert all(stages[k]["status"] == "passed" for k in stages), stages
    rules = stages["result_gate"]["gate"]["rules"]
    # C3: the card-approved solve captured its input, so F6 reads an exact identity.
    assert {r["rule"]: r["verdict"] for r in rules}["kpi.unserved_mwh"] == "pass"
    keys, files, exact = capture_fields(captured)  # C2: the envelope path's capture
    assert (keys, exact) == CAPTURE_FIELDS
    assert files == PILOT_CAPTURE_FILES.get("envelope", files)
    steps = {s["id"]: s for s in record["steps"]}
    harness_runner = run_runner(clicks)
    probe = harness_session.ChatSession(session_id="probe")
    carded = [  # the steps the runner's own card policy cards (Guided, COP-1)
        s["id"]
        for s in record["steps"]
        if s["status"] == "completed"
        and harness_runner._policy(probe, s["tool"]) != "none"
    ]
    assert carded  # the run wrote and ran
    asked = [ref for ref in clicks.used if ref in steps]
    assert sorted(asked) == sorted(carded), [  # equality: one card per write or run
        (i, steps[i]["tool"], steps[i].get("engine")) for i in carded
    ]
    assert len(asked) == len(set(asked))
    envelope_file = run_envelope.path_for(path)
    if envelope_file.exists():
        assert json.loads(envelope_file.read_text())["state"] != "granted"


# ── S1a: no project from an intake the user never saw ────────────────────────


@pytest.mark.parametrize("verdict", ["unattended", "stepwise"])
@pytest.mark.parametrize("digest", [None, "0" * 64])
def test_an_absent_or_drifted_digest_refuses_both_verdicts(
    goal_host, session, verdict, digest
):
    name = f"Step {uuid.uuid4().hex[:6]}"
    view = _start_v2([*goal_host["citations"], goal_host["goal"]], name)
    with pytest.raises(HTTPException) as exc:
        run_envelope.decide(session, view["task_id"], verdict, intake_digest=digest)
    assert exc.value.detail["error_kind"] == "intake_stale"
    assert _count(name) == 0


# ── C1-C3: a stepwise run's own solve step captures its input ────────────────


def test_only_the_runs_own_solve_step_call_is_captured():
    from harness import runner
    from services import solve_capture

    ctx, args = object(), {"label": "run"}
    guard = runner.step_capture("run-solve.1-ab", args, ctx)
    assert guard("run-solve.1-cd", "solve_queue_enqueue", args) is None  # a second call
    assert (
        guard("run-solve.1-ab", "solve_queue_enqueue", {"label": "x"}) is None
    )  # other args
    assert guard("run-solve.1-ab", "update_component", args) is None  # another tool
    assert solve_capture.request() is None
    with guard("run-solve.1-ab", "solve_queue_enqueue", dict(args)):
        assert solve_capture.request() == {"ctx": ctx}
    assert solve_capture.request() is None  # nothing stays on the session or thread


def _solve_1(record):
    return next((s for s in record["steps"] if s["id"] == "solve.1"), {})


def test_a_denied_solve_card_enqueues_and_captures_nothing(
    goal_host, session, site, monkeypatch
):
    from services import solve_capture
    from tests.test_harness_run_report_handoff import runner as run_runner

    captured = []
    real = solve_capture.capture
    monkeypatch.setattr(
        solve_capture, "capture", lambda *a, **k: captured.append(1) or real(*a, **k)
    )
    view = _start_v2(
        [*goal_host["citations"], goal_host["goal"]], f"Deny {uuid.uuid4().hex[:6]}"
    )
    _stepwise(session, view)
    path, task = _new_task(run_handover.read_intent(task_path(site[1], view)))

    denied = []

    def decisions(task_id, ref, args_hash):
        if ref == "solve.1":
            denied.append(ref)
            return "deny"
        return "approve"

    record = _tick_to(
        run_runner(decisions),
        path,
        task["task_id"],
        lambda r: len(denied) >= 2,  # denied, and asked again: it never ran
    )
    solve = _solve_1(record)
    assert solve and solve["status"] != "completed", solve
    assert not (solve.get("result") or {}).get("job_id")
    assert captured == []
