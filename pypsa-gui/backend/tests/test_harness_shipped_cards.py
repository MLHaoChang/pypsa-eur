# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
# ruff: noqa: F811 -- the `baseline` and `queue` fixtures are imported by name
"""Study harness 14 S-B2c (GK B (60) Q1 = A, P1-P3, P7): the shipped cases' pick
card and approval card. More LP cases than the fork solves left wait on a pick
card (ticks, the change-set path); the pick is checked and written under the
run's lease and freezes the step anew; the approval card binds that record."""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from harness import change_set, run_shipped, run_shipped_engine as E
from harness.session import PendingConfirmation
from services import run_actions
from tests.test_harness_run_shipped_engine import (  # noqa: F401
    INSTANCE,
    _cases,
    _children,
    _edit_cases,
    _read,
    _start,
    _task,
    baseline,
    queue,
)

LP = ["low_price", "cannibalised", "cheap_battery"]
FINANCE = ["ppa_price_minus_10", "merchant_heavy", "longer_ppa"]
NOT_RUN = ["tight_connection", "faster_degradation"]
K = run_actions.SHIPPED_KIND


def _pick_task(tmp_path, pid, solves=2):
    """The step frozen at stage entry against the granted envelope (`path`)."""
    path = _task(tmp_path, pid, picked=None, solves=solves)
    task = _read(path)
    step = task["steps"][0]
    for key in ("frozen", "card", "unfrozen"):
        step.pop(key, None)
    task["run"]["explore"] = {}
    run_actions.fill(task["steps"], run_actions.stage_frozen(task, K, path=path), K)
    path.write_text(json.dumps(task))
    return path


def _step(path):
    return _read(path)["steps"][0]


def _pick(path, ticked, digest=None, instance=INSTANCE):
    card = run_actions.pick_card(_step(path))
    return E.pick(path, "explore.1", ticked, digest or card["digest"], instance)


def _kind(fn):
    with pytest.raises(run_shipped.ShippedError) as err:
        fn()
    return err.value.kind


# ── the pick card ──────────────────────────────────────────────────────────────


def test_more_lp_cases_than_the_solves_left_wait_on_a_pick_card(baseline, tmp_path):
    _, pid, _ = baseline
    step = _step(_pick_task(tmp_path, pid, solves=2))
    assert step["unfrozen"]["state"] == "shipped_cases_over_budget"
    assert "frozen" not in step
    shown = step["unfrozen"]["pick"]
    assert shown["cap"] == 2 and shown["total"] == 8
    assert [c["id"] for c in shown["cases"]] == [
        c["id"]
        for c in json.loads(
            (E._TEMPLATES / "eh_ipp_hybrid" / "cases.json").read_text()
        )["cases"]
    ]
    card = run_actions.pick_card(step)
    assert [i["tool_use_id"] for i in card["items"]] == LP  # only LP cases tick
    assert card["pick_max"] == 2
    assert card["digest"] == run_actions.digest(shown)
    says = card["requirements"]["says"]
    for c in shown["cases"]:
        if c["id"] not in LP:
            assert c["label"] in says  # every other case is named on the card
    assert "not run (" in says and "read not evaluated" in says


def test_the_cap_is_held_to_the_solves_the_grant_has_left(baseline, tmp_path):
    _, pid, _ = baseline
    assert _step(_pick_task(tmp_path, pid, solves=1))["unfrozen"]["pick"]["cap"] == 1


def test_enough_solves_left_need_no_pick(baseline, tmp_path):
    _, pid, _ = baseline
    step = _step(_pick_task(tmp_path, pid, solves=3))
    assert "unfrozen" not in step and run_actions.pick_card(step) is None
    assert step["frozen"]["run"] == [
        i for i in step["frozen"]["run"] if i in LP + FINANCE
    ] and set(step["frozen"]["run"]) == set(LP + FINANCE)


def test_no_solve_left_runs_the_finance_cases_and_no_lp_case(baseline, tmp_path, queue):
    _, pid, _ = baseline
    path = _pick_task(tmp_path, pid, solves=0)
    step = _step(path)
    assert run_actions.pick_card(step) is None
    assert step["frozen"]["run"] == FINANCE and step["frozen"]["solves"] == 0
    _start(path, pid)
    cases = _cases(path)
    assert {n for n in FINANCE if cases[n]["status"] == "not_evaluated"} == set(FINANCE)
    for name in LP:
        assert cases[name]["status"] == "not_run"
        assert cases[name]["reason"] == E.NO_SOLVE
    assert _children(pid) == {} and queue == []


def test_a_frozen_record_has_no_pick_card(baseline, tmp_path):
    _, pid, _ = baseline
    step = _step(_task(tmp_path, pid))
    assert "frozen" in step and run_actions.pick_card(step) is None


# ── the pick, checked and written under the lease (P1) ────────────────────────


def test_a_pick_freezes_the_step_and_its_approval_card_binds_it(baseline, tmp_path):
    _, pid, _ = baseline
    path = _pick_task(tmp_path, pid)
    digest = _pick(path, ["cheap_battery", "low_price"])
    task = _read(path)
    step = task["steps"][0]
    assert task["run"]["explore"]["shipped_pick"] == ["cheap_battery", "low_price"]
    assert "unfrozen" not in step and step["frozen"]["digest"] == digest
    assert step["frozen"]["run"] == [
        i
        for i in step["frozen"]["run"]
        if i in ("low_price", "cheap_battery", *FINANCE)
    ]
    assert step["frozen"]["solves"] == 2
    card = run_actions.approval_card(step)
    assert card["digest"] == step["frozen"]["digest"] == digest  # P7
    assert run_actions.pick_card(step) is None


@pytest.mark.parametrize(
    "ticked, kind",
    [
        (None, "shipped_cases_over_budget"),
        ([], "shipped_pick_empty"),
        (["low_price", "low_price"], "shipped_pick_duplicate"),
        (["nope"], "shipped_pick_unknown"),
        (["tight_connection"], "shipped_case_not_runnable"),
        (["merchant_heavy"], "shipped_pick_finance"),
        (LP, "shipped_cases_over_budget"),
        ("low_price", "shipped_pick_invalid"),
    ],
)
def test_each_refused_pick_writes_nothing(baseline, tmp_path, ticked, kind):
    _, pid, _ = baseline
    path = _pick_task(tmp_path, pid)
    before = _read(path)
    assert _kind(lambda: _pick(path, ticked)) == kind
    assert _read(path) == before


def test_a_pick_from_another_card_or_runner_or_a_second_pick_is_closed(
    baseline, tmp_path
):
    _, pid, _ = baseline
    path = _pick_task(tmp_path, pid)
    before = _read(path)
    assert (
        _kind(lambda: _pick(path, ["low_price"], digest="0" * 64))
        == "shipped_pick_closed"
    )
    assert (
        _kind(lambda: _pick(path, ["low_price"], instance="other"))
        == "shipped_pick_closed"
    )
    assert _read(path) == before
    card = run_actions.pick_card(_step(path))
    _pick(path, ["low_price"])

    def again():
        return E.pick(path, "explore.1", ["cannibalised"], card["digest"], INSTANCE)

    assert _kind(again) == "shipped_pick_closed"
    assert _read(path)["run"]["explore"]["shipped_pick"] == ["low_price"]


def test_a_cases_edit_after_the_pick_card_refuses_the_pick(baseline, tmp_path):
    _, pid, root = baseline
    path = _pick_task(tmp_path, pid)
    before = _read(path)
    _edit_cases(root)
    assert _kind(lambda: _pick(path, ["low_price"])) == "shipped_cases_changed"
    assert _read(path) == before


def test_a_cases_edit_after_the_pick_refuses_before_any_fork(baseline, tmp_path, queue):
    _, pid, root = baseline
    path = _pick_task(tmp_path, pid)
    _pick(path, ["low_price"])
    _edit_cases(root)
    _start(path, pid)
    assert "shipped_cases_changed" in _cases(path)["low_price"]["reason"]
    assert _children(pid) == {} and queue == []


def test_a_pick_after_approval_changes_the_digest_and_the_approval_refuses(
    baseline, tmp_path, queue
):
    _, pid, _ = baseline
    (tmp_path / "a").mkdir()
    first = _pick_task(tmp_path / "a", pid)
    approved = _pick(first, ["low_price"])
    path = _pick_task(tmp_path, pid)
    _pick(path, ["cannibalised"])
    assert _step(path)["frozen"]["digest"] != approved
    assert _start(path, pid, approved=approved) == "shipped_card"
    assert _children(pid) == {} and queue == []


# ── the approval card (P7) ─────────────────────────────────────────────────────


def test_the_approval_card_shows_every_case_and_change_in_file_order(
    baseline, tmp_path
):
    _, pid, _ = baseline
    path = _pick_task(tmp_path, pid)
    _pick(path, ["low_price", "cheap_battery"])
    step = _step(path)
    frozen = step["frozen"]
    rows = run_actions.approval_card(step)["rows"]
    heads = [
        r["field"]
        for r in rows
        if r["field"].count(".") == 1 and r["field"].startswith("case.")
    ]
    assert heads == [f"case.{c['id']}" for c in frozen["cases"]]  # all 8, file order
    notes = {r["field"]: r["note"] for r in rows}
    assert "forks the project and solves once" in notes["case.low_price"]
    assert "not run: not picked" in notes["case.cannibalised"]
    assert "read not evaluated" in notes["case.merchant_heavy"]
    for name in NOT_RUN:
        why = next(c["why"] for c in frozen["cases"] if c["id"] == name)
        assert notes[f"case.{name}"].endswith(f"not run: {why}")
    for case in frozen["cases"]:  # every change, old -> new
        for n, change in enumerate(case["changes"]):
            assert "→" in notes[f"case.{case['id']}.{n}"]
    assert notes["case.ppa_price_minus_10.0"].endswith("65.0 → 58.5 EUR/MWh")
    assert notes["case.longer_ppa.0"].endswith("10 → 15 years")
    assert notes["case.cheap_battery.0"].endswith("EUR/MW")
    assert notes["case.cheap_battery.1"].endswith("EUR/MWh")
    assert notes["case.tight_connection.0"].endswith("70.0 → 55.0 MW")
    ref = notes["case.low_price.0"]
    assert "series ipp_da_base (file " in ref and "→ ipp_da_low (file " in ref
    assert frozen["series"]["ipp_da_low"][:12] in ref
    assert "Library" in ref
    assert notes["solves"] == "Reserves 2 solves"
    assert frozen["cases_digest"][:12] in notes["source"]


def test_the_grid_code_card_is_unchanged_by_the_shipped_branch():
    assert (
        run_actions.approval_card(
            {"tool": run_actions.SHIPPED, "frozen": {"digest": "d"}}
        )
        is None
    )


# ── ticks (P2) ─────────────────────────────────────────────────────────────────


def _pc(tool, args):
    return PendingConfirmation(
        token="t",
        tool_name=tool,
        args=args,
        safety_tier="write",
        created_at=0.0,
        expires_at=1e12,
    )


def test_the_pick_card_takes_ticks_within_its_cases_and_cap():
    pc = _pc(run_actions.SHIPPED, {"digest": "d", "tool_use_ids": LP, "pick_max": 2})
    assert change_set.validate_ticks(pc, "approve", ["low_price"]) == ["low_price"]
    assert change_set.validate_ticks(pc, "approve", LP[:2]) == LP[:2]
    for bad in (LP, [], ["merchant_heavy"], ["low_price", "low_price"], "low_price"):
        with pytest.raises(HTTPException) as err:
            change_set.validate_ticks(pc, "approve", bad)
        assert err.value.status_code == 400


@pytest.mark.parametrize(
    "tool, args",
    [
        (run_actions.SHIPPED, {"digest": "d"}),  # the approval card
        (run_actions.SHIPPED, {"digest": "d", "tool_use_ids": LP, "pick_max": "2"}),
        (run_actions.CAMPUS, {"digest": "d", "tool_use_ids": LP, "pick_max": 2}),
    ],
)
def test_no_other_native_card_takes_ticks(tool, args):
    with pytest.raises(HTTPException) as err:
        change_set.validate_ticks(_pc(tool, args), "approve", ["low_price"])
    assert err.value.status_code == 400


# ── the runner's pick card ─────────────────────────────────────────────────────


class _PickSession:
    """The run's session, as far as the pick card uses it."""

    session_id = "run:t"

    def __init__(self, decision, ticked):
        import threading

        self.decision, self.go, self.withdrawn = decision, threading.Event(), []
        self.confirmation_ticks = {"tok": ticked}

    def issue_confirmation(self, *, tool_name, args, safety_tier):
        self.issued = (tool_name, args, safety_tier)
        from types import SimpleNamespace

        return SimpleNamespace(token="tok")

    def wait_for_decision(self, token):
        self.go.wait(10)
        return self.decision

    def withdraw_confirmation(self, token):
        self.withdrawn.append(token)


def _runner_pick(path, monkeypatch, decision, ticked):
    from harness import approvals, runner

    monkeypatch.setattr(approvals, "asked", lambda *a, **k: True)
    monkeypatch.setattr(approvals, "decided", lambda *a, **k: True)
    r, session = runner.Runner(instance=INSTANCE), _PickSession(decision, ticked)
    task = _read(path)
    assert r._native_card(path, None, session, task, task["steps"][0]) == "waiting"
    live = r.live_card(task["task_id"])
    session.go.set()
    r._inflight[task["task_id"]][1].join(10)
    return live, session


def test_the_runner_shows_the_pick_card_and_writes_the_approved_pick(
    baseline, tmp_path, monkeypatch
):
    _, pid, _ = baseline
    path = _pick_task(tmp_path, pid)
    card = run_actions.pick_card(_step(path))
    live, session = _runner_pick(path, monkeypatch, "approve", ["cannibalised"])
    assert live["change_set"] == card and "edit_card" not in live
    assert session.issued[1] == {
        "digest": card["digest"],
        "tool_use_ids": LP,
        "pick_max": 2,
    }
    task = _read(path)
    assert task["run"]["explore"]["shipped_pick"] == ["cannibalised"]
    assert task["steps"][0]["status"] == "pending" and "frozen" in task["steps"][0]
    assert session.confirmation_ticks == {}  # the ticks are taken once


@pytest.mark.parametrize(
    "decision, ticked, reason",
    [
        ("deny", None, "confirmation_denied"),
        ("approve", ["merchant_heavy"], "shipped_pick_finance"),
        ("approve", None, "shipped_cases_over_budget"),
    ],
)
def test_a_denied_or_refused_pick_stops_the_step_and_writes_no_pick(
    baseline, tmp_path, monkeypatch, decision, ticked, reason
):
    _, pid, _ = baseline
    path = _pick_task(tmp_path, pid)
    _runner_pick(path, monkeypatch, decision, ticked)
    task = _read(path)
    assert task["steps"][0]["status"] == "failed"
    assert task["steps"][0]["error"].startswith(reason)
    assert "shipped_pick" not in task["run"]["explore"]


def test_a_closed_pick_leaves_the_step_as_it_is(baseline, tmp_path, monkeypatch):
    _, pid, _ = baseline
    path = _pick_task(tmp_path, pid)
    task = _read(path)
    task["run"]["runner"]["lease_owner"] = "someone-else"
    path.write_text(json.dumps(task))
    _runner_pick(path, monkeypatch, "approve", ["low_price"])
    after = _read(path)
    assert after["steps"][0]["status"] == "pending" and "unfrozen" in after["steps"][0]
