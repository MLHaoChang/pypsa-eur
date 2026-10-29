"""Study harness issue 03: the approval envelope (`services/run_envelope.py`,
design §7; owner decisions SH-ENVELOPE-1, COP-1, SH-RESEARCH-1).

Real project, real runner, real solve queue and the ordinary dispatch path;
no provider calls and no pre-clicked cards: what runs without a card ran
because the envelope admitted it."""
# ruff: noqa: F811 -- imported pytest fixtures
import contextlib
import json

import pytest
from fastapi import HTTPException
from harness import approvals, confirm, edit_card, runner as harness_runner
from harness import session as harness_session
from services import assistant_tasks as tasks, chat_service, chat_tools, run_envelope, run_profiles
from services.pypsa_service import PyPSAService
from tests.test_assistant_tasks_study_brief import (  # noqa: F401
    baseline, dispatch, edit, helpers_state, session, wait_until,
)
from tests.test_harness_run_skeleton import site, start  # noqa: F401
from tests.test_harness_runner import detached, frames_of, settle, task_path  # noqa: F401

# The baseline project has Bus B1, Generator G1 (p_nom 20, marginal_cost 10)
# and Load L1; the test profile's envelope names them.
BLOCK = {
    "edits": [{"tool": "update_component", "target": {"component": "Generator", "name": "G*", "attr": "p_nom"},
               "bounds": {"min": 0.0, "rel": 0.5}},
              {"tool": "update_component", "target": {"component": "Generator", "name": "G*", "attr": "marginal_cost"},
               "bounds": {"min": 0.0, "max": 100.0}}],
    "runs": [{"tool": "solve_queue_enqueue"}],
    "budget": {"solves": 2, "rounds": 6, "wall_time_h": 8.0},
}


@pytest.fixture
def profile(monkeypatch):
    def use(block=BLOCK):
        tunable = [{"component": "Generator", "attr": a} for a in ("p_nom", "marginal_cost")]
        monkeypatch.setitem(run_profiles.PROFILES, "eh_site@1", {**run_profiles.EH_SITE, "envelope": block,
                                                                  "tunable": tunable})
    use()
    return use


@pytest.fixture
def granted(session, site, profile, detached):
    """A run whose envelope the user granted on the plan-review card; then
    no chat session is open."""
    def go(envelope=None, steps=(), detach=True, **kwargs):
        task = start(**kwargs)
        message = run_envelope.decide(session, task["task_id"], "unattended", envelope=envelope)
        path = task_path(site[1], task)
        record = json.loads(path.read_text())
        record["steps"] += [dict(s, status="pending") for s in steps]
        path.write_text(json.dumps(record))
        if detach:
            chat_tools.set_chat_session(None)
            chat_tools.set_acting_user(None)  # the runner acts as the run's owner
        return task, path, message
    return go


def envelope_of(path):
    return json.loads(run_envelope.path_for(path).read_text())


def refusals(path):
    return [a["reason"] for a in envelope_of(path)["audit"] if a["event"] == "refused"]


def run_log(task, path):
    owner = json.loads(path.read_text())["owner_id"]
    return approvals.read(harness_session.ChatSession(session_id="run:" + task["task_id"], owner_user_id=owner))


def step(n, value, attr="p_nom", name="G1"):
    return {"id": f"inputs.{n}", "tool": "update_component",
            "args": {"component_class": "Generator", "name": name, "attrs": {attr: value}}}


def g1(attr="p_nom"):
    return PyPSAService.get_active_context().network.generators.at["G1", attr]


def until(runner, path, task, done):
    return settle(runner, path, task["task_id"], done)


def stopped(record):
    return record["run"]["status"] in ("completed", "needs_user", "paused", "failed")


# ── Use: a listed edit runs without a card, on record as the envelope's ─────


def test_a_listed_edit_within_bounds_runs_without_a_card_and_the_run_completes(granted, session):
    task, path, message = granted(steps=[step(2, 25.0)])
    envelope = envelope_of(path)
    assert envelope["state"] == "granted" and "Approve and run unattended" in message
    record = until(harness_runner.Runner("a"), path, task, stopped)  # no decision source: nobody clicks
    assert record["run"]["status"] == "completed", record["run"]
    assert g1() == 25.0
    frames = frames_of(task["task_id"], record["owner_id"])
    assert not [f for f in frames if f["frame"] == "tool_pending_confirmation"]
    decided = [r for r in run_log(task, path) if r["type"] == "approval/decided"]
    assert {r["tool"] for r in decided} == {"update_component", "continue_at_card", "solve_queue_enqueue"}
    assert all(r["by"] == "envelope:" + envelope["id"] and r["envelope_id"] == envelope["id"] for r in decided)
    used = [a for a in envelope_of(path)["audit"] if a["event"] == "used"]
    assert [a["tool"] for a in used] == ["update_component", "continue_at_card", "solve_queue_enqueue"]


def test_the_grant_is_the_users_card_decision_with_the_envelope_id(granted, session):
    task, path, _ = granted()
    envelope = envelope_of(path)
    grant = [r for r in approvals.read(session) if r["type"] == "approval/decided"]
    assert grant == [dict(grant[0], tool="grant_envelope", outcome="approved", by="user", envelope_id=envelope["id"])]
    assert [a["event"] for a in envelope["audit"]] == ["granted"]
    assert envelope["owner_id"] == json.loads(path.read_text())["owner_id"]
    assert envelope["project_id"] == json.loads(path.read_text())["project_id"]
    assert envelope["edits"][0]["at_grant"] == {"G1": 20.0}


def test_step_by_step_grants_nothing(session, site, profile):
    task = start()
    message = run_envelope.decide(session, task["task_id"], "stepwise")
    assert "step by step" in message
    path = task_path(site[1], task)
    assert envelope_of(path)["state"] == "proposed" and approvals.read(session) == []
    assert not run_envelope.live(path)


def test_the_proposal_is_the_profiles_block_and_the_plan_card_detail(session, site, profile):
    task = start()
    assert task["envelope"]["state"] == "proposed"
    assert task["envelope"]["budget"] == BLOCK["budget"] and "Generator `G*` p_nom" in task["envelope"]["detail"]
    assert chat_tools.get_task(task["task_id"])["envelope"]["id"] == task["envelope"]["id"]


def test_an_ordinary_chat_session_with_a_granted_envelope_still_cards_every_write(session, site, profile, monkeypatch):
    task = start()
    run_envelope.decide(session, task["task_id"], "unattended")
    session.task_id = task["task_id"]  # the same run, bound to the user's chat
    frames, _ = dispatch(session, monkeypatch, "update_component", edit(25.0))
    assert "tool_pending_confirmation" in [kind for kind, _ in frames]
    assert not [r for r in approvals.read(session) if str(r.get("by", "")).startswith("envelope:")]
    assert [a["event"] for a in envelope_of(task_path(site[1], task))["audit"]] == ["granted"]


# ── The refusal matrix (§7), one test per row ────────────────────────────────


@contextlib.contextmanager
def on_run(task, path, ctx=None, guard=None):
    """The runner's own session for the run, as harness/runner.py binds it."""
    owner = json.loads(path.read_text())["owner_id"]
    run = harness_session.ChatSession(session_id="run:" + task["task_id"], owner_user_id=owner)
    run.task_id = task["task_id"]
    run.decision_guard = guard or (lambda *a: True)
    token = tasks.RUN_CONTEXT.set(ctx or PyPSAService.get_active_context())
    try:
        yield run
    finally:
        tasks.RUN_CONTEXT.reset(token)


def ask(run, tool, args, external=None):
    card = None
    if edit_card.is_edit(tool):
        with contextlib.suppress(HTTPException):
            card = edit_card.preview(tool, args)
    return run_envelope.admit(run, call_id="call-" + tool, tool=tool, args=args,
                              tier=confirm._safety_tier_for(tool), external=external, edit_card=card)


@pytest.fixture
def matrix(session, site, profile):
    task = start()
    run_envelope.decide(session, task["task_id"], "unattended")
    path = task_path(site[1], task)
    assert run_envelope.live(path)
    return task, path


def refused(matrix, reason, tool, args, **kwargs):
    task, path = matrix
    with on_run(task, path, **{k: kwargs.pop(k) for k in ("ctx", "guard") if k in kwargs}) as run:
        assert ask(run, tool, args, **kwargs) is None
    assert refusals(path)[-1] == reason, refusals(path)
    assert g1() == 20.0  # nothing ran


def test_refused_destructive_tier(matrix):
    refused(matrix, "destructive", "cluster_network", {"n_clusters": 1})


def test_refused_delete(matrix):
    refused(matrix, "delete", "delete_component", {"component_class": "Generator", "name": "G1"})


def test_refused_mcp_or_external(matrix):
    refused(matrix, "external", "update_component", step(2, 25.0)["args"], external="some-mcp-server")


def test_refused_unknown_tool(matrix):
    refused(matrix, "unknown_tool", "set_everything", {"x": 1})


def test_refused_unlisted_tool(matrix):
    refused(matrix, "unlisted_tool", "set_weather_choice", {"source": "synthetic", "year": 2024})


def test_refused_outside_the_entry(matrix):
    refused(matrix, "outside_entry", "update_component",
            {"component_class": "Generator", "name": "G1", "attrs": {"efficiency": 0.5}})


def test_refused_out_of_bounds(matrix):
    refused(matrix, "out_of_bounds", "update_component", step(2, 31.0)["args"])


def test_refused_expired(matrix, monkeypatch):
    monkeypatch.setattr(run_envelope, "_now", lambda: 4e9)
    refused(matrix, "expired", "update_component", step(2, 25.0)["args"])
    assert envelope_of(matrix[1])["state"] == "expired"


def test_refused_revoked(matrix, session):
    run_envelope.decide(session, matrix[0]["task_id"], "revoke")
    refused(matrix, "revoked", "update_component", step(2, 25.0)["args"])


def test_refused_acl_loss(matrix, monkeypatch):
    from services import project_registry
    task, path = matrix
    ctx = PyPSAService.get_active_context()
    recheck = harness_runner.Runner("a").recheck  # the runner's in-lock re-check, as its guard calls it

    def lost_meanwhile(*_):
        """The owner loses the project after the grant, before this dispatch."""
        monkeypatch.setattr(project_registry, "resolve_project",
                            lambda *a, **k: (_ for _ in ()).throw(HTTPException(404, "gone")))
        return recheck(task["task_id"], path, ctx)
    refused(matrix, "recheck", "update_component", step(2, 25.0)["args"], guard=lost_meanwhile)


def test_refused_wrong_project(matrix):
    _, path = matrix
    envelope = envelope_of(path)  # an envelope granted on another project, copied beside this run
    envelope["project_id"] = "00000000-0000-4000-8000-000000000001"
    run_envelope.path_for(path).write_text(json.dumps(envelope))
    refused(matrix, "wrong_project", "update_component", step(2, 25.0)["args"])


OTHER = "00000000-0000-4000-8000-000000000002"


def run_project():
    ctx = PyPSAService.get_active_context()
    return str(ctx.project_uuid), ctx.loaded_project


@pytest.mark.parametrize("tool,args", [
    ("solve_queue_enqueue", lambda own: {"project_id": OTHER}),
    ("solve_queue_enqueue", lambda own: {"project_id": "Some Other Project"}),
    ("run_sensitivity_sweep", lambda own: {"baseline_project_id": OTHER, "cases": []}),
    ("run_sensitivity_sweep", lambda own: {"baseline_project_id": own, "case_project_ids": [own, OTHER]}),
    ("set_study_parameter", lambda own: {"project": "Some Other Project", "parameter": "x", "value": 1}),
    ("start_decision_study", lambda own: {"project": own, "base_project_name": "Some Other Project"}),
])
def test_refused_a_call_that_names_another_project(matrix, tool, args):
    """SH-ENVELOPE-1 scopes the envelope to the run's project: a listed run
    or edit tool that names any other project is refused (GK B (8))."""
    task, path = matrix
    envelope = envelope_of(path)
    envelope["runs"].append({"tool": "run_sensitivity_sweep"})
    run_envelope.path_for(path).write_text(json.dumps(envelope))
    refused(matrix, "wrong_project", tool, args(run_project()[0]))


@pytest.mark.parametrize("which", [0, 1])
def test_a_listed_run_tool_naming_the_runs_own_project_is_admitted(matrix, which):
    task, path = matrix
    envelope = envelope_of(path)
    envelope["runs"].append({"tool": "run_sensitivity_sweep"})
    run_envelope.path_for(path).write_text(json.dumps(envelope))
    own = run_project()[which]  # the run's uuid, or its name, as the tool resolves either
    with on_run(task, path) as run:
        assert ask(run, "solve_queue_enqueue", {"project_id": own}) == "admitted"
        assert ask(run, "run_sensitivity_sweep", {"baseline_project_id": own, "cases": []}) == "admitted"


def test_refused_foreign_drift(matrix):
    PyPSAService.get_active_context().network.loads.at["L1", "p_set"] = 6.0  # a user edit, not the run's
    refused(matrix, "drift", "update_component", step(2, 25.0)["args"])


def test_refused_brief_stale(matrix):
    task, path = matrix
    record = json.loads(path.read_text())
    record["fingerprint"] = "an-older-brief"
    path.write_text(json.dumps(record))
    refused(matrix, "brief_stale", "update_component", step(2, 25.0)["args"])


def test_refused_a_researched_value(matrix):
    args = dict(step(2, 25.0)["args"], source="research:abc")
    refused(matrix, "research_value", "update_component", args)


def test_grid_code_publish_is_never_admitted_even_when_listed(matrix):
    task, path = matrix
    with pytest.raises(run_envelope.EnvelopeError):
        run_envelope.check_block(dict(BLOCK, edits=[{"tool": "campus_publish_grid_code", "target": {
            "component": "Generator", "name": "G1", "attr": "p_nom"}, "bounds": {"max": 1.0}}]))
    envelope = envelope_of(path)  # listed anyway, as if the file were written by hand
    envelope["runs"].append({"tool": "campus_publish_grid_code"})
    run_envelope.path_for(path).write_text(json.dumps(envelope))
    refused(matrix, "grid_code_publish", "campus_publish_grid_code", {"project_id": task["task_id"], "profile_id": "p"})


# ── Bounds, globs and entries ────────────────────────────────────────────────


def test_bounds_are_relative_to_the_value_at_grant(granted):
    """Three rounds of +40 % with rel 0.5: the second is refused (20 → 28 → 39.2)."""
    task, path, _ = granted(steps=[step(2, 28.0), step(3, 39.2), step(4, 54.88)])
    record = until(harness_runner.Runner("a"), path, task,
                   lambda r: stopped(r) or (r["run"].get("waiting") or {}).get("ref") == "inputs.3")
    assert g1() == 28.0
    assert record["run"]["status"] == "needs_user" and record["run"]["waiting"]["ref"] == "inputs.3"
    assert record["run"]["waiting"]["envelope_refused"] is True
    assert refusals(path) == ["out_of_bounds"]
    assert [s["status"] for s in record["steps"][1:]] == ["completed", "pending", "pending"]
    assert harness_runner.Runner("a").tick(path) == "waiting"  # a card only; no retry of the envelope


def test_a_glob_records_each_instance_at_grant_and_a_later_instance_gets_a_card(matrix):
    task, path = matrix
    ctx = PyPSAService.get_active_context()
    before, _ = run_envelope.chain_head(path, ctx)
    ctx.network.add("Generator", "G2", bus="B1", p_nom=10.0, marginal_cost=5.0)
    run_envelope.settle(path, "the-runs-own-add", ctx, before)  # created by the run: its chain follows
    assert envelope_of(path)["edits"][0]["at_grant"] == {"G1": 20.0}
    refused(matrix, "new_instance", "update_component",
            {"component_class": "Generator", "name": "G2", "attrs": {"p_nom": 11.0}})


def test_a_glob_never_matches_another_component_class(session, site, profile):
    profile(dict(BLOCK, edits=[{"tool": "update_component", "target": {"component": "Generator", "name": "*", "attr": "p_nom"},
                                "bounds": {"rel": 0.5}}]))
    task = start()
    run_envelope.decide(session, task["task_id"], "unattended")
    path = task_path(site[1], task)
    with on_run(task, path) as run:
        assert ask(run, "update_component", {"component_class": "Bus", "name": "B1", "attrs": {"v_nom": 120.0}}) is None
    assert refusals(path) == ["outside_entry"]


@pytest.mark.parametrize("entry", [
    {"tool": "update_component", "target": {"component": "Generator", "name": "G1", "attr": "carrier"}},
    {"tool": "update_component", "target": {"component": "Generator", "name": "G1", "attr": "bus"}, "bounds": {}},
])
def test_an_edit_without_numeric_bounds_or_an_enum_is_invalid_envelope(session, site, profile, entry):
    task = start()
    with pytest.raises(HTTPException) as failure:
        run_envelope.decide(session, task["task_id"], "unattended", envelope=dict(BLOCK, edits=[entry]))
    assert failure.value.detail["error_kind"] == "invalid_envelope"
    with pytest.raises(run_profiles.ProfileError):
        run_profiles.load({**run_profiles.EH_SITE, "envelope": dict(BLOCK, edits=[entry])})


def test_an_enum_entry_admits_only_its_values():
    entry = {"tool": "update_component", "target": {"component": "Generator", "name": "G1", "attr": "carrier"},
             "enum": ["gas", "solar"]}
    assert run_envelope.check_block(dict(BLOCK, edits=[entry]))["edits"] == [entry]
    assert run_envelope._in_bounds(entry, "solar", "gas") and not run_envelope._in_bounds(entry, "coal", "gas")


def test_an_envelope_edit_names_only_the_profiles_tunable_attrs():
    with pytest.raises(run_profiles.ProfileError, match="tunable"):
        run_profiles.load({**run_profiles.EH_SITE, "envelope": BLOCK})  # G* p_nom is not an eh_site lever


def test_a_boundary_is_not_crossed_after_foreign_drift(granted):
    task, path, _ = granted()
    PyPSAService.get_active_context().network.loads.at["L1", "p_set"] = 6.0  # before the solve boundary
    record = until(harness_runner.Runner("a"), path, task, stopped)
    assert record["run"]["status"] == "paused" and record["run"]["reason"].startswith("envelope_")
    assert record["status"] != "completed" and not any(s["id"].startswith("solve.") and s["status"] == "completed"
                                                       for s in record["steps"])
    assert "continue_at_card" not in [a.get("tool") for a in envelope_of(path)["audit"] if a["event"] == "used"]


@pytest.mark.parametrize("attr", ["p_set", "target_lole_h", "grid_code_limit"])
def test_an_envelope_never_names_what_the_agent_never_changes(attr):
    with pytest.raises(run_envelope.EnvelopeError):
        run_envelope.check_block(dict(BLOCK, edits=[{"tool": "update_component", "target": {
            "component": "Load", "name": "L1", "attr": attr}, "bounds": {"max": 1.0}}]))


# ── Narrowing (grant) ────────────────────────────────────────────────────────


def test_narrowing_at_grant_is_kept(granted):
    narrower = dict(BLOCK, edits=[dict(BLOCK["edits"][0], target=dict(BLOCK["edits"][0]["target"], name="G1"),
                                       bounds={"min": 0.0, "rel": 0.2})],
                    runs=[], budget={"solves": 1, "rounds": 2, "wall_time_h": 1.0})
    task, path, _ = granted(envelope=narrower)
    envelope = envelope_of(path)
    assert [e["target"]["name"] for e in envelope["edits"]] == ["G1"] and envelope["edits"][0]["bounds"]["rel"] == 0.2
    assert envelope["runs"] == [] and envelope["budget"] == narrower["budget"]
    assert run_envelope._epoch(envelope["expires_at"]) - run_envelope._epoch(envelope["granted_at"]) == 3600


@pytest.mark.parametrize("wider", [
    dict(BLOCK, budget={"solves": 3, "rounds": 6, "wall_time_h": 8.0}),
    dict(BLOCK, edits=[dict(BLOCK["edits"][0], bounds={"min": 0.0, "rel": 0.9})]),
    dict(BLOCK, edits=[dict(BLOCK["edits"][0], target=dict(BLOCK["edits"][0]["target"], name="*"))]),
    dict(BLOCK, runs=[{"tool": "run_simulation"}]),
    dict(BLOCK, budget={"solves": 2, "rounds": 6, "wall_time_h": 100.0}),
], ids=["budget", "rel", "glob", "run", "wall-time-cap"])
def test_an_envelope_wider_than_the_profiles_is_invalid_envelope(session, site, profile, wider):
    task = start()
    with pytest.raises(HTTPException) as failure:
        run_envelope.decide(session, task["task_id"], "unattended", envelope=wider)
    assert failure.value.detail["error_kind"] == "invalid_envelope"
    assert envelope_of(task_path(site[1], task))["state"] == "proposed" and approvals.read(session) == []


# ── Expiry, revoke, cancel, budget ───────────────────────────────────────────


def test_after_expires_at_the_next_call_cards_and_the_run_pauses(granted, monkeypatch):
    task, path, _ = granted(steps=[step(2, 25.0)])
    expires = run_envelope._epoch(envelope_of(path)["expires_at"])
    monkeypatch.setattr(run_envelope, "_now", lambda: expires + 1)
    record = until(harness_runner.Runner("a"), path, task, stopped)
    assert record["run"]["status"] == "paused" and record["run"]["reason"] == "envelope_expired"
    assert envelope_of(path)["state"] == "expired" and g1() == 20.0
    assert [n["kind"] for n in record["run"].get("notices", [])] == ["paused"]


def test_a_revoke_from_the_plan_card_pauses_the_run(granted, session):
    task, path, _ = granted(steps=[step(2, 25.0)], detach=False)
    run_envelope.decide(session, task["task_id"], "revoke")
    record = json.loads(path.read_text())
    assert record["run"]["status"] == "paused" and record["run"]["reason"] == "envelope_revoked"
    assert envelope_of(path)["state"] == "revoked" and not run_envelope.live(path)


def test_cancel_task_revokes_the_envelope(session, site, profile):
    task = start()
    run_envelope.decide(session, task["task_id"], "unattended")
    chat_tools.cancel_task(task["task_id"])
    envelope = envelope_of(task_path(site[1], task))
    assert envelope["state"] == "revoked" and envelope["audit"][-1]["reason"] == "cancel_task"


def test_the_solve_budget_is_exhausted_and_pauses_the_run(granted):
    task, path, _ = granted(envelope=dict(BLOCK, budget={"solves": 1, "rounds": 6, "wall_time_h": 8.0}))
    envelope = envelope_of(path)
    envelope["audit"].append({"at": envelope["granted_at"], "event": "used", "call_id": "x", "tool": "solve_queue_enqueue"})
    run_envelope.path_for(path).write_text(json.dumps(envelope))
    record = until(harness_runner.Runner("a"), path, task, stopped)
    assert record["run"]["status"] == "paused" and record["run"]["reason"] == "envelope_exhausted"
    assert envelope_of(path)["state"] == "exhausted" and refusals(path) == ["exhausted"]


# ── Fail closed and the freshness chain ──────────────────────────────────────


def test_an_approval_that_cannot_be_recorded_is_denied(granted, monkeypatch):
    task, path, _ = granted(steps=[step(2, 25.0)])

    def broken(session, record):
        raise OSError("disk full")
    monkeypatch.setattr(approvals, "_append", broken)
    runner = harness_runner.Runner("a")
    until(runner, path, task, lambda r: r["steps"][1].get("claim") is None and any(
        f["step"] == "inputs.2" for f in frames_of(task["task_id"], r["owner_id"])))
    frames = [f for f in frames_of(task["task_id"], json.loads(path.read_text())["owner_id"]) if f["step"] == "inputs.2"]
    assert [f.get("error_kind") for f in frames if f["frame"] == "tool_error"] == ["approval_unavailable"]
    assert not [f for f in frames if f["frame"] in ("tool_running", "tool_pending_confirmation")]
    assert g1() == 20.0 and refusals(path) == ["approval_unavailable"]


def test_a_user_edit_between_two_runner_changes_pauses_the_run(granted, monkeypatch):
    task, path, _ = granted(steps=[step(2, 25.0), step(3, 26.0)])
    runner = harness_runner.Runner("a")
    until(runner, path, task, lambda r: r["steps"][1]["status"] == "completed")
    assert len(envelope_of(path)["chain"]) == 1  # the run's own change is the chain's next link
    PyPSAService.get_active_context().network.generators.at["G1", "marginal_cost"] = 12.0  # another writer
    record = until(runner, path, task, stopped)
    assert record["run"]["status"] == "paused" and record["run"]["reason"] in ("envelope_drift", "envelope_brief_stale")
    assert g1() == 25.0 and refusals(path) in (["drift"], ["brief_stale"])
    assert not run_envelope.live(path)


def test_a_user_edit_before_an_uncarded_effectful_step_is_not_taken_into_the_chain(granted):
    """GK B (8): a step the envelope never admits (GUIDED_UNCARDED: no card)
    must not launder a user edit made before it into the chain."""
    preview = {"id": "inputs.2", "tool": "preview_project_changes", "args": {"changes": [
        {"component_class": "Generator", "names": ["G1"], "attribute": "p_nom", "value": 22.0}]}}
    task, path, _ = granted(steps=[preview, step(3, 25.0)])
    assert "preview_project_changes" in confirm.GUIDED_UNCARDED
    PyPSAService.get_active_context().network.loads.at["L1", "p_set"] = 6.0  # a user edit, not the run's
    record = until(harness_runner.Runner("a"), path, task, stopped)
    assert record["run"]["status"] == "paused" and record["run"]["reason"] == "envelope_drift", record["run"]
    assert [s["status"] for s in record["steps"][1:]] == ["pending", "pending"]
    assert g1() == 20.0 and refusals(path) == ["drift"] and envelope_of(path)["chain"] == []


def test_a_step_that_started_off_the_chain_adds_no_link(matrix):
    """A runner change advances the chain only from the chain's last link, read before the step."""
    task, path = matrix
    ctx = PyPSAService.get_active_context()
    ctx.network.loads.at["L1", "p_set"] = 6.0  # a user edit
    before, reason = run_envelope.chain_head(path, ctx)
    assert reason == "drift"
    ctx.network.generators.at["G1", "marginal_cost"] = 12.0  # the step's own change
    run_envelope.settle(path, "a-step", ctx, before)
    assert envelope_of(path)["chain"] == []
    refused(matrix, "drift", "update_component", step(2, 25.0)["args"])


def test_an_unreadable_fingerprint_pauses_the_run(granted, monkeypatch):
    task, path, _ = granted(steps=[step(2, 25.0)])
    monkeypatch.setattr(run_envelope, "_fingerprint", lambda ctx: None)
    record = until(harness_runner.Runner("a"), path, task, stopped)
    assert record["run"]["status"] == "paused" and record["run"]["reason"] == "envelope_fingerprint_unreadable"
    assert refusals(path) == ["fingerprint_unreadable"] and g1() == 20.0


# ── The verdict arrives only as the user's /stream request ──────────────────


def test_an_envelope_verdict_with_a_user_message_is_refused(session, site, profile):
    task = start()
    frames = list(chat_service.run_turn(session, "grant it please", envelope_decision={
        "task_id": task["task_id"], "verdict": "unattended", "envelope": None, "notify_email": False}))
    assert [e for e, _ in frames] == ["error", "session_done"]
    assert frames[0][1]["error_kind"] == "envelope_decision_unavailable"
    assert envelope_of(task_path(site[1], task))["state"] == "proposed"


def test_start_task_takes_no_envelope_from_the_model(session, site, profile):
    with pytest.raises(HTTPException) as failure:
        start(run={"profile": "eh_site@1", "mode": "stepwise", "envelope": BLOCK})
    assert failure.value.detail["error_kind"] == "invalid_task"
