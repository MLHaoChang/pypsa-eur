# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
# ruff: noqa: F811 -- imported pytest fixtures
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
from harness import continuation, job_adapters, run_loops, runner as harness_runner
from services import dirty_state, run_envelope, run_gates, run_profiles
from services.adequacy import coupling, coupling_loop_runner, margin_loop_runner
from services.pypsa_service import PyPSAService
from tests.test_assistant_tasks_study_brief import (  # noqa: F401
    baseline,
    helpers_state,
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


def engine_rule(
    rule_id="res.adequacy.margin", tool="run_margin_loop", max_solves=4, restore=None
):
    rule = {
        "id": rule_id,
        "fact": FACT,
        "op": "eq",
        "value": True,
        "severity": "block",
        "basis": "The plan meets the reliability goal on the MC's own LOLE.",
        "engine": {"tool": tool, "max_solves": max_solves},
    }
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
    stages = {
        **base["stages"],
        "result_gate": {
            "actions": [],
            "rules": [*run_profiles.CORE_RESULT_RULES, *rules],
            "agent": {
                "tools": list(run_profiles.L2_TOOLS),
                "rounds": run_profiles.L2_ROUNDS,
                "changes": run_profiles.L2_CHANGES,
            },
        },
    }
    monkeypatch.setitem(
        run_profiles.PROFILES,
        "eh_site@1",
        {**base, "stages": stages, "envelope": block},
    )


@pytest.fixture
def ok(eh):
    """eh_site@1's edit shapes on a network the input gate passes and the solve
    meets (the L2 tests' `infeasible`, with L1 inside G1's 40 MW cap)."""
    network = ctx().network
    network.generators.loc["G1", ["p_nom", "p_nom_extendable", "capital_cost"]] = [
        0.0,
        True,
        1.0,
    ]
    network.storage_units.at["S1", "capital_cost"] = 1.0
    network.loads.at["L1", "p_set"] = 30.0
    return eh


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
        monkeypatch.setattr(
            margin_loop_runner, "start_margin_loop", self._starter("margin_loop")
        )
        monkeypatch.setattr(
            coupling_loop_runner, "start_coupling_loop", self._starter("coupling_loop")
        )

    def finish_with(self, **fields):
        self.outcomes.append(fields)
        return self

    def _starter(self, study):
        def begin(body, *, solver_state, state_update, publish_study):
            if self.raise_on_start is not None:
                raise self.raise_on_start
            self.calls.append((study, body))
            release, stop = threading.Event(), threading.Event()
            index = len(self.records)
            record = {
                "status": "running",
                "kind": study,
                "started_at": time.time(),
                "iterations": [],
                "restore": getattr(body, "restore", None) or "base",
                "goal_sources": dict(GOAL_SOURCES),
                "target_lole_h": 3.0,
                "max_solves": body.max_solves,
                "solves_used": 0,
                "stop_event": stop,
                **({"probe_solves": 0} if study == "margin_loop" else {}),
            }
            dirty_state.capture(
                record
            )  # the real loops capture their revision at start (LC-40b)

            def work():
                while not (release.is_set() or stop.is_set()):
                    release.wait(0.02)
                queued = self.outcomes[index] if index < len(self.outcomes) else None
                fields = queued or {"status": "met"}
                if stop.is_set() and not release.is_set():
                    fields = {"status": "aborted", "solves_used": 1}
                with PyPSAService.get_solver_state_lock():
                    record.update(
                        {
                            "base_restore_status": "optimal",
                            "base_restored": True,
                            "finished_at": time.time(),
                            **fields,
                        }
                    )

            thread = threading.Thread(target=work, daemon=True, name=f"fake-{study}")
            record["thread"] = thread
            self.records.append(record)
            self._release.append(release)
            if index >= len(self.outcomes) or self.outcomes[index] is not None:
                release.set()  # finishes at once unless `hold()` queued a None for it
            publish_study(study, record, thread)
            return {
                "status": "running",
                "study": study,
                "max_solves": body.max_solves,
                "restore": record["restore"],
                "goal_sources": dict(GOAL_SOURCES),
            }

        return begin

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


def test_an_engine_rule_loads_with_its_fact_and_defaults_restore_to_base(
    monkeypatch, eh
):
    with_rules(monkeypatch, engine_rule())
    loaded = run_profiles.resolve("eh_site@1")
    [rule] = [r for r in loaded["stages"]["result_gate"]["rules"] if r["fact"] == FACT]
    assert rule["engine"] == {
        "tool": "run_margin_loop",
        "max_solves": 4,
        "restore": "base",
    }


@pytest.mark.parametrize(
    "change",
    [
        lambda r: r.pop("engine"),  # the fact without an engine
        lambda r: r.update(
            fact="solve.status", op="eq", value="optimal"
        ),  # an engine on another fact
        lambda r: r["engine"].update(tool="run_mc_study"),
        lambda r: r["engine"].update(max_solves=0),
        lambda r: r["engine"].update(max_solves=coupling.MAX_LOOP_SOLVES + 1),
        lambda r: r["engine"].update(max_solves=True),
        lambda r: r["engine"].update(restore="last"),
        lambda r: r["engine"].update(draws=50),  # never the question (§6.0)
        lambda r: r["engine"].update(target_lole_h=1.0),
        lambda r: r.update(severity="warn"),
    ],
)
def test_a_malformed_engine_rule_is_refused_at_load(change):
    rule = engine_rule()
    change(rule)
    stages = {
        "result_gate": {"actions": [], "rules": [*run_profiles.CORE_RESULT_RULES, rule]}
    }
    with pytest.raises((run_profiles.ProfileError, run_gates.RuleError)):
        run_profiles.load(
            {
                **run_profiles.EH_SITE,
                "stages": {**run_profiles.EH_SITE["stages"], **stages},
            }
        )


def test_an_engine_rule_is_refused_outside_the_result_gate():
    stages = {
        "input_gate": {
            "actions": [],
            "rules": [*run_profiles.CORE_INPUT_RULES, engine_rule()],
        }
    }
    with pytest.raises(run_profiles.ProfileError):
        run_profiles.load(
            {
                **run_profiles.EH_SITE,
                "stages": {**run_profiles.EH_SITE["stages"], **stages},
            }
        )


def test_eh_site_carries_no_engine_rule_yet():
    """Option A (GK A (11)): mechanism only; the reliability rule joins with issue 07."""
    loaded = run_profiles.resolve("eh_site@1")
    assert not [
        r
        for s in loaded["stages"].values()
        for r in s.get("rules", [])
        if "engine" in r or r["fact"] == FACT
    ]


# ── C9 / acceptance 4: the engine drives the rule, no agent turn ───────────


def test_a_native_margin_loop_rule_never_starts_an_agent_turn(
    granted, ok, loops, monkeypatch
):
    with_rules(monkeypatch, engine_rule())
    loops.finish_with(status="met", solves_used=2, probe_solves=1)
    task, path, _ = granted()
    record = go(harness_runner.Runner("a", agent_provider=Never()), path, task)
    run = record["run"]
    assert run["status"] == "completed", (
        run["stage"],
        [
            (r["rule"], r["verdict"], r["facts"][0]["value"], r.get("reason"))
            for r in stage_of(record, run["stage"]).get("gate", {}).get("rules", [])
            if r["verdict"] != "pass"
        ],
    )
    [(study, body)] = loops.calls
    assert study == "margin_loop" and body.target_lole_h is None and body.draws is None
    [step] = engine_steps(record)
    assert step["status"] == "completed" and step["engine"] == "res.adequacy.margin"
    [fact] = [
        r
        for r in stage_of(record)["gate"]["rules"]
        if r["rule"] == "res.adequacy.margin"
    ]
    assert fact["verdict"] == "pass"
    assert "loop" not in run or run["loop"]["kind"] != "L2"


@pytest.mark.parametrize(
    "status", ["budget_exhausted", "unreachable", "aborted", "failed"]
)
def test_a_loop_that_did_not_meet_its_goal_ends_not_converged_with_no_l2_round(
    granted, ok, loops, monkeypatch, status
):
    with_rules(monkeypatch, engine_rule())
    loops.finish_with(status=status, solves_used=4, probe_solves=1)
    task, path, _ = granted()
    record = go(harness_runner.Runner("a", agent_provider=Never()), path, task)
    run = record["run"]
    assert run["status"] == "needs_user" and stage_of(record)["status"] == "failed"
    assert stage_of(record)["reason"] == "not_converged"
    [fact] = [
        r
        for r in stage_of(record)["gate"]["rules"]
        if r["rule"] == "res.adequacy.margin"
    ]
    assert fact["verdict"] != "pass"
    assert not (run.get("loop") or {}).get("rounds")  # never nested: no L2 round ran
    assert run["budget"]["used"].get("rounds", 0) == 1  # the engine call is one round


def test_a_status_no_loop_sets_is_lost_never_read_as_the_steps(
    granted, ok, loops, monkeypatch
):
    """A record ended with a status outside the terminal set is not the
    step's verdict: the step settles `lost` and the run needs the user."""
    with_rules(monkeypatch, engine_rule())
    loops.finish_with(status="weird", solves_used=4, probe_solves=1)
    task, path, _ = granted()
    record = go(harness_runner.Runner("a", agent_provider=Never()), path, task)
    step = engine_steps(record)[0]
    assert step["status"] == "uncertain" and step["job"]["outcome"] == "lost"
    assert record["run"]["status"] == "needs_user"
    assert not (record["run"].get("loop") or {}).get("rounds")


def test_a_non_engine_failure_goes_to_l2_first_without_the_engine_rule_or_its_start(
    granted, infeasible, rated, loops, monkeypatch
):
    infeasible(12)
    with_rules(monkeypatch, engine_rule())
    script = Script(cap(55.0), say("Raised G1's cap."))
    task, path, _ = granted()
    record = go(harness_runner.Runner("a", agent_provider=script), path, task)
    loop = record["run"]["loop"]
    assert loop["kind"] == "L2" and loop["rounds"] == 1
    assert all(
        "res.adequacy.margin" not in cycle["failing"] for cycle in loop["history"]
    )
    sent = json.dumps(script.requests[0].messages, default=str)
    assert "res.adequacy.margin" not in sent and FACT not in sent
    # The engine ran once, after L2's re-solve passed the non-engine rules.
    assert len(loops.calls) == 1


# ── Condition 2: max_solves clamped to the run's solves ─────────────────────


@pytest.mark.parametrize(
    "tool, solves, expected",
    [
        ("run_margin_loop", 12, 4),  # rule 4, room enough
        (
            "run_margin_loop",
            6,
            3,
        ),  # 6 - 1 (the run's solve) - 1 probe - 1 closing re-solve
        ("run_coupling_loop", 6, 4),  # no probe: 6 - 1 - 1
        ("run_margin_loop", 30, 4),
    ],
)
def test_max_solves_is_the_rule_value_clamped_to_the_solves_left(
    granted, ok, loops, monkeypatch, tool, solves, expected
):
    with_rules(
        monkeypatch,
        engine_rule(tool=tool, max_solves=4),
        solves=min(solves, run_envelope.CAPS["solves"]),
    )
    task, path, _ = granted()
    go(harness_runner.Runner("a", agent_provider=Never()), path, task)
    [(_, body)] = loops.calls
    assert body.max_solves == expected


def test_the_clamp_never_exceeds_the_engine_cap(monkeypatch):
    assert (
        run_loops.engine_max_solves(
            {"tool": "run_coupling_loop", "max_solves": coupling.MAX_LOOP_SOLVES},
            solves_left=100,
        )
        == coupling.MAX_LOOP_SOLVES
    )


def test_with_too_few_solves_left_the_loop_is_not_started_and_the_run_needs_the_user(
    granted, ok, loops, monkeypatch
):
    with_rules(
        monkeypatch, engine_rule(), solves=3
    )  # 1 solve + probe + restore leaves 0
    task, path, _ = granted()
    record = go(harness_runner.Runner("a", agent_provider=Never()), path, task)
    assert loops.calls == []
    assert (
        record["run"]["status"] == "needs_user"
        and stage_of(record)["reason"] == "budget_spent"
    )


# ── C4: the loop's solves are the run's solves ──────────────────────────────


def test_the_loop_charges_its_solves_its_probe_and_its_closing_re_solve(
    granted, ok, loops, monkeypatch
):
    with_rules(monkeypatch, engine_rule())
    loops.finish_with(status="met", solves_used=3, probe_solves=1)
    task, path, _ = granted()
    record = go(harness_runner.Runner("a", agent_provider=Never()), path, task)
    used = record["run"]["budget"]["used"]["solves"]
    assert (
        used == 1 + 3 + 1 + 1
    )  # the run's solve, the loop's, the probe, the closing re-solve
    assert run_envelope.solves_used(envelope_of(path)) == used


def test_while_the_loop_runs_its_worst_case_is_reserved(
    granted, ok, loops, monkeypatch
):
    with_rules(monkeypatch, engine_rule(max_solves=4))
    loops.hold()
    task, path, _ = granted()
    runner = harness_runner.Runner("a", agent_provider=Never())
    settle(
        runner,
        path,
        task["task_id"],
        lambda r: bool(engine_steps(r)) and engine_steps(r)[0]["status"] == "waiting",
    )
    assert run_envelope.solves_used(envelope_of(path)) == 1 + 4 + 1 + 1
    loops.release_all(status="met", solves_used=2, probe_solves=1)
    record = go(runner, path, task)
    assert (
        run_envelope.solves_used(envelope_of(path))
        == 1 + 2 + 1 + 1
        == record["run"]["budget"]["used"]["solves"]
    )


# ── Condition 3: restore ────────────────────────────────────────────────────


def test_restore_final_is_refused_unless_the_grant_covers_it(
    granted, ok, loops, monkeypatch
):
    with_rules(monkeypatch, engine_rule(restore="final"))
    task, path, _ = granted()
    record = go(harness_runner.Runner("a", agent_provider=Never()), path, task)
    assert loops.calls == []
    assert (
        record["run"]["status"] == "needs_user"
        and stage_of(record)["reason"] == "restore_uncovered"
    )


def test_restore_final_runs_when_the_grant_covers_it(granted, ok, loops, monkeypatch):
    with_rules(
        monkeypatch,
        engine_rule(restore="final"),
        runs=[
            {"tool": "solve_queue_enqueue"},
            {"tool": "run_margin_loop", "restore": "final"},
        ],
    )
    task, path, _ = granted()
    record = go(harness_runner.Runner("a", agent_provider=Never()), path, task)
    [(_, body)] = loops.calls
    assert body.restore == "final" and record["run"]["status"] == "completed"


def test_restore_defaults_to_base(granted, ok, loops, monkeypatch):
    with_rules(monkeypatch, engine_rule())
    task, path, _ = granted()
    go(harness_runner.Runner("a", agent_provider=Never()), path, task)
    [(_, body)] = loops.calls
    assert body.restore == "base"


# ── Condition 5: the fact reads the loop's own record ───────────────────────


# Statuses that are not a finished loop's: the record while it runs, and one
# iterate's solve outcome (`solve_at`'s `out`, never the study record).
RUNNING = {"running"}
PER_ITERATE = {"error", "no_report", "infeasible"}


def _status_literals(module):
    """(where, literal) for every string the module writes as a status: a
    `verdict`/`status` assignment, a `status=` keyword of `.update(...)`, a
    `"status":` key of a dict literal."""
    found = []

    def constants(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        if isinstance(node, ast.IfExp):
            return constants(node.body) + constants(node.orelse)
        if isinstance(node, ast.BoolOp):
            return [v for value in node.values for v in constants(value)]
        return []

    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) in ("verdict", "status") for t in node.targets
        ):
            found += [("settled", v) for v in constants(node.value)]
        elif (
            isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "update"
        ):
            owner = getattr(node.func.value, "id", None)
            for kw in node.keywords:
                if kw.arg == "status":
                    found += [
                        ("iterate" if owner == "out" else "record", v)
                        for v in constants(kw.value)
                    ]
        elif isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value == "status":
                    found += [("record", c) for c in constants(v)]
    return found


def test_the_terminal_set_is_every_status_the_engine_can_set():
    """GK A (11)(a), (12) Q3: from code. Every status the controller settles
    and the runners write to a record is running or in `LOOP_STATUSES`; a
    per-iterate status never reaches a record. Only `met` converges."""
    seen = set()
    for module in (coupling, coupling_loop_runner, margin_loop_runner):
        for where, literal in _status_literals(module):
            if where == "iterate":
                assert literal in PER_ITERATE, (module.__name__, literal)
            else:
                assert literal in set(coupling.LOOP_STATUSES) | RUNNING, (
                    module.__name__,
                    where,
                    literal,
                )
                seen.add(literal)
    assert seen - RUNNING == set(
        coupling.LOOP_STATUSES
    )  # no constant member the engine never sets
    assert not (set(coupling.LOOP_STATUSES) & (RUNNING | PER_ITERATE))
    assert (
        set(coupling.LOOP_STATUSES) <= job_adapters._STUDY_TERMINAL
    )  # one set, never a fork


@pytest.mark.parametrize(
    "status", sorted(coupling.LOOP_STATUSES | {"weird", "done", "running", "", "MET"})
)
def test_only_met_converges(status):
    assert run_gates.converged({"status": status}) is (status == "met")
    assert run_gates.converged({}) is False and run_gates.converged(None) is False


def test_goal_sources_are_copied_into_the_gate_as_served(
    granted, ok, loops, monkeypatch
):
    with_rules(monkeypatch, engine_rule())
    loops.finish_with(status="met", solves_used=1, probe_solves=1)
    task, path, _ = granted()
    record = go(harness_runner.Runner("a", agent_provider=Never()), path, task)
    [fact] = [
        r
        for r in stage_of(record)["gate"]["rules"]
        if r["rule"] == "res.adequacy.margin"
    ]
    assert fact["facts"][0]["loop"]["goal_sources"] == GOAL_SOURCES
    assert fact["facts"][0]["loop"]["status"] == "met"


def test_a_missing_loop_record_is_no_evidence(granted, ok, loops, monkeypatch):
    with_rules(monkeypatch, engine_rule())
    task, path, _ = granted()
    record = record_of(path)
    env = run_gates.Env(task=record, ctx=ctx(), project=None, project_dir=None)
    rule = run_gates.check_rule({k: v for k, v in engine_rule().items()})
    [result] = run_gates.evaluate([rule], env)
    assert result["verdict"] == "not_evaluated"


def test_a_goal_less_target_is_a_typed_refusal_and_the_run_needs_the_user(
    granted, ok, loops, monkeypatch
):
    with_rules(monkeypatch, engine_rule())
    loops.raise_on_start = HTTPException(
        422, "target_lole_h is required and must be > 0"
    )
    task, path, _ = granted()
    record = go(harness_runner.Runner("a", agent_provider=Never()), path, task)
    assert (
        record["run"]["status"] == "needs_user"
        and stage_of(record)["reason"] == "engine_refused"
    )
    [step] = [s for s in record["steps"] if s.get("engine")]
    assert step["status"] == "failed" and "target_lole_h" in step["error"]


# ── Condition 6: the catch is the 422 set only (#1020) ──────────────────────


def test_a_non_http_error_at_the_start_is_not_read_as_a_refusal(
    granted, ok, loops, monkeypatch
):
    """Mutant: `except Exception` at the catching site would turn this into
    `engine_refused`."""
    with_rules(monkeypatch, engine_rule())
    loops.raise_on_start = RuntimeError("the engine is broken")
    task, path, _ = granted()
    record = go(harness_runner.Runner("a", agent_provider=Never()), path, task)
    assert stage_of(record).get("reason") != "engine_refused"
    [step] = [s for s in record["steps"] if s.get("engine")]
    assert step["status"] in (
        "uncertain",
        "failed",
    ) and "engine_refused" not in json.dumps(step)
    assert record["run"]["status"] != "completed"


# ── Condition 4: one loop at a time ─────────────────────────────────────────


def test_a_second_engine_rule_starts_only_after_the_first_loop_ended(
    granted, ok, loops, monkeypatch
):
    with_rules(
        monkeypatch,
        engine_rule(),
        engine_rule("res.adequacy.cap", tool="run_coupling_loop", max_solves=2),
        solves=20,
    )
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


def test_cancelling_the_run_aborts_the_loop(
    granted, ok, loops, monkeypatch, seeded_identity
):
    from services import assistant_tasks, chat_tools

    with_rules(monkeypatch, engine_rule())
    loops.hold()
    task, path, _ = granted()
    runner = harness_runner.Runner("a", agent_provider=Never())
    settle(
        runner,
        path,
        task["task_id"],
        lambda r: bool(engine_steps(r)) and engine_steps(r)[0]["status"] == "waiting",
    )
    chat_tools.set_acting_user(seeded_identity["user_id"])  # the user cancels from chat
    assistant_tasks.cancel_task(task["task_id"])
    [record_] = loops.records
    assert wait_until(lambda: record_["status"] == "aborted")
    assert record_["stop_event"].is_set()
    assert wait_until(lambda: engine_steps(record_of(path))[0]["status"] != "waiting")
    record = record_of(path)
    assert record["status"] == "cancelled"
    assert engine_steps(record)[0]["job"]["outcome"] == "aborted"
    # C10: the aborted loop's solve and its closing re-solve are charged, with the run's own solve.
    assert run_envelope.solves_used(envelope_of(path)) == 1 + 1 + 1


# ── GK A (11)(b): restart recovery ──────────────────────────────────────────


def _hold_lease(path, owner, until):
    record = record_of(path)
    record["run"].setdefault("runner", {}).update(lease_owner=owner, lease_until=until)
    path.write_text(json.dumps(record))


def _lease_to(path, instance):
    """The holder's lease lapses: `instance` takes the run over on its next
    tick (a takeover, so it recovers the run's waiting steps)."""
    record = record_of(path)
    _hold_lease(path, record["run"]["runner"]["lease_owner"], 0)
    return harness_runner.Runner(instance, agent_provider=Never())


def _waiting(runner, path, task):
    settle(
        runner,
        path,
        task["task_id"],
        lambda r: bool(engine_steps(r)) and engine_steps(r)[0]["status"] == "waiting",
    )
    return engine_steps(record_of(path))[0]


def test_a_loop_still_running_after_a_takeover_is_followed_to_its_end(
    granted, ok, loops, monkeypatch
):
    with_rules(monkeypatch, engine_rule())
    loops.hold()
    task, path, _ = granted()
    _waiting(harness_runner.Runner("a", agent_provider=Never()), path, task)
    second = _lease_to(path, "b")
    second.tick(path)
    loops.release_all(status="met", solves_used=1, probe_solves=1)
    record = go(second, path, task)
    assert len(loops.calls) == 1  # never re-started
    assert record["run"]["status"] == "completed"


def test_a_stale_follower_writes_nothing_and_the_new_holder_settles_from_the_record(
    granted, ok, loops, monkeypatch
):
    """H1: after a takeover the old follower writes nothing; the new holder
    settles the step from the loop's own record (GK A (11)(b))."""
    with_rules(monkeypatch, engine_rule())
    loops.hold()
    task, path, _ = granted()
    _waiting(harness_runner.Runner("a", agent_provider=Never()), path, task)
    _hold_lease(path, "c", time.time() + 3600)  # another process took the run over
    loops.release_all(status="unreachable", solves_used=4, probe_solves=1)
    [record_] = loops.records
    assert wait_until(lambda: record_["status"] == "unreachable")
    time.sleep(
        3 * continuation.ENGINE_POLL_SECONDS
    )  # the old follower polled the ended loop
    assert (
        engine_steps(record_of(path))[0]["status"] == "waiting"
    )  # ... and wrote nothing
    second = _lease_to(path, "b")
    record = go(second, path, task)
    assert len(loops.calls) == 1
    assert engine_steps(record)[0]["result"]["loop"]["status"] == "unreachable"
    assert (
        record["run"]["status"] == "needs_user"
        and stage_of(record)["reason"] == "not_converged"
    )


def test_a_loop_lost_with_the_process_is_never_re_started(
    granted, ok, loops, monkeypatch
):
    with_rules(monkeypatch, engine_rule())
    loops.hold()
    task, path, _ = granted()
    _waiting(harness_runner.Runner("a", agent_provider=Never()), path, task)
    # The process restarted: its in-memory loop record and study handles are gone.
    second = _lease_to(path, "b")
    with PyPSAService.get_solver_state_lock():
        ctx().solver_state.pop("margin_loop", None)
    job_adapters._ACTIVITY_BINDINGS.clear()
    record = go(second, path, task)
    assert len(loops.calls) == 1
    step = engine_steps(record)[0]
    assert step["status"] == "uncertain" and step["job"]["outcome"] == "lost"
    assert record["run"]["status"] == "needs_user"
    assert (
        run_envelope.solves_used(envelope_of(path)) == 1 + 4 + 1 + 1
    )  # C9: lost keeps its reservation
    loops.release_all()


def test_a_stranger_loop_in_the_slot_is_never_read_as_the_steps(
    granted, ok, loops, monkeypatch
):
    """C8: another margin loop (the user's own) in the slot settles the step
    `lost`, never with the stranger's status."""
    with_rules(monkeypatch, engine_rule())
    loops.hold()
    task, path, _ = granted()
    _waiting(harness_runner.Runner("a", agent_provider=Never()), path, task)
    with PyPSAService.get_solver_state_lock():
        ctx().solver_state["margin_loop"] = {
            "status": "met",
            "started_at": time.time() + 1,
            "goal_sources": {},
        }
    assert wait_until(lambda: engine_steps(record_of(path))[0]["status"] != "waiting")
    step = engine_steps(record_of(path))[0]
    assert step["job"]["outcome"] == "lost" and "loop" not in (step.get("result") or {})
    loops.release_all()


def test_two_settles_of_one_step_write_and_charge_once(granted, ok, loops, monkeypatch):
    """C7: the follower, the recover path and the cancel path may race; the
    settle is a compare-and-set on the step, and its solves are charged once."""
    from services import assistant_tasks

    with_rules(monkeypatch, engine_rule())
    loops.hold()
    task, path, _ = granted()
    step = _waiting(harness_runner.Runner("a", agent_provider=Never()), path, task)
    ticket = (task["task_id"], step["id"], step["attempt_id"])
    loop = {
        "status": "met",
        "solves_used": 2,
        "probe_solves": 1,
        "base_restore_status": "optimal",
        "goal_sources": GOAL_SOURCES,
        "started_at": step["job"]["started_at"],
    }
    first = assistant_tasks.settle_engine(path, ticket, loop, "ended")
    second = assistant_tasks.settle_engine(path, ticket, loop, "ended")
    assert first is not None and second is None
    assert run_envelope.solves_used(envelope_of(path)) == 1 + 2 + 1 + 1
    loops.release_all()


def test_the_engine_step_args_come_from_the_rule_only(granted, ok, loops, monkeypatch):
    """H4: the loop's arguments are the rule's engine block (and the clamp):
    no project, target or draws, nothing from a model."""
    with_rules(monkeypatch, engine_rule(max_solves=3))
    task, path, _ = granted()
    record = go(harness_runner.Runner("a", agent_provider=Never()), path, task)
    [step] = engine_steps(record)
    assert step["tool"] == "run_margin_loop" and set(step["args"]) == {
        "max_solves",
        "restore",
    }
    assert (
        "run_margin_loop" not in run_profiles.L2_TOOLS
        and "run_coupling_loop" not in run_profiles.L2_TOOLS
    )
    [(_, body)] = loops.calls
    assert (body.target_lole_h, body.draws, body.seed) == (None, None, None)


def test_a_busy_context_refuses_the_start_and_never_waits(
    granted, ok, loops, monkeypatch
):
    """C11: a start refused because the user's own study or solve holds the
    context (409) is `engine_refused`, needs_user; never retried."""
    with_rules(monkeypatch, engine_rule())
    loops.raise_on_start = HTTPException(409, "another study is running")
    task, path, _ = granted()
    record = go(harness_runner.Runner("a", agent_provider=Never()), path, task)
    assert (
        record["run"]["status"] == "needs_user"
        and stage_of(record)["reason"] == "engine_refused"
    )
    assert len([s for s in record["steps"] if s.get("engine")]) == 1


# ── Acceptance: the LOLE basis ──────────────────────────────────────────────


def test_a_multi_year_goal_reaches_the_loop_once_on_the_horizon_basis(
    granted, ok, loops, monkeypatch
):
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
        state["solver_config"] = dataclasses.replace(
            state["solver_config"], target_lole_h=3.0
        )
    seen = []

    def spy(body, solver_state):
        out = active_loop_goal(body, solver_state)
        seen.append((horizon_years(PyPSAService.get_network()), out))
        return out

    monkeypatch.setattr("services.reliability_goal.active_loop_goal", spy)
    real = loops._starter("margin_loop")

    def begin(body, *, solver_state, state_update, publish_study):
        from services import reliability_goal

        reliability_goal.active_loop_goal(body, solver_state)  # the runner's first read
        return real(
            body,
            solver_state=solver_state,
            state_update=state_update,
            publish_study=publish_study,
        )

    monkeypatch.setattr(margin_loop_runner, "start_margin_loop", begin)
    with_rules(monkeypatch, engine_rule())
    task, path, _ = granted()
    go(harness_runner.Runner("a", agent_provider=Never()), path, task)
    [(years, (target, _draws, sources))] = seen
    assert years == pytest.approx(3.0)
    assert target == pytest.approx(9.0) and sources["target_lole_h"] == "stored"
