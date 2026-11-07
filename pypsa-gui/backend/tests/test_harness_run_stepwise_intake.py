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
    _start_v2,
    _tick_to,
    assert_one_exact_capture,
    goal_host,
    spy_capture,
)
from tests.test_harness_run_report_handoff import fake_report  # noqa: F401
from tests.test_harness_run_skeleton import RUN, site, start  # noqa: F401
from tests.test_harness_runner import task_path
from tests.test_run_intake_grant import _count, _new_task


def harness_session_probe_carded(step):
    """A completed step the runner's card policy would card (Guided, COP-1),
    the engine and native steps included."""
    from tests.test_harness_run_report_handoff import runner as run_runner

    probe = harness_session.ChatSession(session_id="probe")
    return step["status"] == "completed" and (
        step.get("engine")
        or step.get("native")
        or run_runner(None)._policy(probe, step["tool"]) != "none"
    )


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
    # P5a: an intake run's card shows the intake's goal, never the open project's.
    assert "goal" not in view["envelope"]
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
    from harness import run_report
    from tests.test_harness_run_report_handoff import runner as run_runner
    from tests.test_harness_runner import Clicks, mail, run_records

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
    assert_one_exact_capture(captured)  # C2: the same predicate as the envelope pilot
    # Acceptance 2, as the envelope pilot: the report's harness_run chapter and one completion notice.
    doc = run_report.saved(record, path.parent.parent)
    assert "harness_run" in {s.section_id for s in doc.sections}
    completed = [r for r in run_records(record) if r["native_status"] == "completed"]
    assert len(completed) == 1
    steps = {s["id"]: s for s in record["steps"]}
    # E4: every write or run is the user's card exactly once. The engine
    # step's card is the result gate's own (issue 07 B3a, `card_engine`).
    [engine] = [s for s in record["steps"] if s.get("engine")]
    cards = [steps.get(ref, {}).get("id") or ref for ref in clicks.used if ref in steps]
    cards += [engine["id"]] * clicks.used.count("result_gate")
    assert sorted(cards) == ["result_gate.1", "solve.1", "solve.2"], clicks.used
    assert engine["tool"] == "run_coupling_loop" and engine["card"]["restore"] == "base"
    launches = sorted(s["id"] for s in record["steps"] if s.get("native"))
    assert launches == ["handoff.1", "report.1"]  # the harness's own launches (P11)
    assert sorted(cards + launches) == sorted(
        s["id"] for s in record["steps"] if harness_session_probe_carded(s)
    )
    loop = engine["result"]["loop"]
    assert engine["reserved"]["solves"] == int(loop.get("solves_used") or 0) + int(
        loop.get("probe_solves") or 0
    ) + (1 if loop.get("base_restore_status") is not None else 0)
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


def _plain_goal_profile(monkeypatch):
    """The profile fixture's eh_site@1 with eh_site@2's goal param and LOLE
    rule: a plain run (no intake) whose profile needs the user's goal."""
    import copy

    from services import run_profiles

    base, v2 = run_profiles.PROFILES["eh_site@1"], run_profiles.EH_SITE_V2
    stages = {
        **base["stages"],
        "result_gate": copy.deepcopy(v2["stages"]["result_gate"]),
    }
    monkeypatch.setitem(
        run_profiles.PROFILES,
        "eh_site@1",
        {**base, "params": copy.deepcopy(v2["params"]), "stages": stages},
    )
    assert run_profiles.needs_goal(run_profiles.resolve("eh_site@1"))


def test_an_unattended_plain_grant_without_a_goal_is_refused_goal_missing(
    session, site, profile, monkeypatch
):
    """GK B (33) P5/P5a: a plain run (no intake) is judged against the user's
    own goal too; a code default never counts, the card says no goal is set,
    and nothing is granted."""
    from tests.test_harness_envelope import envelope_of

    _plain_goal_profile(monkeypatch)
    task = start()
    assert task["envelope"]["goal"] == {
        "value": None,
        "unit": "h/yr",
        "source": "default",
    }
    assert "No reliability goal is set" in task["envelope"]["detail"]
    with pytest.raises(HTTPException) as exc:
        run_envelope.decide(session, task["task_id"], "unattended")
    assert exc.value.detail["error_kind"] == "goal_missing"
    assert envelope_of(task_path(site[1], task))["state"] != "granted"


def test_a_code_default_with_a_value_still_refuses_goal_missing(
    session, site, profile, monkeypatch
):
    """P5a: a default never counts, even one that carries a value."""
    from services import reliability_goal

    _plain_goal_profile(monkeypatch)
    monkeypatch.setattr(
        reliability_goal,
        "active_goal",
        lambda _state: {
            "target_lole_h": {"value": 1.0, "source": reliability_goal.DEFAULT}
        },
    )
    task = start()
    assert task["envelope"]["goal"]["value"] is None
    with pytest.raises(HTTPException) as exc:
        run_envelope.decide(session, task["task_id"], "unattended")
    assert exc.value.detail["error_kind"] == "goal_missing"


def test_a_template_goal_is_on_the_card_with_its_source_and_frozen_at_the_grant(
    session, site, profile, monkeypatch
):
    """P5a: a goal from template data counts only as the card shows it, with
    its value, unit and source; the grant keeps what the card showed."""
    from services import flow_service
    from tests.test_harness_envelope import envelope_of

    _plain_goal_profile(monkeypatch)
    template = site[1] / flow_service.TEMPLATE_FILE
    template.write_text(
        json.dumps(
            {
                **json.loads(template.read_text()),
                "pack_overrides": {"target_lole_h": 2.0},
            }
        )
    )
    task = start()
    shown = {"value": 2.0, "unit": "h/yr", "source": "template"}
    assert task["envelope"]["goal"] == shown
    assert "2 h/yr (from the eh_site template)" in task["envelope"]["detail"]
    message = run_envelope.decide(session, task["task_id"], "unattended")
    assert "2 h/yr (from the eh_site template)" in message
    granted = envelope_of(task_path(site[1], task))
    assert granted["state"] == "granted" and granted["goal"] == shown
    # Only a proposed card reads the live goal: a granted one shows what was frozen, or none.
    path = task_path(site[1], task)
    run_envelope.path_for(path).write_text(
        json.dumps({k: v for k, v in granted.items() if k != "goal"})
    )
    assert "goal" not in run_envelope.read_for_card(path, task)


def test_a_stored_goal_is_on_the_card_with_its_source(
    session, site, profile, monkeypatch
):
    import dataclasses

    from services.pypsa_service import PyPSAService

    _plain_goal_profile(monkeypatch)
    state = PyPSAService.get_active_context().solver_state
    monkeypatch.setitem(
        state,
        "solver_config",
        dataclasses.replace(state["solver_config"], target_lole_h=3.0),
    )
    task = start()
    assert task["envelope"]["goal"] == {
        "value": 3.0,
        "unit": "h/yr",
        "source": "stored",
    }
    assert "3 h/yr (from the project's solver settings)" in task["envelope"]["detail"]
    run_envelope.decide(session, task["task_id"], "unattended")


def test_a_run_whose_profile_needs_no_goal_shows_none_and_is_granted_without_one(
    session, site, profile
):
    """P5a: the goal row and its refusal are only for a profile with an LOLE rule."""
    from tests.test_harness_envelope import envelope_of

    task = start()
    assert "goal" not in task["envelope"]
    run_envelope.decide(session, task["task_id"], "unattended")
    granted = envelope_of(task_path(site[1], task))
    assert granted["state"] == "granted" and "goal" not in granted


def test_the_card_goal_is_the_open_projects_only(
    session, site, profile, monkeypatch, tmp_path
):
    _plain_goal_profile(monkeypatch)
    task = start()
    path = task_path(site[1], task)
    assert run_envelope.read_for_card(path, task)["goal"]["source"] == "default"
    assert "goal" not in run_envelope.read_for_card(
        path, {**task, "project_id": "another"}
    )
    assert run_envelope.read_for_card(tmp_path / "no-run.json", task) is None


def test_an_ordinary_chat_enqueue_captures_nothing(session, baseline, monkeypatch):
    """GK B (33) C3: outside a run's own card-approved solve step, a chat
    session's `solve_queue_enqueue` queues its solve and captures nothing."""
    from tests.test_assistant_tasks_study_brief import dispatch, tool_result

    _, project_id, _ = baseline
    captured = spy_capture(monkeypatch)
    assert getattr(session, "write_guard", None) is None
    _, results = dispatch(
        session, monkeypatch, "solve_queue_enqueue", {"project_id": project_id}
    )
    assert tool_result(results).get("id"), results[-1]["content"][:200]
    assert captured == []
