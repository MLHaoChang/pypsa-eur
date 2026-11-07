# ruff: noqa: F401, F811 -- imported shared fixtures.
"""Study harness 07 B3a (GK B (32) L2, L2a-L2c, L3, L9): a step-by-step run's
engine loop. A run that never had a grant pauses at the engine step's own
card (COP-1); its Continue reserves at most the max_solves the card showed,
on the run's own solve count, and writes nothing to the envelope. Any other
envelope keeps `no_envelope`."""

import json

import pytest

from harness import run_loops, runner as harness_runner
from services import run_envelope, run_profiles
from tests.test_assistant_tasks_study_brief import baseline, helpers_state, session  # noqa: F401
from tests.test_harness_engine_loops import (  # noqa: F401
    engine_rule,
    loops,
    ok,
    stage_of,
    with_rules,
)
from tests.test_harness_envelope import envelope_of, granted, profile  # noqa: F401
from tests.test_harness_envelope_atomic import eh  # noqa: F401
from tests.test_harness_l2_loop import Never, infeasible  # noqa: F401
from tests.test_harness_run_loops import stopped
from tests.test_harness_runner import (
    Clicks,
    detached,
    fake_report_provider,
    settle,
    task_path,
)  # noqa: F401
from tests.test_harness_run_skeleton import site  # noqa: F401


def engine_steps(record):
    return [s for s in record["steps"] if s.get("engine")]


def stepwise(detached, site):
    task = detached()
    return task, task_path(site[1], task)


def at_engine_card(record):
    return (
        record["status"] == "paused_at_card"
        and record.get("pause", {}).get("card") == "result_gate"
        or (record["run"].get("waiting") or {}).get("ref") == "result_gate"
    )


def to_card(path, task):
    """Tick a step-by-step run to its engine card: every other card is clicked."""
    clicks = Clicks(refs={"solve", "solve.1", "report"})
    runner = harness_runner.Runner("a", agent_provider=Never(), decisions=clicks)
    record = settle(
        runner, path, task["task_id"], lambda r: at_engine_card(r) or stopped(r)
    )
    assert at_engine_card(record), record["run"]
    return record


def go_on(path, task):
    """The user's Continue at the engine card, then the run to its end."""
    clicks = Clicks()
    runner = harness_runner.Runner("a", agent_provider=Never(), decisions=clicks)
    return settle(runner, path, task["task_id"], stopped), clicks


def test_a_step_by_step_run_cards_its_engine_loop_and_runs_it_on_continue(
    ok, loops, detached, site, monkeypatch
):
    with_rules(monkeypatch, engine_rule(max_solves=4), solves=12)
    loops.finish_with(status="met", solves_used=3, probe_solves=1)
    task, path = stepwise(detached, site)
    before = envelope_of(path)
    record = to_card(path, task)
    [step] = engine_steps(record)
    assert step["status"] == "pending" and loops.calls == []
    # Each carded dispatch wrote the proposed envelope's own `not_granted` refusal.
    assert {a.get("reason") for a in envelope_of(path)["audit"]} == {"not_granted"}
    # The card shows what the loop may spend: 4, within 12 - 0 used - 2 overhead.
    assert step["card"] == {
        "tool": "run_margin_loop",
        "rule": "res.adequacy.margin",
        "max_solves": 4,
        "overhead": 2,
        "restore": "base",
        "target_lole_h": step["card"]["target_lole_h"],
    }
    view = json.loads(path.read_text())  # the stored step carries it for the plan view
    from services import assistant_tasks

    shown = [e for e in assistant_tasks._view(view)["steps"] if e["id"] == step["id"]]
    assert shown and shown[0]["card"]["max_solves"] == 4
    record, clicks = go_on(path, task)
    assert clicks.used[0] == "result_gate"  # the engine card's own Continue
    [(_, body)] = loops.calls
    assert body.max_solves == 4 and body.restore == "base"
    [step] = engine_steps(record)
    assert step["status"] == "completed" and step["reserved"] == {
        "call_id": step["attempt_id"],
        "reserved": 6,
        "solves": 5,
    }
    assert (
        record["run"]["budget"]["used"]["solves"] == 5
    )  # the loop's 3, its probe, the closing re-solve
    assert (
        stage_of(record)["status"] == "passed"
        and record["run"]["status"] == "completed"
    )
    # L2c: the card and its loop neither used nor granted the envelope. It
    # only gained the `not_granted` rows every step-by-step card appends.
    after = envelope_of(path)
    assert {k: v for k, v in after.items() if k != "audit"} == {
        k: v for k, v in before.items() if k != "audit"
    }
    assert after["audit"][: len(before["audit"])] == before["audit"]
    added = after["audit"][len(before["audit"]) :]
    assert added and all(
        a["event"] == "refused" and a["reason"] == "not_granted" for a in added
    )
    assert not any(
        a.get("tool") == "run_margin_loop" or "reserved" in a for a in after["audit"]
    )


def lower_budget(path, used):
    record = json.loads(path.read_text())
    harness_runner._budget(record)["used"]["solves"] = used
    path.write_text(json.dumps(record))


def test_a_budget_that_fell_after_the_card_reserves_the_lower_value(
    ok, loops, detached, site, monkeypatch
):
    """L3, and its L9 mutant (the uncapped rule max_solves reserved)."""
    with_rules(monkeypatch, engine_rule(max_solves=4), solves=12)
    task, path = stepwise(detached, site)
    to_card(path, task)
    lower_budget(path, 9)  # 12 - 9 - 2 overhead leaves 1
    record, _ = go_on(path, task)
    [(_, body)] = loops.calls
    assert body.max_solves == 1
    [step] = engine_steps(record)
    assert step["reserved"]["reserved"] == 3 and step["card"]["max_solves"] == 4


def test_no_solve_left_at_the_continue_is_budget_spent_with_no_call(
    ok, loops, detached, site, monkeypatch
):
    with_rules(monkeypatch, engine_rule(max_solves=4), solves=12)
    task, path = stepwise(detached, site)
    to_card(path, task)
    lower_budget(path, 10)
    record, _ = go_on(path, task)
    assert loops.calls == []
    assert (
        record["run"]["status"] == "needs_user"
        and stage_of(record)["reason"] == "budget_spent"
    )
    assert "reserved" not in engine_steps(record)[0]


def test_the_continue_never_reserves_more_than_the_card_showed(
    ok, loops, detached, site, monkeypatch
):
    """L3: a budget that grew after the card does not raise the reservation."""
    with_rules(monkeypatch, engine_rule(max_solves=4), solves=12)
    task, path = stepwise(detached, site)
    lower_budget(path, 8)  # the card shows 12 - 8 - 2 = 2
    record = to_card(path, task)
    assert engine_steps(record)[0]["card"]["max_solves"] == 2
    lower_budget(path, 0)
    go_on(path, task)
    [(_, body)] = loops.calls
    assert body.max_solves == 2


def test_a_granted_run_never_takes_the_card_branch(granted, ok, loops, monkeypatch):
    with_rules(monkeypatch, engine_rule())
    task, path, _ = granted()
    record = settle(
        harness_runner.Runner("a", agent_provider=Never()),
        path,
        task["task_id"],
        stopped,
    )
    [step] = engine_steps(record)
    assert "card" not in step and len(loops.calls) == 1


# ── L2a, L2b: the never-granted shape, unit by unit ─────────────────────────


def proposed(path, **change):
    record = envelope_of(path)
    record.update(change)
    run_envelope.path_for(path).write_text(json.dumps(record))


@pytest.mark.parametrize(
    "change",
    [
        {
            "state": "granted",
            "granted_at": "2026-11-07T00:00:00+00:00",
            "expires_at": "2099-01-01T00:00:00+00:00",
        },
        {
            "state": "revoked",
            "granted_at": "2026-11-07T00:00:00+00:00",
            "expires_at": "2099-01-01T00:00:00+00:00",
        },
        {
            "state": "expired",
            "granted_at": "2026-11-07T00:00:00+00:00",
            "expires_at": "2026-11-07T01:00:00+00:00",
        },
        {
            "state": "exhausted",
            "granted_at": "2026-11-07T00:00:00+00:00",
            "expires_at": "2099-01-01T00:00:00+00:00",
        },
        {"granted_at": "2026-11-07T00:00:00+00:00"},
        {"audit": [{"event": "granted"}]},
        {"audit": [{"event": "used", "tool": "x", "call_id": "c"}]},
        {
            "audit": [
                {"event": "refused", "tool": "x", "call_id": "c", "reason": "drift"}
            ]
        },
        {"audit": [{"event": "engine", "call_id": "c", "reserved": 2}]},
        {"version": 99},
    ],
    ids=[
        "granted",
        "revoked",
        "expired",
        "exhausted",
        "granted_at",
        "granted_row",
        "used_row",
        "refused_row",
        "spent",
        "unreadable",
    ],
)
def test_any_envelope_but_the_never_granted_shape_keeps_no_envelope(
    detached, site, profile, change
):
    """L2a, and the L9 mutant (the card branch taken when any envelope id exists)."""
    task, path = stepwise(detached, site)
    record = json.loads(path.read_text())
    assert run_envelope.never_granted(path, record)
    proposed(path, **change)
    assert not run_envelope.never_granted(path, record)
    record["steps"].append(
        {
            "id": "result_gate.1",
            "tool": "run_margin_loop",
            "engine": "r",
            "status": "running",
            "card": {"max_solves": 2, "restore": "base"},
        }
    )
    path.write_text(json.dumps(record))
    assert run_envelope.reserve_carded(
        path, step_id="result_gate.1", call_id="a", overhead=2, restore="base"
    ) == (None, "no_envelope")


def test_a_missing_envelope_file_is_not_the_never_granted_shape(
    detached, site, profile
):
    task, path = stepwise(detached, site)
    run_envelope.path_for(path).unlink()
    assert not run_envelope.never_granted(path, json.loads(path.read_text()))


def test_an_unanswered_plan_review_gives_no_engine_card(
    ok, detached, site, monkeypatch
):
    """L2b, and its L9 mutant: the intake stage not passed, no card."""
    with_rules(monkeypatch, engine_rule())
    task, path = stepwise(detached, site)
    record = json.loads(path.read_text())
    record["run"]["stages"]["intake"] = {"status": "pending", "attempts": 0}
    record["steps"].append(
        {
            "id": "result_gate.1",
            "tool": "run_margin_loop",
            "engine": "res.adequacy.margin",
            "args": {},
            "status": "pending",
        }
    )
    assert not run_envelope.never_granted(path, record)
    assert not run_loops.card_engine(path, record, run_profiles.resolve("eh_site@1"))
    assert "card" not in record["steps"][-1] and record["status"] != "paused_at_card"
    record["run"]["stages"]["intake"] = {"status": "passed"}
    assert run_loops.card_engine(path, record, run_profiles.resolve("eh_site@1"))


def test_restore_final_is_never_reserved_without_a_grant(detached, site, profile):
    task, path = stepwise(detached, site)
    record = json.loads(path.read_text())
    record["steps"].append(
        {
            "id": "result_gate.1",
            "tool": "run_margin_loop",
            "engine": "r",
            "status": "running",
            "card": {"max_solves": 2, "restore": "final"},
        }
    )
    path.write_text(json.dumps(record))
    assert run_envelope.reserve_carded(
        path, step_id="result_gate.1", call_id="a", overhead=2, restore="final"
    ) == (None, "restore_uncovered")
    assert run_envelope.reserve_carded(
        path, step_id="result_gate.1", call_id="a", overhead=2, restore="base"
    ) == (None, "restore_uncovered")


def test_a_step_with_no_card_is_never_reserved(detached, site, profile):
    task, path = stepwise(detached, site)
    record = json.loads(path.read_text())
    record["steps"].append(
        {
            "id": "result_gate.1",
            "tool": "run_margin_loop",
            "engine": "r",
            "status": "running",
        }
    )
    path.write_text(json.dumps(record))
    assert run_envelope.reserve_carded(
        path, step_id="result_gate.1", call_id="a", overhead=2, restore="base"
    ) == (None, "no_envelope")


def test_a_settled_reservation_settles_once():
    task = {
        "run": {
            "profile": "eh_site@1",
            "budget": {
                "limit": {"cost_eur": 1.0},
                "used": {"solves": 7, "cost_eur": 0.0, "unmetered": False},
            },
        }
    }
    step = {"reserved": {"call_id": "a", "reserved": 6}}
    assert run_envelope.settle_carded(task, step, 2)
    assert (
        task["run"]["budget"]["used"]["solves"] == 3 and step["reserved"]["solves"] == 2
    )
    assert not run_envelope.settle_carded(task, step, 0)
    assert task["run"]["budget"]["used"]["solves"] == 3
    other = {"reserved": {"call_id": "b", "reserved": 4}}
    assert run_envelope.settle_carded(
        task, other, None
    )  # unknown: the whole reservation stays spent
    assert task["run"]["budget"]["used"]["solves"] == 3
