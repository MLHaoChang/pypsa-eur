# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""FILL-10-6 follow-ups to study-harness issues 04 and 22.

SH-22: the data-gap tools (`set_weather_choice`, `attach_tariff`,
`apply_generated_profile`, `generate_load_profile`) write inside
`run_envelope.admitted_write`, as `update_component` does: an edit in the gap
before pickup refuses, and the chain settles only from the call's own
receipt. SH-04: an L1 ledger row names the failing rule that drove it.
"""
# ruff: noqa: F811 -- imported pytest fixtures

import threading

import pytest
from harness import runner as harness_runner, run_loops
from services import chat_tools, dirty_state, run_envelope
from services.library import common as library_common
from services.pypsa_service import PyPSAService
from tests.test_assistant_tasks_study_brief import (  # noqa: F401
    baseline, dispatch, edit, helpers_state, session, wait_until,
)
from tests.test_chat_tools_library import _put
from tests.test_harness_envelope import BLOCK, envelope_of, refusals, until  # noqa: F401
from tests.test_harness_envelope_atomic import gap, writer  # noqa: F401
from tests.test_harness_run_loops import (  # noqa: F401
    WITH_GAPS, Script, apply_from, call, energy_source, generate, go, granted, no_series, profile, rated, say, site,
    stopped)
from tests.test_harness_runner import detached, task_path  # noqa: F401

_names = library_common.names


def ctx():
    return PyPSAService.get_active_context()


def weather_step(project_id, n=2):
    return {"id": f"inputs.{n}", "tool": "set_weather_choice",
            "args": {"project_id": project_id, "source": "synthetic", "year": 2023}}


def test_each_data_gap_tool_has_its_own_atomic_row():
    assert set(run_envelope.GAP_TARGETS) | set(run_envelope.GAP_PREPARE) <= run_envelope.ATOMIC_TOOLS


@pytest.fixture
def own_settles(monkeypatch):
    """The calls the L1 turn settled from their own receipt."""
    seen = []
    original = run_envelope.settle_own

    def spy(task_path, call_id, ctx, before, receipts):
        seen.append((call_id, [dict(r) for r in receipts]))
        return original(task_path, call_id, ctx, before, receipts)
    monkeypatch.setattr(run_envelope, "settle_own", spy)
    return seen


# ── Stepwise runner path (R3) ───────────────────────────────────────────────


def test_stepwise_weather_fill_settles_from_its_own_receipt(granted, profile, site, gap, own_settles):
    """A weather choice is a setting, outside the network's identity: its own
    receipt settles it, and an unchanged identity adds no link."""
    project_id, _ = site
    profile(WITH_GAPS)
    task, path, _ = granted(steps=[weather_step(project_id)])
    fired = gap(lambda: None, tool="set_weather_choice")
    record = until(harness_runner.Runner("a"), path, task, lambda r: r["steps"][-1]["status"] != "pending")
    assert fired and record["steps"][-1]["status"] == "completed", record["steps"][-1]
    [(call_id, receipts)] = own_settles
    assert call_id.startswith("run-inputs.2-") and [r["call_id"] for r in receipts] == [call_id]
    assert receipts[0]["exact"] == run_envelope.identity(ctx())["exact"]
    assert envelope_of(path)["chain"] == [] and refusals(path) == []


def test_stepwise_weather_fill_refuses_an_edit_in_the_gap(granted, profile, site, gap):
    project_id, _ = site
    profile(WITH_GAPS)
    task, path, _ = granted(steps=[weather_step(project_id)])
    fired = gap(lambda: writer(lambda n: n.loads.__setitem__("p_set", 6.0)).join(), tool="set_weather_choice")
    record = until(harness_runner.Runner("a"), path, task, stopped)
    assert fired
    assert record["run"]["status"] == "paused" and record["run"]["reason"] == "envelope_drift", record["run"]
    assert envelope_of(path)["chain"] == [] and refusals(path) == ["drift"]


def test_stepwise_weather_fill_refuses_a_bare_plus_one_bump(granted, profile, site, gap):
    """R2: a fill expects +0 on the edit counter; a +1 alone (no value change) is drift."""
    project_id, _ = site
    profile(WITH_GAPS)
    task, path, _ = granted(steps=[weather_step(project_id)])

    fired = gap(lambda: writer(lambda network: None).join(), tool="set_weather_choice")  # the bump alone
    record = until(harness_runner.Runner("a"), path, task, stopped)
    assert fired
    assert record["run"]["status"] == "paused" and record["run"]["reason"] == "envelope_drift", record["run"]
    assert refusals(path) == ["drift"]


# ── L1 path (R3) ────────────────────────────────────────────────────────────


def test_l1_load_fill_settles_from_its_own_receipt_without_deadlock(granted, profile, site, rated, own_settles):
    """R1: generate_load_profile runs under the held locks; a timed join shows no deadlock."""
    project_id, _ = site
    profile(WITH_GAPS)
    no_series()
    task, path, _ = granted()
    script = Script(call(*generate(project_id)), apply_from(project_id), say("Attached."))
    start = run_envelope.identity(ctx())
    out = {}
    thread = threading.Thread(target=lambda: out.setdefault(
        "record", go(harness_runner.Runner("a", agent_provider=script), path, task)), daemon=True)
    thread.start()
    thread.join(timeout=120)
    assert not thread.is_alive(), "the L1 round did not finish: a lock held across the fill"
    run = out["record"]["run"]
    assert run["status"] == "completed", (run.get("reason"), run.get("loop"))
    call_id = "tu-apply_generated_profile-0"
    [receipt] = [r for c, rs in own_settles if c == call_id for r in rs if r["call_id"] == call_id]
    # R2: the fill moved the exact identity, and its link is its receipt, +0 on the counter.
    [link] = [link for link in envelope_of(path)["chain"] if link["after_change"] == call_id]
    assert link["exact"] == receipt["exact"] and link["exact"] != start["exact"]
    assert link["network_revision"] == receipt["network_revision"] == start["network_revision"]


# ── Settings fills: a tariff ref is fingerprinted ───────────────────────────


def test_a_tariff_fill_moves_the_exact_identity_and_links_from_its_own_receipt(granted, profile, site):
    """attach_tariff writes `solver_config.commercial.import_tariff_ref`, which the
    LP reads and `_network_input_fingerprint` hashes (via `goal_free_config`).
    The pickup, before the handler, expects +0 (no dispatch-seam bump); the
    handler's solver-config write bumps the counter itself
    (`routers/simulation.update_solver_config`, LC40-COUNTER-1). A fill moves
    `exact`, and its own receipt, +1 included, is the link."""
    profile(WITH_GAPS)
    _, path, _ = granted()
    target = ctx()
    with target.mutation_lock:
        before, reason = run_envelope.chain_head(path, target)
        assert reason is None
        start = run_envelope.identity(target)
        config = target.solver_state["solver_config"]
        config.commercial = {**(config.commercial or {}),
                             "import_tariff_ref": {"kind": "tariff", "id": "grid-import", "version": 1}}
        dirty_state.bump_revision(target)  # as update_solver_config does at its sink
        receipt = {"call_id": "tariff-call", **run_envelope.identity(target)}
    assert receipt["exact"] != start["exact"] and receipt["network_revision"] == start["network_revision"] + 1
    run_envelope.settle_own(path, "tariff-call", target, before, [receipt])
    [link] = envelope_of(path)["chain"]
    assert link == {"after_change": "tariff-call", "fingerprint": receipt["fingerprint"], "exact": receipt["exact"],
                    "network_revision": receipt["network_revision"]}


def test_stepwise_real_tariff_fill_runs_inside_the_write_and_links_at_plus_one(client, granted, profile, site,
                                                                               own_settles, monkeypatch):
    """GK B (20): the real attach_tariff (Library resolve, solver-config write
    under the solver-state RLock) inside admitted_write: it completes under a
    timed join, the pickup's +0 admits it, `exact` moves, and its own
    receipt, the handler's +1 included, is the link."""
    # Exactly one held tariff, so the fill is admitted: the shipped common
    # tariffs would also be listed at the grant (then the user chooses).
    monkeypatch.setattr(library_common, "names", lambda kind: [] if kind == "tariff" else _names(kind))
    _put(client)  # a tariff the project's organisation holds, listed at the grant
    network = ctx().network
    network.add("Bus", "grid")
    network.add("Link", "import", bus0="grid", bus1="B1", p_nom=100.0)  # the point of connection
    chat_tools.DISPATCHERS["update_solver_config"](partial={"commercial": {
        "poc_link": "import", "timezone": "Europe/Berlin"}})
    profile({**BLOCK, "gaps": [{"tool": "attach_tariff", "source": {"kind": "library_tariff"},
                                "target": {"component": "Settings", "name": "Commercial",
                                           "attr": "import_tariff_ref"}}]})
    task, path, _ = granted(steps=[{"id": "inputs.2", "tool": "attach_tariff", "args": {"name": "nl"}}])
    start = run_envelope.identity(ctx())
    out = {}
    thread = threading.Thread(target=lambda: out.setdefault("record", until(
        harness_runner.Runner("a"), path, task, lambda r: r["steps"][-1]["status"] != "pending")), daemon=True)
    thread.start()
    thread.join(timeout=120)
    assert not thread.is_alive(), "the tariff fill did not finish: a lock held across its write"
    step = out["record"]["steps"][-1]
    assert step["status"] == "completed", (step, envelope_of(path)["audit"])
    assert refusals(path) == []  # the pickup expected +0 and admitted it
    assert ctx().solver_state["solver_config"].commercial["import_tariff_ref"]["id"] == "nl"
    [(call_id, receipts)] = own_settles
    [receipt] = [r for r in receipts if r["call_id"] == call_id]
    [link] = envelope_of(path)["chain"]
    assert receipt["exact"] != start["exact"] and receipt["network_revision"] == start["network_revision"] + 1
    assert link == {"after_change": call_id, "fingerprint": receipt["fingerprint"], "exact": receipt["exact"],
                    "network_revision": receipt["network_revision"]}


# ── SH-04: the ledger row names its rule ────────────────────────────────────


def test_l1_ledger_row_names_the_one_failing_rule(granted, profile, site, rated):
    project_id, _ = site
    profile(WITH_GAPS)
    no_series()
    task, path, _ = granted()
    script = Script(call(*generate(project_id)), apply_from(project_id), say("Attached."))
    record = go(harness_runner.Runner("a", agent_provider=script), path, task)
    [row] = record["run"]["ledger"]
    assert row["rule"] == "in.loads.series", row


RULES = {"a": {"tunable": ["Generator.p_nom_max"]}, "b": {"tunable": ["StorageUnit.max_hours"]}, "c": {}}


@pytest.mark.parametrize("failing,target,expected", [
    (["c"], ("Load", "p_set"), "c"),  # no rule tunes the target: the only failing rule
    (["a", "b"], ("StorageUnit", "max_hours"), "b"),  # the one failing rule whose tunable names the target
    (["a", "c"], ("Load", "p_set"), None),  # ambiguous: never guessed
    ([], ("Load", "p_set"), None),
])
def test_the_driving_rule(failing, target, expected):
    assert run_loops._driving_rule(target, failing, RULES) == expected
