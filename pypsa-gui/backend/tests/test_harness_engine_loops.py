# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Study harness issue 05, PR C: the engine loops (design §6.2; C9 and GK A
(11) conditions 1-6). A result rule served by `run_margin_loop` or
`run_coupling_loop` is the engine's to drive: no agent turn, never inside
L2, its `max_solves` clamped to the run's solves, its solves counted, and its
gate fact the loop's own terminal record.

The loop routes are real (chat_tools -> routers.results -> the runner's
`start_*`); only `start_margin_loop` / `start_coupling_loop` are replaced by
`FakeLoop`, which publishes a record and a worker thread into the run
context's solver state the way the runners do, and finishes it when the test
says so. The LOLE test keeps the runner's own goal resolution."""
import ast
import inspect
import json
import threading
import time

import pytest
from fastapi import HTTPException
from harness import job_adapters, run_loops, runner as harness_runner
from services import run_envelope, run_gates, run_profiles
from services.adequacy import coupling, coupling_loop_runner, margin_loop_runner
from services.pypsa_service import PyPSAService
from tests.test_assistant_tasks_study_brief import (  # noqa: F401
    session,
)
from tests.test_harness_envelope import envelope_of, granted, profile  # noqa: F401
from tests.test_harness_envelope_atomic import ATOMIC, eh  # noqa: F401
from tests.test_harness_l2_loop import Never, cap, infeasible  # noqa: F401
from tests.test_harness_run_loops import Script, go, rated, record_of, say  # noqa: F401
from tests.test_harness_run_skeleton import site, start  # noqa: F401
from tests.test_harness_runner import detached, settle, task_path  # noqa: F401

FACT = "adequacy.loop.converged"
STUDY = {"run_margin_loop": "margin_loop", "run_coupling_loop": "coupling_loop"}
GOAL_SOURCES = {"target_lole_h": "stored", "mc_draws": "default"}


def ctx():
    return PyPSAService.get_active_context()


def engine_rule(rule_id="res.adequacy.margin", tool="run_margin_loop", max_solves=4, restore=None):
    rule = {"id": rule_id, "fact": FACT, "op": "eq", "value": True, "severity": "block",
            "basis": "The plan meets the reliability goal on the MC's own LOLE.",
            "engine": {"tool": tool, "max_solves": max_solves}}
    if restore is not None:
        rule["engine"]["restore"] = restore
    return rule


def with_rules(monkeypatch, *rules, solves=12, runs=None):
    """eh_site@1's edit shapes, its result gate carrying `rules` after the
    core rules, and an envelope of `solves` solves."""
    base = run_profiles.PROFILES["eh_site@1"]
    block = dict(ATOMIC, budget={"solves": solves, "rounds": 6, "wall_time_h": 8.0})
    if runs is not None:
        block["runs"] = runs
    stages = {**base["stages"], "result_gate": {
        "actions": [], "rules": [*run_profiles.CORE_RESULT_RULES, *rules],
        "agent": {"tools": list(run_profiles.L2_TOOLS), "rounds": run_profiles.L2_ROUNDS,
                  "changes": run_profiles.L2_CHANGES}}}
    monkeypatch.setitem(run_profiles.PROFILES, "eh_site@1", {**base, "stages": stages, "envelope": block})


class FakeLoop:
    """The two loop starts, replaced below the route: a record and its worker
    thread published into the calling context's solver state (as
    `start_margin_loop` does), finished with `finish(**fields)` or by the abort
    route's stop event."""

    def __init__(self, monkeypatch):
        self.calls = []  # (study, body)
        self.records = []
        self.outcomes = []  # queued finishes, consumed in start order
        self.raise_on_start = None
        self._release = []
        monkeypatch.setattr(margin_loop_runner, "start_margin_loop", self._starter("margin_loop"))
        monkeypatch.setattr(coupling_loop_runner, "start_coupling_loop", self._starter("coupling_loop"))

    def finish_with(self, **fields):
        self.outcomes.append(fields)
        return self

    def _starter(self, study):
        def start(body, *, solver_state, state_update, publish_study):
            if self.raise_on_start is not None:
                raise self.raise_on_start
            self.calls.append((study, body))
            release, stop = threading.Event(), threading.Event()
            index = len(self.records)
            record = {"status": "running", "kind": study, "started_at": time.time(), "iterations": [],
                      "restore": getattr(body, "restore", None) or "base", "goal_sources": dict(GOAL_SOURCES),
                      "target_lole_h": 3.0, "max_solves": body.max_solves, "solves_used": 0, "stop_event": stop,
                      **({"probe_solves": 0} if study == "margin_loop" else {})}

            def work():
                while not (release.is_set() or stop.is_set()):
                    release.wait(0.02)
                queued = self.outcomes[index] if index < len(self.outcomes) else None
                fields = queued or {"status": "met"}
                if stop.is_set() and not release.is_set():
                    fields = {"status": "aborted", "solves_used": 1}
                with PyPSAService.get_solver_state_lock():
                    record.update({"base_restore_status": "optimal", "base_restored": True,
                                   "finished_at": time.time(), **fields})
            thread = threading.Thread(target=work, daemon=True, name=f"fake-{study}")
            record["thread"] = thread
            self.records.append(record)
            self._release.append(release)
            if index >= len(self.outcomes) or self.outcomes[index] is not None:
                release.set()  # finishes at once unless `hold()` queued a None for it
            publish_study(study, record, thread)
            return {"status": "running", "study": study, "max_solves": body.max_solves,
                    "restore": record["restore"], "goal_sources": dict(GOAL_SOURCES)}
        return start

    def hold(self):
        """The next loop stays running until `release_all`."""
        self.outcomes.clear()
        self.outcomes.append(None)
        return self

    def release_all(self, **fields):
        for i, release in enumerate(self._release):
            if i < len(self.outcomes) and self.outcomes[i] is None:
                self.outcomes[i] = fields or {"status": "met"}
            release.set()


@pytest.fixture
def loops(monkeypatch):
    return FakeLoop(monkeypatch)


def engine_steps(record):
    return [s for s in record["steps"] if (s.get("job") or {}).get("kind") == "engine"]


def stage_of(record, name="result_gate"):
    return record["run"]["stages"][name]


def wait_until(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


# ── Profile: the engine block and its fact are loaded together ──────────────


def test_an_engine_rule_loads_with_its_fact_and_defaults_restore_to_base(monkeypatch, eh):
    with_rules(monkeypatch, engine_rule())
    loaded = run_profiles.resolve("eh_site@1")
    [rule] = [r for r in loaded["stages"]["result_gate"]["rules"] if r["fact"] == FACT]
    assert rule["engine"] == {"tool": "run_margin_loop", "max_solves": 4, "restore": "base"}


@pytest.mark.parametrize("change", [
    lambda r: r.pop("engine"),  # the fact without an engine
    lambda r: r.update(fact="solve.status", op="eq", value="optimal"),  # an engine on another fact
    lambda r: r["engine"].update(tool="run_mc_study"),
    lambda r: r["engine"].update(max_solves=0),
    lambda r: r["engine"].update(max_solves=coupling.MAX_LOOP_SOLVES + 1),
    lambda r: r["engine"].update(max_solves=True),
    lambda r: r["engine"].update(restore="last"),
    lambda r: r["engine"].update(draws=50),  # never the question (§6.0)
    lambda r: r["engine"].update(target_lole_h=1.0),
    lambda r: r.update(severity="warn"),
])
def test_a_malformed_engine_rule_is_refused_at_load(change):
    rule = engine_rule()
    change(rule)
    stages = {"result_gate": {"actions": [], "rules": [*run_profiles.CORE_RESULT_RULES, rule]}}
    with pytest.raises((run_profiles.ProfileError, run_gates.RuleError)):
        run_profiles.load({**run_profiles.EH_SITE, "stages": {**run_profiles.EH_SITE["stages"], **stages}})


def test_an_engine_rule_is_refused_outside_the_result_gate():
    stages = {"input_gate": {"actions": [], "rules": [*run_profiles.CORE_INPUT_RULES, engine_rule()]}}
    with pytest.raises(run_profiles.ProfileError):
        run_profiles.load({**run_profiles.EH_SITE, "stages": {**run_profiles.EH_SITE["stages"], **stages}})


def test_eh_site_carries_no_engine_rule_yet():
    """Option A (GK A (11)): mechanism only; the reliability rule joins with issue 07."""
    loaded = run_profiles.resolve("eh_site@1")
    assert not [r for s in loaded["stages"].values() for r in s.get("rules", []) if "engine" in r or r["fact"] == FACT]


# ── C9 / acceptance 4: the engine drives the rule, no agent turn ───────────


def test_a_native_margin_loop_rule_never_starts_an_agent_turn(granted, eh, loops, monkeypatch):
    with_rules(monkeypatch, engine_rule())
    loops.finish_with(status="met", solves_used=2, probe_solves=1)
    task, path, _ = granted()
    record = go(harness_runner.Runner("a", agent_provider=Never()), path, task)
    run = record["run"]
    assert run["status"] == "completed", (run["stage"], stage_of(record).get("reason"))
    [(study, body)] = loops.calls
    assert study == "margin_loop" and body.target_lole_h is None and body.draws is None
    [step] = engine_steps(record)
    assert step["status"] == "completed" and step["engine"] == "res.adequacy.margin"
    [fact] = [r for r in stage_of(record)["gate"]["rules"] if r["rule"] == "res.adequacy.margin"]
    assert fact["verdict"] == "pass"
    assert "loop" not in run or run["loop"]["kind"] != "L2"


@pytest.mark.parametrize("status", ["budget_exhausted", "unreachable", "aborted", "failed", "weird"])
def test_a_loop_that_did_not_meet_its_goal_ends_not_converged_with_no_l2_round(granted, eh, loops, monkeypatch,
                                                                                status):
    with_rules(monkeypatch, engine_rule())
    loops.finish_with(status=status, solves_used=4, probe_solves=1)
    task, path, _ = granted()
    record = go(harness_runner.Runner("a", agent_provider=Never()), path, task)
    run = record["run"]
    assert run["status"] == "needs_user" and stage_of(record)["status"] == "failed"
    assert stage_of(record)["reason"] == "not_converged"
    [fact] = [r for r in stage_of(record)["gate"]["rules"] if r["rule"] == "res.adequacy.margin"]
    assert fact["verdict"] != "pass"
    assert not (run.get("loop") or {}).get("rounds")  # never nested: no L2 round ran
    assert run["budget"]["used"].get("rounds", 0) == 1  # the engine call is one round


def test_a_non_engine_failure_goes_to_l2_first_without_the_engine_rule_or_its_start(
        granted, infeasible, rated, loops, monkeypatch):
    infeasible(12)
    with_rules(monkeypatch, engine_rule())
    script = Script(cap(55.0), say("Raised G1's cap."))
    task, path, _ = granted()
    record = go(harness_runner.Runner("a", agent_provider=script), path, task)
    loop = record["run"]["loop"]
    assert loop["kind"] == "L2" and loop["rounds"] == 1
    assert all("res.adequacy.margin" not in cycle["failing"] for cycle in loop["history"])
    sent = json.dumps(script.requests[0].messages, default=str)
    assert "res.adequacy.margin" not in sent and FACT not in sent
    # The engine ran once, after L2's re-solve passed the non-engine rules.
    assert len(loops.calls) == 1


# ── Condition 2: max_solves clamped to the run's solves ─────────────────────


@pytest.mark.parametrize("tool, solves, expected", [
    ("run_margin_loop", 12, 4),  # rule 4, room enough
    ("run_margin_loop", 6, 3),  # 6 - 1 (the run's solve) - 1 probe - 1 closing re-solve
    ("run_coupling_loop", 6, 4),  # no probe: 6 - 1 - 1
    ("run_margin_loop", 30, 4),
])
def test_max_solves_is_the_rule_value_clamped_to_the_solves_left(granted, eh, loops, monkeypatch, tool, solves,
                                                                 expected):
    with_rules(monkeypatch, engine_rule(tool=tool, max_solves=4), solves=min(solves, run_envelope.CAPS["solves"]))
    task, path, _ = granted()
    go(harness_runner.Runner("a", agent_provider=Never()), path, task)
    [(_, body)] = loops.calls
    assert body.max_solves == expected


def test_the_clamp_never_exceeds_the_engine_cap(monkeypatch):
    assert run_loops.engine_max_solves({"tool": "run_coupling_loop", "max_solves": coupling.MAX_LOOP_SOLVES},
                                       solves_left=100) == coupling.MAX_LOOP_SOLVES


def test_with_too_few_solves_left_the_loop_is_not_started_and_the_run_needs_the_user(granted, eh, loops,
                                                                                    monkeypatch):
    with_rules(monkeypatch, engine_rule(), solves=3)  # 1 solve + probe + restore leaves 0
    task, path, _ = granted()
    record = go(harness_runner.Runner("a", agent_provider=Never()), path, task)
    assert loops.calls == []
    assert record["run"]["status"] == "needs_user" and stage_of(record)["reason"] == "budget_spent"


# ── C4: the loop's solves are the run's solves ──────────────────────────────


def test_the_loop_charges_its_solves_its_probe_and_its_closing_re_solve(granted, eh, loops, monkeypatch):
    with_rules(monkeypatch, engine_rule())
    loops.finish_with(status="met", solves_used=3, probe_solves=1)
    task, path, _ = granted()
    record = go(harness_runner.Runner("a", agent_provider=Never()), path, task)
    used = record["run"]["budget"]["used"]["solves"]
    assert used == 1 + 3 + 1 + 1  # the run's solve, the loop's, the probe, the closing re-solve
    assert run_envelope.solves_used(envelope_of(path)) == used


def test_while_the_loop_runs_its_worst_case_is_reserved(granted, eh, loops, monkeypatch):
    with_rules(monkeypatch, engine_rule(max_solves=4))
    loops.hold()
    task, path, _ = granted()
    runner = harness_runner.Runner("a", agent_provider=Never())
    settle(runner, path, task["task_id"], lambda r: bool(engine_steps(r)) and engine_steps(r)[0]["status"] == "waiting")
    assert run_envelope.solves_used(envelope_of(path)) == 1 + 4 + 1 + 1
    loops.release_all(status="met", solves_used=2, probe_solves=1)
    record = go(runner, path, task)
    assert run_envelope.solves_used(envelope_of(path)) == 1 + 2 + 1 + 1 == record["run"]["budget"]["used"]["solves"]


# ── Condition 3: restore ────────────────────────────────────────────────────


def test_restore_final_is_refused_unless_the_grant_covers_it(granted, eh, loops, monkeypatch):
    with_rules(monkeypatch, engine_rule(restore="final"))
    task, path, _ = granted()
    record = go(harness_runner.Runner("a", agent_provider=Never()), path, task)
    assert loops.calls == []
    assert record["run"]["status"] == "needs_user" and stage_of(record)["reason"] == "restore_uncovered"


def test_restore_final_runs_when_the_grant_covers_it(granted, eh, loops, monkeypatch):
    with_rules(monkeypatch, engine_rule(restore="final"),
               runs=[{"tool": "solve_queue_enqueue"}, {"tool": "run_margin_loop", "restore": "final"}])
    task, path, _ = granted()
    record = go(harness_runner.Runner("a", agent_provider=Never()), path, task)
    [(_, body)] = loops.calls
    assert body.restore == "final" and record["run"]["status"] == "completed"


def test_restore_defaults_to_base(granted, eh, loops, monkeypatch):
    with_rules(monkeypatch, engine_rule())
    task, path, _ = granted()
    go(harness_runner.Runner("a", agent_provider=Never()), path, task)
    [(_, body)] = loops.calls
    assert body.restore == "base"


# ── Condition 5: the fact reads the loop's own record ───────────────────────


def test_the_terminal_set_is_every_status_the_engine_can_set():
    """GK A (11)(a): from code. Every status literal the controller returns
    and the runners' fallback are terminal; only `met` converges."""
    literals = set()
    for fn in (coupling.run_coupling_loop,):
        for node in ast.walk(ast.parse(inspect.getsource(fn).lstrip())):
            if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "verdict" for t in node.targets) \
                    and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                literals.add(node.value.value)
            if isinstance(node, ast.IfExp) and isinstance(node.body, ast.Constant):
                literals.update(v.value for v in (node.body, node.orelse) if isinstance(v, ast.Constant))
    for module in (margin_loop_runner, coupling_loop_runner):
        source = inspect.getsource(module)
        assert '.get("status") or "failed"' in source
    literals.add("failed")
    assert literals and literals <= set(coupling.LOOP_STATUSES)
    assert "running" not in coupling.LOOP_STATUSES
    assert run_gates.converged({"status": "met"}) is True
    for status in set(coupling.LOOP_STATUSES) - {"met"}:
        assert run_gates.converged({"status": status}) is False
    for odd in ("weird", "done", "running", None, "", "MET"):
        assert run_gates.converged({"status": odd}) is False


def test_goal_sources_are_copied_into_the_gate_as_served(granted, eh, loops, monkeypatch):
    with_rules(monkeypatch, engine_rule())
    loops.finish_with(status="met", solves_used=1, probe_solves=1)
    task, path, _ = granted()
    record = go(harness_runner.Runner("a", agent_provider=Never()), path, task)
    [fact] = [r for r in stage_of(record)["gate"]["rules"] if r["rule"] == "res.adequacy.margin"]
    assert fact["facts"][0]["loop"]["goal_sources"] == GOAL_SOURCES
    assert fact["facts"][0]["loop"]["status"] == "met"


def test_a_missing_loop_record_is_no_evidence(granted, eh, loops, monkeypatch):
    with_rules(monkeypatch, engine_rule())
    task, path, _ = granted()
    record = record_of(path)
    env = run_gates.Env(task=record, ctx=ctx(), project=None, project_dir=None)
    rule = run_gates.check_rule({k: v for k, v in engine_rule().items()})
    [result] = run_gates.evaluate([rule], env)
    assert result["verdict"] == "not_evaluated"


def test_a_goal_less_target_is_a_typed_refusal_and_the_run_needs_the_user(granted, eh, loops, monkeypatch):
    with_rules(monkeypatch, engine_rule())
    loops.raise_on_start = HTTPException(422, "target_lole_h is required and must be > 0")
    task, path, _ = granted()
    record = go(harness_runner.Runner("a", agent_provider=Never()), path, task)
    assert record["run"]["status"] == "needs_user" and stage_of(record)["reason"] == "engine_refused"
    [step] = [s for s in record["steps"] if s.get("engine")]
    assert step["status"] == "failed" and "target_lole_h" in step["error"]


# ── Condition 6: the catch is the 422 set only (#1020) ──────────────────────


def test_a_non_http_error_at_the_start_is_not_read_as_a_refusal(granted, eh, loops, monkeypatch):
    """Mutant: `except Exception` at the catching site would turn this into
    `engine_refused`."""
    with_rules(monkeypatch, engine_rule())
    loops.raise_on_start = RuntimeError("the engine is broken")
    task, path, _ = granted()
    record = go(harness_runner.Runner("a", agent_provider=Never()), path, task)
    assert stage_of(record).get("reason") != "engine_refused"
    [step] = [s for s in record["steps"] if s.get("engine")]
    assert step["status"] in ("uncertain", "failed") and "engine_refused" not in json.dumps(step)
    assert record["run"]["status"] != "completed"


# ── Condition 4: one loop at a time ─────────────────────────────────────────


def test_a_second_engine_rule_starts_only_after_the_first_loop_ended(granted, eh, loops, monkeypatch):
    with_rules(monkeypatch, engine_rule(), engine_rule("res.adequacy.cap", tool="run_coupling_loop", max_solves=2),
               solves=20)
    loops.hold()
    task, path, _ = granted()
    runner = harness_runner.Runner("a", agent_provider=Never())
    settle(runner, path, task["task_id"], lambda r: bool(engine_steps(r)))
    for _ in range(5):
        runner.tick(path)
    assert [study for study, _ in loops.calls] == ["margin_loop"]
    loops.release_all(status="met", solves_used=1, probe_solves=1)
    loops.outcomes.append({"status": "met", "solves_used": 1})
    record = go(runner, path, task)
    assert [study for study, _ in loops.calls] == ["margin_loop", "coupling_loop"]
    first, second = engine_steps(record)
    assert first["result"]["loop"]["finished_at"] <= second["job"]["started_at"]


# ── Condition 1: a cancelled run stops the loop through its abort route ─────


def test_cancelling_the_run_aborts_the_loop(granted, eh, loops, monkeypatch):
    from services import assistant_tasks
    with_rules(monkeypatch, engine_rule())
    loops.hold()
    task, path, _ = granted()
    runner = harness_runner.Runner("a", agent_provider=Never())
    settle(runner, path, task["task_id"], lambda r: bool(engine_steps(r)) and engine_steps(r)[0]["status"] == "waiting")
    assistant_tasks.cancel_task(task["task_id"])
    [record_] = loops.records
    assert wait_until(lambda: record_["status"] == "aborted")
    assert record_["stop_event"].is_set()
    assert wait_until(lambda: engine_steps(record_of(path))[0]["status"] != "waiting")
    record = record_of(path)
    assert record["status"] == "cancelled"
    assert engine_steps(record)[0]["job"]["outcome"] == "aborted"


# ── GK A (11)(b): restart recovery ──────────────────────────────────────────


def _restarted(path, task):
    """The process restarted: the study handles are gone, a fresh runner takes
    the lapsed lease."""
    job_adapters._ACTIVITY_BINDINGS.clear()
    job_adapters._STUDIES.clear()
    record = record_of(path)
    record["run"].setdefault("runner", {})["lease_until"] = 0
    path.write_text(json.dumps(record))
    return harness_runner.Runner("b", agent_provider=Never())


def test_a_loop_still_running_after_a_takeover_is_followed_to_its_end(granted, eh, loops, monkeypatch):
    with_rules(monkeypatch, engine_rule())
    loops.hold()
    task, path, _ = granted()
    first = harness_runner.Runner("a", agent_provider=Never())
    settle(first, path, task["task_id"], lambda r: bool(engine_steps(r)) and engine_steps(r)[0]["status"] == "waiting")
    second = _restarted(path, task)
    second.tick(path)
    loops.release_all(status="met", solves_used=1, probe_solves=1)
    record = go(second, path, task)
    assert len(loops.calls) == 1  # never re-started
    assert record["run"]["status"] == "completed"


def test_a_loop_that_ended_while_no_runner_watched_is_settled_from_its_record(granted, eh, loops, monkeypatch):
    with_rules(monkeypatch, engine_rule())
    loops.hold()
    task, path, _ = granted()
    first = harness_runner.Runner("a", agent_provider=Never())
    settle(first, path, task["task_id"], lambda r: bool(engine_steps(r)) and engine_steps(r)[0]["status"] == "waiting")
    monkeypatch.setattr("harness.continuation.ENGINE_POLL_SECONDS", 3600.0, raising=False)
    loops.release_all(status="unreachable", solves_used=4, probe_solves=1)
    [record_] = loops.records
    assert wait_until(lambda: record_["status"] == "unreachable")
    second = _restarted(path, task)
    record = go(second, path, task)
    assert len(loops.calls) == 1
    assert engine_steps(record)[0]["result"]["loop"]["status"] == "unreachable"
    assert record["run"]["status"] == "needs_user" and stage_of(record)["reason"] == "not_converged"


def test_a_loop_lost_with_the_process_is_never_re_started(granted, eh, loops, monkeypatch):
    with_rules(monkeypatch, engine_rule())
    loops.hold()
    task, path, _ = granted()
    first = harness_runner.Runner("a", agent_provider=Never())
    settle(first, path, task["task_id"], lambda r: bool(engine_steps(r)) and engine_steps(r)[0]["status"] == "waiting")
    with PyPSAService.get_solver_state_lock():
        ctx().solver_state.pop("margin_loop", None)  # the process that held it is gone
    second = _restarted(path, task)
    record = go(second, path, task)
    assert len(loops.calls) == 1
    assert engine_steps(record)[0]["status"] == "uncertain"
    assert record["run"]["status"] == "needs_user"
    assert run_envelope.solves_used(envelope_of(path)) == 1 + 4 + 1 + 1  # the reservation stays spent
    loops.release_all()


# ── Acceptance: the LOLE basis ──────────────────────────────────────────────


def test_a_multi_year_goal_reaches_the_loop_once_on_the_horizon_basis(granted, eh, loops, monkeypatch):
    """3 h/yr on a 3-year horizon whose snapshots are not 1 h: the loop's
    target is 9 horizon hours, from `loop_goal(horizon_years(n))`, and the
    harness passes neither target nor draws."""
    import dataclasses

    from services.adequacy.metrics import horizon_years
    from services.reliability_goal import active_loop_goal
    network = ctx().network
    network.snapshot_weightings.loc[:, :] = 3 * 8760 / len(network.snapshots)
    assert network.snapshot_weightings["objective"].iloc[0] != 1.0
    with PyPSAService.get_solver_state_lock():
        state = ctx().solver_state
        state["solver_config"] = dataclasses.replace(state["solver_config"], target_lole_h=3.0)
    seen = []

    def spy(body, solver_state):
        out = active_loop_goal(body, solver_state)
        seen.append((horizon_years(PyPSAService.get_network()), out))
        return out
    monkeypatch.setattr("services.reliability_goal.active_loop_goal", spy)
    real = loops._starter("margin_loop")

    def start(body, *, solver_state, state_update, publish_study):
        from services import reliability_goal
        reliability_goal.active_loop_goal(body, solver_state)  # the runner's first read
        return real(body, solver_state=solver_state, state_update=state_update, publish_study=publish_study)
    monkeypatch.setattr(margin_loop_runner, "start_margin_loop", start)
    with_rules(monkeypatch, engine_rule())
    task, path, _ = granted()
    go(harness_runner.Runner("a", agent_provider=Never()), path, task)
    [(years, (target, _draws, sources))] = seen
    assert years == pytest.approx(3.0)
    assert target == pytest.approx(9.0) and sources["target_lole_h"] == "stored"
