# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
# ruff: noqa: F811  (pytest fixtures imported from sibling test modules)
"""Study harness 36 (GK B (60) plan GO; Q1, Q2 (a), A1-A5, B1, O1-O6, 36-K1,
K2): eh_site@4 runs the investment case at the head of the report stage (after
the solve, the result gate and its loop), reserves its baseline's solves at
admission, reads the stored case's figures in the hand-off, and a case that
does not end done leaves the investment chapter a listed gap with its typed
kind, never a missing chapter."""

from __future__ import annotations

import contextvars
import json
import os
import threading
import time
from datetime import datetime, timedelta, UTC
from types import SimpleNamespace

import pytest
from harness import run_handoff, run_ic
from services import assistant_tasks, ic_counterfactual, run_envelope, run_profiles
from services.reports import chapters as C
from services.reports import evidence
from services.reports.evidence import SECTION_TITLES, EvidenceSection
from services.results import baseline as B
from tests.test_harness_run_eh import PROJECT, ctx_for

REF = "eh_site@4"
STEP = "report.1"
TICKET = ("t1", STEP, "a1")
HOLD: dict = {}
TITLE = SECTION_TITLES[C.INVESTMENT]


# ── the profile (L8, A4, O1) ─────────────────────────────────────────────────


def _plain(ref):
    return json.loads(json.dumps(run_profiles.PROFILES[ref]))


def test_v4_differs_from_v3_only_by_the_case_its_grant_its_budget_and_its_kpis():
    v3, v4 = _plain("eh_site@3"), _plain(REF)
    assert (v3.pop("version"), v4.pop("version")) == (3, 4)
    report = v4["stages"].pop("report")
    assert report["actions"][0] == {
        "tool": "run_investment_case",
        "args": {},
        "on_end": "chapter_gap",
    }
    assert (
        report["actions"][1:] == v3["stages"].pop("report")["actions"]
    )  # then the report
    assert v4["envelope"].pop("runs") == [
        {"tool": "solve_queue_enqueue"},
        {"tool": "run_investment_case", "baseline_solves": 2},
        {"tool": "generate_report"},
    ]
    assert v3["envelope"].pop("runs") == [
        {"tool": "solve_queue_enqueue"},
        {"tool": "generate_report"},
    ]
    assert (
        v3["envelope"]["budget"].pop("solves"),
        v4["envelope"]["budget"].pop("solves"),
    ) == (6, 8)
    kpis = v4.pop("kpis")
    assert kpis[: len(v3["kpis"])] == v3["kpis"]
    assert [k["key"] for k in kpis[len(v3.pop("kpis")) :]] == [
        "npv_at_wacc",
        "post_tax_irr",
        "pre_tax_irr",
    ]
    assert v4 == v3  # nothing else moved
    assert v4["chapters"] == ["sizing", "mc", "investment_case"]


def test_the_worst_case_of_solves_fits_the_v4_budget():
    """A4: the queued solve, the coupling loop's worst case and the case's
    baseline fit in budget.solves; v3's budget is what it was."""
    v4 = run_profiles.resolve(REF)
    queued = sum(
        1
        for a in v4["stages"]["solve"]["actions"]
        if a["tool"] == "solve_queue_enqueue"
    )
    loop = max(
        r["engine"]["max_solves"]
        for r in v4["stages"]["result_gate"]["rules"]
        if r.get("engine")
    )
    assert (queued, loop) == (1, 3)
    assert (
        queued + loop + run_envelope.CASE_BASELINE_SOLVES
        <= v4["envelope"]["budget"]["solves"]
        == 8
    )
    assert run_profiles.resolve("eh_site@3")["envelope"]["budget"]["solves"] == 6


def test_the_baseline_runs_at_most_the_reserved_solves(monkeypatch):
    """A1: the most LPs the case's baseline can run (the island-relaxed retry
    included) is the bound reserved; a third attempt would fail here."""
    from services.pypsa_service import PyPSAService

    calls = []

    def solve(conf, base, *a, **k):
        calls.append(conf.island_constraints)
        return "infeasible", "infeasible"

    monkeypatch.setattr(B, "baseline_network", lambda n, plan, supplies: object())
    monkeypatch.setattr(B, "_CACHE", {})
    monkeypatch.setattr(
        PyPSAService, "solve_target_context", staticmethod(lambda: None), raising=False
    )
    plan = B.BaselinePlan(removed=(), kept=(("Generator", "genset_1"),), flags=())
    cfg = SimpleNamespace(island_constraints=True)
    try:
        B.solve_baseline(SimpleNamespace(), cfg, plan, solve=solve)
    except B.BaselineRefused:
        pass
    assert len(calls) == run_envelope.CASE_BASELINE_SOLVES == 2
    assert calls == [True, False]


def test_every_ic_kpi_names_a_reader_of_the_stored_case():
    """36-K1: each figure is one a stored-case reader already returns."""
    for kpi in run_profiles.EH_SITE_IC_KPIS:
        reader, _, _ = run_handoff.IC_KPIS[kpi["key"]]
        assert callable(getattr(ic_counterfactual, reader))
        assert kpi["source"] == run_handoff.IC_SOURCE


def test_the_step_carries_on_end_and_older_versions_and_ipp_do_not():
    steps = run_profiles.materialise(run_profiles.resolve(REF), "report", "p")
    assert steps[0]["id"] == STEP and steps[0]["tool"] == run_ic.TOOL
    assert steps[0]["on_end"] == "chapter_gap"
    for ref in ("eh_site@1", "eh_site@2", "eh_site@3", "eh_ipp_hybrid@1"):
        profile = run_profiles.resolve(ref)
        for stage in profile["stages"]:
            assert not any(
                "on_end" in s for s in run_profiles.materialise(profile, stage, "p")
            )


@pytest.mark.parametrize(
    "stage, action",
    [
        ("report", {"tool": "run_investment_case", "args": {}, "on_end": "skip"}),
        (
            "report",
            {"tool": "solve_queue_enqueue", "args": {}, "on_end": "chapter_gap"},
        ),
        ("solve", {"tool": "run_investment_case", "args": {}, "on_end": "chapter_gap"}),
    ],
)
def test_on_end_is_the_report_stage_case_only(stage, action):
    """O1: any other on_end is refused at profile load, by its own message."""
    profile = _plain(REF)
    profile["stages"][stage] = {"actions": [action], "rules": []}
    with pytest.raises(run_profiles.ProfileError, match="on_end is chapter_gap"):
        run_profiles.load(profile)


@pytest.mark.parametrize(
    "action",
    [
        {"tool": "run_investment_case", "on_end": "chapter_gap"},
        {"tool": "run_investment_case", "args": [], "on_end": "chapter_gap"},
        {"tool": "run_investment_case", "args": {}, "on_end": "chapter_gap", "x": 1},
    ],
    ids=["no-args", "args-not-a-dict", "another-key"],
)
def test_the_case_action_has_tool_args_and_on_end_only(action):
    """O1: on_end is the one key a report-stage case adds to tool and args."""
    profile = _plain(REF)
    profile["stages"]["report"]["actions"][0] = action
    with pytest.raises(
        run_profiles.ProfileError, match="each action has tool and args"
    ):
        run_profiles.load(profile)


@pytest.mark.parametrize(
    "entry",
    [
        {"tool": "run_investment_case", "baseline_solves": 1},
        {"tool": "run_investment_case", "baseline_solves": 3},
        {"tool": "run_investment_case", "baseline_solves": True},
        {"tool": "solve_queue_enqueue", "baseline_solves": 2},
        {"tool": "run_investment_case", "baseline_solves": 2.0},
    ],
)
def test_a_grant_names_the_case_baseline_at_the_bound_only(entry):
    with pytest.raises(run_envelope.EnvelopeError):
        run_envelope._check_run(entry)


def test_the_case_grant_entry_is_accepted():
    entry = {"tool": "run_investment_case", "baseline_solves": 2}
    assert run_envelope._check_run(entry) == entry


@pytest.mark.parametrize("ref", ["eh_site@2", "eh_site@3", REF])
def test_a_run_started_at_each_version_runs_that_versions_profile(ref):
    """Q4: an older version's run keeps its own profile."""
    brief = {"objective": "Size BESS for the site", "cards": ["capacity"]}
    checked, _ = assistant_tasks._check_run(
        {"profile": ref, "mode": "stepwise"}, [], brief
    )
    assert checked["version"] == int(ref[-1])
    tools = [
        s["tool"]
        for st in checked["stages"]
        for s in run_profiles.materialise(checked, st, "p-1")
    ]
    assert (run_ic.TOOL in tools) is (ref == REF)


# ── admission: the baseline reserved at the bound (A2, A3) ───────────────────


def _case_refusal(monkeypatch, *, runs, kept=True):
    from services.results import finance_case

    plan = B.BaselinePlan(
        removed=(), kept=(("Generator", "genset_1"),) if kept else (), flags=()
    )
    monkeypatch.setattr(finance_case, "baseline_plan", lambda n, cfg, owner=None: plan)
    monkeypatch.setattr(run_envelope, "_standing", lambda *a, **k: None)
    monkeypatch.setattr(run_envelope, "_names_another_project", lambda *a: False)
    note = {"call_id": "c1"}
    reason = run_envelope._refusal(
        SimpleNamespace(run_step_call="c1"),
        {"runs": runs, "edits": []},
        {},
        run_ic.TOOL,
        {},
        "write",
        False,
        None,
        SimpleNamespace(network=object(), solver_state={"solver_config": object()}),
        note,
    )
    return reason, note


def test_a_grant_with_the_case_baseline_admits_it_with_its_solves(monkeypatch):
    reason, note = _case_refusal(
        monkeypatch, runs=[{"tool": run_ic.TOOL, "baseline_solves": 2}]
    )
    assert reason != "baseline_solve" and note["case_solves"] == 2


def test_a_grant_without_it_keeps_the_baseline_refusal(monkeypatch):
    """A3: eh_ipp_hybrid@1's grant (no baseline_solves) refuses as (53) B2."""
    ipp = run_profiles.resolve("eh_ipp_hybrid@1")["envelope"]["runs"]
    assert {"tool": run_ic.TOOL} in ipp
    reason, note = _case_refusal(monkeypatch, runs=ipp)
    assert reason == "baseline_solve" and "case_solves" not in note


def test_a_case_with_no_baseline_reserves_nothing(monkeypatch):
    reason, note = _case_refusal(
        monkeypatch, runs=[{"tool": run_ic.TOOL, "baseline_solves": 2}], kept=False
    )
    assert reason != "baseline_solve" and "case_solves" not in note
    assert (
        run_envelope.case_budget_refusal({"audit": [], "budget": {"solves": 0}}, note)
        is None
    )


def _envelope(used):
    audit = [
        {"event": "used", "tool": "solve_queue_enqueue", "call_id": f"s{i}"}
        for i in range(used)
    ]
    return {"audit": audit, "budget": {"solves": 8}}


@pytest.mark.parametrize(
    "used, refused", [(5, False), (6, False), (7, True), (8, True)]
)
def test_the_baseline_runs_only_when_its_solves_fit(used, refused):
    """A2: exactly 2 left runs it; 1 left is `ic_budget` (no LP runs)."""
    got = run_envelope.case_budget_refusal(_envelope(used), {"case_solves": 2})
    assert got == (run_envelope.IC_BUDGET_KIND if refused else None)


def test_a_reserved_case_counts_its_solves_on_the_run():
    envelope = _envelope(1)
    envelope["audit"].append(
        {
            "event": "used",
            "tool": run_ic.TOOL,
            "call_id": "c",
            "reserved": 2,
            "solves": 2,
        }
    )
    assert run_envelope.solves_used(envelope) == 3


def _reserved_task(tmp_path, monkeypatch, audit):
    path = tmp_path / "task.json"
    path.write_text("{}")
    monkeypatch.setattr(assistant_tasks, "_read", lambda task_id: (path, {}))
    monkeypatch.setattr(run_envelope, "_load", lambda p: {"audit": audit})
    return run_envelope.case_baseline_reserved("t1")


def test_the_build_may_solve_the_baseline_only_after_a_reserving_admission(
    tmp_path, monkeypatch
):
    used = {"event": "used", "tool": run_ic.TOOL}
    assert (
        _reserved_task(tmp_path, monkeypatch, [{**used, "reserved": 2, "solves": 2}])
        is True
    )
    # The latest admission of the case is the one that counts.
    assert (
        _reserved_task(tmp_path, monkeypatch, [{**used, "reserved": 2}, used]) is False
    )
    assert _reserved_task(tmp_path, monkeypatch, []) is False
    assert run_envelope.case_baseline_reserved(None) is False


@pytest.mark.parametrize("reserved", [False, True])
def test_the_build_inside_a_run_solves_the_baseline_only_when_reserved(
    monkeypatch, reserved
):
    """A3 at the build (F1): a run's case with nothing reserved never solves it."""
    from routers import results
    from services.finance.case import FinanceRefused
    from services.pypsa_service import PyPSAService
    from services.results import finance_case

    solved = []

    def solve_baseline(*a, **k):
        solved.append(1)
        raise B.BaselineRefused("stop", "the probe stops after the solve")

    monkeypatch.setattr(
        finance_case,
        "baseline_plan",
        lambda n, cfg, owner=None: B.BaselinePlan(
            removed=(), kept=(("G", "g"),), flags=()
        ),
    )
    monkeypatch.setattr(B, "solve_baseline", solve_baseline)
    monkeypatch.setattr("services.workflow_tools._network_copy", lambda n: n)
    monkeypatch.setattr(
        "models.finance.FinanceInputs.model_validate", classmethod(lambda cls, raw: raw)
    )
    monkeypatch.setattr(
        PyPSAService, "get_lock", staticmethod(lambda: threading.Lock())
    )
    monkeypatch.setattr(
        run_envelope, "case_baseline_reserved", lambda task_id: reserved
    )
    token = assistant_tasks.RUN_CONTEXT.set(object())
    try:
        build = results._ic_build_case(
            object(), SimpleNamespace(finance={}), owner=None, lost_load=None
        )
    finally:
        assistant_tasks.RUN_CONTEXT.reset(token)
    with pytest.raises(FinanceRefused) as err:
        build()
    assert (err.value.code, solved) == (
        ("stop", [1]) if reserved else ("baseline_solve", [])
    )


@pytest.mark.parametrize("reserved", [False, True])
def test_the_build_reads_the_reservation_of_its_own_run(
    tmp_path, monkeypatch, reserved
):
    """F1 (call site): the build asks `case_baseline_reserved` of the run it
    is in (`harness_locks.in_run`), read from that run's envelope; a run with
    nothing reserved runs no baseline LP."""
    from fastapi import HTTPException
    from routers import results
    from services import harness_locks
    from services.finance.case import FinanceRefused
    from services.pypsa_service import PyPSAService
    from services.results import finance_case

    path = tmp_path / "task.json"
    path.write_text("{}")
    used = {"event": "used", "tool": run_ic.TOOL, "call_id": "c"}
    audit = [{**used, "reserved": 2, "solves": 2} if reserved else used]

    def read(task_id):
        if task_id != "t-run":
            raise HTTPException(404, "not this run")
        return path, {}

    solved = []

    def solve_baseline(*a, **k):
        solved.append(1)
        raise B.BaselineRefused("stop", "the probe stops after the solve")

    monkeypatch.setattr(assistant_tasks, "_read", read)
    monkeypatch.setattr(run_envelope, "_load", lambda p: {"audit": audit})
    monkeypatch.setattr(
        finance_case,
        "baseline_plan",
        lambda n, cfg, owner=None: B.BaselinePlan(
            removed=(), kept=(("G", "g"),), flags=()
        ),
    )
    monkeypatch.setattr(B, "solve_baseline", solve_baseline)
    monkeypatch.setattr("services.workflow_tools._network_copy", lambda n: n)
    monkeypatch.setattr(
        "models.finance.FinanceInputs.model_validate", classmethod(lambda cls, raw: raw)
    )
    monkeypatch.setattr(
        PyPSAService, "get_lock", staticmethod(lambda: threading.Lock())
    )
    token = assistant_tasks.RUN_CONTEXT.set(object())
    run = harness_locks.enter_run("t-run")
    try:
        build = results._ic_build_case(
            object(), SimpleNamespace(finance={}), owner=None, lost_load=None
        )
    finally:
        harness_locks.leave_run(run)
        assistant_tasks.RUN_CONTEXT.reset(token)
    with pytest.raises(FinanceRefused) as err:
        build()
    assert (err.value.code, solved) == (
        ("stop", [1]) if reserved else ("baseline_solve", [])
    )


def test_a_reservation_is_read_only_from_the_runs_latest_admitted_case(
    tmp_path, monkeypatch
):
    """A2, F1: another tool's later row, or a later refused admission of the
    case, leaves the latest admitted case's reservation; no envelope or an
    unknown run is none."""
    used = {"event": "used", "tool": run_ic.TOOL, "reserved": 2, "solves": 2}
    after = [
        {"event": "used", "tool": "solve_queue_enqueue"},
        {"event": "refused", "tool": run_ic.TOOL, "reason": "ic_budget"},
    ]
    assert _reserved_task(tmp_path, monkeypatch, [used, *after]) is True
    monkeypatch.setattr(run_envelope, "_load", lambda p: None)
    assert run_envelope.case_baseline_reserved("t1") is False

    def unknown(task_id):
        from fastapi import HTTPException

        raise HTTPException(404, "Task not found in this project.")

    monkeypatch.setattr(assistant_tasks, "_read", unknown)
    assert run_envelope.case_baseline_reserved("t-gone") is False


@pytest.mark.parametrize(
    "runs",
    [
        [{"tool": "solve_queue_enqueue"}],
        [{"tool": run_ic.TOOL, "baseline_solves": 3}],
        [{"tool": run_ic.TOOL, "baseline_solves": 2.0}],
    ],
    ids=["no-case-entry", "another-bound", "not-an-int"],
)
def test_only_the_bound_grant_entry_reserves_the_baseline(monkeypatch, runs):
    """A2, A3: a grant without the case entry, or one naming anything but the
    bound as an int, reserves nothing and keeps the baseline refusal."""
    reason, note = _case_refusal(monkeypatch, runs=runs)
    assert reason == "baseline_solve" and "case_solves" not in note
    assert run_envelope._case_baseline_solves({"runs": runs}) is None
    assert run_envelope._case_baseline_solves({}) is None  # a grant with no runs


# ── admission at the bound, on a real granted run (A2, F1) ───────────────────

CASE_BLOCK = {
    "edits": [],
    "runs": [
        {"tool": "solve_queue_enqueue"},
        {"tool": run_ic.TOOL, "baseline_solves": 2},
        {"tool": "generate_report"},
    ],
    "budget": {"solves": 4, "rounds": 6, "wall_time_h": 8.0},
}


@pytest.fixture
def case_run(granted, profile, monkeypatch):
    """A granted run whose grant reserves the case's baseline, with `used`
    solves already spent; the case would re-solve its baseline."""
    from harness import confirm

    profile(CASE_BLOCK)
    monkeypatch.setattr(run_envelope, "_case_solves_baseline", lambda *a: True)

    def go(used):
        task, path, _ = granted(detach=False)
        envelope = run_envelope._load(path)
        envelope["audit"] += [
            {"event": "used", "tool": "solve_queue_enqueue", "call_id": f"s{i}"}
            for i in range(used)
        ]
        run_envelope._save(path, envelope)
        return task, path

    def admit(task, path, call_id, ctx=None):
        with on_run(task, path, ctx=ctx) as run:
            run.run_step_call = call_id
            return run_envelope.admit(
                run,
                call_id=call_id,
                tool=run_ic.TOOL,
                args={},
                tier=confirm._safety_tier_for(run_ic.TOOL),
                external=None,
                edit_card=None,
            )

    return go, admit


def _rows(path, tool=run_ic.TOOL):
    envelope = run_envelope._load(path)
    return envelope, [a for a in envelope["audit"] if a.get("tool") == tool]


@pytest.mark.parametrize("left", [1, 2])
def test_the_case_is_admitted_only_when_its_baseline_solves_are_left(case_run, left):
    """A2 (M3): exactly 2 solves left admits the case with both reserved and
    counted on the run; 1 left refuses it `ic_budget`, with nothing reserved
    or charged (no LP may run)."""
    go, admit = case_run
    task, path = go(CASE_BLOCK["budget"]["solves"] - left)
    got = admit(task, path, "run-report.1-a")
    envelope, rows = _rows(path)
    if left == 2:
        assert got == "admitted"
        assert rows == [{**rows[0], "event": "used", "reserved": 2, "solves": 2}]
        assert run_envelope.solves_used(envelope) == 4
        assert _counted(path) == 4
        assert run_envelope.case_baseline_reserved(task["task_id"]) is True
    else:
        assert got is None
        assert [(r["event"], r["reason"]) for r in rows] == [
            ("refused", run_envelope.IC_BUDGET_KIND)
        ]
        assert not any("reserved" in r for r in rows)
        assert run_envelope.solves_used(envelope) == 3
        assert run_envelope.case_baseline_reserved(task["task_id"]) is False


def test_a_case_with_no_baseline_to_solve_is_admitted_reserving_nothing(
    case_run, monkeypatch
):
    """A2: the case solves no LP of its own: admitted with no reservation,
    and the solves left are not checked for it."""
    go, admit = case_run
    task, path = go(CASE_BLOCK["budget"]["solves"])
    monkeypatch.setattr(run_envelope, "_case_solves_baseline", lambda *a: False)
    assert admit(task, path, "run-report.1-a") == "admitted"
    envelope, rows = _rows(path)
    assert [r["event"] for r in rows] == ["used"]
    assert not any("reserved" in r or "solves" in r for r in rows)
    assert run_envelope.solves_used(envelope) == CASE_BLOCK["budget"]["solves"]
    assert run_envelope.case_baseline_reserved(task["task_id"]) is False


def test_each_admission_that_spends_solves_is_counted_on_the_run_at_once(
    case_run,
):
    """A2: the run's card counts a queued solve and the case's reservation
    the moment each is admitted, from the one count the budget check reads."""
    from harness import confirm

    go, admit = case_run
    task, path = go(0)
    with on_run(task, path) as run:
        assert (
            run_envelope.admit(
                run,
                call_id="run-solve.1-a",
                tool="solve_queue_enqueue",
                args={},
                tier=confirm._safety_tier_for("solve_queue_enqueue"),
                external=None,
                edit_card=None,
            )
            == "admitted"
        )
    assert _counted(path) == 1
    assert admit(task, path, "run-report.1-a") == "admitted"
    assert _counted(path) == 3 == run_envelope.solves_used(run_envelope._load(path))


def _counted(path):
    return json.loads(path.read_text())["run"]["budget"]["used"]["solves"]


def test_an_admission_that_could_not_be_recorded_reserves_nothing(
    case_run, monkeypatch
):
    """A2, F1: no approval record, no reservation and no charge."""
    from harness import approvals

    go, admit = case_run
    task, path = go(2)
    monkeypatch.setattr(approvals, "decided", lambda *a, **k: False)
    assert admit(task, path, "run-report.1-a") == "unavailable"
    envelope, rows = _rows(path)
    assert not any("reserved" in r or "solves" in r for r in rows)
    assert run_envelope.solves_used(envelope) == 2


def test_the_charge_of_a_case_that_ran_is_never_refunded(case_run):
    """F1: a later admission refused `ic_budget` (the solves are spent) keeps
    the first case's whole charge on the run."""
    go, admit = case_run
    task, path = go(2)
    assert admit(task, path, "run-report.1-a") == "admitted"
    assert admit(task, path, "run-report.1-b") is None
    envelope, rows = _rows(path)
    assert [(r["event"], r.get("solves")) for r in rows] == [
        ("used", 2),
        ("refused", None),
    ]
    assert run_envelope.solves_used(envelope) == 4


def test_two_concurrent_case_admissions_at_two_left_admit_exactly_one(
    case_run, monkeypatch
):
    """F1: the reservation is checked and written under the one admit lock:
    two admissions that both read 2 solves left before it never both pass."""
    from services.pypsa_service import PyPSAService

    go, admit = case_run
    task, path = go(2)
    ctx = PyPSAService.get_active_context()
    both = threading.Barrier(2, timeout=10)
    real = run_envelope._refusal

    def refusal(*a, **k):
        reason = real(*a, **k)
        both.wait()  # each read the envelope before either holds the lock
        return reason

    monkeypatch.setattr(run_envelope, "_refusal", refusal)
    got = {}

    def one(call_id):
        got[call_id] = admit(task, path, call_id, ctx=ctx)

    threads = [
        threading.Thread(target=contextvars.copy_context().run, args=(one, c))
        for c in ("report.1-a", "report.1-b")
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert sorted(map(str, got.values())) == ["None", "admitted"]
    envelope, rows = _rows(path)
    assert sorted((r["event"], r.get("reason")) for r in rows) == [
        ("refused", run_envelope.IC_BUDGET_KIND),
        ("used", None),
    ]
    assert run_envelope.solves_used(envelope) == 4


# ── the step: a case that does not end done is the chapter's gap ─────────────


def _task(tmp_path, *, on_end=True, job=None, status="waiting"):
    path = tmp_path / "task.json"
    step = {
        "id": STEP,
        "tool": run_ic.TOOL,
        "status": status,
        "attempt_id": "a1",
        "job": job or {"job_id": "ad:1", "continuation": "armed"},
    }
    if on_end:
        step["on_end"] = "chapter_gap"
    path.write_text(
        json.dumps(
            {
                "task_id": "t1",
                "project_id": PROJECT,
                "status": "waiting_job",
                "steps": [step],
                "run": {
                    "profile": REF,
                    "status": "running",
                    "stage": "report",
                    "stages": {},
                    "runner": {"lease_owner": "i1", "lease_until": time.time() + 600},
                },
            }
        )
    )
    return path


def _step(path):
    return json.loads(path.read_text())["steps"][0]


@pytest.fixture
def settled(monkeypatch):
    """`settle_job` applied to the task file as production does: only a
    waiting step of this job settles (else None), with its status and outcome."""
    calls = []

    def settle_job(path, ticket, job_id, native, status, result=None):
        task = json.loads(path.read_text())
        step = next(s for s in task["steps"] if s["id"] == ticket[1])
        if step["status"] != "waiting" or step["job"].get("job_id") != job_id:
            return None
        calls.append((native, status, result))
        step["status"] = status
        step["job"]["outcome"] = native
        if status == "completed":
            step["result"] = {
                "job_id": job_id,
                "final_status": "completed",
                **(result or {}),
            }
        path.write_text(json.dumps(task))
        return {"ok": True}

    monkeypatch.setattr(assistant_tasks, "settle_job", settle_job)
    monkeypatch.setattr(assistant_tasks, "await_job", lambda *a, **k: HOLD["path"])
    monkeypatch.setattr(run_ic, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(run_ic, "_FOLLOWERS", {})
    monkeypatch.setattr(run_envelope, "_load", lambda p: None)
    return calls


def _record(status="done", started=1.0, **extra):
    return {"status": status, "started_at": started, "error": None, **extra}


def _ctx(tmp_path, record):
    ctx = ctx_for(tmp_path, template=None)
    ctx.solver_state[run_ic.STUDY] = record
    return ctx


def _wait(tmp_path, monkeypatch, record, *, on_end=True, expires=None):
    path = _task(tmp_path, on_end=on_end)
    HOLD["path"] = path
    task = json.loads(path.read_text())
    monkeypatch.setattr(assistant_tasks, "_read", lambda task_id: (path, task))
    envelope = None if expires is None else {"state": "granted", "expires_at": expires}
    monkeypatch.setattr(run_envelope, "read", lambda p: envelope)
    return path, run_ic.wait(TICKET, "ad:1", "sess", _ctx(tmp_path, record), 1.0)


def _join():
    thread = run_ic._FOLLOWERS.get(TICKET)
    assert thread is not None
    thread.join(10)
    assert not thread.is_alive()


@pytest.mark.parametrize(
    "status, kind, code",
    [
        ("refused", "ic_case_refused", "no_finance"),
        ("failed", "ic_case_failed", "engine_error"),
        ("aborted", "ic_case_aborted", None),
    ],
)
def test_each_case_end_is_judged_by_the_report_with_its_own_kind(
    tmp_path, monkeypatch, settled, status, kind, code
):
    """O4: the step's error is its own kind, the case's typed code and fixed
    words; the raw error text never reaches it."""
    record = _record(
        status, error_code=code, error="Traceback (most recent call last): /srv/x.py"
    )
    path, followed = _wait(tmp_path, monkeypatch, record)
    assert followed is True
    _join()
    assert settled == [(status, "failed", None)]
    step = _step(path)
    assert step["status"] == "failed" and step["judged_by"] == run_ic.JUDGED
    assert step["error"].startswith(kind + (f" ({code})" if code else "") + ": ")
    assert "Traceback" not in step["error"] and "/srv" not in step["error"]
    assert run_ic.chapter_gaps(json.loads(path.read_text())) == {
        C.INVESTMENT: step["error"]
    }


def test_an_untyped_error_code_is_left_out(tmp_path, monkeypatch, settled):
    path, _ = _wait(tmp_path, monkeypatch, _record("failed", error_code="../etc Bad"))
    _join()
    assert _step(path)["error"].startswith("ic_case_failed: ")


def test_a_done_case_is_no_gap_and_records_no_solve_without_a_reservation(
    tmp_path, monkeypatch, settled
):
    path, _ = _wait(tmp_path, monkeypatch, _record("done"))
    _join()
    step = _step(path)
    assert step["status"] == "completed" and "judged_by" not in step
    assert step["result"]["solves"] == 0
    assert run_ic.chapter_gaps(json.loads(path.read_text())) == {}


def test_a_done_case_records_its_reserved_baseline_solves(
    tmp_path, monkeypatch, settled
):
    audit = [
        {
            "event": "used",
            "tool": run_ic.TOOL,
            "call_id": "c",
            "reserved": 2,
            "solves": 2,
        }
    ]
    monkeypatch.setattr(run_envelope, "_load", lambda p: {"audit": audit})
    path, _ = _wait(tmp_path, monkeypatch, _record("done"))
    _join()
    assert _step(path)["result"]["solves"] == 2


def test_an_unverifiable_case_still_stops_the_run(tmp_path, monkeypatch, settled):
    """O5: a replaced record is `uncertain`: never judged, so the runner stops on it."""
    path, _ = _wait(tmp_path, monkeypatch, _record("done", started=2.0))
    _join()
    step = _step(path)
    assert step["status"] == "uncertain" and "judged_by" not in step


def test_a_step_without_on_end_keeps_the_stop(tmp_path, monkeypatch, settled):
    """O2: eh_ipp_hybrid@1's contract: a failed case stops the run (no judge),
    with its error words as before."""
    path, _ = _wait(
        tmp_path, monkeypatch, _record("failed", error_code="x"), on_end=False
    )
    _join()
    step = _step(path)
    assert step["status"] == "failed" and "judged_by" not in step
    assert step["error"] == "The investment case ended failed: x"
    assert run_ic.chapter_gaps(json.loads(path.read_text())) == {}


def test_a_superseded_or_unbound_step_is_no_gap():
    step = {
        "id": STEP,
        "tool": run_ic.TOOL,
        "status": "failed",
        "on_end": "chapter_gap",
    }
    assert run_ic.chapter_gaps({"steps": [{**step, "superseded": True}]}) == {}
    assert run_ic.chapter_gaps({"steps": [{**step, "on_end": None}]}) == {}
    assert run_ic.chapter_gaps({"steps": [{**step, "status": "completed"}]}) == {}
    assert (
        run_ic.chapter_gaps({"steps": [{**step, "tool": "solve_queue_enqueue"}]}) == {}
    )


def test_an_ic_budget_refusal_fails_the_gap_step_with_its_kind(tmp_path):
    """A2, A5: refused at admission, the step fails `ic_budget` and the run goes on."""
    path = _task(tmp_path, status="pending")
    assert run_ic.budget_gap(path, STEP) is True
    step = _step(path)
    assert (step["status"], step["judged_by"]) == ("failed", run_ic.JUDGED)
    assert step["error"].startswith("ic_budget: ")
    assert run_ic.budget_gap(path, STEP) is False  # once


def test_an_ic_budget_refusal_of_any_other_step_keeps_its_card(tmp_path):
    path = _task(tmp_path, status="pending", on_end=False)
    assert run_ic.budget_gap(path, STEP) is False
    assert _step(path)["status"] == "pending"


def test_the_runner_turns_an_ic_budget_refusal_into_the_gap(tmp_path, monkeypatch):
    from harness import runner

    path = _task(tmp_path, status="pending")
    monkeypatch.setattr(
        run_envelope, "last_refusal", lambda p: run_envelope.IC_BUDGET_KIND
    )
    waited = []
    fake = SimpleNamespace(_wait_for_user=lambda *a, **k: waited.append(a))
    runner.Runner._envelope_refused(fake, path, {"id": STEP})
    assert waited == [] and _step(path)["error"].startswith("ic_budget: ")
    other = _task(tmp_path, status="pending", on_end=False)
    runner.Runner._envelope_refused(fake, other, {"id": STEP})
    assert len(waited) == 1  # a step without on_end keeps its card


# ── B1, O6: the run's wall time bounds the wait ──────────────────────────────


def _iso(seconds):
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


def test_the_wait_ends_at_the_runs_wall_time_when_it_comes_first(
    tmp_path, monkeypatch, settled
):
    record = _record("running")
    expires = _iso(0.3)
    path, _ = _wait(tmp_path, monkeypatch, record, expires=expires)
    _join()
    assert settled == [("timed_out", "failed", None)]  # never completed
    step = _step(path)
    # The deadline is the run's wall time, not the 6 h follow deadline.
    assert (
        abs(step["job"]["deadline_at"] - datetime.fromisoformat(expires).timestamp())
        < 1e-3
    )
    assert step["job"]["deadline_kind"] == run_ic.WALL_TIME_KIND
    assert step["error"] == run_ic.WALL_TIME and step["error"].startswith(
        "ic_wall_time: "
    )
    assert step["judged_by"] == run_ic.JUDGED
    assert run_ic.chapter_gaps(json.loads(path.read_text())) == {
        C.INVESTMENT: run_ic.WALL_TIME
    }


def test_a_late_finish_cannot_flip_the_settled_step(tmp_path, monkeypatch, settled):
    """O6: the case finishing after its wall time settles nothing more: the
    step stays failed and judged; the case may store a record, read as its own."""
    record = _record("running")
    path, _ = _wait(tmp_path, monkeypatch, record, expires=_iso(0.2))
    _join()
    before = _step(path)
    record["status"] = "done"  # the worker finishes late
    assert run_ic.settle(path, TICKET, "ad:1", record, 1.0) is None
    assert run_ic.recover(path, _ctx(tmp_path, record), instance="i1") == 0
    assert _step(path) == before and settled == [("timed_out", "failed", None)]
    assert run_ic.chapter_gaps(json.loads(path.read_text())) == {
        C.INVESTMENT: run_ic.WALL_TIME
    }


def test_a_case_that_ends_before_the_wall_time_settles_from_its_record(
    tmp_path, monkeypatch, settled
):
    record = _record("running")
    path, _ = _wait(tmp_path, monkeypatch, record, expires=_iso(600))
    assert _step(path)["job"]["deadline_kind"] == run_ic.WALL_TIME_KIND
    record["status"] = "done"
    _join()
    assert settled == [
        (
            "done",
            "completed",
            {"study": "investment_case", "started_at": 1.0, "solves": 0},
        )
    ]


def test_a_wall_time_past_the_follow_deadline_keeps_the_follow_deadline(
    tmp_path, monkeypatch, settled
):
    record = _record("running")
    path, _ = _wait(
        tmp_path, monkeypatch, record, expires=_iso(run_ic.MAX_FOLLOW_SECONDS + 3600)
    )
    job = _step(path)["job"]
    assert "deadline_kind" not in job
    assert job["deadline_at"] < time.time() + run_ic.MAX_FOLLOW_SECONDS + 60
    record["status"] = "done"
    _join()


def test_a_step_without_on_end_ignores_the_wall_time(tmp_path, monkeypatch, settled):
    """O2: eh_ipp_hybrid@1's follow deadline is unchanged: its follower polls
    with no wall time until the case ends, and settles from its record."""
    record = _record("running")
    path, _ = _wait(tmp_path, monkeypatch, record, on_end=False, expires=_iso(0.2))
    assert "deadline_kind" not in _step(path)["job"]
    time.sleep(0.4)  # past the envelope's expires_at, the case still running
    assert run_ic.following(TICKET)
    record["status"] = "done"
    _join()
    assert [s[:2] for s in settled] == [("done", "completed")]


def test_recovery_past_the_wall_time_settles_the_typed_refusal(tmp_path, settled):
    job = {
        "job_id": "ad:1",
        "continuation": "armed",
        "kind": run_ic.STUDY,
        "started_at": 1.0,
        "call_id": "a1",
        "deadline_at": time.time() - 1,
        "deadline_kind": run_ic.WALL_TIME_KIND,
    }
    path = _task(tmp_path, job=job)
    assert run_ic.recover(path, _ctx(tmp_path, _record("running")), instance="i1") == 1
    assert settled == [("timed_out", "failed", None)]
    step = _step(path)
    assert step["error"] == run_ic.WALL_TIME and step["judged_by"] == run_ic.JUDGED


def test_recovery_past_a_plain_follow_deadline_stays_uncertain(tmp_path, settled):
    job = {
        "job_id": "ad:1",
        "continuation": "armed",
        "kind": run_ic.STUDY,
        "started_at": 1.0,
        "call_id": "a1",
        "deadline_at": time.time() - 1,
    }
    path = _task(tmp_path, job=job)
    assert run_ic.recover(path, _ctx(tmp_path, _record("running")), instance="i1") == 1
    assert settled == [("timed_out", "uncertain", None)]
    assert "judged_by" not in _step(path)


def test_the_wall_time_is_read_from_a_granted_envelope_only(monkeypatch):
    """B1: a revoked, expired, missing or open-ended envelope sets no wall time."""
    expires = _iso(60)
    for envelope, wall in [
        (None, None),
        ({"state": "revoked", "expires_at": expires}, None),
        ({"state": "granted"}, None),
        ({"state": "granted", "expires_at": None}, None),
        (
            {"state": "granted", "expires_at": expires},
            datetime.fromisoformat(expires).timestamp(),
        ),
    ]:
        monkeypatch.setattr(run_envelope, "read", lambda p, e=envelope: e)
        assert run_ic._wall_deadline("task.json") == wall, envelope


def test_only_a_wall_time_deadline_is_the_steps_wall(tmp_path):
    """B1: the follower ends at the step's deadline only when it is the run's
    wall time; a plain follow deadline, a non-number, no job, no step or no
    task file is no wall."""
    job = {"job_id": "ad:1", "deadline_at": 5.0}
    assert run_ic._wall_of(_task(tmp_path, job=job), TICKET) is None
    wall = {**job, "deadline_kind": run_ic.WALL_TIME_KIND}
    assert run_ic._wall_of(_task(tmp_path, job=wall), TICKET) == 5.0
    bad = {**wall, "deadline_at": "soon"}
    assert run_ic._wall_of(_task(tmp_path, job=bad), TICKET) is None
    path = _task(tmp_path)
    task = json.loads(path.read_text())
    task["steps"][0]["job"] = None
    path.write_text(json.dumps(task))
    assert run_ic._wall_of(path, TICKET) is None
    assert run_ic._wall_of(path, ("t1", "report.9", "a1")) is None
    assert run_ic._wall_of(tmp_path / "gone.json", TICKET) is None


def test_a_case_done_at_the_wall_time_settles_from_its_record(
    tmp_path, monkeypatch, settled
):
    """B1: past the wall time, only a case still running is `ic_wall_time`;
    one that ended done is completed from its own record."""
    path, _ = _wait(tmp_path, monkeypatch, _record("done"), expires=_iso(-1))
    _join()
    assert [s[:2] for s in settled] == [("done", "completed")]
    assert "judged_by" not in _step(path)


@pytest.mark.parametrize(
    "record",
    [None, _record("running", started=2.0)],
    ids=["gone", "replaced"],
)
def test_a_lost_or_replaced_record_at_the_wall_time_stays_unverifiable(
    tmp_path, monkeypatch, settled, record
):
    """B1, O5: past the wall time, a record that is not the step's own is
    unverifiable (`uncertain`, the run stops), never the wall-time gap."""
    path, _ = _wait(tmp_path, monkeypatch, record, expires=_iso(-1))
    _join()
    assert settled == [("unknown", "uncertain", None)]
    assert _step(path)["error"] != run_ic.WALL_TIME


def test_a_dead_worker_before_the_wall_time_is_unverifiable(
    tmp_path, monkeypatch, settled
):
    """B1: a case whose worker died with its record still running, before the
    wall time, is `uncertain`, not the wall-time gap."""
    dead = threading.Thread(target=lambda: None)
    dead.start()
    dead.join()
    record = _record("running", thread=dead)
    path, _ = _wait(tmp_path, monkeypatch, record, expires=_iso(600))
    _join()
    assert settled == [("unknown", "uncertain", None)]
    assert _step(path)["error"] != run_ic.WALL_TIME


def test_recovery_before_the_wall_time_settles_a_done_case_completed(tmp_path, settled):
    """B1: a recovered step whose wall time has not come is settled from its
    record (done: completed), never as the wall-time gap."""
    job = {
        "job_id": "ad:1",
        "continuation": "armed",
        "kind": run_ic.STUDY,
        "started_at": 1.0,
        "call_id": "a1",
        "deadline_at": time.time() + 600,
        "deadline_kind": run_ic.WALL_TIME_KIND,
    }
    path = _task(tmp_path, job=job)
    assert run_ic.recover(path, _ctx(tmp_path, _record("done")), instance="i1") == 1
    assert [s[:2] for s in settled] == [("done", "completed")]


def test_the_wall_time_of_the_real_v4_report_step_ends_its_wait(
    tmp_path, monkeypatch, settled
):
    """B1 (M3): the step as `_advance_run` materialises it from eh_site@4's
    report stage, with the envelope's `expires_at` before the 6 h follow
    deadline, ends failed `ic_wall_time` at that wall time."""
    step = run_profiles.materialise(run_profiles.resolve(REF), "report", PROJECT)[0]
    assert step["id"] == STEP and step["on_end"] == run_ic.CHAPTER_GAP
    path = _task(tmp_path)
    task = json.loads(path.read_text())
    task["steps"] = [
        {
            **step,
            "status": "waiting",
            "attempt_id": "a1",
            "job": task["steps"][0]["job"],
        }
    ]
    path.write_text(json.dumps(task))
    HOLD["path"] = path
    monkeypatch.setattr(assistant_tasks, "_read", lambda task_id: (path, task))
    expires = _iso(0.3)
    monkeypatch.setattr(
        run_envelope, "read", lambda p: {"state": "granted", "expires_at": expires}
    )
    record = _record("running")
    assert run_ic.wait(TICKET, "ad:1", "sess", _ctx(tmp_path, record), 1.0) is True
    _join()
    assert settled == [("timed_out", "failed", None)]
    got = _step(path)
    assert got["job"]["deadline_kind"] == run_ic.WALL_TIME_KIND
    assert got["job"]["deadline_at"] < time.time() + run_ic.MAX_FOLLOW_SECONDS - 60
    assert (got["error"], got["judged_by"]) == (run_ic.WALL_TIME, run_ic.JUDGED)


def test_a_completed_case_is_never_run_again_on_re_entry(tmp_path, settled):
    """M2: re-entering the report stage (a resume or a recovery reads `_next`)
    goes on after the completed case, or after its judged gap; recovery
    settles nothing more."""
    steps = run_profiles.materialise(run_profiles.resolve(REF), "report", PROJECT)
    for end in (
        {"status": "completed", "result": {"study": run_ic.STUDY, "solves": 2}},
        {"status": "failed", "error": run_ic.BUDGET, "judged_by": run_ic.JUDGED},
    ):
        task = {
            "steps": [
                {**steps[0], **end},
                *(dict(s, status="pending") for s in steps[1:]),
            ]
        }
        assert assistant_tasks._next(task)["id"] == steps[1]["id"] != STEP
    path = _task(tmp_path, status="completed")
    assert run_ic.recover(path, _ctx(tmp_path, _record("running")), instance="i1") == 0
    assert settled == [] and _step(path)["status"] == "completed"


def test_the_gap_step_and_its_text_are_read_defensively():
    """O4: a missing step is no gap step; a failed gap step with no error
    still names its status; a task with no steps has no gap."""
    assert run_ic._gap_step({"steps": []}, STEP) is False
    assert run_ic.chapter_gaps({}) == {}
    step = {
        "id": STEP,
        "tool": run_ic.TOOL,
        "status": "failed",
        "on_end": "chapter_gap",
    }
    assert run_ic.chapter_gaps({"steps": [step]}) == {
        C.INVESTMENT: "the investment case step failed"
    }


def test_an_ic_budget_refusal_of_a_non_case_step_is_no_gap(tmp_path):
    """A2, A5: only the case's own step is turned into the `ic_budget` gap."""
    path = _task(tmp_path, status="pending")
    task = json.loads(path.read_text())
    task["steps"][0]["tool"] = "solve_queue_enqueue"
    path.write_text(json.dumps(task))
    assert run_ic.budget_gap(path, STEP) is False
    assert _step(path)["status"] == "pending"


def test_the_reserved_solves_are_the_latest_admitted_cases(monkeypatch):
    """A2: the settled result counts the latest `used` row of the case, never
    another tool's row or a later refused admission."""
    audit = [
        {
            "event": "used",
            "tool": run_ic.TOOL,
            "call_id": "c",
            "reserved": 2,
            "solves": 2,
        },
        {"event": "used", "tool": "solve_queue_enqueue", "call_id": "s"},
        {"event": "refused", "tool": run_ic.TOOL, "call_id": "d", "solves": 0},
    ]
    monkeypatch.setattr(run_envelope, "_load", lambda p: {"audit": audit})
    assert run_ic.reserved("task.json") == 2


def test_run_ic_owns_the_set_of_gap_kinds():
    """GK B (61) A5 (a): the set is exactly the five, and the envelope's
    refusal kind is the very same as run_ic's."""
    assert run_ic.CHAPTER_GAP_KINDS == {
        "ic_budget",
        "ic_wall_time",
        "ic_case_refused",
        "ic_case_failed",
        "ic_case_aborted",
    }
    assert run_envelope.IC_BUDGET_KIND == run_ic.BUDGET_KIND
    assert run_envelope.IC_BUDGET_KIND is run_ic.BUDGET_KIND


def _kind(error):
    return error.split(":", 1)[0].split(" (", 1)[0]


def test_every_gap_error_carries_a_kind_from_the_set():
    """GK B (61) A5 (b): each chapter-gap error is one of run_ic's kinds,
    with or without a record."""
    errors = [run_ic.WALL_TIME, run_ic.BUDGET]
    for native in run_ic.GAP_KINDS:
        errors += [
            run_ic.gap_error(native, {"error_code": "x"}),
            run_ic.gap_error(native, None),
        ]
    assert {_kind(e) for e in errors} == run_ic.CHAPTER_GAP_KINDS


def test_no_tool_error_frame_carries_a_gap_kind():
    """GK B (61) A5 (c): a gap is the report's, never a `tool_error` frame's
    kind on the run path (nor anywhere the manifest guard derives one)."""
    from tests.test_tool_error_kind_manifest import derive_tool_error_kinds

    assert not derive_tool_error_kinds() & run_ic.CHAPTER_GAP_KINDS


# ── the report: the run's own case outranks a stored one (O4) ────────────────


def _sections(status="ok"):
    return {
        C.INVESTMENT: EvidenceSection(
            section_id=C.INVESTMENT,
            title=TITLE,
            kind="investment",
            status=status,
            source="investment_case_report",
            payload={"x": 1} if status == "ok" else None,
        )
    }


def _gap_rows(tables):
    return [str(r[0]) for r in tables["harness_gaps"].rows if r]


@pytest.mark.parametrize(
    "error, kind",
    [
        (run_ic.gap_error("refused", {"error_code": "no_finance"}), "ic_case_refused"),
        (run_ic.gap_error("failed", {"error_code": "engine_error"}), "ic_case_failed"),
        (run_ic.gap_error("aborted", {}), "ic_case_aborted"),
        (run_ic.WALL_TIME, "ic_wall_time"),
        (run_ic.BUDGET, "ic_budget"),
    ],
)
def test_a_gap_run_case_is_the_chapter_gap_over_an_older_stored_case(error, kind):
    task = {
        "steps": [
            {
                "id": STEP,
                "tool": run_ic.TOOL,
                "status": "failed",
                "on_end": "chapter_gap",
                "judged_by": "report",
                "error": error,
            }
        ],
        "run": {},
    }
    sections = _sections("ok")  # a case the project stored before this run
    tables = evidence._harness_section(
        {"chapters": [C.INVESTMENT], "task": task}, sections
    )
    assert (
        sections[C.INVESTMENT].status != "ok" and sections[C.INVESTMENT].note == error
    )
    row = next(r for r in _gap_rows(tables) if TITLE in r)
    assert row == f"Chapter not established: {TITLE} ({error})" and f"({kind}" in row


def test_a_done_run_case_lists_no_investment_gap():
    task = {
        "steps": [
            {
                "id": STEP,
                "tool": run_ic.TOOL,
                "status": "completed",
                "on_end": "chapter_gap",
            }
        ],
        "run": {},
    }
    sections = _sections("ok")
    tables = evidence._harness_section(
        {"chapters": [C.INVESTMENT], "task": task}, sections
    )
    assert sections[C.INVESTMENT].status == "ok"
    assert not any(TITLE in g for g in _gap_rows(tables))


def test_a_run_with_no_task_record_keeps_the_chapter_gap():
    """O4: a hand-off with no task record lists the chapter as before."""
    sections = {}
    tables = evidence._harness_section(
        {"chapters": [C.INVESTMENT], "task": None}, sections
    )
    assert f"Chapter not established: {TITLE} (no source)" in _gap_rows(tables)


def test_no_case_run_keeps_the_gap_with_the_evidence_note():
    """eh_site@3 (no case step): the chapter stays a gap as before."""
    sections = {}
    tables = evidence._harness_section(
        {"chapters": [C.INVESTMENT], "task": {"steps": [], "run": {}}}, sections
    )
    assert f"Chapter not established: {TITLE} (no source)" in _gap_rows(tables)


# ── the hand-off's case KPIs (36-K1, K2, O3) ─────────────────────────────────


KPIS = {"kpis": [*run_profiles.EH_SITE_IC_KPIS]}


def _readers(monkeypatch, returns, pre):
    calls = []

    def read_returns(ctx, on):
        calls.append("returns")
        return returns

    def read_pre(ctx, on):
        calls.append("pre")
        return pre

    monkeypatch.setattr(ic_counterfactual, "read_returns", read_returns)
    monkeypatch.setattr(ic_counterfactual, "read_pre_tax_irr", read_pre)
    return calls


def _established(npv=1.5e6, irr=0.091, pre=0.12, currency="EUR"):
    return (
        {
            "state": "established",
            "reasons": [],
            "returns": {"npv_at_wacc": npv, "irr": irr, "currency": currency},
        },
        {"state": "established", "reasons": [], "irr": pre},
    )


def _by_id(rows):
    return {r["id"]: r for r in rows}


def test_a_current_case_reads_its_stored_figures(monkeypatch):
    calls = _readers(monkeypatch, *_established())
    rows = _by_id(run_handoff.read_kpis(KPIS))
    assert calls == ["returns", "pre"]  # one read of each reader per hand-off
    assert (rows["ic_npv_at_wacc"]["value"], rows["ic_npv_at_wacc"]["unit"]) == (
        1.5e6,
        "EUR",
    )
    assert rows["ic_post_tax_irr"]["value"] == 0.091
    assert rows["ic_pre_tax_irr"]["value"] == 0.12
    assert all(r["status"] == "read" for r in rows.values())
    assert rows["ic_npv_at_wacc"]["source"] == (
        "services.ic_counterfactual.read_returns:project_post_tax_npv_at_wacc"
    )


@pytest.mark.parametrize("reason", ["case_stale", "results_not_current"])
def test_a_stale_case_reads_not_current_never_its_old_number(monkeypatch, reason):
    """K2: the network changed after the case: no figure is read, and the
    gate never takes it, even as stored."""
    fact = {"state": "not_run", "reasons": [reason]}
    _readers(monkeypatch, {**fact, "returns": None}, {**fact, "irr": None})
    rows = run_handoff.read_kpis(KPIS)
    assert {(r["status"], r["value"]) for r in rows} == {("not_current", None)}
    assert all(r["reasons"] == [reason] for r in rows)
    assert run_handoff.kpis_read({"run": {"handoff": {"kpis": rows}}}, KPIS) is False


@pytest.mark.parametrize(
    "reason", ["no_case", "case_failed", "refused", "irr_not_established"]
)
def test_a_figure_the_case_did_not_establish_is_stated_with_its_reason(
    monkeypatch, reason
):
    """O3: never read as a value; the gate takes the stated row only when the
    stored row says the same."""
    fact = {"state": "not_established", "reasons": [reason]}
    _readers(monkeypatch, {**fact, "returns": None}, {**fact, "irr": None})
    rows = run_handoff.read_kpis(KPIS)
    assert {(r["status"], r["value"]) for r in rows} == {("not_established", None)}
    assert all(r["reasons"] == [reason] for r in rows)
    assert run_handoff.kpis_read({"run": {"handoff": {"kpis": rows}}}, KPIS) is True
    other = [
        {**r, "reasons": ["no_case"] if reason != "no_case" else ["refused"]}
        for r in rows
    ]
    assert run_handoff.kpis_read({"run": {"handoff": {"kpis": other}}}, KPIS) is False
    read = [{**r, "status": "read", "value": 1.0} for r in rows]
    assert run_handoff.kpis_read({"run": {"handoff": {"kpis": read}}}, KPIS) is False


def test_a_fact_with_no_reasons_is_stated_with_none(monkeypatch):
    """O3: a not-established fact without reasons is stated, not unreadable."""
    fact = {"state": "not_established", "reasons": None}
    _readers(monkeypatch, {**fact, "returns": None}, {**fact, "irr": None})
    rows = run_handoff.read_kpis(KPIS)
    assert {(r["status"], tuple(r["reasons"])) for r in rows} == {
        ("not_established", ())
    }


def test_a_case_that_names_no_currency_is_read_in_the_kpis_unit(monkeypatch):
    """36-K1: only a case in another named currency is a mismatch."""
    _readers(monkeypatch, *_established(npv=2.0, currency=None))
    rows = _by_id(run_handoff.read_kpis(KPIS))
    assert (rows["ic_npv_at_wacc"]["status"], rows["ic_npv_at_wacc"]["value"]) == (
        "read",
        2.0,
    )


def test_a_reader_that_raises_is_unreadable_and_fails_the_gate(monkeypatch):
    def boom(ctx, on):
        raise RuntimeError("x")

    monkeypatch.setattr(ic_counterfactual, "read_returns", boom)
    monkeypatch.setattr(ic_counterfactual, "read_pre_tax_irr", boom)
    rows = run_handoff.read_kpis(KPIS)
    assert {(r["status"], r["value"]) for r in rows} == {("unreadable", None)}
    assert run_handoff.kpis_read({"run": {"handoff": {"kpis": rows}}}, KPIS) is False


def test_a_case_in_another_currency_is_not_read_as_eur(monkeypatch):
    _readers(monkeypatch, *_established(npv=1.0, currency="USD"))
    rows = _by_id(run_handoff.read_kpis(KPIS))
    assert rows["ic_npv_at_wacc"]["status"] == "unreadable"
    assert rows["ic_npv_at_wacc"]["reasons"] == ["currency_mismatch"]
    assert rows["ic_post_tax_irr"]["status"] == "read"  # a fraction has no currency


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, "0.1", None])
def test_a_figure_that_is_not_a_finite_number_is_never_read(monkeypatch, value):
    _readers(monkeypatch, *_established(npv=value, irr=value, pre=value))
    rows = run_handoff.read_kpis(KPIS)
    assert {(r["status"], r["value"]) for r in rows} == {("unreadable", None)}


def test_an_unknown_case_kpi_is_unreadable():
    rows = run_handoff.read_kpis(
        {
            "kpis": [
                {
                    "id": "x",
                    "label": "x",
                    "unit": "EUR",
                    "source": "investment_case",
                    "key": "lcoe",
                }
            ]
        }
    )
    assert rows[0]["status"] == "unreadable" and rows[0]["reasons"] == ["unknown_kpi"]


def test_kpis_read_fails_on_a_figure_that_moved_since_the_hand_off(monkeypatch):
    _readers(monkeypatch, *_established(npv=2.0))
    rows = run_handoff.read_kpis(KPIS)
    assert run_handoff.kpis_read({"run": {"handoff": {"kpis": rows}}}, KPIS) is True
    moved = [{**r, "value": 3.0} if r["id"] == "ic_npv_at_wacc" else r for r in rows]
    assert run_handoff.kpis_read({"run": {"handoff": {"kpis": moved}}}, KPIS) is False


# ── the pilot on eh_site@4 (real runner, solve, EH study, loop and case) ─────

from tests.test_assistant_tasks_study_brief import (  # noqa: E402,F401
    baseline,
    dispatch,
    edit,
    helpers_state,
    session,
    wait_until,
)
from tests.test_harness_envelope import granted, on_run, profile  # noqa: E402,F401
from tests.test_harness_run_eh_site_v2 import _tick_to, goal_host  # noqa: E402,F401
from tests.test_harness_run_report_handoff import fake_report  # noqa: E402,F401
from tests.test_harness_run_skeleton import RUN, site, start  # noqa: E402,F401
from tests.test_harness_runner import detached, task_path  # noqa: E402,F401


def _spies(monkeypatch):
    """Each start of the run's case (`run_ic.wait`, step ids) and each LP its
    baseline solves."""
    spy = {"starts": [], "lps": []}
    wait, solve_baseline = run_ic.wait, B.solve_baseline

    def started(ticket, *a):
        spy["starts"].append(ticket[1])
        return wait(ticket, *a)

    def lp(*a, **k):
        from services import solver_service

        spy["lps"].append(1)
        return solver_service.run_simulation(*a, **k)

    monkeypatch.setattr(run_ic, "wait", started)
    monkeypatch.setattr(
        B,
        "solve_baseline",
        lambda n, cfg, plan, **k: solve_baseline(n, cfg, plan, solve=lp),
    )
    return spy


def _leave(monkeypatch, held, left):
    """M3: on entering the report stage, the grant's budget leaves exactly
    `left` solves for the case (whatever the solve, EH study and loop spent)."""
    materialise = run_profiles.materialise

    def at_report(profile, stage, project_id, start=1):
        if stage == "report" and start == 1:
            with assistant_tasks._LOCK:
                envelope = run_envelope._load(held["path"])
                envelope["budget"]["solves"] = run_envelope.solves_used(envelope) + left
                run_envelope._save(held["path"], envelope)
        return materialise(profile, stage, project_id, start=start)

    monkeypatch.setattr(run_profiles, "materialise", at_report)


def _project_ctx(record):
    from services.pypsa_service import PyPSAService

    return next(
        c
        for c in list(PyPSAService._contexts.values())
        if str(c.project_uuid) == record["project_id"]
    )


def _pilot(goal_host, session, site, monkeypatch, left=None):
    import uuid

    from services import run_handover
    from tests.test_harness_run_report_handoff import runner as run_runner
    from tests.test_harness_runner import Clicks, mail
    from tests.test_run_intake_grant import _new_task

    mail(monkeypatch)
    name = f"Pilot {uuid.uuid4().hex[:6]}"
    view = start(
        request_id=f"v4-{uuid.uuid4().hex[:6]}",
        run={
            **RUN,
            "profile": REF,
            "intake": {
                "project_name": name,
                "citations": [*goal_host["citations"], goal_host["goal"]],
            },
        },
    )
    run_envelope.decide(
        session,
        view["task_id"],
        "unattended",
        intake_digest=view["run"]["intake"]["digest"],
    )
    intent = run_handover.read_intent(task_path(site[1], view))
    path, task = _new_task(intent)
    if left is not None:
        _leave(monkeypatch, {"path": path}, left)
    clicks = Clicks(refs={"solve.2"})  # the EH study is always the user's card (run_eh)
    record = _tick_to(
        run_runner(clicks),
        path,
        task["task_id"],
        lambda r: r["run"]["status"] in ("completed", "needs_user", "failed", "paused"),
    )
    audit = json.loads(run_envelope.path_for(path).read_text())["audit"]
    if os.environ.get("V4_DUMP"):
        with open(os.environ["V4_DUMP"], "w") as f:
            f.write(json.dumps({"record": record, "audit": audit}, default=str))
    return path, record, audit, clicks


def test_pilot_on_v4_runs_the_case_and_hands_off_its_figures(
    goal_host, session, site, fake_report, monkeypatch
):
    """Acceptance 36-1/2 (envelope mode): the case runs after the solve, the
    result gate and its loop, its baseline's solves reserved on the run; its
    chapter is established (no gap row) and the hand-off reads its figures.
    M3: the budget leaves exactly the case's 2 solves at its admission, and
    it runs. F1: its whole reservation stays charged, never less than the
    LPs its baseline ran. M1, M2: it ran once, on the final solve."""
    from harness import run_report
    from routers import results
    from services import run_gates
    from services.dispatch_status import dispatch_digest

    spy = _spies(monkeypatch)
    path, record, audit, clicks = _pilot(
        goal_host, session, site, monkeypatch, left=run_envelope.CASE_BASELINE_SOLVES
    )
    run = record["run"]
    assert run["status"] == "completed", (run.get("reason"), run["stage"])
    assert clicks.used == ["solve.2"]  # the case asked the user nothing
    case = next(s for s in record["steps"] if s["tool"] == run_ic.TOOL)
    assert (
        case["id"] == STEP and case["status"] == "completed" and "judged_by" not in case
    )
    assert spy["starts"] == [STEP]  # M2: started once, never again on re-entry
    used = [
        a for a in audit if a.get("event") == "used" and a.get("tool") == run_ic.TOOL
    ]
    assert (
        len(used) == 1
        and used[0]["reserved"] == used[0]["solves"] == case["result"]["solves"] == 2
    )
    assert 1 <= len(spy["lps"]) <= used[0]["solves"]  # F1: no refund below what ran
    envelope = json.loads(run_envelope.path_for(path).read_text())
    assert (
        run["budget"]["used"]["solves"]
        == run_envelope.solves_used(envelope)
        == envelope["budget"]["solves"]
    )
    # M1: the stored case's inputs are the final solved network's (after the
    # coupling loop's last re-solve): its key is the current one.
    ctx = _project_ctx(record)
    stored = ctx.solver_state["investment_case_report"]
    inputs = stored["sections"]["project"]["payload"]["provenance"]["inputs"]
    current, why = run_gates._on(ctx, results._ic_current_assumptions)
    assert why is None and stored["assumptions_hash"] == current[0]
    assert inputs == current[1]
    assert inputs["dispatch"] == run_gates._on(
        ctx, lambda: dispatch_digest(ctx.network, results._result_df)
    )
    doc = run_report.saved(record, path.parent.parent)
    sections = {s.section_id: s for s in doc.sections}
    assert sections[C.INVESTMENT].status == "ok"
    gaps = [str(r[0]) for r in doc.tables["harness_gaps"].rows if r]
    assert not any(TITLE in g for g in gaps), gaps
    rows = {r["id"]: r for r in run["handoff"]["kpis"]}
    for kpi in run_profiles.EH_SITE_IC_KPIS:
        assert rows[kpi["id"]]["source"].startswith("services.ic_counterfactual."), (
            rows[kpi["id"]]
        )
        assert rows[kpi["id"]]["status"] in ("read", "not_established"), rows[kpi["id"]]
    assert rows["ic_pre_tax_irr"]["status"] == "read", rows
    verdicts = {
        r["rule"]: r["verdict"] for r in run["stages"]["handoff"]["gate"]["rules"]
    }
    assert verdicts["ho.kpis_read"] == "pass", verdicts
    coverage = {c["part"]: c["status"] for c in run["handoff"]["coverage"]}
    assert coverage[f"Chapter {C.INVESTMENT}"] == "covered"
    # M2, K2: the network changed after the case: its figures read not
    # current, never the old number.
    gens = ctx.network.generators
    gens.loc[gens.index[0], "p_nom"] = float(gens["p_nom"].iloc[0]) + 1.0
    stale = run_gates._on(
        ctx, run_handoff.read_kpis, {"kpis": run_profiles.EH_SITE_IC_KPIS}
    )
    assert {(r["status"], r["value"]) for r in stale} == {("not_current", None)}, stale


def _refused(raw):
    from services.finance.case import FinanceRefused

    return FinanceRefused("engine_error", raw)


@pytest.mark.parametrize(
    "error, kind",
    [
        (_refused, "ic_case_refused (engine_error): "),
        (RuntimeError, "ic_case_failed (internal_error): "),
    ],
    ids=["refused", "foreign-error"],
)
def test_pilot_on_v4_with_a_failed_case_completes_with_the_chapter_gap(
    goal_host, session, site, fake_report, monkeypatch, error, kind
):
    """O3, acceptance 36-3: a case that fails leaves the chapter a listed gap
    with its kind; the run completes, but never reads as a clean success: the
    report lists the gap, the hand-off covers the chapter as a gap (counted in
    the completion notice), and the case figures read not established.
    GK B (61) A5 (b, d): a foreign RuntimeError in the case's build is the
    catch-all `ic_case_failed`, never its raw text. F1: the reservation made
    at admission stays charged although no baseline LP ran."""
    from harness import run_report
    from routers import results

    def failing(*a, **k):
        def build():
            raise error("boom in /srv/secret/case.py")

        return build

    monkeypatch.setattr(results, "_ic_build_case", failing)
    spy = _spies(monkeypatch)
    path, record, audit, _ = _pilot(goal_host, session, site, monkeypatch)
    run = record["run"]
    assert run["status"] == "completed", (run.get("reason"), run["stage"])
    case = next(s for s in record["steps"] if s["tool"] == run_ic.TOOL)
    assert case["status"] == "failed" and case["judged_by"] == run_ic.JUDGED
    assert case["error"].startswith(kind) and "/srv" not in case["error"]
    assert _kind(case["error"]) in run_ic.CHAPTER_GAP_KINDS
    used = [
        a for a in audit if a.get("event") == "used" and a.get("tool") == run_ic.TOOL
    ]
    assert [u["solves"] for u in used] == [2] and spy["lps"] == []
    doc = run_report.saved(record, path.parent.parent)
    assert {s.section_id: s for s in doc.sections}[C.INVESTMENT].status != "ok"
    gaps = [str(r[0]) for r in doc.tables["harness_gaps"].rows if r]
    assert f"Chapter not established: {TITLE} ({case['error']})" in gaps, gaps
    coverage = {c["part"]: c["status"] for c in run["handoff"]["coverage"]}
    assert coverage[f"Chapter {C.INVESTMENT}"] == "gap"
    assert run_handoff.gaps(record) >= 1  # the completion notice names the gap count
    rows = {r["id"]: r for r in run["handoff"]["kpis"]}
    assert all(
        rows[k["id"]]["status"] == "not_established"
        for k in run_profiles.EH_SITE_IC_KPIS
    ), rows


def test_pilot_on_v4_with_one_solve_left_at_the_case_lists_the_budget_gap(
    goal_host, session, site, fake_report, monkeypatch
):
    """A2 at the case's report-stage position (M3): with exactly 1 solve left
    when the case is admitted (after the solve, the EH study and the loop),
    the step fails `ic_budget`, no baseline LP runs and nothing is charged
    for it, and the run completes with the chapter's gap."""
    from harness import run_report

    spy = _spies(monkeypatch)
    path, record, audit, _ = _pilot(goal_host, session, site, monkeypatch, left=1)
    run = record["run"]
    assert run["status"] == "completed", (run.get("reason"), run["stage"])
    case = next(s for s in record["steps"] if s["tool"] == run_ic.TOOL)
    assert (case["id"], case["status"], case["judged_by"]) == (
        STEP,
        "failed",
        run_ic.JUDGED,
    )
    assert case["error"] == run_ic.BUDGET and _kind(case["error"]) == "ic_budget"
    assert spy["starts"] == [] and spy["lps"] == []  # no case and no LP ran
    rows = [a for a in audit if a.get("tool") == run_ic.TOOL]
    assert [(a["event"], a.get("reason")) for a in rows] == [
        ("refused", run_envelope.IC_BUDGET_KIND)
    ]
    envelope = json.loads(run_envelope.path_for(path).read_text())
    assert (
        run["budget"]["used"]["solves"]
        == run_envelope.solves_used(envelope)
        == envelope["budget"]["solves"] - 1
    )
    doc = run_report.saved(record, path.parent.parent)
    gaps = [str(r[0]) for r in doc.tables["harness_gaps"].rows if r]
    assert f"Chapter not established: {TITLE} ({run_ic.BUDGET})" in gaps, gaps
